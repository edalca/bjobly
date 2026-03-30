// Copyright (c) 2026, Bjobly and contributors

frappe.ui.form.on("Salary Slip", {
	onload: function (frm) {
		frappe.db.get_single_value("Payroll Settings", "sort_employees_by").then(function (fmt) {
			if (!fmt || !frm.doc.employee_name) return;
			var formatted = bjobly.format_emp_name(frm.doc.employee_name, fmt);
			if (formatted !== frm.doc.employee_name) {
				frm.set_value("employee_name", formatted);
			}
		});
	},
});
