import frappe
import json
from frappe import _
from frappe.model.naming import make_autoname
from frappe.utils import flt, getdate, date_diff, cint, formatdate, add_days, add_months
from hrms.payroll.doctype.salary_slip.salary_slip import SalarySlip, eval_tax_slab_condition

class BjoblySalarySlip(SalarySlip):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 1. Custom naming series
        self.series = f"SS/{self.employee}/.#####"
        
        # 2. Add custom functions to component formulas (Bonus, min, date_diff)
        self.whitelisted_globals.update({
            "date_diff": date_diff,
            "min": lambda a, b: a if a < b else b,
            "bonus": self.calculate_bonus,
        })

    def validate(self):
        # 1. Validar que el rango de fechas coincida con la frecuencia
        self.validate_payroll_range()
        
        # 2. Ejecutar las validaciones estándar de ERPNext
        super().validate()

    def validate_payroll_range(self):
        """
        Validates that the selected date range matches the logical payroll frequency.
        Example: Monthly from Feb 2 must end on March 1.
        """
        if not self.start_date or not self.end_date or not self.payroll_frequency:
            return

        start = getdate(self.start_date)
        end = getdate(self.end_date)
        
        # Lógica de bloques lógicos de tiempo
        if self.payroll_frequency == "Monthly":
            # Un mes lógico: (Mes + 1) - 1 día. 
            # Ej: Feb 2 + 1 mes = Mar 2 -> Mar 2 - 1 día = Mar 1.
            expected_end = add_days(add_months(start, 1), -1)
            
        elif self.payroll_frequency == "Fortnightly":
            # Quincena lógica: 15 días calendario
            expected_end = add_days(start, 14)
            
        elif self.payroll_frequency == "Weekly":
            # Semana lógica: 7 días calendario
            expected_end = add_days(start, 6)
            
        elif self.payroll_frequency == "Bimonthly":
            # Bimestre lógico: (Mes + 2) - 1 día
            expected_end = add_days(add_months(start, 2), -1)
            
        elif self.payroll_frequency == "Daily":
            expected_end = start
        else:
            return

        # Si la fecha seleccionada no es la esperada, lanzamos error
        if end != expected_end:
            frappe.throw(
                _("The date range is incorrect for <b>{0}</b> frequency.<br><br>"
                  "Based on Start Date <b>{1}</b>, the End Date must be <b>{2}</b>.")
                .format(self.payroll_frequency, formatdate(start), formatdate(expected_end)),
                title=_("Invalid Date Range")
            )

    def get_working_days_details(self, lwp=None, for_preview=0,lwp_days_corrected=None):
        """
        OVERRIDE: Standardizes working days based on payroll frequency.
        Monthly = 30, Bimonthly = 60, Fortnightly = 15, Weekly = 7, Daily = 1.
        Calendar days are ignored; calculations subtract absences from these fixed bases.
        """
        # 1. Define standard days mapping
        frequency_days_map = {
            "Monthly": 30,
            "Bimonthly": 60,
            "Fortnightly": 15,
            "Weekly": 7,
            "Daily": 1
        }
        
        # 2. Identify the target standard days for this slip
        standard_days = frequency_days_map.get(self.payroll_frequency, 30)

        # 3. Execute standard ERPNext logic
        # This will fetch real absent_days and leave_without_pay from Attendance/Leave records
        super().get_working_days_details(lwp, for_preview,lwp_days_corrected)

        # 4. FORCE Standard Totals (The "Choluteca Rule")
        # We overwrite self.total_working_days so that formulas using it as a divisor are correct.
        self.total_working_days = standard_days
        
        # 5. Calculate Payment Days: Standard - (Absences + LWPs)
        # Even if Feb has 28 days, if there is 1 absence, it calculates: 30 - 1 = 29.
        total_absences = flt(self.absent_days) + flt(self.leave_without_pay)
        self.payment_days = standard_days - total_absences

        if self.payment_days < 0:
            self.payment_days = 0

    def calculate_bonus(self, start_date, end_date, days, amount):
        """Custom logic for Bonus/Aguinaldo calculation based on worked days"""
        start_date = getdate(start_date)
        end_date = getdate(end_date)
        # Calculate day difference and ensure it does not exceed the limit
        worked_days = min(abs(date_diff(start_date, end_date)), days)
        return amount * (worked_days / days)

    def autoname(self):
        """Override document name using the custom series (SS/...)"""
        self.name = make_autoname(self.series)

    def add_structure_component(self, struct_row, component_type):
        """
        MODIFIED: Redirects components marked as 'Statistical' 
        to custom statistical tables.
        """
        amount = self.eval_condition_and_formula(struct_row, self.data)
        remove_if_zero_valued = frappe.get_cached_value(
            "Salary Component", struct_row.salary_component, "remove_if_zero_valued"
        )
        default_amount = self.eval_condition_and_formula(struct_row, self.default_data)

        # Update internal data for future calculations
        self.default_data[struct_row.abbr] = flt(amount)

        if struct_row.depends_on_payment_days:
            payment_days_amount = (
                flt(amount) * flt(self.payment_days) / cint(self.total_working_days)
                if self.total_working_days else 0
            )
            self.data[struct_row.abbr] = flt(payment_days_amount, struct_row.precision("amount"))
        else:
            self.data[struct_row.abbr] = flt(amount, struct_row.precision("amount"))

        # Redirect logic for Statistical Tables (Earnings vs Deductions)
        if struct_row.statistical_component:
            target_table = "earnings_statistical" if component_type == "earnings" else "deductions_statistical"
            
            self.update_component_row(
                struct_row,
                amount,
                target_table,
                default_amount=default_amount,
                remove_if_zero_valued=remove_if_zero_valued
            )
        else:
            # Standard behavior for components affecting Net Pay
            self.update_component_row(
                struct_row,
                amount,
                component_type,
                data=self.data,
                default_amount=default_amount,
                remove_if_zero_valued=remove_if_zero_valued
            )

    def compute_taxable_earnings_for_year(self):
        """Includes custom ISR deductions (taxable_deductions_till_date) in annual calculation"""
        super().compute_taxable_earnings_for_year()
        
        # Subtract custom deductions from the total annual taxable base
        self.taxable_deductions_till_date = self.get_opening_for(
            "taxable_deductions_till_date", self.payroll_period.start_date, self.start_date
        )
        
        self.total_taxable_earnings -= flt(self.taxable_deductions_till_date)
        self.total_taxable_earnings_without_full_tax_addl_components -= flt(self.taxable_deductions_till_date)

    def compute_income_tax_breakup(self):
        """Ensures that the tax breakdown correctly populates custom fields"""
        # Reset custom fields before calculation
        self.taxable_deductions_till_date = 0
        self.taxable_earnings = 0
        
        super().compute_income_tax_breakup()
        
        # Map values to custom fields after standard calculation
        self.taxable_deductions_till_date = self.get_opening_for(
            "taxable_deductions_till_date", self.payroll_period.start_date, self.start_date
        )
        self.taxable_earnings = flt(self.total_earnings) - flt(self.non_taxable_earnings)

# --- GLOBAL FUNCTION REDEFINITION (Shadowing) ---
# Since this function is defined in this file, class methods will prioritize this version over HRMS.

def calculate_tax_by_tax_slab(annual_taxable_earning, tax_slab, eval_globals=None, eval_locals=None):
    """Custom Income Tax Slab logic using 'amount_previusly_taxed' for tiered scaling"""
    eval_locals.update({"annual_taxable_earning": annual_taxable_earning})
    tax_amount = 0
    other_taxes_and_charges = 0
    amount_previusly_taxed = 0

    for slab in tax_slab.slabs:
        cond = str(slab.condition).strip()
        if cond and not eval_tax_slab_condition(cond, eval_globals, eval_locals):
            continue
            
        if not slab.to_amount and annual_taxable_earning >= slab.from_amount:
            tax_amount += (annual_taxable_earning - slab.from_amount + 1) * slab.percent_deduction * 0.01
            continue

        if annual_taxable_earning >= slab.from_amount and annual_taxable_earning < slab.to_amount:
            tax_amount += (annual_taxable_earning - amount_previusly_taxed) * slab.percent_deduction * 0.01
            amount_previusly_taxed = slab.to_amount
        elif annual_taxable_earning >= slab.from_amount and annual_taxable_earning >= slab.to_amount:
            tax_amount += (slab.to_amount - amount_previusly_taxed) * slab.percent_deduction * 0.01
            amount_previusly_taxed = slab.from_amount

    # Handle other taxes and charges (e.g., Local/Municipal taxes)
    for d in tax_slab.other_taxes_and_charges:
        if flt(d.min_taxable_income) and flt(d.min_taxable_income) > annual_taxable_earning:
            continue
        if flt(d.max_taxable_income) and flt(d.max_taxable_income) < annual_taxable_earning:
            continue

        charge = tax_amount * flt(d.percent) / 100
        tax_amount += charge
        other_taxes_and_charges += charge

    return tax_amount, other_taxes_and_charges