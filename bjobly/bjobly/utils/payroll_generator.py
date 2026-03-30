# Copyright (c) 2026, Bjobly and contributors
# SPDX-License-Identifier: MIT

"""
bjobly/bjobly/utils/payroll_generator.py
=========================================
Background engine for mass payroll generation from the Payroll Dashboard.
Triggered via frappe.call → generate_payroll() → frappe.enqueue → _run_generation().
"""

import uuid
import frappe
from frappe import _
from frappe.utils import flt, nowdate, formatdate


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@frappe.whitelist()
def preview_payroll(
    company,
    payroll_type,
    start_date,
    end_date,
    period_label=None,
    letter_case="Uppercase",
    group_by_department=False,
    group_by_branch=False,
    departments=None,
):
    """
    Returns a list of dicts describing what Payroll Entry batches would be
    created — without actually creating anything.
    """
    frappe.only_for("HR Manager")

    import json
    if isinstance(departments, str):
        try:
            departments = json.loads(departments)
        except Exception:
            departments = [departments] if departments else []

    group_by_department = frappe.utils.sbool(group_by_department)
    group_by_branch     = frappe.utils.sbool(group_by_branch)

    payroll_types = ["Regular Salary", "Honorariums"] if payroll_type == "Both" else [payroll_type]
    date_range    = period_label or _format_date_range(start_date, end_date)

    preview = []
    for pt in payroll_types:
        groups = _build_groups(company, pt, departments, group_by_department, group_by_branch, start_date=start_date)
        for group_key, group_data in groups.items():
            employees = group_data["employees"]
            if not employees:
                continue

            # Exclude employees that already have a salary slip for this period + payroll type
            already_slipped = _get_employees_with_existing_slips(employees, start_date, end_date, pt)
            employees = [e for e in employees if e not in already_slipped]
            if not employees:
                continue

            description = _build_description(pt, group_key, date_range, group_by_department, group_by_branch, letter_case)
            totals = _calculate_totals_in_memory(employees, company, start_date, end_date, payroll_type=pt)
            preview.append({
                "description":    description,
                "payroll_type":   pt,
                "group_key":      group_key,
                "employee_count": len(employees),
                "gross_pay":      totals["gross_pay"],
                "total_deduction": totals["total_deduction"],
                "net_pay":        totals["net_pay"],
                "errors":         totals["errors"],
            })

    return preview


@frappe.whitelist()
def generate_payroll(
    company,
    payroll_type,
    start_date,
    end_date,
    payment_account,
    payroll_frequency="Monthly",
    period_label=None,
    posting_date=None,
    letter_case="Uppercase",
    group_by_department=False,
    group_by_branch=False,
    departments=None,
    currency=None,
):
    """
    Entry point called from the Dashboard's "Generate Payroll" dialog.
    Validates inputs, then enqueues the background job.

    payroll_type: "Regular Salary" | "Honorariums" | "Both"
    """
    frappe.only_for("HR Manager")

    if not all([company, payroll_type, start_date, end_date, payment_account]):
        frappe.throw(_("Company, Payroll Type, Dates, and Payment Account are mandatory."))

    if payroll_type not in ("Regular Salary", "Honorariums", "Both"):
        frappe.throw(_("Payroll Type must be 'Regular Salary', 'Honorariums', or 'Both'."))

    if not currency:
        currency = frappe.db.get_value("Company", company, "default_currency")

    import json
    if isinstance(departments, str):
        try:
            departments = json.loads(departments)
        except Exception:
            departments = [departments] if departments else []

    group_by_department = frappe.utils.sbool(group_by_department)
    group_by_branch     = frappe.utils.sbool(group_by_branch)

    run_id = str(uuid.uuid4())

    job_kwargs = dict(
        company=company,
        payroll_type=payroll_type,
        start_date=start_date,
        end_date=end_date,
        payment_account=payment_account,
        payroll_frequency=payroll_frequency,
        period_label=period_label or _format_date_range(start_date, end_date),
        posting_date=posting_date or nowdate(),
        letter_case=letter_case,
        group_by_department=group_by_department,
        group_by_branch=group_by_branch,
        departments=departments or [],
        currency=currency,
        run_id=run_id,
    )

    if frappe.conf.get("developer_mode"):
        # In dev mode run synchronously so there's no silent queue failure
        _run_generation(**job_kwargs)
    else:
        frappe.enqueue(
            _run_generation,
            queue="long",
            timeout=3000,
            job_name=f"bjobly_payroll_{run_id[:8]}",
            **job_kwargs,
        )

    return {
        "run_id":   run_id,
        "message": _("Payroll generation queued. Run ID: {0}").format(run_id),
    }


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

def _run_generation(
    company, payroll_type, start_date, end_date,
    payment_account, payroll_frequency,
    period_label, posting_date, letter_case, group_by_department, group_by_branch,
    departments, currency, run_id,
):
    """
    Creates Payroll Entry lots grouped by payroll_type and optionally
    department/branch. All entries share the same run_id.
    After inserting each Payroll Entry, calculates salary slips in-memory
    so the employees grid is populated with financial values.
    """
    from bjobly.overrides.payroll_entry import calculate_salary_slips_for_employees

    try:
        payroll_types = ["Regular Salary", "Honorariums"] if payroll_type == "Both" else [payroll_type]

        for pt in payroll_types:
            groups = _build_groups(company, pt, departments, group_by_department, group_by_branch, start_date=start_date)

            for group_key, group_data in groups.items():
                employees = group_data["employees"]
                if not employees:
                    continue

                # Skip employees that already have a slip for this period + payroll type
                already_slipped = _get_employees_with_existing_slips(employees, start_date, end_date, pt)
                employees = [e for e in employees if e not in already_slipped]
                if not employees:
                    continue

                description = _build_description(pt, group_key, period_label, group_by_department, group_by_branch, letter_case)

                pe = frappe.get_doc({
                    "doctype":           "Payroll Entry",
                    "company":           company,
                    "start_date":        start_date,
                    "end_date":          end_date,
                    "posting_date":      posting_date,
                    "payroll_frequency": payroll_frequency,
                    "currency":          currency,
                    "exchange_rate":     1,
                    "payroll_payable_account": payment_account,
                    "description":       description,
                    "custom_payroll_type": pt,
                    "custom_run_id":     run_id,
                    "validate_attendance": 0,
                    "department":        group_data["department"],
                    "branch":            group_data["branch"],
                })

                for emp in employees:
                    pe.append("employees", {"employee": emp})

                pe.number_of_employees = len(employees)
                pe.insert(ignore_permissions=True)
                frappe.db.commit()

                # Calculate salary slips in-memory to populate employee grid values
                calc_args = frappe._dict({
                    "doctype":                                    "Salary Slip",
                    "salary_slip_based_on_timesheet":             0,
                    "payroll_frequency":                          payroll_frequency,
                    "start_date":                                 start_date,
                    "end_date":                                   end_date,
                    "company":                                    company,
                    "posting_date":                               posting_date,
                    "custom_payroll_type":                        pt,
                    "deduct_tax_for_unsubmitted_tax_exemption_proof": 0,
                    "payroll_entry":                              pe.name,
                    "exchange_rate":                              1,
                    "currency":                                   currency,
                })
                calculate_salary_slips_for_employees(employees, calc_args, publish_progress=False)

    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Bjobly Payroll Generator failed (run_id={run_id})")
        raise
    finally:
        frappe.publish_realtime(
            "bjobly_payroll_generated",
            {"run_id": run_id},
            user=frappe.session.user,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _calculate_totals_in_memory(employees, company, start_date, end_date, payroll_type=None):
    """
    Instantiates Salary Slip docs in RAM (no insert) for each employee,
    runs validate() to trigger all business logic, and returns aggregated totals.
    """
    gross_pay       = 0.0
    total_deduction = 0.0
    net_pay         = 0.0
    errors          = []

    slip_args = frappe._dict({
        "doctype":            "Salary Slip",
        "company":            company,
        "start_date":         start_date,
        "end_date":           end_date,
        "posting_date":       nowdate(),
        "salary_slip_based_on_timesheet": 0,
        "custom_payroll_type": payroll_type,
    })

    for emp in employees:
        try:
            args = slip_args.copy()
            args["employee"] = emp
            slip = frappe.get_doc(args)
            slip.validate()
            gross_pay       += flt(slip.gross_pay)
            total_deduction += flt(slip.total_deduction)
            net_pay         += flt(slip.net_pay)
        except Exception as e:
            errors.append({"employee": emp, "error": str(e)})

    return {
        "gross_pay":       gross_pay,
        "total_deduction": total_deduction,
        "net_pay":         net_pay,
        "errors":          errors,
    }


def _build_groups(company, payroll_type, departments, group_by_department, group_by_branch, start_date=None):
    """
    Returns { group_key: {"employees": [...], "department": str|None, "branch": str|None} }
    for a single payroll_type. group_key encodes dept and/or branch depending on options.
    Excludes employees whose relieving_date is before start_date.
    """
    conds = ["e.company = %(company)s", "e.status = 'Active'"]
    sql_args = {"company": company}

    if start_date:
        conds.append("(e.relieving_date IS NULL OR e.relieving_date >= %(start_date)s)")
        sql_args["start_date"] = start_date

    if departments:
        conds.append("e.department IN %(departments)s")
        sql_args["departments"] = tuple(departments)

    employees = frappe.db.sql(
        "SELECT e.name, e.department, e.branch FROM `tabEmployee` e WHERE " + " AND ".join(conds),
        sql_args, as_dict=True,
    )

    eligible = [e for e in employees if _employee_has_payroll_type(e.name, payroll_type)]

    if not eligible:
        return {}

    # Build a map of department name → department_name (display label without company suffix)
    dept_names = {}
    dept_values = list({e.department for e in eligible if e.department})
    if dept_values:
        rows = frappe.get_all("Department", filters={"name": ["in", dept_values]}, fields=["name", "department_name"])
        dept_names = {r.name: r.department_name for r in rows}

    groups = {}
    for emp in eligible:
        dept        = emp.department or None
        dept_label  = dept_names.get(dept, dept) if dept else None
        branch      = emp.branch or None

        parts = []
        if group_by_department:
            parts.append(dept_label or _("Sin Departamento"))
        if group_by_branch and branch:
            parts.append(branch)

        key = " - ".join(parts) if parts else payroll_type

        if key not in groups:
            groups[key] = {
                "employees":  [],
                "department": dept if group_by_department else None,
                "branch":     branch if group_by_branch else None,
            }
        groups[key]["employees"].append(emp.name)

    return groups


def _build_description(payroll_type, group_key, date_range, group_by_department, group_by_branch, letter_case="Uppercase"):
    """
    {Payroll Type} - {group_key} / {date_range}   (with grouping)
    {Payroll Type} / {date_range}                  (without grouping)
    Applies letter_case: Uppercase | Lowercase | Title Case
    """
    has_group = group_by_department or group_by_branch
    if has_group and group_key != payroll_type:
        text = f"{_(payroll_type)} - {group_key} / {date_range}"
    else:
        text = f"{_(payroll_type)} / {date_range}"

    if letter_case == "Uppercase":
        return text.upper()
    elif letter_case == "Lowercase":
        return text.lower()
    elif letter_case == "Title Case":
        return text.title()
    return text


def _get_employees_with_existing_slips(employees, start_date, end_date, payroll_type):
    """Returns a set of employees that already have a salary slip for the given period and payroll type."""
    if not employees:
        return set()
    rows = frappe.db.sql("""
        SELECT DISTINCT employee
        FROM `tabSalary Slip`
        WHERE employee IN ({})
          AND start_date = %s
          AND end_date   = %s
          AND custom_payroll_type = %s
          AND docstatus < 2
    """.format(", ".join(["%s"] * len(employees))),
        tuple(employees) + (start_date, end_date, payroll_type)
    )
    return {r[0] for r in rows}


def _format_date_range(start_date, end_date):
    """Returns e.g. '01/03/2026 al 31/03/2026'"""
    return f"{formatdate(start_date)} al {formatdate(end_date)}"


def _employee_has_payroll_type(employee, payroll_type):
    """
    Checks if an employee has an active Salary Structure Assignment
    with the specified payroll_type.
    """
    return frappe.db.exists(
        "Salary Structure Assignment",
        {
            "employee": employee,
            "docstatus": 1,
            "salary_structure": (
                "in",
                frappe.get_all("Salary Structure",
                    filters={"custom_payroll_type": payroll_type}, pluck="name")
            )
        }
    )
