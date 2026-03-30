frappe.ui.form.on("Salary Structure", {
    setup: function(frm) {
        frm.set_query("salary_component", "employer_contributions", function() {
            return {
                filters: {
                    "custom_is_employer_contribution": 1
                }
            };
        });
    }
});