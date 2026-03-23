import frappe
from frappe import _
from frappe.utils import flt
from hrms.payroll.doctype.salary_structure_assignment.salary_structure_assignment import (
	SalaryStructureAssignment,
)


class BjoblySalaryStructureAssignment(SalaryStructureAssignment):
	def validate(self):
		if not flt(self.base):
			frappe.throw(
				_("Base salary cannot be zero. Please enter a valid base salary."),
				title=_("Invalid Base Salary"),
			)
		super().validate()
