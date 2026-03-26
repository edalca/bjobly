import frappe
import json
from frappe import _
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

import unicodedata

class BjoblyPayrollEntry(PayrollEntry):
    def validate(self):
        self.validate_payroll_range()
        super(BjoblyPayrollEntry, self).validate()

        # Pre-check attendance configuration if validation is OFF
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

    def save(self, *args, **kwargs):
        """Override save para reaplique el formato de nombre tras cada guardado."""
        super().save(*args, **kwargs)
        self._reapply_employee_name_format()

    def _reapply_employee_name_format(self):
        """Reaplica el formato de employee_name en el child table después de un save."""
        sort_by = getattr(self, "sort_employees_by", None)
        LASTNAME_FIRST = {
            "Last Name, First Name Middle Name",
            "Last Name, First Name",
            "Last Name First Name Middle Name",
        }
        FIRSTNAME_FIRST = {
            "First Name Middle Name Last Name",
            "First Name Last Name",
            "First Name Middle Initial. Last Name",
        }
        if not self.employees or sort_by not in (LASTNAME_FIRST | FIRSTNAME_FIRST):
            return

        emp_ids = [row.employee for row in self.employees]
        emp_data = frappe.get_all(
            "Employee",
            filters={"name": ["in", emp_ids]},
            fields=["name", "first_name", "middle_name", "last_name"],
        )
        name_map = {e.name: e for e in emp_data}

        for emp_row in self.employees:
            info = name_map.get(emp_row.employee)
            if not info:
                continue
            formatted = _format_employee_name(
                info.get("first_name") or "",
                info.get("middle_name") or "",
                info.get("last_name") or "",
                sort_by,
            )
            if emp_row.employee_name != formatted:
                frappe.db.set_value(
                    "Payroll Employee Detail",
                    emp_row.name,
                    "employee_name",
                    formatted,
                    update_modified=False,
                )
                emp_row.employee_name = formatted


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
            # Pre-check attendance configuration if validation is OFF
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

            args = frappe._dict(
                {
                    "salary_slip_based_on_timesheet": self.salary_slip_based_on_timesheet,
                    "payroll_frequency": self.payroll_frequency,
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

            frappe.throw(error_msg, title=_("No employees found"))

        # Ordenar empleados según el campo sort_employees_by (Predeterminado: Last Name, First Name Middle Name)
        sort_by = getattr(self, "sort_employees_by", None) or "Last Name, First Name Middle Name"
        LASTNAME_FIRST = {
            "Last Name, First Name Middle Name",
            "Last Name, First Name",
            "Last Name First Name Middle Name",
        }
        FIRSTNAME_FIRST = {
            "First Name Middle Name Last Name",
            "First Name Last Name",
            "First Name Middle Initial. Last Name",
        }

        # Construir name_map si se necesita ordenar/formatear
        name_map = {}
        if sort_by in (LASTNAME_FIRST | FIRSTNAME_FIRST):
            emp_ids = [e.employee for e in employees]
            emp_names = frappe.get_all(
                "Employee",
                filters={"name": ["in", emp_ids]},
                fields=["name", "first_name", "middle_name", "last_name"],
            )
            
            if emp_names:
                name_map = {e.name: e for e in emp_names}
            
            # ALWAYS sort by Last Name, First Name, Middle Name for consistent Payroll lists
            # even if the display format is different.
            employees = sorted(
                employees,
                key=lambda e: (
                    _normalize_for_sort(name_map.get(e.employee, {}).get("last_name") or ""),
                    _normalize_for_sort(name_map.get(e.employee, {}).get("first_name") or ""),
                    _normalize_for_sort(name_map.get(e.employee, {}).get("middle_name") or ""),
                )
            )

        self.set("employees", employees)
        self.number_of_employees = len(self.employees)
        self.update_employees_with_withheld_salaries()
        self.salary_slips_calculated = 0
        self.save(ignore_permissions=True)  # _reapply_employee_name_format() se llama automáticamente

        return self.get_employees_with_unmarked_attendance()


def _format_employee_name(first_name, middle_name, last_name, format_type):
    """Formats employee name based on the selected format_type."""
    f = first_name.strip()
    m = middle_name.strip()
    l = last_name.strip()
    
    m_initial = f"{m[0]}." if m else ""

    if format_type == "Last Name, First Name Middle Name":
        return f"{l}, {f} {m}".strip()
    elif format_type == "First Name Middle Name Last Name":
        return f"{f} {m} {l}".strip()
    elif format_type == "Last Name, First Name":
        return f"{l}, {f}".strip()
    elif format_type == "First Name Last Name":
        return f"{f} {l}".strip()
    elif format_type == "Last Name First Name Middle Name":
        return f"{l} {f} {m}".strip()
    elif format_type == "First Name Middle Initial. Last Name":
        return f"{f} {m_initial} {l}".strip()
    
    return f"{f} {l}".strip()

def _normalize_for_sort(s):
    """Normalize string for sorting: lowercase and remove accents."""
    if not s:
        return ""
    s = str(s).lower()
    # Normalize unicode to NFKD and encode to ASCII to strip accents
    return unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('ASCII')


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