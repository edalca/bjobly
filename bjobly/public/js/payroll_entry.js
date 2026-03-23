// Copyright (c) 2026, Bjobly and contributors

var in_progress = false;

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

        // Real-time listeners
        frappe.realtime.on("completed_salary_slip_creation", () => frm.reload_doc());
        frappe.realtime.on("completed_salary_slip_submission", () => frm.reload_doc());
        frappe.realtime.on("bjobly_payroll_cancelled", (data) => {
            if (data && data.name === frm.doc.name) frm.reload_doc();
        });
    },

    refresh: function (frm) {
        // 1. Stop any previous polling cycle
        frm.events._stop_polling(frm);

        // 2. Currency & Exchange Rate Logic
        frm.trigger('toggle_exchange_rate');

        // 3. Button Management
        // Clear any buttons added by HRMS's refresh handler (runs before this one)
        if (frm.doc.docstatus === 0 && !frm.is_new()) {
            frm.clear_custom_buttons();
            frm.page.clear_primary_action();

            if (frm.doc.status === "Queued") {
                frm.page.set_indicator(__("Processing..."), "orange");
                frm.events._start_polling(frm);
            } else {
                const has_employees = !!(frm.doc.employees || []).length;

                // Always visible — allows refreshing the employee list
                frm.add_custom_button(__("Get Employees"), () => frm.events.get_employee_details(frm))
                    .toggleClass("btn-primary", !has_employees);

                if (has_employees) {
                    // Always allow recalculating until slips are created (on submit)
                    frm.add_custom_button(__("Payroll Data Calculation"), () => frm.trigger("run_calculation")).addClass("btn-secondary");
                    if (frm.doc.salary_slips_calculated) {
                        frm.add_custom_button(__("Create Salary Slips"), () => frm.savesubmit()).addClass("btn-primary");
                    }
                }
            }
        }

        // 4. Render Component Tables in Grid
        frm.trigger("render_all_html_tables");

        if (frm.doc.docstatus == 1) {
            frm.clear_custom_buttons();

            if (frm.doc.status === "Queued") {
                frm.page.set_indicator(__("Processing..."), "orange");
                frm.events._start_polling(frm);
            } else if (cint(frm.doc.salary_slips_submitted)) {
                const show_correct_button = () => {
                    frm.clear_custom_buttons();
                    if (!cint(frm.doc.completed_journal_entry_creation)) {
                        frm.events._set_jv_button(frm);
                    } else if (!cint(frm.doc.completed_bank_entry)) {
                        frm.events._set_bank_entry_button(frm);
                    }
                };
                show_correct_button();
                // HRMS adds "Make Bank Entry" async (~300ms); clear + re-add after it resolves
                setTimeout(show_correct_button, 1000);
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
                callback: function() {
                    frm.reload_doc();
                    frappe.show_alert({ message: __("Journal Entry created"), indicator: "green" });
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

    _start_polling: function (frm) {
        frm._bjobly_poller = setInterval(() => {
            frappe.db.get_value("Payroll Entry", frm.doc.name, ["status", "docstatus"]).then((r) => {
                if (!r.message) return;
                const changed_status = r.message.status !== "Queued";
                const cancelled = cint(r.message.docstatus) === 2;
                if (changed_status || cancelled) {
                    frm.events._stop_polling(frm);
                    frm.reload_doc();
                }
            });
        }, 3000);
    },

    _stop_polling: function (frm) {
        if (frm._bjobly_poller) {
            clearInterval(frm._bjobly_poller);
            frm._bjobly_poller = null;
        }
    },

    // --- CURRENCY TRIGGERS ---
    currency: function(frm) { frm.trigger('toggle_exchange_rate'); },
    company: function(frm) { frm.trigger('toggle_exchange_rate'); },

    toggle_exchange_rate: function(frm) {
        if (frm.doc.currency && frm.doc.company) {
            frappe.call({
                method: "frappe.client.get_value",
                args: {
                    doctype: "Company",
                    filters: { name: frm.doc.company },
                    fieldname: "default_currency"
                },
                callback: function(r) {
                    if (r.message && r.message.default_currency) {
                        const is_same = frm.doc.currency === r.message.default_currency;
                        frm.toggle_display('exchange_rate', !is_same);
                        if (is_same) {
                            frm.set_value('exchange_rate', 1.0);
                        }
                    }
                }
            });
        }
    },

    // --- CALCULATION LOGIC ---
    run_calculation: function(frm) {
        frappe.call({
            doc: frm.doc,
            method: "calculate_salary_slips", // Python method in your override
            freeze: true,
            freeze_message: __("Calculating payroll ..."),
            callback: function() {
                frm.reload_doc();
                frappe.show_alert({message: __("Salaries calculated successfully"), indicator: 'green'});
            }
        });
    },

    // --- HTML GRID RENDERING ---
    render_all_html_tables: function(frm) {
        if (frm.doc.employees && frm.doc.employees.length) {
            frm.doc.employees.forEach((employee) => {
                // Leemos la "memoria" que guardamos en el Python
                if (employee.earnings_json && employee.deductions_json) {
                    const earnings = JSON.parse(employee.earnings_json);
                    const deductions = JSON.parse(employee.deductions_json);

                    // Inyectamos el HTML usando set_df_property en la propiedad 'options'
                    // Argumentos: fieldname del child table, propiedad, valor, docname, fieldname del grid, name de la fila
                    frm.set_df_property("employees", "options", 
                        render_component_table(earnings, frm.doc.currency, "Earnings"), 
                        frm.doc.name, "earnings_html", employee.name);
                    
                    frm.set_df_property("employees", "options", 
                        render_component_table(deductions, frm.doc.currency, "Deductions"), 
                        frm.doc.name, "deductions_html", employee.name);
                } else {
                    // Si no hay datos, limpiamos los campos HTML
                    frm.set_df_property("employees", "options", "", frm.doc.name, "earnings_html", employee.name);
                    frm.set_df_property("employees", "options", "", frm.doc.name, "deductions_html", employee.name);
                }
            });
            // Refrescamos el campo de la tabla para que Frappe dibuje los cambios
            frm.refresh_field("employees");
        }
    },

    get_employee_details: function (frm) {
        return frappe.call({
            doc: frm.doc,
            method: "fill_employee_details",
            freeze: true,
            freeze_message: __("Fetching Employees..."),
        }).then(() => {
            frm.refresh();
        });
    },

    payroll_payable_account_filters: function (frm) {
        frm.set_query("payroll_payable_account", () => ({
            filters: { company: frm.doc.company, root_type: "Liability", is_group: 0 }
        }));
    }
});

// --- HELPER FUNCTION: RENDER COMPONENT TABLE ---
const render_component_table = function (components, currency, title = "") {
    if (!components || !components.length) return `<div class="text-muted" style="padding:10px;">${__("No data")}</div>`;

    let html = `
    <div class="grid-field" style="padding: 5px;">
        <label class="control-label" style="font-weight: bold; color: #545e66; font-size: 11px;">${__(title)}</label>
        <div class="form-grid-container" style="border: 1px solid #d1d8dd; border-radius: 4px; background: white;">
            <table class="table table-condensed" style="margin-bottom: 0; font-size: 11px;">
                <thead>
                    <tr style="background: #f8f9fa;">
                        <th>${__("Component")}</th>
                        <th class="text-right">${__("Amount")}</th>
                    </tr>
                </thead>
                <tbody>
    `;
    
    let total = 0;
    components.forEach(comp => {
        total += flt(comp.amount);
        html += `
            <tr>
                <td>${comp.salary_component}</td>
                <td class="text-right" style="font-family: monospace;">${format_currency(comp.amount, currency)}</td>
            </tr>
        `;
    });

    html += `
                </tbody>
                <tfoot>
                    <tr style="font-weight: bold; background: #f8f9fa;">
                        <td>${__("Total")}</td>
                        <td class="text-right">${format_currency(total, currency)}</td>
                    </tr>
                </tfoot>
            </table>
        </div>
    </div>`;

    return html;
};