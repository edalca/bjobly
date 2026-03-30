import frappe
import json
from frappe import _
from bjobly.bjobly.utils import validate_payroll_range
from frappe.utils import flt, getdate, formatdate, add_days, add_months
from hrms.payroll.doctype.payroll_entry.payroll_entry import (
    PayrollEntry,
    get_salary_structure,
    get_existing_salary_slips,
    log_payroll_failure,
    remove_payrolled_employees,
    set_fields_to_select,
    set_searchfield,
    set_filter_conditions,
    set_match_conditions,
    show_payroll_submission_status
)
from frappe.utils import (
    DATE_FORMAT,
    add_days,
    add_to_date,
    cint,
    comma_and,
    date_diff,
    flt,
    get_link_to_form,
    getdate,
)
import unicodedata

class BjoblyPayrollEntry(PayrollEntry):
    def validate(self):
        validate_payroll_range(self.start_date, self.end_date, self.payroll_frequency)

        super(BjoblyPayrollEntry, self).validate()
        self._check_attendance_settings()


    def on_submit(self):
        if not self.salary_slips_calculated:
            frappe.throw(_("Salary Slips need to be calculated first."), title=_("Calculate Salary Slips"))

        self.set_status(update=True, status="Submitted")

        employees = [emp.employee for emp in self.employees]
        if not employees:
            return

        args = frappe._dict({
            "salary_slip_based_on_timesheet": self.salary_slip_based_on_timesheet,
            "payroll_frequency": self.payroll_frequency,
            "custom_payroll_type": self.custom_payroll_type,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "company": self.company,
            "posting_date": self.posting_date,
            "deduct_tax_for_unsubmitted_tax_exemption_proof": self.deduct_tax_for_unsubmitted_tax_exemption_proof,
            "payroll_entry": self.name,
            "exchange_rate": self.exchange_rate,
            "currency": self.currency,
        })

        if len(employees) > 30 or frappe.flags.enqueue_payroll_entry:
            self.db_set("status", "Queued")
            frappe.enqueue(
                create_and_submit_salary_slips,
                timeout=3000,
                payroll_entry_name=self.name,
                employees=employees,
                args=args,
            )
            frappe.msgprint(
                _("Salary Slip creation and submission is queued. It may take a few minutes"),
                alert=True,
                indicator="blue",
            )
        else:
            create_and_submit_salary_slips(self.name, employees, args)

    def on_cancel(self):
        self.ignore_linked_doctypes = ("GL Entry", "Salary Slip", "Journal Entry")
        # 1. Cancel + delete salary slips FIRST (so GL entries from slips are gone before cancelling JV)
        for slip in self.get_linked_salary_slips():
            if slip.docstatus == 1:
                frappe.get_doc("Salary Slip", slip.name).cancel()
            frappe.delete_doc("Salary Slip", slip.name, ignore_permissions=True)
        # 2. Now cancel Journal Entries (accrual + bank)
        self.cancel_linked_journal_entries()
        # 3. Cancel any remaining payment ledger entries
        self.cancel_linked_payment_ledger_entries()
        self.db_set("salary_slips_created", 0)
        self.db_set("salary_slips_submitted", 0)
        self.db_set("completed_journal_entry_creation", 0)
        self.db_set("completed_bank_entry", 0)
        self.set_status(update=True, status="Cancelled")
        self.db_set("error_message", "")
        frappe.publish_realtime("bjobly_payroll_cancelled", {"name": self.name}, user=frappe.session.user)

    def validate_existing_salary_slips(self):
        if not self.employees:
            return

        existing_salary_slips = []
        SalarySlip = frappe.qb.DocType("Salary Slip")

        existing_salary_slips = (
            frappe.qb.from_(SalarySlip)
            .select(SalarySlip.employee, SalarySlip.name)
            .where(
                (SalarySlip.employee.isin([emp.employee for emp in self.employees]))
                & (SalarySlip.start_date == self.start_date)
                & (SalarySlip.custom_payroll_type == self.custom_payroll_type)
                & (SalarySlip.end_date == self.end_date)
                & (SalarySlip.docstatus != 2)
            )
        ).run(as_dict=True)

        if len(existing_salary_slips):
            msg = _("Salary Slip already exists for {0} for the given dates").format(
                comma_and([frappe.bold(d.employee) for d in existing_salary_slips])
            )
            msg += "<br><br>"
            msg += _("Reference: {0}").format(
                comma_and([get_link_to_form("Salary Slip", d.name) for d in existing_salary_slips])
            )
            frappe.throw(
                msg,
                title=_("Duplicate Entry"),
            )

    @frappe.whitelist()
    def create_journal_entry(self):
        """Create the accrual journal entry for submitted salary slips."""
        submitted_salary_slips = self.get_sal_slip_list(ss_status=1, as_dict=True)
        if not submitted_salary_slips:
            frappe.throw(
                _("No submitted salary slips without a journal entry were found."),
                title=_("Nothing to Process"),
            )
        self.make_accrual_jv_entry(submitted_salary_slips)
        self.db_set("completed_journal_entry_creation", 1)

    @frappe.whitelist()
    def make_bank_entry(self, for_withheld_salaries=False):
        """Delegate to HRMS and mark bank entry as completed."""
        result = super().make_bank_entry(for_withheld_salaries=for_withheld_salaries)
        self.db_set("completed_bank_entry", 1)
        return result

    @frappe.whitelist()
    def get_attendance_for_range(self, employee, from_date, to_date):
        """Returns existing attendance records for an employee in a given range."""
        return frappe.get_all("Attendance", 
            filters={
                "employee": employee,
                "attendance_date": ["between", [from_date, to_date]],
                "docstatus": ["<", 2]
            },
            fields=["attendance_date", "status"]
        )

    @frappe.whitelist()
    def bulk_create_attendance(self, employee, dates, status, shift=None, late_entry=0, early_exit=0):
        """Creates attendance records for multiple dates for a single employee."""
        if isinstance(dates, str):
            dates = json.loads(dates)
            
        created_count = 0
        for date_str in dates:
            if frappe.db.exists("Attendance", {"employee": employee, "attendance_date": date_str, "docstatus": ["<", 2]}):
                continue
                
            doc = frappe.get_doc({
                "doctype": "Attendance",
                "employee": employee,
                "attendance_date": date_str,
                "status": status,
                "shift": shift,
                "late_entry": late_entry,
                "early_exit": early_exit,
                "docstatus": 1 # Submit immediately
            })
            doc.insert()
            created_count += 1
            
        return created_count
        
    @frappe.whitelist()
    def calculate_salary_slips(self):
        """
        Calculate salary slip for selected employees if already not created
        """
        self.check_permission("write")
        employees = [emp.employee for emp in self.employees]

        if employees:
            self._check_attendance_settings()
            args = frappe._dict(
                {
                    "salary_slip_based_on_timesheet": self.salary_slip_based_on_timesheet,
                    "payroll_frequency": self.payroll_frequency,
                    "custom_payroll_type": self.custom_payroll_type,
                    "start_date": self.start_date,
                    "end_date": self.end_date,
                    "company": self.company,
                    "posting_date": self.posting_date,
                    "deduct_tax_for_unsubmitted_tax_exemption_proof": self.deduct_tax_for_unsubmitted_tax_exemption_proof,
                    "payroll_entry": self.name,
                    "exchange_rate": self.exchange_rate,
                    "currency": self.currency,
                }
            )
            if len(employees) > 30 or frappe.flags.enqueue_payroll_entry:
                self.db_set("status", "Queued")
                frappe.enqueue(
                    calculate_salary_slips_for_employees,
                    timeout=3000,
                    employees=employees,
                    args=args,
                    publish_progress=True,
                )
                frappe.msgprint(
                    _("Salary Slip creation is queued. It may take a few minutes"),
                    alert=True,
                    indicator="blue",
                )
            else:
                calculate_salary_slips_for_employees(employees, args, publish_progress=False)
                # since this method is called via frm.call this doc needs to be updated manually
                self.reload()

    @frappe.whitelist()
    def fill_employee_details(self):
        filters = self.make_filters()
        # Llamamos a nuestra función personalizada de búsqueda que no filtra por cuenta
        employees = get_employee_list(filters=filters, as_dict=True, ignore_match_conditions=True)
        self.set("employees", [])

        if not employees:
            # Mensaje de error idéntico al tuyo, pero sin la cuenta por pagar
            error_msg = _(
                "No employees found for the mentioned criteria:<br>Company: {0}<br> Currency: {1}"
            ).format(
                frappe.bold(self.company),
                frappe.bold(self.currency),
            )
            if self.branch:
                error_msg += "<br>" + _("Branch: {0}").format(frappe.bold(self.branch))
            if self.department:
                error_msg += "<br>" + _("Department: {0}").format(frappe.bold(self.department))
            if self.designation:
                error_msg += "<br>" + _("Designation: {0}").format(frappe.bold(self.designation))
            if self.start_date:
                error_msg += "<br>" + _("Start date: {0}").format(frappe.bold(self.start_date))
            if self.end_date:
                error_msg += "<br>" + _("End date: {0}").format(frappe.bold(self.end_date))
            if self.custom_payroll_type:
                error_msg += "<br>" + _("Payroll Type: {0}").format(frappe.bold(_(self.custom_payroll_type)))


            frappe.throw(error_msg, title=_("No employees found"))

        self.set("employees", employees)
        self.number_of_employees = len(self.employees)
        self.update_employees_with_withheld_salaries()
        self.salary_slips_calculated = 0
        self.save(ignore_permissions=True)

        return self.get_employees_with_unmarked_attendance()

    def _check_attendance_settings(self):
        """
        Helper method to check attendance configuration in Payroll Settings
        if validate_attendance is disabled for the current Payroll Entry.
        """
        if not self.validate_attendance:
            ps = frappe.get_cached_value("Payroll Settings", None, 
                ["assign_attendance_at_calculating_salary_slips", "unmarked_attendance_status"], 
                as_dict=1)
            if not ps or not ps.assign_attendance_at_calculating_salary_slips or not ps.unmarked_attendance_status:
                frappe.throw(
                    _("Please configure 'Assign attendance records at calculating salary slips' and 'Unmarked attendance status' in Payroll Settings "
                      "because 'Validate Attendance' is disabled."),
                    title=_("Missing Configuration")
                )
    
    def make_filters(self):
        filters = frappe._dict(
            company=self.company,
            branch=self.branch,
            department=self.department,
            designation=self.designation,
            grade=self.grade,
            currency=self.currency,
            start_date=self.start_date,
            end_date=self.end_date,
            payroll_payable_account=self.payroll_payable_account,
            salary_slip_based_on_timesheet=self.salary_slip_based_on_timesheet,
            custom_payroll_type=self.custom_payroll_type
        )

        if not self.salary_slip_based_on_timesheet:
            filters.update(dict(payroll_frequency=self.payroll_frequency))

        return filters

def get_employee_list(
    filters: frappe._dict,
    searchfield=None,
    search_string=None,
    fields: list[str] | None = None,
    as_dict=True,
    limit=None,
    offset=None,
    ignore_match_conditions=False,
) -> list:
    sal_struct = get_salary_structure(
        filters.company,
        filters.currency,
        filters.salary_slip_based_on_timesheet,
        filters.payroll_frequency,
        filters.custom_payroll_type
    )

    if not sal_struct:
        return []

    emp_list = get_filtered_employees(
        sal_struct,
        filters,
        searchfield,
        search_string,
        fields,
        as_dict=as_dict,
        limit=limit,
        offset=offset,
        ignore_match_conditions=ignore_match_conditions,
    )

    if as_dict:
        employees_to_check = {emp.employee: emp for emp in emp_list}
    else:
        employees_to_check = {emp[0]: emp for emp in emp_list}

    return remove_payrolled_employees(employees_to_check, filters.start_date, filters.end_date,filters.custom_payroll_type)

def remove_payrolled_employees(emp_list, start_date, end_date,custom_payroll_type=None):
	SalarySlip = frappe.qb.DocType("Salary Slip")

	employees_with_payroll = (
		frappe.qb.from_(SalarySlip)
		.select(SalarySlip.employee)
		.where(
			(SalarySlip.docstatus == 1)

			& (SalarySlip.start_date == start_date)
			& (SalarySlip.end_date == end_date)
			& (SalarySlip.custom_payroll_type == custom_payroll_type)
		)
	).run(pluck=True)

	return [emp_list[emp] for emp in emp_list if emp not in employees_with_payroll]

def get_salary_structure(
    company: str, currency: str, salary_slip_based_on_timesheet: int, payroll_frequency: str, custom_payroll_type: str
) -> list[str]:
    SalaryStructure = frappe.qb.DocType("Salary Structure")

    query = (
        frappe.qb.from_(SalaryStructure)
        .select(SalaryStructure.name)
        .where(
            (SalaryStructure.docstatus == 1)
            & (SalaryStructure.is_active == "Yes")
            & (SalaryStructure.company == company)
            & (SalaryStructure.currency == currency)
            & (SalaryStructure.custom_payroll_type == custom_payroll_type)
            & (SalaryStructure.salary_slip_based_on_timesheet == salary_slip_based_on_timesheet)
        )
    )
    if not salary_slip_based_on_timesheet:
        query = query.where(SalaryStructure.payroll_frequency == payroll_frequency)

    return query.run(pluck=True)

def get_filtered_employees(
    sal_struct,
    filters,
    searchfield=None,
    search_string=None,
    fields=None,
    as_dict=False,
    limit=None,
    offset=None,
    ignore_match_conditions=False,
) -> list:
    SalaryStructureAssignment = frappe.qb.DocType("Salary Structure Assignment")
    Employee = frappe.qb.DocType("Employee")

    query = (
        frappe.qb.from_(Employee)
        .join(SalaryStructureAssignment)
        .on(Employee.name == SalaryStructureAssignment.employee)
        .where(
            (SalaryStructureAssignment.docstatus == 1)
            & (Employee.status != "Inactive")
            & (Employee.company == filters.company)
            & ((Employee.date_of_joining <= filters.end_date) | (Employee.date_of_joining.isnull()))
            & ((Employee.relieving_date >= filters.start_date) | (Employee.relieving_date.isnull()))
            & (SalaryStructureAssignment.salary_structure.isin(sal_struct))
            & (filters.end_date >= SalaryStructureAssignment.from_date)
        )
    )

    query = set_fields_to_select(query, fields)
    query = set_searchfield(query, searchfield, search_string, qb_object=Employee)
    query = set_filter_conditions(query, filters, qb_object=Employee)

    if not ignore_match_conditions:
        query = set_match_conditions(query=query, qb_object=Employee)

    if limit:
        query = query.limit(limit)

    if offset:
        query = query.offset(offset)

    return query.run(as_dict=as_dict)

def create_and_submit_salary_slips(payroll_entry_name, employees, args):
    """
    BJOBLY: Creates salary slips and immediately submits them in a single step.
    Called from on_submit — either synchronously or as a background job.
    """
    payroll_entry = frappe.get_doc("Payroll Entry", payroll_entry_name)
    submitted = []

    try:
        existing = get_existing_salary_slips(employees, args)
        to_create = list(set(employees) - set(existing))

        frappe.flags.via_payroll_entry = True

        # Map manual overrides from the current payroll_entry object
        overrides = {e.employee: e for e in payroll_entry.employees}

        for emp in to_create:
            try:
                emp_overrides = overrides.get(emp)
                
                args.update({
                    "doctype": "Salary Slip", 
                    "employee": emp
                })
                slip = frappe.get_doc(args)
                slip.insert()
                if flt(slip.net_pay) >= 0:
                    slip.submit()
                    submitted.append(slip)
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"Bjobly: error creating/submitting slip for {emp}")

        payroll_entry.db_set("salary_slips_created", 1)

        if submitted:
            try:
                payroll_entry.email_salary_slip(submitted)
            except Exception:
                pass
            payroll_entry.db_set({"salary_slips_submitted": 1, "status": "Submitted", "error_message": ""})

    except Exception as e:
        log_payroll_failure("creation", payroll_entry, e)
    finally:
        frappe.flags.via_payroll_entry = False
        frappe.db.commit()  # nosemgrep
        frappe.publish_realtime("completed_salary_slip_submission", user=frappe.session.user)


def calculate_salary_slips_for_employees(employees, args, publish_progress=True):
    """
    BJOBLY: Pre-calculates salaries in RAM and updates the Payroll Entry grid.
    This version triggers the custom validation rules (30-day basis and date ranges).
    """
    # 1. Use get_doc to ensure we have the live object to update totals
    payroll_entry = frappe.get_doc("Payroll Entry", args.payroll_entry)
    frappe.flags.current_payroll_entry = payroll_entry

    try:
        count = 0
        employees_list = list(set(employees))
        
        for emp in payroll_entry.employees:
                # 3. Instantiate Salary Slip in RAM
                args.update({
                    "doctype": "Salary Slip", 
                    "employee": emp.employee
                })
                salary_slip = frappe.get_doc(args)
                
                # 4. TRIGGER VALIDATE (The magic step)
                # This executes your custom 30-day logic and date range check.
                # If the dates are wrong (e.g., Feb 2 to March 3), it will throw an error here.
                salary_slip.validate()
                
                # 5. Sync calculated results back to the Child Table row
                sync_employees_with_salary_slips(emp, salary_slip)
                
                count += 1
                if publish_progress:
                    frappe.publish_progress(
                        count * 100 / len(employees_list), 
                        title=_("Calculating Salaries ...")
                    )

        # 7. Update Global Totals safely using flt()
        payroll_entry.gross_pay = sum(flt(e.gross_pay) for e in payroll_entry.employees if e.employee in employees_list)
        payroll_entry.total_deduction = sum(flt(e.total_deductions) for e in payroll_entry.employees if e.employee in employees_list)
        payroll_entry.net_pay = payroll_entry.gross_pay - payroll_entry.total_deduction
        
        # Clear previous errors, reset status to Draft, and save the preview data
        payroll_entry.db_set({"salary_slips_calculated": 1, "status": "Draft", "error_message": ""})
        payroll_entry.save(ignore_permissions=True)

    except Exception as e:
        frappe.db.rollback()
        payroll_entry.db_set("status", "Draft")
        from hrms.payroll.doctype.payroll_entry.payroll_entry import log_payroll_failure
        log_payroll_failure("calculation", payroll_entry, e)

    finally:
        frappe.db.commit()
        # Notify JS that the pre-calculation is finished
        frappe.publish_realtime("completed_salary_slip_creation", user=frappe.session.user)

def sync_employees_with_salary_slips(employee, slip):
    employee.salary_structure = slip.salary_structure
    employee.total_working_days = slip.total_working_days
    employee.unmarked_days = slip.unmarked_days
    employee.leave_without_pay = slip.leave_without_pay
    employee.absent_days = slip.absent_days
    employee.payment_days = slip.payment_days
    employee.gross_pay = slip.gross_pay
    employee.total_deductions = slip.total_deduction
    employee.net_pay = slip.net_pay
    employee.rounded_total = slip.rounded_total
    employee.ctc = slip.ctc
    employee.income_from_other_sources = slip.income_from_other_sources
    employee.total_earnings = slip.total_earnings
    employee.taxable_earnings = slip.taxable_earnings
    employee.non_taxable_earnings = slip.non_taxable_earnings
    employee.taxable_deductions_till_date = slip.taxable_deductions_till_date
    employee.standard_tax_exemption_amount = slip.standard_tax_exemption_amount
    employee.tax_exemption_declaration = slip.tax_exemption_declaration
    employee.deductions_before_tax_calculation = slip.deductions_before_tax_calculation
    employee.annual_taxable_amount = slip.annual_taxable_amount
    employee.income_tax_deducted_till_date = slip.income_tax_deducted_till_date
    employee.current_month_income_tax = slip.current_month_income_tax
    employee.future_income_tax_deductions = slip.future_income_tax_deductions
    employee.total_income_tax = slip.total_income_tax

def submit_salary_slips_for_employees(payroll_entry, salary_slips, publish_progress=True):
    submitted = []
    unsubmitted = []
    frappe.flags.via_payroll_entry = True

    for count, entry in enumerate(salary_slips, 1):
        salary_slip = frappe.get_doc("Salary Slip", entry[0])
        if salary_slip.net_pay < 0:
            unsubmitted.append(entry[0])
        else:
            try:
                salary_slip.submit()
                submitted.append(salary_slip)
            except Exception:
                unsubmitted.append(entry[0])
                frappe.log_error(frappe.get_traceback(), f"Error submitting Salary Slip {entry[0]}")

        if publish_progress:
            frappe.publish_progress(
                count * 100 / len(salary_slips), title=_("Submitting Salary Slips...")
            )

    if submitted:
        try:
            payroll_entry.email_salary_slip(submitted)
        except Exception:
            pass  # Email failure must not block submission
        payroll_entry.db_set({"salary_slips_submitted": 1, "status": "Submitted", "error_message": ""})

    frappe.db.commit()  # nosemgrep
    frappe.publish_realtime("completed_salary_slip_submission", user=frappe.session.user)
    show_payroll_submission_status(submitted, unsubmitted, payroll_entry)
    frappe.flags.via_payroll_entry = False