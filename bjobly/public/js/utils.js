// Bjobly shared utilities — available globally in Frappe desk

frappe.provide("bjobly");

/**
 * Format employee_name according to Payroll Settings → sort_employees_by.
 * Guatemalan names: "First1 First2 Last1 Last2"
 * Heuristic: ≥4 words → last 2 = apellidos; else last 1 = apellido.
 */
bjobly.format_emp_name = function (raw, fmt) {
	var parts = (raw || "").trim().replace(/\s+/g, " ").split(" ").filter(Boolean);
	if (!parts.length) return "—";
	if (parts.length === 1) return parts[0];

	var split     = parts.length >= 4 ? 2 : 1;
	var nombres   = parts.slice(0, parts.length - split);
	var apellidos = parts.slice(parts.length - split);

	var first  = nombres[0] || "";
	var middle = nombres.slice(1).join(" ");
	var last1  = apellidos[0] || "";
	var last2  = apellidos.slice(1).join(" ");
	var last   = apellidos.join(" ");
	var mi     = middle ? middle[0].toUpperCase() + "." : "";

	switch (fmt) {
		case "Last Name, First Name Middle Name":    return last + ", " + nombres.join(" ");
		case "First Name Middle Name Last Name":     return parts.join(" ");
		case "Last Name, First Name":                return last1 + (last2 ? " " + last2 : "") + ", " + first;
		case "First Name Last Name":                 return first + " " + last;
		case "Last Name First Name Middle Name":     return last + " " + nombres.join(" ");
		case "First Name Middle Initial. Last Name": return (first + (mi ? " " + mi : "") + " " + last).trim();
		default:                                     return last + ", " + nombres.join(" ");
	}
};

/**
 * Applies name format to employee_name fields in a child table grid.
 * @param {object} frm - Frappe form object
 * @param {string} table_field - child table fieldname (e.g. "employees")
 * @param {string} name_fmt - format string from Payroll Settings
 */
bjobly.apply_name_format_to_grid = function (frm, table_field, name_fmt) {
	if (!name_fmt) return;
	var grid = frm.fields_dict[table_field] && frm.fields_dict[table_field].grid;
	if (!grid) return;

	(frm.doc[table_field] || []).forEach(function (row) {
		if (!row.employee_name) return;
		var formatted = bjobly.format_emp_name(row.employee_name, name_fmt);
		if (formatted !== row.employee_name) {
			frappe.model.set_value(row.doctype, row.name, "employee_name", formatted);
		}
	});
	grid.refresh();
};
