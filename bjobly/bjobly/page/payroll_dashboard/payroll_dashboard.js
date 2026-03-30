/**
 * Bjobly Payroll Dashboard v16
 * Uses Frappe native CSS variables — supports light / dark themes automatically.
 */

frappe.pages["payroll-dashboard"].on_page_load = function (wrapper) {
	try {
		var page = frappe.ui.make_app_page({
			parent: wrapper,
			title: __("Payroll Dashboard"),
			single_column: true,
		});

		wrapper.page = page;

		// Inject page structure and styles directly — no bundle/template system required.
		inject_dashboard_styles();
		page.body.append(build_dashboard_html());

		setup_dashboard_interface(wrapper);
		setup_filter_bar(wrapper);
		render_loading_state(wrapper);

		// Load name format from Payroll Settings → sort_employees_by
		frappe.db.get_single_value("Payroll Settings", "sort_employees_by").then(function (val) {
			wrapper.name_format = val || "Last Name, First Name Middle Name";
			refresh_dashboard(wrapper);
		});
	} catch (err) {
		console.error("Dashboard Load Error:", err);
	}
};

// ---------------------------------------------------------------------------
// Page header: primary action + resync only — filters live in the body bar
// ---------------------------------------------------------------------------
function setup_dashboard_interface(wrapper) {
	var page = wrapper.page;

	page.set_primary_action(__("Generate Payroll"), function () {
		open_generate_payroll_dialog(wrapper);
	});

	var $reload_btn = page.add_inner_button("", function () {
		// Force gross pay recalculation then refresh list
		var company = wrapper.fc && wrapper.fc.company ? wrapper.fc.company.get_value() : null;
		if (company) trigger_gross_calculation(wrapper, company, true);
		else refresh_dashboard(wrapper);
	});
	$reload_btn.addClass("icon-btn text-muted")
		.attr("title", __("Recalcular y recargar"))
		.html('<svg class="es-icon es-line icon-sm" aria-hidden="true"><use href="#es-line-reload"></use></svg>');
}

// ---------------------------------------------------------------------------
// Filter bar — rendered inside the page body, below the header
// ---------------------------------------------------------------------------
function setup_filter_bar(wrapper) {
	var $bar = $(wrapper).find("#pd-filter-bar");
	wrapper.fc = {};

	function make(df) {
		var $col = $('<div class="pd-fc-col"></div>').appendTo($bar);
		var ctrl = frappe.ui.form.make_control({
			df: df,
			parent: $col[0],
			render_input: true,
		});
		ctrl.refresh();
		wrapper.fc[df.fieldname] = ctrl;
		return ctrl;
	}

	make({
		label: __("Company"), fieldname: "company",
		fieldtype: "Link", options: "Company",
		onchange: function () {
			wrapper.fc.department.set_value("");
			refresh_dashboard(wrapper);
		},
	});
	wrapper.fc.company.set_value(frappe.defaults.get_default("company") || "");

	make({
		label: __("Payroll Type"), fieldname: "payroll_type",
		fieldtype: "Select", options: "\nRegular Salary\nHonorariums",
		onchange: function () { refresh_dashboard(wrapper); },
	});

	make({
		label: __("Department"), fieldname: "department",
		fieldtype: "Link", options: "Department",
		get_query: function () {
			var company = wrapper.fc.company ? wrapper.fc.company.get_value() : null;
			return company ? { filters: { company: company } } : {};
		},
		onchange: function () { refresh_dashboard(wrapper); },
	});

	make({
		label: __("Empleado"), fieldname: "search",
		fieldtype: "Link", options: "Employee",
		get_query: function () {
			var company = wrapper.fc.company ? wrapper.fc.company.get_value() : null;
			return company ? { filters: { company: company } } : {};
		},
		onchange: function () { apply_local_filters(wrapper); },
	});

	make({
		label: __("Sort By"), fieldname: "sort_by",
		fieldtype: "Select",
		options: [
			{ label: __("Employee (A-Z)"), value: "name_asc" },
			{ label: __("Employee (Z-A)"), value: "name_desc" },
			{ label: __("Salary (High → Low)"), value: "salary_desc" },
			{ label: __("Salary (Low → High)"), value: "salary_asc" },
		],
		onchange: function () { apply_local_filters(wrapper); },
	});
	wrapper.fc.sort_by.set_value("name_asc");
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function get_filters(wrapper) {
	var fc = wrapper.fc || {};
	return {
		company: fc.company ? fc.company.get_value() : null,
		payroll_type: fc.payroll_type ? fc.payroll_type.get_value() : null,
		department: fc.department ? fc.department.get_value() : null,
		search: fc.search ? fc.search.get_value() : "",
		sort_by: fc.sort_by ? fc.sort_by.get_value() : "name_asc",
	};
}

/** Format value as currency */
function fmt_c(v, currency) {
	return frappe.format(v || 0, { fieldtype: "Currency", currency: currency || frappe.boot.sysdefaults.currency });
}

// Delegate to shared utility in utils.js
var format_emp_name = bjobly.format_emp_name;

/** Build 2-letter initials from formatted name */
function initials(name) {
	return (name || "?")
		.split(/[\s,]+/)
		.filter(Boolean)
		.map(function (w) { return w[0] || ""; })
		.join("")
		.substring(0, 2)
		.toUpperCase();
}

// ---------------------------------------------------------------------------
// Data refresh (hits server)
// ---------------------------------------------------------------------------
function refresh_dashboard(wrapper) {
	var f = get_filters(wrapper);
	if (!f.company) return;

	render_loading_state(wrapper);

	frappe.call({
		method: "bjobly.bjobly.api.get_dashboard_employees",
		args: {
			company: f.company,
			payroll_type: f.payroll_type,
			departments: f.department,
		},
		callback: function (r) {
			var resp = r.message || {};
			var company_doc = frappe.get_doc(":Company", f.company);
			wrapper.currency       = company_doc ? company_doc.default_currency : frappe.boot.sysdefaults.currency;
			wrapper.last_data      = resp.employees || [];
			wrapper.gross_cache_ts = resp.gross_cache_ts || null;

			// Auto-trigger gross pay calculation if cache is empty
			if (!resp.has_gross_cache) {
				trigger_gross_calculation(wrapper, f.company, false);
			}

			render_stats(wrapper, wrapper.last_data);
			render_list_html(wrapper, wrapper.last_data);
		},
	});
}

/**
 * Enqueue gross pay calculation. On completion, refreshes the dashboard.
 * @param {boolean} force  true = always enqueue (reload button); false = only if no cache
 */
function trigger_gross_calculation(wrapper, company, force) {
	frappe.show_alert({ message: __("Calculando bruto en segundo plano..."), indicator: "blue" });

	frappe.call({
		method: "bjobly.bjobly.api.calculate_gross_pay",
		args: { company: company },
	});

	// Avoid duplicate listeners
	frappe.realtime.off("bjobly_gross_calculated");
	frappe.realtime.on("bjobly_gross_calculated", function (data) {
		if ((data.company || data) === company) {
			frappe.realtime.off("bjobly_gross_calculated");
			refresh_dashboard(wrapper);
		}
	});

	if (force) refresh_dashboard(wrapper);
}

function apply_local_filters(wrapper) {
	if (wrapper.last_data) render_list_html(wrapper, wrapper.last_data);
}

// ---------------------------------------------------------------------------
// Stats cards
// ---------------------------------------------------------------------------
function render_stats(wrapper, data) {
	var $grid = $(wrapper).find("#payroll-summary");
	if (!$grid.length) return;

	var total_emp = new Set(data.map(function (e) { return e.employee; })).size;
	var unassigned = data.filter(function (e) {
		return !e.payroll_type || e.payroll_type === "Sin Asignar";
	}).length;

	function emp_gross(e) {
		return e.gross_pay != null
			? flt(e.gross_pay)
			: flt(e.current_base_salary) + flt(e.variable_salary);
	}

	var reg_total = data
		.filter(function (e) { return e.payroll_type === "Regular Salary"; })
		.reduce(function (acc, e) { return acc + emp_gross(e); }, 0);

	var hon_total = data
		.filter(function (e) { return e.payroll_type === "Honorariums"; })
		.reduce(function (acc, e) { return acc + emp_gross(e); }, 0);

	var grand = reg_total + hon_total;

	var emp_label = total_emp + (unassigned
		? ' <span style="font-size:12px;font-weight:500;color:var(--red,#e74c3c)">(' + unassigned + ' ' + __("unassigned") + ')</span>'
		: "");

	$grid.html(
		stat_card(__("Total Employees"), emp_label, "") +
		stat_card(__("Regular Total"), fmt_c(reg_total, wrapper.currency), "pd-green") +
		stat_card(__("Honorarium Total"), fmt_c(hon_total, wrapper.currency), "pd-blue") +
		stat_card(__("Grand Total Budget"), fmt_c(grand, wrapper.currency), "", "pd-stat-total")
	);
}

function stat_card(label, value, value_class, card_class) {
	return (
		'<div class="widget number-widget-box ' + (card_class || "") + '">' +
		'<div class="widget-head">' +
		'<div class="widget-label">' +
		'<div class="widget-title"><span class="ellipsis" title="' + label + '">' + label + "</span></div>" +
		"</div>" +
		"</div>" +
		'<div class="widget-body">' +
		'<div class="widget-content">' +
		'<div class="number ' + (value_class || "") + '">' + value + "</div>" +
		"</div>" +
		"</div>" +
		'<div class="widget-footer"></div>' +
		"</div>"
	);
}

// ---------------------------------------------------------------------------
// Employee list — native Frappe .frappe-list structure
// ---------------------------------------------------------------------------
var PD_PAGE_SIZES = [20, 100, 500];

function render_list_html(wrapper, data) {
	var $c = $(wrapper).find("#payroll-list-wrapper");
	var f = get_filters(wrapper);

	/* Search — Link field returns employee ID */
	var rows = data;
	if (f.search) {
		var q = f.search.toLowerCase();
		rows = data.filter(function (r) {
			return (r.employee || "").toLowerCase() === q;
		});
	}

	/* Sort — use formatted name so A-Z matches what the user sees */
	var _fmt = wrapper.name_format || "Last Name, First Name Middle Name";
	function row_gross(r) {
		return r.gross_pay != null ? flt(r.gross_pay) : flt(r.total_salary);
	}

	var sort_fns = {
		name_asc:    function (a, b) { return format_emp_name(a.employee_name, _fmt).localeCompare(format_emp_name(b.employee_name, _fmt)); },
		name_desc:   function (a, b) { return format_emp_name(b.employee_name, _fmt).localeCompare(format_emp_name(a.employee_name, _fmt)); },
		salary_desc: function (a, b) { return row_gross(b) - row_gross(a); },
		salary_asc:  function (a, b) { return row_gross(a) - row_gross(b); },
	};
	if (sort_fns[f.sort_by]) rows.sort(sort_fns[f.sort_by]);

	/* Pagination */
	var page_size = wrapper.pd_page_size || 20;
	var page_start = wrapper.pd_page_start || 0;
	var slice = rows.slice(page_start, page_start + page_size);
	var total = rows.length;

	if (!total) {
		$c.html(
			'<div class="frappe-list">' +
			'<div class="no-result text-muted flex justify-center align-center" style="min-height:200px">' +
			'<div class="msg-box no-border">' +
			'<svg class="icon icon-xl mb-2" style="stroke:var(--text-light)"><use href="#icon-small-file"></use></svg>' +
			"<p>" + __("No matching employees found") + "</p>" +
			"</div>" +
			"</div>" +
			"</div>"
		);
		return;
	}

	/* Header row */
	var header =
		'<div class="list-row-container">' +
		'<header class="level list-row-head text-muted">' +
		'<div class="level-left list-header-subject">' +
		list_col(__("Employee"), "list-subject level name", true) +
		list_col(__("Payroll Type"), "hidden-xs") +
		list_col(__("Department"), "hidden-xs") +
		list_col(__("Base Salary"), "hidden-xs pd-col-right") +
		list_col(__("Variable"), "hidden-xs pd-col-right") +
		list_col(__("Gross Pay"), "hidden-xs pd-col-right") +
		"</div>" +
		'<div class="level-right">' +
		'<span class="list-count" style="white-space:nowrap">' +
		"<span>" + __("{0} of {1}", [Math.min(page_start + page_size, total), total]) + "</span>" +
		"</span>" +
		"</div>" +
		"</header>" +
		"</div>";

	/* Data rows */
	var body = "";
	var name_fmt = wrapper.name_format || "Last Name, First Name Middle Name";

	slice.forEach(function (row) {
		var is_hon = row.payroll_type === "Honorariums";
		var is_unassigned = !row.payroll_type || row.payroll_type === "Sin Asignar";
		var emp_link = frappe.utils.get_form_link("Employee", row.employee);
		var display_name = format_emp_name(row.employee_name, name_fmt);
		var init = initials(display_name);
		var av_cls = is_unassigned ? "pd-avatar-gray" : (is_hon ? "pd-avatar-orange" : "pd-avatar-blue");
		var sal_cls = is_unassigned ? " text-muted" : "";

		var badge = is_unassigned
			? '<span class="indicator-pill red no-indicator-dot pd-badge-dashed">' + __("Unassigned") + "</span>"
			: is_hon
				? '<span class="indicator-pill orange no-indicator-dot">' + __(row.payroll_type) + "</span>"
				: '<span class="indicator-pill gray no-indicator-dot">' + __(row.payroll_type) + "</span>";

		var data_attrs =
			'data-employee="' + frappe.utils.escape_html(row.employee) + '" ' +
			'data-name="' + frappe.utils.escape_html(row.employee_name) + '"';

		var ic_open = frappe.utils.icon("link-url", "xs");
		var ic_assign = frappe.utils.icon("add", "xs");
		var ic_history = frappe.utils.icon("clock", "xs");

		var action = is_unassigned
			? '<button class="btn btn-xs btn-default pd-assign-btn pd-icon-btn" ' + data_attrs +
			' title="' + __("Assign Salary Structure") + '">' + ic_assign + "</button>"
			: '<div class="d-flex gap-1">' +
			'<a class="btn btn-xs btn-default pd-icon-btn" href="' + emp_link +
			'" title="' + __("Open Employee") + '">' + ic_open + "</a>" +
			'<button class="btn btn-xs btn-default pd-assign-btn pd-icon-btn" ' + data_attrs +
			' title="' + __("Assign Salary Structure") + '">' + ic_assign + "</button>" +
			'<button class="btn btn-xs btn-default pd-history-btn pd-icon-btn" ' + data_attrs +
			' title="' + __("Salary History") + '">' + ic_history + "</button>" +
			"</div>";

		body +=
			'<div class="list-row-container' + (is_hon ? " pd-hon-row" : "") + '" tabindex="1">' +
			'<div class="level list-row">' +
			'<div class="level-left ellipsis">' +

			// Employee — main subject column
			'<div class="list-row-col ellipsis list-subject level name">' +
			'<span class="level-item bold ellipsis">' +
			'<div class="pd-emp-cell">' +
			'<div class="pd-avatar ' + av_cls + '">' + init + "</div>" +
			"<div>" +
			'<div class="bold ellipsis">' +
			'<a class="ellipsis" href="' + emp_link + '" title="' + frappe.utils.escape_html(row.employee_name) + '">' +
			frappe.utils.escape_html(display_name || "—") +
			"</a>" +
			"</div>" +
			'<div class="text-muted" style="font-size:11px">' +
			frappe.utils.escape_html(row.employee) +
			"</div>" +
			"</div>" +
			"</div>" +
			"</span>" +
			"</div>" +

			// Payroll type
			'<div class="list-row-col ellipsis hidden-xs">' + badge + "</div>" +

			// Department
			'<div class="list-row-col ellipsis hidden-xs text-muted">' +
			frappe.utils.escape_html(row.department || "—") +
			"</div>" +

			// Salaries
			'<div class="list-row-col ellipsis hidden-xs pd-col-right' + sal_cls + '">' + fmt_c(row.current_base_salary, wrapper.currency) + "</div>" +
			'<div class="list-row-col ellipsis hidden-xs pd-col-right pd-var-val' + sal_cls + '">' + fmt_c(row.variable_salary || 0, wrapper.currency) + "</div>" +
			'<div class="list-row-col ellipsis hidden-xs pd-col-right bold' + sal_cls + '">' +
				(row.gross_pay != null
					? fmt_c(row.gross_pay, wrapper.currency)
					: '<span class="text-muted" style="font-size:11px">—</span>') +
			"</div>" +

			"</div>" +

			// Right side: action
			'<div class="level-right text-muted ellipsis border-0">' +
			'<div class="level-item list-row-activity hidden-xs">' + action + "</div>" +
			"</div>" +

			"</div>" +
			"</div>";
	});

	/* Pagination bar */
	var size_btns = PD_PAGE_SIZES.map(function (s) {
		var active = s === page_size ? " btn-info" : "";
		var dis = s === page_size ? " disabled" : "";
		return '<button type="button" class="btn btn-default btn-sm btn-paging' + active + '" data-value="' + s + '"' + dis + ">" + s + "</button>";
	}).join("");

	var load_more = (page_start + page_size < total)
		? '<button class="btn btn-default btn-more btn-sm">' + __("Load more") + "</button>"
		: '<button class="btn btn-default btn-more btn-sm" style="display:none">' + __("Load more") + "</button>";

	var paging =
		'<div class="list-paging-area level">' +
		'<div class="level-left"><div class="btn-group">' + size_btns + "</div></div>" +
		'<div class="level-right">' + load_more + "</div>" +
		"</div>";

	/* Assemble — result-container gets scrollbar, paging sits outside.
	   Measure actual offset of the list wrapper so nothing overflows the page. */
	var $wrapper_el = $(wrapper).find("#payroll-list-wrapper");
	var offset_top = $wrapper_el.length ? $wrapper_el.offset().top : 320;
	var paging_h = 52; // list-paging-area approx height
	var container_h = Math.max(200, $(window).height() - offset_top - paging_h - 8) + "px";

	$c.html(
		'<div class="frappe-list">' +
		'<div class="result-container" style="height:' + container_h + ';overflow-y:auto">' +
		'<div class="result no-assign-to">' +
		header +
		body +
		"</div>" +
		"</div>" +
		paging +
		"</div>"
	);

	/* Pagination events — off both namespaces first to avoid stacking handlers */
	$c.off("click.pd-page click.pd-assign")
		.on("click.pd-page", ".btn-paging:not([disabled])", function () {
			wrapper.pd_page_size = parseInt($(this).data("value"), 10);
			wrapper.pd_page_start = 0;
			render_list_html(wrapper, data);
		})
		.on("click.pd-page", ".btn-more", function () {
			wrapper.pd_page_start = (wrapper.pd_page_start || 0) + (wrapper.pd_page_size || 20);
			render_list_html(wrapper, data);
		})
		.on("click.pd-assign", ".pd-assign-btn", function () {
			open_assign_salary_dialog(wrapper, $(this).data("employee"), $(this).data("name"));
		})
		.on("click.pd-assign", ".pd-history-btn", function () {
			open_salary_history_dialog($(this).data("employee"), $(this).data("name"), wrapper.currency);
		});
}

function list_col(label, extra_cls, is_subject) {
	var base = "list-row-col ellipsis" + (is_subject ? " list-subject level name" : "");
	return '<div class="' + base + " " + (extra_cls || "") + '"><span>' + label + "</span></div>";
}

// ---------------------------------------------------------------------------
// Loading state
// ---------------------------------------------------------------------------
function render_loading_state(wrapper) {
	$(wrapper).find("#payroll-list-wrapper").html(
		'<div class="frappe-list">' +
		'<div class="pd-empty-state text-muted">' + __("Loading records…") + "</div>" +
		"</div>"
	);
}

// ---------------------------------------------------------------------------
// Assign Salary dialog  (quick-create Salary Structure Assignment)
// ---------------------------------------------------------------------------
function open_assign_salary_dialog(wrapper, employee, employee_name) {
	var company = wrapper.page.fields_dict.company
		? wrapper.page.fields_dict.company.get_value()
		: null;

	var d = new frappe.ui.Dialog({
		title: __("Assign Salary Structure — {0}", [employee_name]),
		fields: [
			{
				label: __("Salary Structure"),
				fieldname: "salary_structure",
				fieldtype: "Link",
				options: "Salary Structure",
				reqd: 1,
				get_query: function () {
					return { filters: { is_active: "Yes" } };
				},
				onchange: function () {
					var ss = d.get_value("salary_structure");
					if (!ss) {
						d.set_value("payroll_type_display", "");
						d.set_df_property("income_tax_slab", "hidden", 1);
						d.set_df_property("income_tax_slab", "reqd", 0);
						d.refresh_field("income_tax_slab");
						return;
					}
					frappe.db.get_value("Salary Structure", ss, "custom_payroll_type", function (r) {
						var pt = (r && r.custom_payroll_type) || __("Not set");
						var needs_isr = pt === "Regular Salary";
						d.set_value("payroll_type_display", pt);
						d.set_df_property("income_tax_slab", "hidden", needs_isr ? 0 : 1);
						d.set_df_property("income_tax_slab", "reqd", needs_isr ? 1 : 0);
						d.refresh_field("income_tax_slab");
					});
				},
			},
			{
				label: __("Payroll Type"),
				fieldname: "payroll_type_display",
				fieldtype: "Data",
				read_only: 1,
				description: __("Pulled from the selected Salary Structure"),
			},
			{
				label: __("Income Tax Slab (ISR)"),
				fieldname: "income_tax_slab",
				fieldtype: "Link",
				options: "Income Tax Slab",
				hidden: 1,
				reqd: 0,
			},
			{ fieldtype: "Column Break" },
			{
				label: __("Effective From"),
				fieldname: "from_date",
				fieldtype: "Date",
				reqd: 1,
				default: frappe.datetime.get_today(),
			},
			{
				label: __("Base Salary"),
				fieldname: "base",
				fieldtype: "Currency",
				reqd: 1,
			},
			{
				label: __("Variable"),
				fieldname: "variable",
				fieldtype: "Currency",
				default: 0,
			},
		],
		primary_action_label: __("Save & Submit"),
		primary_action: function (values) {
			frappe.call({
				method: "frappe.client.insert",
				args: {
					doc: {
						doctype: "Salary Structure Assignment",
						employee: employee,
						company: company,
						salary_structure: values.salary_structure,
						from_date: values.from_date,
						base: values.base,
						variable: values.variable || 0,
						income_tax_slab: values.income_tax_slab || null,
						docstatus: 1,
					},
				},
				freeze: true,
				freeze_message: __("Saving…"),
				callback: function (r) {
					if (!r.exc) {
						d.hide();
						frappe.show_alert({
							message: __("Salary assigned to {0}", [employee_name]),
							indicator: "green",
						});
						refresh_dashboard(wrapper);
					}
				},
			});
		},
	});

	d.show();
}

// ---------------------------------------------------------------------------
// Salary History dialog
// ---------------------------------------------------------------------------
function open_salary_history_dialog(employee, employee_name, currency) {
	var d = new frappe.ui.Dialog({
		title: __("Salary History — {0}", [employee_name]),
		fields: [
			{
				fieldname: "history_html",
				fieldtype: "HTML",
				options: '<div class="pd-history-loading text-muted" style="padding:20px;text-align:center">' + __("Loading…") + "</div>",
			},
		],
	});

	d.show();

	frappe.call({
		method: "frappe.client.get_list",
		args: {
			doctype: "Salary Structure Assignment",
			filters: { employee: employee },
			fields: ["name", "from_date", "salary_structure", "base", "variable", "docstatus"],
			order_by: "from_date desc",
			limit_page_length: 100,
		},
		callback: function (r) {
			var rows = r.message || [];

			if (!rows.length) {
				d.fields_dict.history_html.$wrapper.html(
					'<div class="text-muted" style="padding:20px;text-align:center">' +
					__("No salary assignments found for this employee.") +
					"</div>"
				);
				return;
			}

			// Fetch payroll_type for each unique salary structure
			var structures = [...new Set(rows.map(function (r) { return r.salary_structure; }))];
			frappe.call({
				method: "frappe.client.get_list",
				args: {
					doctype: "Salary Structure",
					filters: [["name", "in", structures]],
					fields: ["name", "custom_payroll_type"],
					limit_page_length: 100,
				},
				callback: function (sr) {
					var pt_map = {};
					(sr.message || []).forEach(function (s) {
						pt_map[s.name] = s.custom_payroll_type || "—";
					});

					var thead =
						'<thead><tr>' +
						'<th style="text-align:left">' + __("From Date") + "</th>" +
						'<th style="text-align:left">' + __("Salary Structure") + "</th>" +
						'<th style="text-align:left">' + __("Payroll Type") + "</th>" +
						'<th style="text-align:right">' + __("Base") + "</th>" +
						'<th style="text-align:right">' + __("Variable") + "</th>" +
						'<th style="text-align:center">' + __("Status") + "</th>" +
						"</tr></thead>";

					var tbody = "<tbody>" + rows.map(function (row) {
						var pt = pt_map[row.salary_structure] || "—";
						var is_hon = pt === "Honorariums";
						var pt_badge = is_hon
							? '<span class="indicator-pill orange no-indicator-dot">' + __(pt) + "</span>"
							: '<span class="indicator-pill gray no-indicator-dot">' + __(pt) + "</span>";
						var status_badge = row.docstatus === 1
							? '<span class="indicator-pill green no-indicator-dot">' + __("Submitted") + "</span>"
							: row.docstatus === 2
								? '<span class="indicator-pill red no-indicator-dot">' + __("Cancelled") + "</span>"
								: '<span class="indicator-pill gray no-indicator-dot">' + __("Draft") + "</span>";
						var ss_link =
							'<a href="' + frappe.utils.get_form_link("Salary Structure Assignment", row.name) + '" target="_blank">' +
							frappe.utils.escape_html(row.salary_structure) +
							"</a>";

						return (
							"<tr>" +
							"<td>" + frappe.utils.escape_html(row.from_date || "—") + "</td>" +
							"<td>" + ss_link + "</td>" +
							"<td>" + pt_badge + "</td>" +
							'<td style="text-align:right">' + fmt_c(row.base, currency) + "</td>" +
							'<td style="text-align:right">' + fmt_c(row.variable, currency) + "</td>" +
							'<td style="text-align:center">' + status_badge + "</td>" +
							"</tr>"
						);
					}).join("") + "</tbody>";

					var table =
						'<div style="overflow-x:auto">' +
						'<table class="table table-bordered table-condensed" style="font-size:12px;margin:0">' +
						thead + tbody +
						"</table>" +
						"</div>";

					d.fields_dict.history_html.$wrapper.html(table);
				},
			});
		},
	});
}

// ---------------------------------------------------------------------------
// Generate Payroll dialog — with preview
// ---------------------------------------------------------------------------
function open_generate_payroll_dialog(wrapper) {
	var company = wrapper.fc && wrapper.fc.company ? wrapper.fc.company.get_value() : null;

	if (!company) {
		frappe.msgprint(__("Please select a company first."));
		return;
	}

	var d = new frappe.ui.Dialog({
		title: __("Generate Payroll"),
		size: "large",
		fields: [
			{
				label: __("Start Date"), fieldname: "start_date", fieldtype: "Date", reqd: 1,
				onchange: function () {
					clear_preview(d);
					suggest_period_label(d);
				},
			},
			{ fieldtype: "Column Break" },
			{
				label: __("End Date"), fieldname: "end_date", fieldtype: "Date", reqd: 1,
				onchange: function () {
					clear_preview(d);
					suggest_period_label(d);
				},
			},
			{ fieldtype: "Section Break" },
			{
				label: __("Period"), fieldname: "period_label", fieldtype: "Data", reqd: 1,
				description: __("Editable. Used in the Payroll Entry description."),
			},
			{ fieldtype: "Column Break" },
			{
				label: __("Letter Case"), fieldname: "letter_case", fieldtype: "Select",
				options: "Uppercase\nLowercase\nTitle Case",
				default: "Uppercase",
			},
			{ fieldtype: "Section Break", label: __("Payroll Options") },
			{
				label: __("Payroll Type"), fieldname: "payroll_type", fieldtype: "Select",
				options: "Regular Salary\nHonorariums\nBoth",
				default: "Regular Salary", reqd: 1,
				onchange: function () { clear_preview(d); },
			},
			{ fieldtype: "Column Break" },
			{
				label: __("Frequency"), fieldname: "payroll_frequency", fieldtype: "Select",
				options: "Monthly\nFortnightly\nWeekly",
				default: "Monthly", reqd: 1,
			},
			{ fieldtype: "Column Break" },
			{
				label: __("Posting Date"), fieldname: "posting_date", fieldtype: "Date",
				reqd: 1, default: frappe.datetime.nowdate(),
			},
			{ fieldtype: "Section Break", label: __("Grouping") },
			{
				label: __("Group by Department"), fieldname: "group_by_department",
				fieldtype: "Check", default: 0,
				onchange: function () { clear_preview(d); },
			},
			{ fieldtype: "Column Break" },
			{
				label: __("Group by Branch"), fieldname: "group_by_branch",
				fieldtype: "Check", default: 0,
				onchange: function () { clear_preview(d); },
			},
			{ fieldtype: "Section Break", label: __("Payment Account") },
			{
				label: __("Payroll Payable Account"), fieldname: "payment_account",
				fieldtype: "Link", options: "Account", reqd: 1,
				get_query: function () {
					return { filters: { company: company, root_type: "Liability", is_group: 0 } };
				},
			},
			{ fieldtype: "Section Break", label: __("Preview") },
			{
				fieldname: "preview_html", fieldtype: "HTML",
				options: '<div class="text-muted" style="padding:8px 0;font-size:12px">' +
					__("Click «Preview» to see what will be generated.") + "</div>",
			},
		],
		primary_action_label: __("Generate"),
		primary_action: function (values) {
			var $area = d.fields_dict.preview_html.$wrapper;
			if (!$area.data("previewed")) {
				frappe.msgprint(__("Please run Preview first to verify the batches before generating."));
				return;
			}
			frappe.call({
				method: "bjobly.bjobly.utils.payroll_generator.generate_payroll",
				args: {
					company:             company,
					payroll_type:        values.payroll_type,
					start_date:          values.start_date,
					end_date:            values.end_date,
					payment_account:     values.payment_account,
					payroll_frequency:   values.payroll_frequency,
					period_label:        values.period_label,
					posting_date:        values.posting_date,
					letter_case:         values.letter_case || "Uppercase",
					group_by_department: values.group_by_department ? 1 : 0,
					group_by_branch:     values.group_by_branch     ? 1 : 0,
				},
				freeze: true,
				freeze_message: __("Queuing payroll generation…"),
				callback: function (r) {
					if (r.exc) return;
					d.hide();
					frappe.show_alert({
						message: __("Payroll generation started — Run ID: {0}", [r.message.run_id]),
						indicator: "green",
					});
				},
			});
		},
	});

	// Auto-populate payment_account from the company's default payroll payable account
	frappe.db.get_value("Company", { name: company }, "default_payroll_payable_account", function (r) {
		if (r && r.default_payroll_payable_account) {
			d.set_value("payment_account", r.default_payroll_payable_account);
		}
	});

	d.add_custom_action(__("Preview"), function () {
		var values = d.get_values();
		if (!values || !values.start_date || !values.end_date || !values.payroll_type) {
			frappe.msgprint(__("Please fill Start Date, End Date, and Payroll Type first."));
			return;
		}
		var $area = d.fields_dict.preview_html.$wrapper;
		$area.html('<div class="text-muted" style="padding:8px 0;font-size:12px">' + __("Loading…") + "</div>");
		$area.removeData("previewed");

		frappe.call({
			method: "bjobly.bjobly.utils.payroll_generator.preview_payroll",
			args: {
				company:             company,
				payroll_type:        values.payroll_type,
				start_date:          values.start_date,
				end_date:            values.end_date,
				period_label:        values.period_label,
				letter_case:         values.letter_case || "Uppercase",
				group_by_department: values.group_by_department ? 1 : 0,
				group_by_branch:     values.group_by_branch     ? 1 : 0,
			},
			callback: function (r) {
				var rows = r.message || [];
				if (!rows.length) {
					$area.html('<div class="text-muted" style="padding:8px 0;font-size:12px">' +
						__("No eligible employees found for the selected options.") + "</div>");
					return;
				}
				var currency = wrapper.currency || frappe.boot.sysdefaults.currency;
				var total_emp  = rows.reduce(function (s, r) { return s + r.employee_count; }, 0);
				var total_gross = rows.reduce(function (s, r) { return s + (r.gross_pay || 0); }, 0);
				var total_ded   = rows.reduce(function (s, r) { return s + (r.total_deduction || 0); }, 0);
				var total_net   = rows.reduce(function (s, r) { return s + (r.net_pay || 0); }, 0);
				var all_errors  = [];
				rows.forEach(function (r) { if (r.errors && r.errors.length) all_errors = all_errors.concat(r.errors); });

				var thead =
					'<thead><tr>' +
					'<th style="text-align:left">'   + __("Payroll Entry")  + "</th>" +
					'<th style="text-align:left">'   + __("Type")           + "</th>" +
					'<th style="text-align:center">' + __("Employees")      + "</th>" +
					'<th style="text-align:right">'  + __("Gross Pay")      + "</th>" +
					'<th style="text-align:right">'  + __("Deductions")     + "</th>" +
					'<th style="text-align:right">'  + __("Net Pay")        + "</th>" +
					"</tr></thead>";

				var tbody = "<tbody>" + rows.map(function (row) {
					var is_hon = row.payroll_type === "Honorariums";
					var badge  = is_hon
						? '<span class="indicator-pill orange no-indicator-dot">' + __(row.payroll_type) + "</span>"
						: '<span class="indicator-pill gray no-indicator-dot">'   + __(row.payroll_type) + "</span>";
					var err_icon = (row.errors && row.errors.length)
						? ' <span class="indicator-pill red no-indicator-dot" title="' +
							row.errors.length + ' ' + __("error(s)") + '">!</span>'
						: "";
					return "<tr>" +
						"<td><b>" + frappe.utils.escape_html(row.description) + "</b>" + err_icon + "</td>" +
						"<td>" + badge + "</td>" +
						'<td style="text-align:center">' + row.employee_count + "</td>" +
						'<td style="text-align:right">'  + fmt_c(row.gross_pay, currency)       + "</td>" +
						'<td style="text-align:right;color:var(--red)">'  + fmt_c(row.total_deduction, currency) + "</td>" +
						'<td style="text-align:right;font-weight:600">'   + fmt_c(row.net_pay, currency)         + "</td>" +
						"</tr>";
				}).join("") +
				'<tr style="background:var(--subtle-accent)">' +
					'<td colspan="2"><b>' + __("Total") + "</b></td>" +
					'<td style="text-align:center"><b>' + total_emp + "</b></td>" +
					'<td style="text-align:right"><b>'  + fmt_c(total_gross, currency) + "</b></td>" +
					'<td style="text-align:right;color:var(--red)"><b>' + fmt_c(total_ded, currency) + "</b></td>" +
					'<td style="text-align:right;font-weight:600"><b>'  + fmt_c(total_net,  currency) + "</b></td>" +
				"</tr></tbody>";

				var error_section = "";
				if (all_errors.length) {
					error_section =
						'<div class="alert" style="margin-top:8px;font-size:11px;background:var(--alert-bg-warning,#fff3cd);border:1px solid var(--yellow);border-radius:4px;padding:8px">' +
						'<b>' + __("{0} employee(s) could not be calculated:", [all_errors.length]) + "</b><ul style='margin:4px 0 0 16px;padding:0'>" +
						all_errors.map(function (e) {
							return "<li>" + frappe.utils.escape_html(e.employee) + " — " + frappe.utils.escape_html(e.error) + "</li>";
						}).join("") +
						"</ul></div>";
				}

				$area.html(
					'<div style="overflow-x:auto;margin-top:4px">' +
					'<table class="table table-bordered table-condensed" style="font-size:12px;margin:0">' +
					thead + tbody + "</table></div>" + error_section
				);
				$area.data("previewed", true);
			},
		});
	}, "btn-default");

	d.show();
}

function suggest_period_label(d) {
	var start = d.get_value("start_date");
	var end   = d.get_value("end_date");
	if (!start || !end) return;
	// Only overwrite if the user hasn't manually edited the field
	// (we detect this by comparing against the last auto-suggestion stored on d)
	var label = frappe.format(start, { fieldtype: "Date" }) + " al " + frappe.format(end, { fieldtype: "Date" });
	if (!d._period_label_edited || d._period_label_last === d.get_value("period_label")) {
		d.set_value("period_label", label);
		d._period_label_last = label;
		d._period_label_edited = false;
	}
}

function clear_preview(d) {
	if (!d || !d.fields_dict || !d.fields_dict.preview_html) return;
	var $area = d.fields_dict.preview_html.$wrapper;
	$area.html('<div class="text-muted" style="padding:8px 0;font-size:12px">' +
		__("Click «Preview» to see what will be generated.") + "</div>");
	$area.removeData("previewed");
}

// ---------------------------------------------------------------------------
// DOM bootstrap — injected directly so no bundle/template system is needed
// ---------------------------------------------------------------------------
function build_dashboard_html() {
	return $([
		'<div id="pd-filter-bar" class="pd-filter-bar"></div>',
		'<div class="pd-wrapper">',
		'<div id="payroll-summary" class="pd-stats-grid"></div>',
		'</div>',
		'<div id="payroll-list-wrapper"></div>',
	].join(""));
}

function inject_dashboard_styles() {
	if (document.getElementById("pd-styles")) return; // inject only once
	var el = document.createElement("style");
	el.id = "pd-styles";
	el.textContent = [
		/* Filter bar */
		".pd-filter-bar{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;padding:12px var(--padding-lg,24px) 16px;background:var(--card-bg);border-bottom:1px solid var(--border-color)}",
		".pd-fc-col{min-width:140px;flex:1;max-width:220px}",
		".pd-fc-col .control-label{font-size:11px;margin-bottom:2px}",
		".pd-fc-col .form-group{margin-bottom:0}",

		/* Icon-only action buttons */
		".pd-icon-btn{width:26px;height:26px;padding:0;display:inline-flex;align-items:center;justify-content:center}",
		".pd-icon-btn .icon{width:14px;height:14px}",

		/* Layout */
		".pd-wrapper{padding:var(--padding-lg,24px) var(--padding-lg,24px) 0;background:var(--bg-color)}",

		/* Stat grid — wraps native .widget.number-widget-box cards */
		".pd-stats-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}",
		".pd-stats-grid .pd-stat-total{border-left:3px solid var(--blue)}",
		/* Color overrides for the .number value inside the widget */
		".pd-stats-grid .number.pd-green{color:var(--green)}",
		".pd-stats-grid .number.pd-blue{color:var(--blue)}",

		/* Honorarium rows — amber tint on the native .list-row-container */
		".pd-hon-row{background:#fffbeb}.pd-hon-row:hover{background:#fef3c7}",
		".pd-hon-row .list-row{background:transparent}",
		"[data-theme=dark] .pd-hon-row{background:rgba(239,108,0,.08)}",
		"[data-theme=dark] .pd-hon-row:hover{background:rgba(239,108,0,.12)}",

		/* Avatar */
		".pd-avatar{width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;font-size:10px;font-weight:700;flex-shrink:0}",
		".pd-avatar-blue{background:var(--blue)}.pd-avatar-orange{background:var(--orange)}.pd-avatar-gray{background:var(--gray-400,#adb5bd)}",

		/* Employee cell */
		".pd-emp-cell{display:flex;align-items:center;gap:8px}",

		/* Right-aligned salary columns */
		".pd-col-right{text-align:right;font-variant-numeric:tabular-nums}",
		".pd-var-val{color:var(--blue);font-style:italic}",

		/* Dashed pill for Unassigned */
		".pd-badge-dashed{border-style:dashed!important;background:transparent!important}",

		/* Pagination — aligned with the list, not full wrapper width */
		"#payroll-list-wrapper .list-paging-area{margin-top:8px;padding:var(--padding-sm,8px) 0}",

		/* Empty / loading */
		".pd-empty-state{padding:60px;text-align:center;font-size:13px}",

		/* Responsive */
		"@media(max-width:1024px){.pd-stats-grid{grid-template-columns:repeat(2,1fr)}}",
		"@media(max-width:640px){.pd-stats-grid{grid-template-columns:1fr 1fr}.pd-wrapper{padding:16px}}",
	].join("\n");
	document.head.appendChild(el);
}
