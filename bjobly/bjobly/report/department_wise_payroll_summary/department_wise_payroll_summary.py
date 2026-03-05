# Copyright (c) 2026, edwinalonso162@hotmail.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt
import erpnext

def execute(filters=None):
    """
    Main entry point for the report. 
    Calculates and groups payroll summary data by department.
    """
    if not filters:
        filters = {}

    company_currency = erpnext.get_company_currency(filters.get("company"))
    
    # 1. Fetch records from database
    salary_slips = get_salary_slips(filters)
    if not salary_slips:
        return [], []

    # 2. Define report columns
    columns = get_columns(company_currency)

    # 3. Aggregate data by department
    data = []
    department_summary = {}

    # Check if the loan column exists once to optimize the loop
    has_loans = frappe.db.has_column("Salary Slip", "total_loan_repayment")

    for ss in salary_slips:
        # Group by department; use "No Department" if empty
        dept = ss.department or _("No Department")
        
        if dept not in department_summary:
            department_summary[dept] = {
                "department": dept,
                "gross_pay": 0.0,
                "total_deduction": 0.0,
                "net_pay": 0.0,
                "currency": company_currency
            }

        # Handle currency exchange rate
        ex_rate = flt(ss.exchange_rate) if flt(ss.exchange_rate) > 0 else 1.0
        
        # Accumulate totals converted to company currency
        department_summary[dept]["gross_pay"] += flt(ss.gross_pay) * ex_rate
        
        # Calculate deductions including loans if applicable
        total_ded = flt(ss.total_deduction)
        if has_loans:
            total_ded += flt(ss.get("total_loan_repayment", 0))
            
        department_summary[dept]["total_deduction"] += total_ded * ex_rate
        department_summary[dept]["net_pay"] += flt(ss.net_pay) * ex_rate

    # 4. Format data for the report grid
    for dept in sorted(department_summary.keys()):
        data.append(department_summary[dept])

    return columns, data

def get_columns(currency):
    """
    Define the headers for the report table.
    """
    return [
        {
            "label": _("Department"),
            "fieldname": "department",
            "fieldtype": "Link",
            "options": "Department",
            "width": 200,
        },
        {
            "label": _("Gross Pay"),
            "fieldname": "gross_pay",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 150,
        },
        {
            "label": _("Total Deduction"),
            "fieldname": "total_deduction",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 150,
        },
        {
            "label": _("Net Pay"),
            "fieldname": "net_pay",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 150,
        },
        {
            "label": _("Currency"),
            "fieldname": "currency",
            "fieldtype": "Data",
            "hidden": 1
        }
    ]

def get_salary_slips(filters):
    """
    Fetch Salary Slip records from the database using Query Builder (qb) 
    based on the provided filters.
    """
    salary_slip = frappe.qb.DocType("Salary Slip")
    
    # Safe base columns
    select_cols = [
        salary_slip.department,
        salary_slip.gross_pay,
        salary_slip.total_deduction,
        salary_slip.net_pay,
        salary_slip.exchange_rate
    ]

    # Add loan column to SELECT only if it exists in the database schema
    if frappe.db.has_column("Salary Slip", "total_loan_repayment"):
        select_cols.append(salary_slip.total_loan_repayment)

    query = frappe.qb.from_(salary_slip).select(*select_cols)

    # Apply Document Status filter
    if filters.get("docstatus"):
        status_map = {"Draft": 0, "Submitted": 1, "Cancelled": 2}
        query = query.where(salary_slip.docstatus == status_map.get(filters.get("docstatus"), 1))
    else:
        # Default to Submitted if no status is selected
        query = query.where(salary_slip.docstatus == 1)

    # Apply optional filters
    if filters.get("from_date"):
        query = query.where(salary_slip.start_date >= filters.get("from_date"))
    if filters.get("to_date"):
        query = query.where(salary_slip.end_date <= filters.get("to_date"))
    if filters.get("company"):
        query = query.where(salary_slip.company == filters.get("company"))
    if filters.get("department"):
        query = query.where(salary_slip.department == filters.get("department"))

    return query.run(as_dict=1)