import frappe
import json
from frappe import _, cstr
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

    def get_data_for_eval(self):
        """
        OVERRIDE: Ensures default_data uses full days for correct annual projection.
        In Bjobly, we treat every month as 30 days. For projection (rest of the year),
        we must assume the employee will earn the full month's salary.
        """
        data, default_data = super().get_data_for_eval()
        
        frequency_days_map = {
            "Monthly": 30,
            "Bimonthly": 60,
            "Fortnightly": 15,
            "Weekly": 7,
            "Daily": 1
        }
        standard_days = frequency_days_map.get(self.payroll_frequency, 30)
        
        # update standard days in default_data so evaluation of formulas 
        # (which use payment_days/total_working_days) returns the FULL amount.
        default_data.update({
            "payment_days": standard_days,
            "total_working_days": standard_days
        })
        return data, default_data

    def get_taxable_earnings(self, allow_tax_exemption=False, based_on_payment_days=0):
        """
        OVERRIDE: Fixes a base HRMS bug where deductions for future projection 
        use prorated current amounts instead of full default amounts.
        """
        res = super().get_taxable_earnings(allow_tax_exemption, based_on_payment_days)
        
        if not based_on_payment_days and allow_tax_exemption:
            # HRMS uses ded.amount (prorated) in its loop. For projection base, we need the full amount.
            taxable_earnings = res.taxable_earnings
            for ded in self.deductions:
                if ded.exempted_from_income_tax and not ded.additional_salary:
                    # 'res.taxable_earnings' has already subtracted 'ded.amount'. 
                    # We correct it to subtract 'ded.default_amount' instead.
                    taxable_earnings += flt(ded.amount)
                    taxable_earnings -= flt(ded.default_amount or 0)
            
            res.taxable_earnings = taxable_earnings
            
        return res

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

    def get_working_days_details(self, lwp=None, for_preview=0, lwp_days_corrected=None):
        """
        OVERRIDE: Standardizes working days based on payroll frequency.
        Monthly = 30, Bimonthly = 60, Fortnightly = 15, Weekly = 7, Daily = 1.

        Rules:
        - total_working_days is always the standard value (30 for monthly).
        - For employees who worked the full period: payment_days = standard_days - (absences + lwp).
        - For employees who joined or left mid-period: payment_days = actual_days_worked - (absences + lwp),
          where actual_days_worked counts the relieving/joining date as a worked day.
        - No decimals.
        """
        frequency_days_map = {
            "Monthly": 30,
            "Bimonthly": 60,
            "Fortnightly": 15,
            "Weekly": 7,
            "Daily": 1
        }
        standard_days = frequency_days_map.get(self.payroll_frequency, 30)

        # 1. Capture manual overrides (passed from Payroll Entry via get_doc(args))
        # 2. Run standard logic
        super().get_working_days_details(lwp, for_preview, lwp_days_corrected)

        # 3. Apply custom attendance logic if validation is OFF or enabled by settings
        if self.payroll_entry:
            pe = getattr(frappe.flags, "current_payroll_entry", None)
            pe_validate_attendance = pe.validate_attendance if pe else frappe.db.get_value("Payroll Entry", self.payroll_entry, "validate_attendance")
            
            # Fetch Payroll Settings
            ps = frappe.get_cached_value("Payroll Settings", None, 
                ["assign_attendance_at_calculating_salary_slips", "unmarked_attendance_status", 
                 "include_holidays_in_total_working_days", "payroll_based_on",
                 "daily_wages_fraction_for_half_day", "consider_marked_attendance_on_holidays"], 
                as_dict=1)

            # FORCE attendance logic if custom settings are active, even if global is 'Leave'
            force_attendance = (not pe_validate_attendance) or ps.assign_attendance_at_calculating_salary_slips

            if force_attendance:
                # If global setting is not Attendance, super() skipped fetching, so we do it here
                if ps.payroll_based_on != "Attendance":
                    holidays = self.get_holidays_for_employee(self.start_date, self.end_date)
                    actual_lwp, absent = self.calculate_lwp_ppl_and_absent_days_based_on_attendance(
                        holidays, 
                        ps.daily_wages_fraction_for_half_day or 0.5,
                        ps.consider_marked_attendance_on_holidays
                    )
                    self.absent_days = absent
                    self.payment_days -= flt(absent)

            # Handle unmarked days
            if not pe_validate_attendance:
                # If validation is OFF, REQUIRE settings
                if not ps.assign_attendance_at_calculating_salary_slips or not ps.unmarked_attendance_status:
                    frappe.throw(
                        _("Please configure 'Assign attendance records at calculating salary slips' and 'Unmarked attendance status' in Payroll Settings "
                          "because 'Validate Attendance' is disabled."),
                        title=_("Missing Configuration")
                    )
                
                status = ps.unmarked_attendance_status
                unmarked_days = self.get_unmarked_days(cint(ps.include_holidays_in_total_working_days), holidays)
                self.unmarked_days = unmarked_days
                if status == "Absent":
                    if unmarked_days > 0:
                        self.absent_days += unmarked_days
                        self.payment_days -= unmarked_days
            # Else: If validation is ON, standard HRMS might have already validated or fetched.

        start = getdate(self.start_date)
        joining = getdate(self.joining_date) if self.joining_date else None
        relieving = getdate(self.relieving_date) if self.relieving_date else None
        end = getdate(self.end_date)

        # Base days always starts at standard_days (30 for monthly)
        base_days = standard_days

        # Employee joined mid-period: subtract days before joining from the standard base
        # e.g. joined day 16 → 30 - date_diff(day16, day1) = 30 - 15 = 15
        if joining and joining > start:
            base_days = standard_days - date_diff(joining, start)

        # Employee left mid-period: count only days up to (but NOT including) the relieving date
        # e.g. left day 15 → date_diff(day15, day1) = 14
        if relieving and relieving < end:
            if joining and joining > start:
                base_days = date_diff(relieving, joining)
            else:
                base_days = date_diff(relieving, start)

        # Force standard total
        self.total_working_days = standard_days

        total_absences = flt(self.absent_days) + flt(self.leave_without_pay)
        self.payment_days = cint(max(0, base_days - total_absences))

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

    def compute_taxable_earnings_for_year(self):
        """Includes custom ISR deductions (taxable_deductions_till_date) in annual calculation"""
        if not self.tax_slab:
            # If no tax slab, skip annual calculation
            return

        super().compute_taxable_earnings_for_year()
        
        # Subtract custom deductions from the total annual taxable base
        self.taxable_deductions_till_date = self.get_opening_for(
            "taxable_deductions_till_date", self.payroll_period.start_date, self.start_date
        )
        
        self.total_taxable_earnings -= flt(self.taxable_deductions_till_date)
        self.total_taxable_earnings_without_full_tax_addl_components -= flt(self.taxable_deductions_till_date)

    def compute_variable_tax(self):
        """
        OVERRIDE: Guard against missing tax slab before HRMS tries to fetch it.
        """
        if not self._salary_structure_assignment.get("income_tax_slab"):
            return
        
        super().compute_variable_tax()

    def get_income_tax_slabs(self):
        """
        OVERRIDE: Return None if no tax slab is assigned, instead of throwing.
        This allows payroll for employees who don't pay ISR.
        """
        if not self._salary_structure_assignment.income_tax_slab:
            return None
        
        return super().get_income_tax_slabs()

    def get_amount_from_formula(self, struct_row, sub_period=1):
        """
        OVERRIDE: Use default_data (base amounts) instead of data (prorated amounts)
        so future period projections use full-month values, not partial-period values.
        """
        if self.payroll_frequency == "Monthly":
            start_date = frappe.utils.add_months(self.start_date, sub_period)
            end_date = frappe.utils.add_months(self.end_date, sub_period)
            posting_date = frappe.utils.add_months(self.posting_date, sub_period)
        else:
            days_to_add = 0
            if self.payroll_frequency == "Weekly":
                days_to_add = sub_period * 6
            if self.payroll_frequency == "Fortnightly":
                days_to_add = sub_period * 13
            if self.payroll_frequency == "Daily":
                days_to_add = sub_period
            start_date = add_days(self.start_date, days_to_add)
            end_date = add_days(self.end_date, days_to_add)
            posting_date = start_date

        # Use default_data (base amounts) instead of data (prorated amounts)
        local_data = self.default_data.copy()
        local_data.update({"start_date": start_date, "end_date": end_date, "posting_date": posting_date})

        return flt(self.eval_condition_and_formula(struct_row, local_data))

    def calculate_variable_tax(self, tax_component, has_additional_salary_tax_component=False):
        """
        OVERRIDE: Copied from HRMS to ensure it calls the local version of
        calculate_tax_by_tax_slab and handles ISR proration based on payment days.
        """
        if not self.tax_slab:
            # Defensive guard: if no tax slab is assigned, skip variable tax calculation
            self.current_tax_amount = 0
            return

        self.previous_total_paid_taxes = self.get_tax_paid_in_period(
            self.payroll_period.start_date, self.start_date, tax_component
        )

        # 1. Structured tax amount calculation (calls local calculate_tax_by_tax_slab)
        eval_locals, default_data = self.get_data_for_eval()
        self.total_structured_tax_amount, __ = calculate_tax_by_tax_slab(
            self.total_taxable_earnings_without_full_tax_addl_components,
            self.tax_slab,
            self.whitelisted_globals,
            eval_locals,
        )

        if has_additional_salary_tax_component:
            self.current_structured_tax_amount = self.additional_salary_amount
        else:
            self.current_structured_tax_amount = (
                self.total_structured_tax_amount - self.previous_total_paid_taxes
            ) / self.remaining_sub_periods

        # 2. Total taxable earnings with additional earnings with full tax
        self.full_tax_on_additional_earnings = 0.0
        if self.current_additional_earnings_with_full_tax:
            self.total_tax_amount, __ = calculate_tax_by_tax_slab(
                self.total_taxable_earnings, self.tax_slab, self.whitelisted_globals, eval_locals
            )
            self.full_tax_on_additional_earnings = self.total_tax_amount - self.total_structured_tax_amount

        # 3. Base tax amount
        self.current_tax_amount = max(
            0,
            flt(
                self.current_structured_tax_amount
                if has_additional_salary_tax_component
                else (self.current_structured_tax_amount + self.full_tax_on_additional_earnings)
            ),
        )

        # 4. BJOBLY: Prorate the withheld tax amount for partial months if enabled
        prorate = frappe.db.get_single_value("Payroll Settings", "prorate_isr_based_on_payment_days")
        if prorate:
            standard_days = 30
            if self.payroll_frequency == "Monthly":
                standard_days = 30
            # If it's a partial month and we are not paying for the full period, prorate
            if flt(self.payment_days) < flt(standard_days) and standard_days > 0:
                factor = flt(self.payment_days) / flt(standard_days)
                self.current_tax_amount = flt(self.current_tax_amount * factor, self.precision("current_tax_amount"))

        # 5. Update internal state
        self._component_based_variable_tax[tax_component].update(
            {
                "previous_total_paid_taxes": self.previous_total_paid_taxes,
                "total_structured_tax_amount": self.total_structured_tax_amount,
                "current_structured_tax_amount": self.current_structured_tax_amount,
                "full_tax_on_additional_earnings": self.full_tax_on_additional_earnings,
                "current_tax_amount": self.current_tax_amount,
            }
        )

    def compute_income_tax_breakup(self):
        """
        OVERRIDE: Populates custom Bjobly fields for the ISR breakdown UI.
        """
        super().compute_income_tax_breakup()
        
        # Populate fields used in the Guatemala/Bjobly print formats and UI breakdown
        self.taxable_earnings = flt(self.gross_pay) - flt(self.non_taxable_earnings or 0)
        self.taxable_deductions_till_date = self.get_opening_for(
            "taxable_deductions_till_date", self.payroll_period.start_date, self.start_date
        )

# --- GLOBAL FUNCTION REDEFINITION (Shadowing) ---
# Since this function is defined in this file, class methods will prioritize this version over HRMS.

def calculate_tax_by_tax_slab(annual_taxable_earning, tax_slab, eval_globals=None, eval_locals=None):
    """Custom Income Tax Slab logic using 'amount_previusly_taxed' for tiered scaling"""
    tax_amount = 0
    other_taxes_and_charges = 0
    amount_previusly_taxed = 0

    if annual_taxable_earning > flt(tax_slab.get("tax_relief_limit") or 0):
        eval_locals.update({"annual_taxable_earning": annual_taxable_earning})

        for slab in tax_slab.slabs:
            cond = cstr(slab.condition).strip()
            if cond and not eval_tax_slab_condition(cond, eval_globals, eval_locals):
                continue
                
            if not slab.to_amount and annual_taxable_earning >= slab.from_amount:
                # Use amount_previusly_taxed for the base of the last slab if it's contiguous
                base = amount_previusly_taxed if amount_previusly_taxed > 0 else slab.from_amount
                tax_amount += (annual_taxable_earning - base + 1) * slab.percent_deduction * 0.01
                continue

            if annual_taxable_earning >= slab.from_amount and annual_taxable_earning < slab.to_amount:
                tax_amount += (annual_taxable_earning - amount_previusly_taxed) * slab.percent_deduction * 0.01
                amount_previusly_taxed = slab.to_amount
            elif annual_taxable_earning >= slab.from_amount and annual_taxable_earning >= slab.to_amount:
                tax_amount += (slab.to_amount - amount_previusly_taxed) * slab.percent_deduction * 0.01
                amount_previusly_taxed = slab.to_amount

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