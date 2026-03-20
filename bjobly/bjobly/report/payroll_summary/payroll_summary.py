# Copyright (c) 2026, edwinalonso162@hotmail.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, cint
import erpnext

EMPLOYER_IGSS_RATE = 0.1267  # 12.67%

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
    employment_type = filters.get("employment_type")
    all_et = cint(filters.get("all_employment_types", 1))

    salary_slips = get_salary_slips(filters, group_by_field, include_employment_type=all_et)
    if not salary_slips:
        return [], []

    # When showing all employment types, collect distinct ones from the data
    employment_types = []
    if all_et:
        seen = []
        for ss in salary_slips:
            et = ss.get("employment_type") or _("No Type")
            if et not in seen:
                seen.append(et)
        employment_types = sorted(seen)

    columns = get_columns(company_currency, group_by_label, employment_type, employment_types)

    slip_names = [ss.name for ss in salary_slips]
    igss_by_slip = get_component_amounts_by_slip(slip_names, "I.G.S.S.")
    nominal_by_slip = get_component_amounts_by_slip(slip_names, "Nominal")

    summary = {}
    for ss in salary_slips:
        group_val = ss.get(group_by_field) or _("No {0}").format(group_by_label)

        if group_val not in summary:
            row = {
                "group_by": group_val,
                "total_igss_payment": 0.0,
                "gran_total": 0.0,
                "currency": company_currency,
            }
            if all_et:
                for et in employment_types:
                    row[_et_fieldname(et)] = 0.0
            else:
                row["net_pay"] = 0.0
            summary[group_val] = row

        ex_rate = flt(ss.exchange_rate) if flt(ss.exchange_rate) > 0 else 1.0
        igss_laboral = flt(igss_by_slip.get(ss.name, 0)) * ex_rate
        igss_patronal = flt(nominal_by_slip.get(ss.name, 0)) * ex_rate * EMPLOYER_IGSS_RATE
        net = flt(ss.net_pay) * ex_rate

        if all_et:
            et = ss.get("employment_type") or _("No Type")
            summary[group_val][_et_fieldname(et)] += net
        else:
            summary[group_val]["net_pay"] += net

        summary[group_val]["total_igss_payment"] += igss_laboral + igss_patronal
        summary[group_val]["gran_total"] += net + igss_laboral + igss_patronal

    data = [summary[k] for k in sorted(summary.keys())]

    # Grand total row
    grand_row = {
        "group_by": _("Gran Total"),
        "total_igss_payment": sum(r["total_igss_payment"] for r in data),
        "gran_total": sum(r["gran_total"] for r in data),
        "currency": company_currency,
        "bold": 1,
    }
    if all_et:
        for et in employment_types:
            fn = _et_fieldname(et)
            grand_row[fn] = sum(r.get(fn, 0) for r in data)
    else:
        grand_row["net_pay"] = sum(r["net_pay"] for r in data)

    data.append(grand_row)
    return columns, data


def _et_fieldname(employment_type):
    """Converts an employment type name to a safe fieldname."""
    return "net_pay_" + frappe.scrub(employment_type)


def get_columns(currency, group_by_label, employment_type=None, employment_types=None):
    cols = [
        {
            "label": _(group_by_label),
            "fieldname": "group_by",
            "fieldtype": "Data",
            "width": 200,
        }
    ]

    if employment_types:
        # One Net Pay column per employment type
        for et in employment_types:
            cols.append({
                "label": _("{0}").format(et),
                "fieldname": _et_fieldname(et),
                "fieldtype": "Currency",
                "options": "currency",
                "width": 160,
            })
    else:
        net_pay_label = _("{0}").format(employment_type) if employment_type else _("Net Pay")
        cols.append({
            "label": net_pay_label,
            "fieldname": "net_pay",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 150,
        })

    cols += [
        {
            "label": _("Total IGSS Payment"),
            "fieldname": "total_igss_payment",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 180,
        },
        {
            "label": _("Gran Total"),
            "fieldname": "gran_total",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 180,
        },
        {
            "label": _("Currency"),
            "fieldname": "currency",
            "fieldtype": "Data",
            "hidden": 1,
        },
    ]
    return cols


def get_component_amounts_by_slip(slip_names, component_name):
    if not slip_names:
        return {}
    sd = frappe.qb.DocType("Salary Detail")
    rows = (
        frappe.qb.from_(sd)
        .select(sd.parent, sd.amount)
        .where(sd.parent.isin(slip_names))
        .where(sd.salary_component == component_name)
        .where(sd.parentfield.isin(["earnings", "deductions"]))
    ).run(as_dict=1)
    return {r.parent: flt(r.amount) for r in rows}


def get_salary_slips(filters, group_by_field, include_employment_type=False):
    ss = frappe.qb.DocType("Salary Slip")
    emp = frappe.qb.DocType("Employee")

    select_cols = [
        ss.name,
        ss.net_pay,
        ss.exchange_rate,
        ss[group_by_field],
    ]
    if include_employment_type:
        select_cols.append(emp.employment_type)

    query = (
        frappe.qb.from_(ss)
        .left_join(emp).on(ss.employee == emp.name)
        .select(*select_cols)
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
    if filters.get("employment_type"):
        query = query.where(emp.employment_type == filters.get("employment_type"))

    return query.run(as_dict=1)
