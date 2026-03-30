import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe import _

def after_install():
    """
    Called after the app is installed. It sets up the custom fields,
    titles, and list view configurations.
    """
    # 1. Crear campos personalizados
    create_custom_fields(get_custom_fields(), ignore_validate=True)
    
    # 2. Configurar el campo de título
    set_title_field("Payroll Entry", "description")
    
    # 3. Limpiar la vista de lista
    payroll_list_fields = ["description", "posting_date", "gross_pay", "total_deduction", "net_pay", "status"]
    configure_list_view("Payroll Entry", payroll_list_fields)

    payroll_employee_list_fields = ["employee_name", "payment_days", "gross_pay", "total_deductions", "net_pay"]
    configure_list_view("Payroll Employee Detail", payroll_employee_list_fields)
    
    set_property_dynamic("Payroll Entry", "payroll_payable_account", "reqd", 0, "Check")

    set_property_dynamic("Bulk Salary Structure Assignment", "payroll_payable_account", "hidden", 1, "Check")
    set_property_dynamic("Salary Structure Assignment", "payroll_payable_account", "hidden", 1, "Check")

    set_property_dynamic("Employee", "employee_name", "hidden", 1, "Check")
    set_property_dynamic("Employee", "salutation", "hidden", 1, "Check")

    flatten_hrms_desktop_icons()
    remove_rh_entries()
    hide_non_hrms_desktop_icons()
    add_payroll_sidebar_item()

    set_property_dynamic("Payroll Employee Detail", "absent_days", "read_only", 1, "Check")
    set_property_dynamic("Payroll Employee Detail", "leave_without_pay", "read_only", 1, "Check")
    set_property_dynamic("Employee", "employment_type", "reqd", 1, "Check")
    set_property_dynamic("Employee", "department", "reqd", 1, "Check")
    set_property_dynamic("Salary Slip", "payroll_entry", "reqd", 1, "Check")

    frappe.db.commit()


def flatten_hrms_desktop_icons():
    """
    Removes the 'Frappe HR' folder from the desktop so all HRMS modules
    appear directly at the top level instead of nested inside a folder.
    """
    # Move all children of "Frappe HR" to the top level
    frappe.db.sql(
        "UPDATE `tabDesktop Icon` SET parent_icon = NULL WHERE parent_icon = 'Frappe HR'"
    )
    # Hide the now-empty "Frappe HR" folder icon
    frappe.db.set_value("Desktop Icon", "Frappe HR", "hidden", 1, update_modified=False)

def before_uninstall():
    """
    Cleans up all customizations before the app is removed,
    returning the system to its original state.
    """
    # Restore "Frappe HR" folder grouping
    restore_hrms_desktop_icons()
    restore_non_hrms_desktop_icons()
    remove_payroll_sidebar_item()

    # 1. Borrar campos
    # Obtener los campos personalizados actualmente definidos en la aplicación
    current_custom_fields = get_custom_fields()
    
    delete_custom_fields(current_custom_fields)
    
    doctypes_to_clean = [
        "Payroll Entry", 
        "Payroll Employee Detail", 
        "Salary Structure Assignment",
        "Bulk Salary Structure Assignment",
        "Salary Slip"
    ]

    delete_app_property_setters(doctypes_to_clean)

    frappe.db.commit()


def hide_non_hrms_desktop_icons():
    """Hides all desktop icons that don't belong to the hrms app."""
    frappe.db.sql(
        "UPDATE `tabDesktop Icon` SET hidden = 1 WHERE app != 'hrms' OR app IS NULL"
    )

def restore_non_hrms_desktop_icons():
    """Restores visibility of non-hrms desktop icons."""
    frappe.db.sql(
        "UPDATE `tabDesktop Icon` SET hidden = 0 WHERE app != 'hrms' OR app IS NULL"
    )

PAYROLL_SIDEBAR_ITEM_NAME = "bjobly-dept-payroll-rpt"
DASHBOARD_SIDEBAR_ITEM_NAME = "bjobly-payroll-dashboard"
PAYROLL_SIDEBAR_ITEM_AFTER = "Salary Register"  # insert after this label

def add_payroll_sidebar_item():
    """
    - Inserts Payroll Dashboard page at idx 2 (right after Home), with layout-dashboard icon.
    - Removes the native Dashboard item (link_type=Dashboard) from the Payroll sidebar.
    - Inserts Payroll Summary report after Salary Register.
    """
    # 1. Remove native Dashboard sidebar item
    frappe.db.delete(
        "Workspace Sidebar Item",
        {"parent": "Payroll", "link_type": "Dashboard"},
    )

    # 2. Insert Payroll Dashboard page at idx 2 (after Home at idx 1)
    if not frappe.db.exists("Workspace Sidebar Item", DASHBOARD_SIDEBAR_ITEM_NAME):
        # Shift everything from idx 2 onwards up by 1
        frappe.db.sql(
            "UPDATE `tabWorkspace Sidebar Item` SET idx = idx + 1 WHERE parent = 'Payroll' AND idx > 1"
        )
        frappe.db.sql("""
            INSERT INTO `tabWorkspace Sidebar Item`
                (name, parent, parenttype, parentfield, idx, label, link_to, link_type,
                 type, child, collapsible, keep_closed, show_arrow, icon, indent,
                 creation, modified, modified_by, owner, docstatus)
            VALUES
                (%s, 'Payroll', 'Workspace Sidebar', 'items', 2,
                 'Payroll Dashboard', 'payroll-dashboard', 'Page',
                 'Link', 1, 0, 0, 0, 'layout-dashboard', 0,
                 NOW(), NOW(), 'Administrator', 'Administrator', 0)
        """, (DASHBOARD_SIDEBAR_ITEM_NAME,))

    # 3. Insert Payroll Summary report after Salary Register
    if not frappe.db.exists("Workspace Sidebar Item", PAYROLL_SIDEBAR_ITEM_NAME):
        after_idx = frappe.db.get_value(
            "Workspace Sidebar Item",
            {"parent": "Payroll", "label": PAYROLL_SIDEBAR_ITEM_AFTER},
            "idx"
        ) or 9
        frappe.db.sql(
            "UPDATE `tabWorkspace Sidebar Item` SET idx = idx + 1 WHERE parent = 'Payroll' AND idx > %s",
            (after_idx,)
        )
        frappe.db.sql("""
            INSERT INTO `tabWorkspace Sidebar Item`
                (name, parent, parenttype, parentfield, idx, label, link_to, link_type,
                 type, child, collapsible, keep_closed, show_arrow, icon, indent,
                 creation, modified, modified_by, owner, docstatus)
            VALUES
                (%s, 'Payroll', 'Workspace Sidebar', 'items', %s,
                 'Payroll Summary', 'Payroll Summary', 'Report',
                 'Link', 1, 0, 0, 0, '', 0,
                 NOW(), NOW(), 'Administrator', 'Administrator', 0)
        """, (PAYROLL_SIDEBAR_ITEM_NAME, after_idx + 1))



def remove_payroll_sidebar_item():
    """Removes Bjobly entries from the Payroll workspace sidebar and re-indexes correctly."""
    items_to_delete = frappe.get_all(
        "Workspace Sidebar Item",
        filters={"name": ("in", [PAYROLL_SIDEBAR_ITEM_NAME, DASHBOARD_SIDEBAR_ITEM_NAME])},
        fields=["name", "idx"],
        order_by="idx DESC"  # Process from highest index to lowest to avoid conflicts
    )

    if not items_to_delete:
        return

    for item in items_to_delete:
        # Delete the item
        frappe.db.delete("Workspace Sidebar Item", {"name": item.name})

        # Shift all subsequent items in the same parent down by one.
        frappe.db.sql(
            "UPDATE `tabWorkspace Sidebar Item` SET idx = idx - 1 WHERE parent = 'Payroll' AND idx > %s",
            (item.idx,)
        )

def remove_rh_entries():
    """Deletes the custom 'RH' Desktop Icon and its Workspace Sidebar."""
    frappe.db.delete("Workspace Sidebar Item", {"parent": "RH"})
    frappe.db.delete("Workspace Sidebar", {"name": "RH"})
    frappe.db.delete("Desktop Icon", {"name": "RH"})


def restore_hrms_desktop_icons():
    """Reverts the HRMS desktop icons back to their original folder structure."""
    hrms_modules = [
        "Expenses", "Leaves", "Payroll", "People", "Performance",
        "Recruitment", "Shift & Attendance", "Tax & Benefits", "Tenure"
    ]
    frappe.db.sql(
        "UPDATE `tabDesktop Icon` SET parent_icon = 'Frappe HR' WHERE name IN ({})".format(
            ", ".join(["%s"] * len(hrms_modules))
        ),
        hrms_modules
    )
    frappe.db.set_value("Desktop Icon", "Frappe HR", "hidden", 0, update_modified=False)


def get_custom_fields():
    """
    Returns a dictionary of custom fields to be added. 
    
    This function uses the 'insert_after' property to place new sections, 
    columns, and data fields around the standard ERPNext fields, 
    preserving the integrity of the original DocType structure.
    """
    return {
        "Salary Structure Assignment": [
            {"fieldname": "custom_payroll_type", "label": _("Payroll Type"), "fieldtype": "Select", "options": "\nRegular Salary\nHonorariums", "insert_after": "salary_structure", "in_list_view": 1, "fetch_from": "salary_structure.custom_payroll_type", "read_only": 1},
        ],
        "Employee": [
            {"fieldname": "dpi", "label": _("DPI"), "fieldtype": "Data", "insert_after": "column_break_9"},
            {"fieldname": "nit", "label": _("NIT"), "fieldtype": "Data", "insert_after": "dpi"},
        ],
        "Payroll Entry": [
            {"fieldname": "description", "label": _("Description"), "fieldtype": "Data", "insert_after": "posting_date", "reqd": 1},
            {"fieldname": "employment_type", "label": _("Employment Type"), "fieldtype": "Link", "options": "Employment Type", "insert_after": "branch"},
            # --- v2: Mixed Payroll fields ---
            {"fieldname": "custom_payroll_type", "label": _("Payroll Type"), "fieldtype": "Select","reqd": 1,"options": "\nRegular Salary\nHonorariums", "insert_after": "company", "in_list_view": 1},
            {"fieldname": "custom_run_id", "label": _("Run ID"), "fieldtype": "Data", "hidden": 1, "read_only": 1, "insert_after": "custom_payroll_type"},
            # --- v1 operational fields ---
            {"fieldname": "section_break_ops", "fieldtype": "Section Break", "insert_after": "number_of_employees"},
            {"fieldname": "column_break_actions", "label": _("Payroll Actions"), "fieldtype": "Column Break", "insert_after": "section_break_ops"},
            {"fieldname": "get_employees_btn", "label": _("Get Employees"), "fieldtype": "Button", "insert_after": "column_break_actions"},
            {"fieldname": "calculate_salaries_btn", "label": _("Payroll Data Calculation"), "fieldtype": "Button", "insert_after": "get_employees_btn"},
            {"fieldname": "totals_section", "label": _("Totals"), "fieldtype": "Section Break", "insert_after": "employees"},
            {"fieldname": "gross_pay", "label": _("Gross Pay"), "fieldtype": "Currency", "read_only": 1, "insert_after": "totals_section","in_list_view": 1},
            {"fieldname": "column_break_hwip", "fieldtype": "Column Break", "insert_after": "gross_pay"},
            {"fieldname": "total_deduction", "label": _("Total Deduction"), "fieldtype": "Currency", "read_only": 1, "insert_after": "column_break_hwip","in_list_view": 1},
            {"fieldname": "column_break_cssf", "fieldtype": "Column Break", "insert_after": "total_deduction"},
            {"fieldname": "net_pay", "label": _("Net Pay"), "fieldtype": "Currency", "read_only": 1, "insert_after": "column_break_cssf","in_list_view": 1},
            {"fieldname": "salary_slips_calculated", "label": _("Salary Slips Calculated"), "fieldtype": "Check", "read_only": 1, "insert_after": "bank_account","hidden": 1},
            {"fieldname": "completed_journal_entry_creation", "label": _("Journal Entry Created"), "fieldtype": "Check", "read_only": 1, "insert_after": "salary_slips_calculated", "hidden": 1},
            {"fieldname": "completed_bank_entry", "label": _("Bank Entry Created"), "fieldtype": "Check", "read_only": 1, "insert_after": "completed_journal_entry_creation", "hidden": 1},
        ],
        "Payroll Employee Detail": [
            # Standard fields: employee, employee_name, column_break_3, department, designation, is_salary_withheld
            
            # Adding Salary Structure after Employee Name
            {"fieldname": "salary_structure", "label": _("Salary Structure"), "fieldtype": "Link", "options": "Salary Structure", "read_only": 1, "insert_after": "employee_name"},
            
            # New Payment Days Section after the last standard field
            {"fieldname": "section_break_iode", "label": _("Payment Days"), "fieldtype": "Section Break", "collapsible": 1, "insert_after": "is_salary_withheld"},
            {"fieldname": "total_working_days", "label": _("Working Days"), "fieldtype": "Float", "read_only": 1, "allow_on_submit": 1, "insert_after": "section_break_iode"},
            {"fieldname": "unmarked_days", "label": _("Unmarked days"), "fieldtype": "Float", "read_only": 1, "allow_on_submit": 1, "insert_after": "total_working_days"},
            {"fieldname": "column_break_reqb", "fieldtype": "Column Break", "insert_after": "unmarked_days"},
            {"fieldname": "leave_without_pay", "label": _("Leave Without Pay"), "fieldtype": "Float", "read_only": 1, "allow_on_submit": 1, "insert_after": "column_break_reqb"},
            {"fieldname": "absent_days", "label": _("Absent Days"), "fieldtype": "Float", "read_only": 1, "allow_on_submit": 1, "insert_after": "leave_without_pay"},
            {"fieldname": "column_break_uhtu", "fieldtype": "Column Break", "insert_after": "absent_days"},
            {"fieldname": "payment_days", "label": _("Payment Days"), "fieldtype": "Float", "read_only": 1, "allow_on_submit": 1, "insert_after": "column_break_uhtu"},
            
            # Totals Section (v2: removed earnings_html, earnings_json, deductions_html, deductions_json — moved to Dashboard SPA)
            {"fieldname": "totals_section", "label": _("Totals"), "fieldtype": "Section Break", "collapsible": 1, "collapsible_depends_on": "true", "insert_after": "payment_days"},
            {"fieldname": "totals_column", "fieldtype": "Column Break", "insert_after": "totals_section"},
            {"fieldname": "gross_pay", "label": _("Gross Pay"), "fieldtype": "Currency", "read_only": 1, "allow_on_submit": 1, "insert_after": "totals_column"},
            {"fieldname": "column_break_nswf", "fieldtype": "Column Break", "insert_after": "gross_pay"},
            {"fieldname": "total_deductions", "label": _("Total Deductions"), "fieldtype": "Currency", "read_only": 1, "allow_on_submit": 1, "insert_after": "column_break_nswf"},
            {"fieldname": "column_break_iwsm", "fieldtype": "Column Break", "insert_after": "total_deductions"},
            {"fieldname": "net_pay", "label": _("Net Pay"), "fieldtype": "Currency", "read_only": 1, "allow_on_submit": 1, "insert_after": "column_break_iwsm"},
            
            # Income Tax Breakup
            {"fieldname": "income_tax_breakup_section", "label": _("Income Tax Breakup"), "fieldtype": "Section Break", "collapsible": 1, "insert_after": "net_pay"},
            {"fieldname": "ctc", "label": _("CTC"), "fieldtype": "Currency", "read_only": 1, "allow_on_submit": 1, "insert_after": "income_tax_breakup_section"},
            {"fieldname": "income_from_other_sources", "label": _("Income from Other Sources"), "fieldtype": "Currency", "read_only": 1, "allow_on_submit": 1, "insert_after": "ctc"},
            {"fieldname": "total_earnings", "label": _("Total Earnings"), "fieldtype": "Currency", "read_only": 1, "allow_on_submit": 1, "insert_after": "income_from_other_sources"},
            {"fieldname": "taxable_earnings", "label": _("Taxable Earnings"), "fieldtype": "Currency", "read_only": 1, "allow_on_submit": 1, "insert_after": "total_earnings"},
            {"fieldname": "non_taxable_earnings", "label": _("Non Taxable Earnings"), "fieldtype": "Currency", "read_only": 1, "allow_on_submit": 1, "insert_after": "taxable_earnings"},
            {"fieldname": "column_break_rfhc", "fieldtype": "Column Break", "insert_after": "non_taxable_earnings"},
            {"fieldname": "taxable_deductions_till_date", "label": _("Taxable Deductions Till Date"), "fieldtype": "Currency", "read_only": 1, "allow_on_submit": 1, "insert_after": "column_break_rfhc"},
            {"fieldname": "standard_tax_exemption_amount", "label": _("Standard Tax Exemption Amount"), "fieldtype": "Currency", "read_only": 1, "allow_on_submit": 1, "insert_after": "taxable_deductions_till_date"},
            {"fieldname": "tax_exemption_declaration", "label": _("Tax Exemption Declaration"), "fieldtype": "Currency", "read_only": 1, "allow_on_submit": 1, "insert_after": "standard_tax_exemption_amount"},
            {"fieldname": "deductions_before_tax_calculation", "label": _("Deductions before tax calculation"), "fieldtype": "Currency", "read_only": 1, "allow_on_submit": 1, "insert_after": "tax_exemption_declaration"},
            {"fieldname": "column_break_aewv", "fieldtype": "Column Break", "insert_after": "deductions_before_tax_calculation"},
            {"fieldname": "annual_taxable_amount", "label": _("Annual Taxable Amount"), "fieldtype": "Currency", "read_only": 1, "allow_on_submit": 1, "insert_after": "column_break_aewv"},
            {"fieldname": "income_tax_deducted_till_date", "label": _("Income Tax Deducted Till Date"), "fieldtype": "Currency", "read_only": 1, "allow_on_submit": 1, "insert_after": "annual_taxable_amount"},
            {"fieldname": "current_month_income_tax", "label": _("Current Month Income Tax"), "fieldtype": "Currency", "read_only": 1, "allow_on_submit": 1, "insert_after": "income_tax_deducted_till_date"},
            {"fieldname": "future_income_tax_deductions", "label": _("Future Income Tax"), "fieldtype": "Currency", "read_only": 1, "allow_on_submit": 1, "insert_after": "current_month_income_tax"},
            {"fieldname": "total_income_tax", "label": _("Total Income Tax"), "fieldtype": "Currency", "read_only": 1, "allow_on_submit": 1, "insert_after": "future_income_tax_deductions"},
        ],
        "Salary Component": [
            {
                "fieldname": "custom_is_employer_contribution",
                "label": _("Is a Component of Contribution"),
                "fieldtype": "Check",
                "insert_after": "statistical_component",
                "description": _("If checked, this component will be tracked separately as an employer contribution and will not affect the employee's net pay."),
            },
        ],
        "Salary Structure": [
            {"fieldname": "custom_payroll_type", "label": _("Payroll Type"), "fieldtype": "Select", "options": "\nRegular Salary\nHonorariums", "insert_after": "is_active", "in_list_view": 1},
            {"fieldname": "employer_contributions", "label": _("Employer Contributions"), "fieldtype": "Table", "options": "Salary Detail", "insert_after": "deductions","allow_on_submit": 1},
        ],
        "Salary Slip": [
            # --- v2: Mixed Payroll type tag ---
            {"fieldname": "custom_payroll_type", "label": _("Payroll Type"), "fieldtype": "Select", "options": "\nRegular Salary\nHonorariums", "insert_after": "salary_structure", "in_list_view": 1},
            # --- SECCIÓN ESTADÍSTICA (Nueva) ---
            {"fieldname": "unmarked_days", "label": _("Unmarked Days"), "fieldtype": "Float", "read_only": 1, "insert_after": "base_hour_rate"},
            # --- SECCIÓN ACUMULADOS (YTD) ---
            {"fieldname": "gross_year_to_date", "label": _("Gross Year To Date"), "fieldtype": "Currency", "read_only": 1, "insert_after": "column_break_25"},

            # --- SECCIÓN IMPUESTOS (Tax Breakup extendido) ---
            {"fieldname": "taxable_earnings", "label": _("Taxable Earnings"), "fieldtype": "Currency", "read_only": 1, "insert_after": "total_earnings"},
            {"fieldname": "non_taxable_earnings", "label": _("Non Taxable Earnings"), "fieldtype": "Currency", "read_only": 1, "insert_after": "taxable_earnings"},
            {"fieldname": "taxable_deductions_till_date", "label": _("Taxable Deductions Till Date"), "fieldtype": "Currency", "read_only": 1, "insert_after": "non_taxable_earnings"},

            # --- SECCIÓN DISEÑO (Section Breaks adicionales de tu JSON) ---
            {"fieldname": "section_break_ppum", "fieldtype": "Section Break", "insert_after": "deduct_tax_for_unsubmitted_tax_exemption_proof"},
            {"fieldname": "section_break_fusm", "fieldtype": "Section Break", "insert_after": "year_to_date"},
            {"fieldname": "section_break_vgrs", "fieldtype": "Section Break", "insert_after": "base_year_to_date"},

            # --- APORTES PATRONALES ---
            {"fieldname": "section_employer_contributions", "label": "", "fieldtype": "Section Break", "insert_after": "deductions", "collapsible": 1},
            {"fieldname": "employer_contributions", "label": _("Employer Contributions"), "fieldtype": "Table", "options": "Salary Detail", "read_only": 1, "insert_after": "section_employer_contributions"},
        ],
        "Payroll Settings": [
            {
                "fieldname": "prorate_isr_based_on_payment_days",
                "label": _("Prorate Income Tax based on Payment Days"),
                "fieldtype": "Check",
                "insert_after": "create_overtime_slip",
                "description": _("If checked, Income Tax (ISR) will be prorated based on payment days for partial months. Otherwise, it will be divided by the number of sub-periods (usually 12).")
            },
            {
                "fieldname": "assign_attendance_at_calculating_salary_slips",
                "label": _("Assign attendance records at calculating salary slips"),
                "fieldtype": "Check",
                "insert_after": "consider_marked_attendance_on_holidays"
            },
            {
                "fieldname": "unmarked_attendance_status",
                "label": _("Unmarked attendance status"),
                "fieldtype": "Select",
                "options": "Present\nAbsent",
                "depends_on": "eval:doc.assign_attendance_at_calculating_salary_slips",
                "insert_after": "assign_attendance_at_calculating_salary_slips"
            },
            {
                "fieldname": "section_bjobly_display",
                "label": _("Display Settings"),
                "fieldtype": "Section Break",
                "insert_after": "daily_wages_fraction_for_half_day",
            },
            {
                "fieldname": "sort_employees_by",
                "label": _("Employee Name Format"),
                "fieldtype": "Select",
                "options": "Last Name, First Name Middle Name\nFirst Name Middle Name Last Name\nLast Name, First Name\nFirst Name Last Name\nLast Name First Name Middle Name\nFirst Name Middle Initial. Last Name",
                "default": "Last Name, First Name Middle Name",
                "insert_after": "section_bjobly_display",
                "description": _("Controls how employee names are displayed in the Payroll Dashboard.")
            }
        ]
    }

def delete_custom_fields(custom_fields: dict):
    """Deletes the specified custom fields from the database."""
    for doctype, fields in custom_fields.items():
        frappe.db.delete(
            "Custom Field",
            {
                "fieldname": ("in", [field["fieldname"] for field in fields]),
                "dt": doctype,
            },
        )

def delete_app_property_setters(doctypes: list):
    """
    Removes property setters related to titles and list views 
    for the specified doctypes.
    """
    for dt in doctypes:
        frappe.db.delete("Property Setter", {"doc_type": dt})

def configure_list_view(doctype, fields_to_show):
    """
    Dynamically manages the 'in_list_view' property for all fields of a DocType.
    It hides everything else and shows only the specified fields.
    """
    # Get all standard and custom fields for this DocType
    standard_fields = frappe.get_all("DocField", filters={"parent": doctype}, pluck="fieldname")
    custom_fields = frappe.get_all("Custom Field", filters={"dt": doctype}, pluck="fieldname")
    
    all_fields = list(set(standard_fields + custom_fields))
    
    for fieldname in all_fields:
        # If field is in our 'show' list, set value to 1, otherwise 0
        is_visible = 1 if fieldname in fields_to_show else 0
        set_property_dynamic(doctype, fieldname, "in_list_view", is_visible, "Check")

def set_title_field(doctype, fieldname):
    """Sets the title field property dynamically."""
    set_property_dynamic(doctype, None, "title_field", fieldname, "Data","DocType")

def set_property_dynamic(doctype, fieldname, property, value, property_type,doctype_or_field="DocField"):
    """
    Universal helper to create or update Property Setters safely.
    Works for both DocType-level and Field-level properties.
    """
    # Define a unique name for the property setter to avoid duplicates
    if doctype_or_field == "DocField":
        ps_name = f"{doctype}-{fieldname}-{property}"
    else:
        ps_name = f"{doctype}-main-{property}"

    if frappe.db.exists("Property Setter", ps_name):
        frappe.db.set_value("Property Setter", ps_name, "value", value)
    else:
        frappe.make_property_setter({
            "doctype": doctype,
            "fieldname": fieldname,
            "property": property,
            "value": value,
            "property_type": property_type,
            "doctype_or_field": doctype_or_field
        }, ignore_validate=True)


def sync_custom_fields():
    """
    Surgically syncs custom fields defined in this module.
    """
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    create_custom_fields(get_custom_fields(), ignore_validate=True)

    set_property_dynamic("Employee", "department", "reqd", 1, "Check")
    set_property_dynamic("Salary Slip", "payroll_entry", "reqd", 1, "Check")

    frappe.db.commit()
        