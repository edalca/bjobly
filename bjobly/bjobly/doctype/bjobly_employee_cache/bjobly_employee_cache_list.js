frappe.listview_settings["Bjobly Employee Cache"] = {
	add_fields: ["payroll_type"],
	formatters: {
		payroll_type: function (value, df, doc) {
			if (value === "Honorariums") {
				return `<span class="badge badge-orange" style="background-color: var(--orange-100); color: var(--orange-700); border: 1px solid var(--orange-200); padding: 2px 8px; border-radius: 12px; font-weight: bold;">${__(
					value,
				)}</span>`;
			} else if (value === "Regular Salary") {
				return `<span class="badge badge-blue" style="background-color: var(--blue-100); color: var(--blue-700); border: 1px solid var(--blue-200); padding: 2px 8px; border-radius: 12px; font-weight: bold;">${__(
					value,
				)}</span>`;
			} else {
				return `<span class="badge badge-gray" style="background-color: var(--gray-100); color: var(--gray-700); border: 1px solid var(--gray-200); padding: 2px 8px; border-radius: 12px;">${__(
					value || "Not Assigned",
				)}</span>`;
			}
		},
	},
};
