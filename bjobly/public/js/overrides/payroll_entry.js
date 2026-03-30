// Copyright (c) 2026, Bjobly and contributors

frappe.provide("erpnext.accounts.dimensions");

frappe.ui.form.on("Payroll Entry", {
	onload: function (frm) {
		frm.ignore_doctypes_on_cancel_all = ["Salary Slip", "Journal Entry"];

		if (!frm.doc.posting_date) {
			frm.doc.posting_date = frappe.datetime.nowdate();
		}

		erpnext.accounts.dimensions.setup_dimension_filters(frm, frm.doctype);
		frm.events.department_filters(frm);
		frm.events.payroll_payable_account_filters(frm);

		frappe.db.get_single_value("Payroll Settings", "sort_employees_by").then(function (fmt) {
			frm._bjobly_name_fmt = fmt;
			bjobly.apply_name_format_to_grid(frm, "employees", fmt);
		});
	},

	get_employees_btn: function (frm) {
		frappe.call({
			doc: frm.doc,
			method: "fill_employee_details",
			freeze: true,
			freeze_message: __("Getting Employees..."),
			callback: function () {
				frm.refresh();
				bjobly.apply_name_format_to_grid(frm, "employees", frm._bjobly_name_fmt);
				frappe.show_alert({ message: __("Employees loaded"), indicator: "green" });
			},
		});
	},

	calculate_salaries_btn: function (frm) {
		if (!frm.doc.employees || !frm.doc.employees.length) {
			frappe.msgprint(__("Please get employees first."));
			return;
		}
		frappe.call({
			doc: frm.doc,
			method: "calculate_salary_slips",
			freeze: true,
			freeze_message: __("Calculating salaries..."),
			callback: function () {
				frm.reload_doc();
				frappe.show_alert({ message: __("Salaries calculated"), indicator: "green" });
			},
		});
	},

	refresh: function (frm) {
		// Clean refresh: remove most automated buttons to favor the Dashboard
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.clear_custom_buttons();
		}

		if (frm.doc.docstatus === 1) {
			frm.clear_custom_buttons();

			if (!cint(frm.doc.completed_journal_entry_creation)) {
				frm.events._set_jv_button(frm);
			} else if (!cint(frm.doc.completed_bank_entry)) {
				frm.events._set_bank_entry_button(frm);
			}
		}
	},

	_set_jv_button: function (frm) {
		frm.add_custom_button(__("Create Journal Entry"), () => {
			frappe.call({
				doc: frm.doc,
				method: "create_journal_entry",
				freeze: true,
				freeze_message: __("Creating Journal Entry..."),
				callback: function () {
					frm.reload_doc();
					frappe.show_alert({
						message: __("Journal Entry created"),
						indicator: "green",
					});
				},
			});
		}).addClass("btn-primary");
	},

	_set_bank_entry_button: function (frm) {
		frm.add_custom_button(__("Make Bank Entry"), () => {
			if (!frm.doc.payment_account) {
				frappe.msgprint(__("Payment Account is mandatory"));
				frm.scroll_to_field("payment_account");
				return;
			}
			frappe.call({
				method: "run_doc_method",
				args: {
					method: "make_bank_entry",
					dt: "Payroll Entry",
					dn: frm.doc.name,
					args: { for_withheld_salaries: 0 },
				},
				freeze: true,
				freeze_message: __("Creating Payment Entries..."),
				callback: function () {
					frm.reload_doc();
					frappe.set_route("List", "Journal Entry", {
						"Journal Entry Account.reference_name": frm.doc.name,
					});
				},
			});
		}).addClass("btn-primary");
	},

	payroll_payable_account_filters: function (frm) {
		frm.set_query("payroll_payable_account", () => ({
			filters: { company: frm.doc.company, root_type: "Liability", is_group: 0 },
		}));
	},

	department_filters: function (frm) {
		frm.set_query("department", () => ({
			filters: { company: frm.doc.company },
		}));
	},
});
