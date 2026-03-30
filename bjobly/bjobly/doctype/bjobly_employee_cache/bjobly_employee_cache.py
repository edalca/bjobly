# Copyright (c) 2026, Bjobly and contributors
# SPDX-License-Identifier: MIT

import frappe
import json
from frappe.model.document import Document
from frappe.utils import flt

class BjoblyEmployeeCache(Document):
    """
    Virtual DocType for the Payroll Dashboard.
    Provides real-time, aggregated data from Employee and Salary Structure Assignment.
    """

    @staticmethod
    def get_list(args):
        args = frappe._dict(args)
        filters = args.get("filters") or {}
        
        # Helper to extract a single clean value or list
        def get_clean_filter(fieldname):
            val = args.get(fieldname)
            if val is not None: return val
            
            # Search in filters
            if isinstance(filters, dict):
                return filters.get(fieldname)
            
            # Search in list-style filters
            if isinstance(filters, (list, tuple)):
                for f in filters:
                    if isinstance(f, (list, tuple)) and len(f) >= 4 and f[1] == fieldname:
                        return f[3]
            
            # Try attribute access for Filters object
            return getattr(filters, fieldname, None)

        company = get_clean_filter("company")
        payroll_type = get_clean_filter("payroll_type")
        department = get_clean_filter("department")

        where_clause = [
            "e.status = 'Active'",
            "(e.relieving_date IS NULL OR e.relieving_date >= CURDATE())",
        ]
        query_args = {}

        if company and isinstance(company, str):
            where_clause.append("e.company = %(company)s")
            query_args["company"] = company

        if department:
            if isinstance(department, list):
                if len(department) == 2 and department[0] == "in":
                    where_clause.append("e.department IN %(departments)s")
                    query_args["departments"] = tuple(department[1])
                else:
                    where_clause.append("e.department IN %(departments)s")
                    query_args["departments"] = tuple(department)
            elif isinstance(department, str):
                where_clause.append("e.department = %(department)s")
                query_args["department"] = department

        # Base SQL
        # The subquery groups by (employee, payroll_type) so dual-contract employees
        # produce two rows — one for Regular Salary and one for Honorariums.
        sql = f"""
            SELECT
                CONCAT(COALESCE(pt_agg.custom_payroll_type, 'Sin Asignar'), '-', e.name) AS name,
                e.name                                                          AS employee,
                e.employee_name                                                 AS employee_name,
                COALESCE(pt_agg.custom_payroll_type, 'Sin Asignar')            AS payroll_type,
                COALESCE(pt_agg.custom_payroll_type, 'Sin Asignar')            AS status,
                e.company                                                       AS company,
                e.department                                                    AS department,
                e.designation                                                   AS designation,
                e.branch                                                        AS branch,
                e.image                                                         AS image,
                e.modified                                                      AS modified,
                e.creation                                                      AS creation,
                COALESCE(pt_agg.base, 0)                                       AS current_base_salary,
                COALESCE(pt_agg.variable, 0)                                   AS variable_salary,
                COALESCE(pt_agg.base, 0) + COALESCE(pt_agg.variable, 0)       AS total_salary
            FROM `tabEmployee` e
            LEFT JOIN (
                -- Latest assignment per (employee, payroll_type)
                SELECT ssa1.employee, ss1.custom_payroll_type, ssa1.base, ssa1.variable
                FROM `tabSalary Structure Assignment` ssa1
                INNER JOIN `tabSalary Structure` ss1
                    ON ssa1.salary_structure = ss1.name
                INNER JOIN (
                    SELECT
                        ssa2.employee,
                        COALESCE(ss2.custom_payroll_type, 'Sin Asignar') AS pt,
                        MAX(ssa2.from_date)                              AS max_date
                    FROM `tabSalary Structure Assignment` ssa2
                    INNER JOIN `tabSalary Structure` ss2
                        ON ssa2.salary_structure = ss2.name
                    WHERE ssa2.docstatus = 1
                    GROUP BY ssa2.employee, COALESCE(ss2.custom_payroll_type, 'Sin Asignar')
                ) latest
                    ON  ssa1.employee = latest.employee
                    AND COALESCE(ss1.custom_payroll_type, 'Sin Asignar') = latest.pt
                    AND ssa1.from_date = latest.max_date
                WHERE ssa1.docstatus = 1
            ) pt_agg ON e.name = pt_agg.employee
            WHERE
                {" AND ".join(where_clause)}
        """

        if payroll_type and isinstance(payroll_type, str):
            sql += " AND COALESCE(pt_agg.custom_payroll_type, 'Sin Asignar') = %(payroll_type)s"
            query_args["payroll_type"] = payroll_type

        # Sort and Limit
        order_by = args.get("order_by") or "e.name ASC"
        
        # Sanitize order_by: 
        # 1. Handle magic Frappe flags
        if "KEEP_DEFAULT_ORDERING" in order_by:
            order_by = "e.name ASC"
            
        # 2. Handle virtual table prefix
        vt_prefix = f"`tabBjobly Employee Cache`."
        if vt_prefix in order_by:
            order_by = order_by.replace(vt_prefix, "e.")
        order_by = order_by.replace("tabBjobly Employee Cache.", "e.")

        limit_start = int(flt(args.get("limit_start") or 0))
        limit_page_length = int(flt(args.get("limit_page_length") or 20))

        sql += f" ORDER BY {order_by} LIMIT {limit_start}, {limit_page_length}"

        return frappe.db.sql(sql, query_args, as_dict=True)

    @staticmethod
    def get_count(args):
        args = frappe._dict(args)
        filters = args.get("filters") or {}

        def get_clean_filter(fieldname):
            val = args.get(fieldname)
            if val is not None: return val
            if isinstance(filters, dict): return filters.get(fieldname)
            if isinstance(filters, (list, tuple)):
                for f in filters:
                    if isinstance(f, (list, tuple)) and len(f) >= 4 and f[1] == fieldname:
                        return f[3]
            return getattr(filters, fieldname, None)

        company = get_clean_filter("company")
        payroll_type = get_clean_filter("payroll_type")
        department = get_clean_filter("department")

        where_clause = [
            "e.status = 'Active'",
            "(e.relieving_date IS NULL OR e.relieving_date >= CURDATE())",
        ]
        query_args = {}

        if company and isinstance(company, str):
            where_clause.append("e.company = %(company)s")
            query_args["company"] = company

        if department:
            if isinstance(department, list):
                if len(department) == 2 and department[0] == "in":
                    where_clause.append("e.department IN %(departments)s")
                    query_args["departments"] = tuple(department[1])
                else:
                    where_clause.append("e.department IN %(departments)s")
                    query_args["departments"] = tuple(department)
            elif isinstance(department, str):
                where_clause.append("e.department = %(department)s")
                query_args["department"] = department

        sql = f"""
            SELECT COUNT(*)
            FROM `tabEmployee` e
            LEFT JOIN (
                SELECT ssa1.employee, ss1.custom_payroll_type
                FROM `tabSalary Structure Assignment` ssa1
                INNER JOIN `tabSalary Structure` ss1
                    ON ssa1.salary_structure = ss1.name
                INNER JOIN (
                    SELECT
                        ssa2.employee,
                        COALESCE(ss2.custom_payroll_type, 'Sin Asignar') AS pt,
                        MAX(ssa2.from_date)                              AS max_date
                    FROM `tabSalary Structure Assignment` ssa2
                    INNER JOIN `tabSalary Structure` ss2
                        ON ssa2.salary_structure = ss2.name
                    WHERE ssa2.docstatus = 1
                    GROUP BY ssa2.employee, COALESCE(ss2.custom_payroll_type, 'Sin Asignar')
                ) latest
                    ON  ssa1.employee = latest.employee
                    AND COALESCE(ss1.custom_payroll_type, 'Sin Asignar') = latest.pt
                    AND ssa1.from_date = latest.max_date
                WHERE ssa1.docstatus = 1
            ) pt_agg ON e.name = pt_agg.employee
            WHERE {" AND ".join(where_clause)}
        """

        if payroll_type and isinstance(payroll_type, str):
            sql += " AND COALESCE(pt_agg.custom_payroll_type, 'Sin Asignar') = %(payroll_type)s"
            query_args["payroll_type"] = payroll_type

        return frappe.db.sql(sql, query_args)[0][0]

    @staticmethod
    def get_stats(args):
        return {}
