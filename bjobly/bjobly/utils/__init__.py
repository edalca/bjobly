import frappe
from frappe import _
from frappe.utils import getdate, formatdate, add_days, add_months

def validate_payroll_range(start_date, end_date, payroll_frequency):
    """
    Validates that the selected date range matches the logical payroll frequency.
    Example: Monthly from Feb 2 must end on March 1.
    """
    if not start_date or not end_date or not payroll_frequency:
        return

    start = getdate(start_date)
    end = getdate(end_date)
    
    # Lógica de bloques lógicos de tiempo
    if payroll_frequency == "Monthly":
        # Un mes lógico: (Mes + 1) - 1 día. 
        # Ej: Feb 2 + 1 mes = Mar 2 -> Mar 2 - 1 día = Mar 1.
        expected_end = add_days(add_months(start, 1), -1)
        
    elif payroll_frequency == "Fortnightly":
        # Quincena lógica: 15 días calendario
        expected_end = add_days(start, 14)
        
    elif payroll_frequency == "Weekly":
        # Semana lógica: 7 días calendario
        expected_end = add_days(start, 6)
        
    elif payroll_frequency == "Bimonthly":
        # Bimestre lógico: (Mes + 2) - 1 día
        expected_end = add_days(add_months(start, 2), -1)
        
    elif payroll_frequency == "Daily":
        expected_end = start
    else:
        return

    # Si la fecha seleccionada no es la esperada, lanzamos error
    if end != expected_end:
        frappe.throw(
            _("The date range is incorrect for <b>{0}</b> frequency.<br><br>"
              "Based on Start Date <b>{1}</b>, the End Date must be <b>{2}</b>.")
            .format(payroll_frequency, formatdate(start), formatdate(expected_end)),
            title=_("Invalid Date Range")
        )