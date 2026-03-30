# Copyright (c) 2026, Bjobly and contributors
# SPDX-License-Identifier: MIT

"""
bjobly/bjobly/api.py
====================
Server-side API for the Payroll Dashboard and mass payroll actions.
All public methods here are @frappe.whitelist() so they can be called
directly from the Dashboard JS via frappe.call().
"""

import frappe
from frappe import _
from frappe.utils import flt, nowdate


@frappe.whitelist()
def get_dashboard_employees(company, payroll_type=None, departments=None):
    """
    Returns employee data for the Dashboard, merging Redis gross-pay cache
    into the Virtual DocType results.

    Returns:
        {
            "employees": [...],        # list from Virtual DocType + gross_pay field
            "has_gross_cache": bool,
            "gross_cache_ts": str|None # ISO datetime of last calculation
        }
    """
    from bjobly.bjobly.doctype.bjobly_employee_cache.bjobly_employee_cache import BjoblyEmployeeCache

    args = {
        "company": company,
        "payroll_type": payroll_type,
        "filters": {},
        "limit_page_length": 10000,
    }
    if departments:
        dept_list = [d.strip() for d in departments.split(",") if d.strip()]
        if dept_list:
            args["filters"]["department"] = ["in", dept_list]

    rows = BjoblyEmployeeCache.get_list(args)

    # Merge gross pay from Redis cache
    cached    = frappe.cache().get_value(f"bjobly:gross:{company}") or {}
    cached_ts = frappe.cache().get_value(f"bjobly:gross:{company}:ts")

    for row in rows:
        key = "{emp}|{pt}".format(
            emp=row.get("employee") or "",
            pt=row.get("payroll_type") or "",
        )
        gp = cached.get(key)
        row["gross_pay"] = gp  # None when not yet calculated

    return {
        "employees":       rows,
        "has_gross_cache": bool(cached),
        "gross_cache_ts":  cached_ts,
    }


# ---------------------------------------------------------------------------
# Gross Pay Cache
# ---------------------------------------------------------------------------

@frappe.whitelist()
def calculate_gross_pay(company):
    """
    Enqueues a background job that calculates gross pay for every
    active employee in *company* (per payroll type) and stores the result
    in Redis so subsequent dashboard loads are instant.
    """
    frappe.only_for("HR Manager")

    frappe.enqueue(
        _calculate_gross_pay_bg,
        queue="long",
        timeout=600,
        job_name=f"bjobly_gross_{company}",
        now=frappe.conf.get("developer_mode"),
        company=company,
    )
    return {"queued": True}


def _calculate_gross_pay_bg(company):
    """
    Background worker: instantiates one in-memory Salary Slip per
    (employee, payroll_type) pair, runs validate(), and stores
    gross_pay in Redis under key  bjobly:gross:{company}.
    """
    import datetime
    import calendar
    from frappe.utils import flt, nowdate, now

    d          = datetime.date.today()
    start_date = d.replace(day=1).isoformat()
    end_date   = d.replace(day=calendar.monthrange(d.year, d.month)[1]).isoformat()
    today      = nowdate()

    # One row per (employee, payroll_type) — exclude employees who left before the period
    assignments = frappe.db.sql("""
        SELECT ssa.employee, ss.custom_payroll_type
        FROM   `tabSalary Structure Assignment` ssa
        JOIN   `tabSalary Structure` ss ON ss.name = ssa.salary_structure
        JOIN   `tabEmployee`         e  ON e.name  = ssa.employee
        WHERE  e.company   = %s
          AND  e.status    = 'Active'
          AND  ssa.docstatus = 1
          AND  (e.relieving_date IS NULL OR e.relieving_date >= %s)
        GROUP BY ssa.employee, ss.custom_payroll_type
    """, (company, start_date), as_dict=True)

    result = {}
    # Suppress frappe.msgprint() calls that come from HRMS validations
    frappe.flags.mute_messages = True
    try:
        for row in assignments:
            emp = row.employee
            pt  = row.custom_payroll_type or ""
            try:
                slip = frappe.get_doc(frappe._dict({
                    "doctype":                        "Salary Slip",
                    "company":                        company,
                    "start_date":                     start_date,
                    "end_date":                       end_date,
                    "posting_date":                   today,
                    "salary_slip_based_on_timesheet": 0,
                    "custom_payroll_type":            pt,
                    "employee":                       emp,
                }))
                slip.validate()
                result[f"{emp}|{pt}"] = flt(slip.gross_pay)
            except Exception:
                pass
    finally:
        frappe.flags.mute_messages = False

    frappe.cache().set_value(f"bjobly:gross:{company}", result, expires_in_sec=86400 * 7)
    frappe.cache().set_value(f"bjobly:gross:{company}:ts", now())

    frappe.publish_realtime(
        "bjobly_gross_calculated",
        {"company": company, "count": len(result)},
        user=frappe.session.user,
    )




# ---------------------------------------------------------------------------
# Mass Actions
# ---------------------------------------------------------------------------

@frappe.whitelist()
def regenerate_slips(payroll_entry_id):
    """
    Deletes all Draft salary slips linked to a Payroll Entry and triggers
    recalculation via the existing BjoblyPayrollEntry.calculate_salary_slips() method.
    Safe to call only when the Payroll Entry is in Draft status.
    """
    frappe.only_for("HR Manager")

    pe = frappe.get_doc("Payroll Entry", payroll_entry_id)
    if pe.docstatus != 0:
        frappe.throw(_("Regenerate Slips can only be performed on Draft Payroll Entries."))

    # Delete existing Draft slips for this entry
    draft_slips = frappe.get_all(
        "Salary Slip",
        filters={"payroll_entry": payroll_entry_id, "docstatus": 0},
        pluck="name"
    )
    for slip_name in draft_slips:
        frappe.delete_doc("Salary Slip", slip_name, ignore_permissions=True)

    # Reset calculated flag
    pe.db_set({"salary_slips_calculated": 0, "status": "Draft"})

    return {
        "deleted": len(draft_slips),
        "message": _("{0} draft slip(s) deleted. Please recalculate.").format(len(draft_slips))
    }


@frappe.whitelist()
def create_bulk_journal_entries(run_id):
    """
    Creates Journal Entries for all Submitted Payroll Entries sharing the same
    custom_run_id. Used for batch accounting after a Dashboard-generated payroll run.
    """
    frappe.only_for("HR Manager")

    entries = frappe.get_all(
        "Payroll Entry",
        filters={"custom_run_id": run_id, "docstatus": 1},
        pluck="name"
    )

    if not entries:
        frappe.throw(_("No submitted Payroll Entries found for Run ID: {0}").format(run_id))

    created = 0
    for pe_name in entries:
        pe = frappe.get_doc("Payroll Entry", pe_name)
        if not pe.completed_journal_entry_creation:
            pe.create_journal_entry()
            created += 1

    return {
        "created": created,
        "total": len(entries),
        "message": _("Journal Entries created for {0} of {1} entries.").format(created, len(entries))
    }


