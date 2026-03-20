// Copyright (c) 2026, edwinalonso162@hotmail.com and contributors
// For license information, please see license.txt

frappe.query_reports["Payroll Summary"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: "Department\nDesignation\nBranch",
			default: "Department",
			reqd: 1,
		},
		{
			fieldname: "employment_type",
			label: __("Employment Type"),
			fieldtype: "Link",
			options: "Employment Type",
			depends_on: "eval:!doc.all_employment_types",
			mandatory_depends_on: "eval:!doc.all_employment_types",
		},
		{
			fieldname: "all_employment_types",
			label: __("All Employment Types"),
			fieldtype: "Check",
			default: 1,
			on_change: function() {
				const checked = frappe.query_report.get_filter_value("all_employment_types");
				if (checked) {
					frappe.query_report.set_filter_value("employment_type", "");
				}
			},
		},
	],
};
