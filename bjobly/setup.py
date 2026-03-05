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

    payroll_employee_list_fields = ["employee", "payment_days", "gross_pay", "total_deductions", "net_pay"]
    configure_list_view("Payroll Employee Detail", payroll_employee_list_fields)
    
    set_property_dynamic("Payroll Entry", "payroll_payable_account", "reqd", 0, "Check")

    set_property_dynamic("Bulk Salary Structure Assignment", "payroll_payable_account", "hidden", 1, "Check")
    set_property_dynamic("Salary Structure Assignment", "payroll_payable_account", "hidden", 1, "Check")

    frappe.db.commit()
    frappe.clear_cache(doctype="Payroll Entry")
    frappe.clear_cache(doctype="Salary Structure Assignment")

def before_uninstall():
    """
    Cleans up all customizations before the app is removed,
    returning the system to its original state.
    """
    # 1. Borrar campos
    delete_custom_fields(get_custom_fields())
    
    doctypes_to_clean = [
        "Payroll Entry", 
        "Payroll Employee Detail", 
        "Salary Structure Assignment",
        "Bulk Salary Structure Assignment",
        "Salary Slip"
    ]

    delete_app_property_setters(doctypes_to_clean)
    
    frappe.db.commit()


def get_custom_fields():
    """
    Returns a dictionary of custom fields to be added. 
    
    This function uses the 'insert_after' property to place new sections, 
    columns, and data fields around the standard ERPNext fields, 
    preserving the integrity of the original DocType structure.
    """
    return {
        "Payroll Entry": [
            {"fieldname": "description", "label": _("Description"), "fieldtype": "Data", "insert_after": "posting_date", "reqd": 1},
            {"fieldname": "employment_type", "label": _("Employment Type"), "fieldtype": "Link", "options": "Employment Type", "insert_after": "branch"},
            {"fieldname": "column_break_mfhl", "fieldtype": "Column Break", "insert_after": "grade"},
            {"fieldname": "totals_section", "label": _("Totals"), "fieldtype": "Section Break", "insert_after": "employees"},
            {"fieldname": "gross_pay", "label": _("Gross Pay"), "fieldtype": "Currency", "read_only": 1, "insert_after": "totals_section","in_list_view": 1},
            {"fieldname": "column_break_hwip", "fieldtype": "Column Break", "insert_after": "gross_pay"},
            {"fieldname": "total_deduction", "label": _("Total Deduction"), "fieldtype": "Currency", "read_only": 1, "insert_after": "column_break_hwip","in_list_view": 1},
            {"fieldname": "column_break_cssf", "fieldtype": "Column Break", "insert_after": "total_deduction"},
            {"fieldname": "net_pay", "label": _("Net Pay"), "fieldtype": "Currency", "read_only": 1, "insert_after": "column_break_cssf","in_list_view": 1},
            {"fieldname": "payroll_payable_account", "label": _("Payroll Payable Account"), "fieldtype": "Link", "options": "Account", "insert_after": "cost_center"},
            {"fieldname": "salary_slips_calculated", "label": _("Salary Slips Calculated"), "fieldtype": "Check", "read_only": 1, "insert_after": "bank_account","hidden": 1},
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
            
            # Earnings & Deductions HTML
            {"fieldname": "earnings_deductions_section", "label": _("Earnings & Deductions"), "fieldtype": "Section Break", "collapsible": 1, "insert_after": "payment_days"},
            {"fieldname": "earnings_html", "label": _("Earnings"), "fieldtype": "HTML", "allow_on_submit": 1, "insert_after": "earnings_deductions_section"},
            {"fieldname": "earnings_json", "label": _("Earnings JSON"), "fieldtype": "JSON", "hidden": 1, "insert_after": "earnings_html"},
            {"fieldname": "column_break_fdjf", "fieldtype": "Column Break", "insert_after": "earnings_json"},
            {"fieldname": "deductions_html", "label": _("Deductions"), "fieldtype": "HTML", "allow_on_submit": 1, "insert_after": "column_break_fdjf"},
            {"fieldname": "deductions_json", "label": _("Deductions JSON"), "fieldtype": "JSON", "hidden": 1, "insert_after": "deductions_html"},
            
            # Totals Section
            {"fieldname": "totals_section", "label": _("Totals"), "fieldtype": "Section Break", "collapsible": 1, "collapsible_depends_on": "true", "insert_after": "deductions_html"},
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
        "Salary Slip": [
            # --- SECCIÓN ESTADÍSTICA (Nueva) ---
            {"fieldname": "section_break_pmbt", "fieldtype": "Section Break", "insert_after": "deductions"},
            {"fieldname": "earnings_statistical", "label": _("Statistical Earnings"), "fieldtype": "Table", "options": "Salary Detail", "read_only": 1, "insert_after": "section_break_pmbt"},
            {"fieldname": "column_break_kfvn", "fieldtype": "Column Break", "insert_after": "earnings_statistical"},
            {"fieldname": "deductions_stadistical", "label": _("Statistical Deductions"), "fieldtype": "Table", "options": "Salary Detail", "read_only": 1, "insert_after": "column_break_kfvn"},
            
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
    set_property_dynamic(doctype, None, "title_field", fieldname, "Data")

def set_property_dynamic(doctype, fieldname, property, value, property_type):
    """
    Universal helper to create or update Property Setters safely.
    Works for both DocType-level and Field-level properties.
    """
    # Define a unique name for the property setter to avoid duplicates
    if fieldname:
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
            "property_type": property_type
        }, ignore_validate=True)
        