import frappe
import json
from frappe import _
from frappe.utils import flt, getdate, formatdate, add_days, add_months
from hrms.payroll.doctype.payroll_entry.payroll_entry import (
    PayrollEntry,
    get_salary_structure,
    log_payroll_failure,
    remove_payrolled_employees, 
    set_fields_to_select, 
    set_searchfield, 
    set_filter_conditions, 
    set_match_conditions,
    show_payroll_submission_status
)

class BjoblyPayrollEntry(PayrollEntry):
    def validate(self):
        # Standard validations
        self.validate_payroll_range()
        super(BjoblyPayrollEntry, self).validate()

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
    @frappe.whitelist()
    def create_salary_slips(self):
        if not self.salary_slips_calculated:
            frappe.throw(_("Salary Slips need to be calculated."), title=_("Calculate Salary Slips"))
        
        super().create_salary_slips()
        
    @frappe.whitelist()
    def calculate_salary_slips(self):
        """
        Calculate salary slip for selected employees if already not created
        """
        self.check_permission("write")
        employees = [emp.employee for emp in self.employees]

        if employees:
            args = frappe._dict(
                {
                    "salary_slip_based_on_timesheet": self.salary_slip_based_on_timesheet,
                    "payroll_frequency": self.payroll_frequency,
                    "start_date": self.start_date,
                    "end_date": self.end_date,
                    "company": self.company,
                    "posting_date": self.posting_date,
                    "deduct_tax_for_unclaimed_employee_benefits": self.deduct_tax_for_unclaimed_employee_benefits,
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
    def submit_salary_slips(self):
        self.check_permission("write")
        salary_slips = self.get_sal_slip_list(ss_status=0)

        if len(salary_slips) > 30 or frappe.flags.enqueue_payroll_entry:
            self.db_set("status", "Queued")
            frappe.enqueue(
                submit_salary_slips_for_employees,
                timeout=3000,
                payroll_entry=self,
                salary_slips=salary_slips,
                publish_progress=False,
            )
            frappe.msgprint(
                _("Salary Slip submission is queued. It may take a few minutes"),
                alert=True,
                indicator="blue",
            )
        else:
            submit_salary_slips_for_employees(self, salary_slips, publish_progress=False)

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
            
            frappe.throw(error_msg, title=_("No employees found"))

        self.set("employees", employees)
        self.number_of_employees = len(self.employees)
        self.update_employees_with_withheld_salaries()
        self.salary_slips_calculated=0
        self.save(ignore_permissions=True)
        return self.get_employees_with_unmarked_attendance()


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

    return remove_payrolled_employees(employees_to_check, filters.start_date, filters.end_date)


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

def calculate_salary_slips_for_employees(employees, args, publish_progress=True):
    """
    BJOBLY: Pre-calculates salaries in RAM and updates the Payroll Entry grid.
    This version triggers the custom validation rules (30-day basis and date ranges).
    """
    # 1. Use get_doc to ensure we have the live object to update totals
    payroll_entry = frappe.get_doc("Payroll Entry", args.payroll_entry)

    try:
        count = 0
        employees_list = list(set(employees))
        
        for emp in payroll_entry.employees:
            if emp.employee in employees_list:
                # 2. Prepare virtual Salary Slip arguments
                # Pass emp.employee (the string ID) instead of the row object
                args.update({
                    "doctype": "Salary Slip", 
                    "employee": emp.employee,
                    "salary_slip_based_on_timesheet": payroll_entry.salary_slip_based_on_timesheet
                })
                
                # 3. Instantiate Salary Slip in RAM
                salary_slip = frappe.get_doc(args)
                
                # 4. TRIGGER VALIDATE (The magic step)
                # This executes your custom 30-day logic and date range check.
                # If the dates are wrong (e.g., Feb 2 to March 3), it will throw an error here.
                salary_slip.validate()
                
                # 5. Sync calculated results back to the Child Table row
                sync_employees_with_salary_slips(emp, salary_slip)
                
                # 6. Generate JSON for HTML rendering in the Grid
                emp.earnings_json = json.dumps([{"salary_component": d.salary_component, "amount": d.amount} for d in salary_slip.earnings])
                emp.deductions_json = json.dumps([{"salary_component": d.salary_component, "amount": d.amount} for d in salary_slip.deductions])
                
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
        
        # Clear previous errors and save the preview data
        payroll_entry.db_set({ "salary_slips_calculated": 1, "error_message": ""})
        payroll_entry.save(ignore_permissions=True)

    except Exception as e:
        frappe.db.rollback()
        # Log error so it appears in the "Error Message" field of Payroll Entry
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
    try:
        submitted = []
        unsubmitted = []
        frappe.flags.via_payroll_entry = True
        count = 0

        for entry in salary_slips:
            salary_slip = frappe.get_doc("Salary Slip", entry[0])
            if salary_slip.net_pay < 0:
                unsubmitted.append(entry[0])
            else:
                try:
                    salary_slip.submit()
                    submitted.append(salary_slip)
                except frappe.ValidationError:
                    unsubmitted.append(entry[0])

            count += 1
            if publish_progress:
                frappe.publish_progress(
                    count * 100 / len(salary_slips), title=_("Submitting Salary Slips...")
                )

        if submitted:
            payroll_entry.email_salary_slip(submitted)
            payroll_entry.db_set({"salary_slips_submitted": 1, "status": "Submitted", "error_message": ""})

        show_payroll_submission_status(submitted, unsubmitted, payroll_entry)

    except Exception as e:
        frappe.db.rollback()
        log_payroll_failure("submission", payroll_entry, e)

    finally:
        frappe.db.commit()  # nosemgrep
        frappe.publish_realtime("completed_salary_slip_submission", user=frappe.session.user)

    frappe.flags.via_payroll_entry = False