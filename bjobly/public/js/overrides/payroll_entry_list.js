frappe.listview_settings['Payroll Entry'] = {
    // 1. Aseguramos que estos campos se traigan de la base de datos aunque no estén en columnas
    add_fields: ["gross_pay", "total_deduction", "net_pay", "status"],

    // 2. Definimos indicadores de color para el estado de la nómina
    get_indicator: function(doc) {
        if (doc.status === "Draft") {
            return [__("Draft"), "red", "status,=,Draft"];
        } else if (doc.status === "Submitted") {
            return [__("Submitted"), "blue", "status,=,Submitted"];
        } else if (doc.status === "Cancelled") {
            return [__("Cancelled"), "darkgrey", "status,=,Cancelled"];
        } else {
            return [__(doc.status), "green", "status,=," + doc.status]; // Paid, etc.
        }
    },

    // 3. Formateo de columnas (Opcional: puedes forzar negritas o colores)
    formatters: {
        net_pay(val, df, doc) {
            return `<span style="font-weight: bold; color: #2ecc71;">${frappe.format(val, df, doc)}</span>`;
        },
        total_deduction(val, df, doc) {
            return `<span style="color: #e74c3c;">${frappe.format(val, df, doc)}</span>`;
        }
    },

    // 4. Se ejecuta al cargar la lista
    onload: function(listview) {
        // Aquí puedes añadir botones personalizados en la barra superior
        // listview.page.add_inner_button(__('Reporte Rápido'), () => { ... });
    }
};