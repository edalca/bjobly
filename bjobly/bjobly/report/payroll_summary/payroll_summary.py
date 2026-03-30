# Copyright (c) 2026, edwinalonso162@hotmail.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt
import erpnext

GROUP_BY_MAP = {
    "Department": "department",
    "Designation": "designation",
    "Branch": "branch",
}


def execute(filters=None):
    if not filters:
        filters = {}

    company_currency = erpnext.get_company_currency(filters.get("company"))
    group_by_label = filters.get("group_by") or "Department"
    group_by_field = GROUP_BY_MAP.get(group_by_label, "department")

    salary_slips = get_salary_slips(filters, group_by_field)
    if not salary_slips:
        return [], []

    # Collect ordered distinct payroll types from the data
    seen_types = []
    for ss in salary_slips:
        pt = ss.get("custom_payroll_type") or _("No Type")
        if pt not in seen_types:
            seen_types.append(pt)
    seen_types = sorted(seen_types)

    columns = get_columns(company_currency, group_by_label, seen_types)

    slip_names = [ss.name for ss in salary_slips]

    # Fuente A: Salary Detail rows where the component has custom_is_employer_contribution = 1
    employer_from_detail = get_employer_amounts_from_detail(slip_names)
    # Fuente B: employer_contributions child table (Salary Detail parentfield)
    employer_from_table = get_employer_amounts_from_table(slip_names)

    summary = {}
    employees_by_group = {}

    for ss in salary_slips:
        group_val = ss.get(group_by_field) or _("No {0}").format(group_by_label)

        if group_val not in summary:
            row = {
                "group_by": group_val,
                "employee_count": 0,
                "total_igss_payment": 0.0,
                "gran_total": 0.0,
                "currency": company_currency,
            }
            for pt in seen_types:
                row[_pt_fieldname(pt)] = 0.0
            summary[group_val] = row
            employees_by_group[group_val] = set()

        employees_by_group[group_val].add(ss.get("employee"))

        ex_rate = flt(ss.exchange_rate) if flt(ss.exchange_rate) > 0 else 1.0
        net = flt(ss.net_pay) * ex_rate

        # Consolidate employer contribution from both sources
        employer_total = (
            flt(employer_from_detail.get(ss.name, 0)) +
            flt(employer_from_table.get(ss.name, 0))
        ) * ex_rate

        pt = ss.get("custom_payroll_type") or _("No Type")
        summary[group_val][_pt_fieldname(pt)] += net
        summary[group_val]["total_igss_payment"] += employer_total
        summary[group_val]["gran_total"] += net + employer_total

    for group_val, row in summary.items():
        row["employee_count"] = len(employees_by_group[group_val])

    data = [summary[k] for k in sorted(summary.keys())]

    all_employees = set()
    for s in employees_by_group.values():
        all_employees |= s

    grand_row = {
        "group_by": _("Gran Total"),
        "employee_count": len(all_employees),
        "total_igss_payment": sum(r["total_igss_payment"] for r in data),
        "gran_total": sum(r["gran_total"] for r in data),
        "currency": company_currency,
        "bold": 1,
    }
    for pt in seen_types:
        fn = _pt_fieldname(pt)
        grand_row[fn] = sum(r.get(fn, 0) for r in data)

    data.append(grand_row)
    return columns, data


def _pt_fieldname(payroll_type):
    return "net_pay_" + frappe.scrub(payroll_type)


def get_columns(currency, group_by_label, payroll_types):
    cols = [
        {"label": _(group_by_label), "fieldname": "group_by",       "fieldtype": "Data",     "width": 200},
        {"label": _("Employees"),    "fieldname": "employee_count",  "fieldtype": "Int",      "width": 100},
    ]

    for pt in payroll_types:
        cols.append({
            "label": _(pt),
            "fieldname": _pt_fieldname(pt),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 180,
        })

    cols += [
        {"label": _("Total IGSS Payment"), "fieldname": "total_igss_payment", "fieldtype": "Currency", "options": "currency", "width": 180},
        {"label": _("Gran Total"),         "fieldname": "gran_total",          "fieldtype": "Currency", "options": "currency", "width": 180},
        {"label": _("Currency"),           "fieldname": "currency",            "fieldtype": "Data",     "hidden": 1},
    ]
    return cols


def get_employer_amounts_from_detail(slip_names):
    """
    Fuente A: SUM of Salary Detail amounts where the Salary Component
    has custom_is_employer_contribution = 1 (earnings/deductions parentfields).
    """
    if not slip_names:
        return {}

    sd = frappe.qb.DocType("Salary Detail")
    sc = frappe.qb.DocType("Salary Component")

    rows = (
        frappe.qb.from_(sd)
        .join(sc).on(sd.salary_component == sc.name)
        .select(sd.parent, sd.amount)
        .where(sd.parent.isin(slip_names))
        .where(sd.parentfield.isin(["earnings", "deductions"]))
        .where(sc.custom_is_employer_contribution == 1)
    ).run(as_dict=1)

    result = {}
    for r in rows:
        result[r.parent] = result.get(r.parent, 0) + flt(r.amount)
    return result


def get_employer_amounts_from_table(slip_names):
    """
    Fuente B: SUM of amounts from the employer_contributions child table
    (parentfield = 'employer_contributions') in Salary Detail.
    """
    if not slip_names:
        return {}

    sd = frappe.qb.DocType("Salary Detail")

    rows = (
        frappe.qb.from_(sd)
        .select(sd.parent, sd.amount)
        .where(sd.parent.isin(slip_names))
        .where(sd.parentfield == "employer_contributions")
    ).run(as_dict=1)

    result = {}
    for r in rows:
        result[r.parent] = result.get(r.parent, 0) + flt(r.amount)
    return result


def get_salary_slips(filters, group_by_field):
    ss = frappe.qb.DocType("Salary Slip")
    emp = frappe.qb.DocType("Employee")

    query = (
        frappe.qb.from_(ss)
        .left_join(emp).on(ss.employee == emp.name)
        .select(
            ss.name,
            ss.employee,
            ss.net_pay,
            ss.exchange_rate,
            ss.custom_payroll_type,
            emp[group_by_field],
        )
    )

    if filters.get("docstatus"):
        status_map = {"Draft": 0, "Submitted": 1, "Cancelled": 2}
        query = query.where(ss.docstatus == status_map.get(filters.get("docstatus"), 1))
    else:
        query = query.where(ss.docstatus == 1)

    if filters.get("from_date"):
        query = query.where(ss.start_date >= filters.get("from_date"))
    if filters.get("to_date"):
        query = query.where(ss.end_date <= filters.get("to_date"))
    if filters.get("company"):
        query = query.where(ss.company == filters.get("company"))
    if filters.get("payroll_type"):
        query = query.where(ss.custom_payroll_type == filters.get("payroll_type"))

    return query.run(as_dict=1)
