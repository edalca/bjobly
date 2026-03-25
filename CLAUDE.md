# bjobly

App de Frappe que extiende **hrms** (Frappe HR) sin modificar su repositorio original. Desarrollada para **Bjobly**, empresa guatemalteca.

## Estructura

```
bjobly/
├── hooks.py                        # Registra overrides de clases y JS
├── setup.py                        # Crea/elimina custom fields en install/uninstall
├── install.py / uninstall.py       # Entry points
├── overrides/
│   ├── payroll_entry.py            # BjoblyPayrollEntry (extiende PayrollEntry)
│   └── salary_slip.py              # BjoblySalarySlip (extiende SalarySlip)
├── public/js/
│   ├── payroll_entry.js            # UI del formulario Payroll Entry
│   └── payroll_entry_list.js       # Vista de lista
└── bjobly/report/
    └── department_wise_payroll_summary/
```

## Flujo de nómina (Payroll Entry)

1. **Crear** el Payroll Entry y guardar (borrador)
2. **Obtener empleados** → botón "Calcular Recibos de Salario"
3. **Calcular** → previsualiza salarios en la grilla (en RAM, sin guardar slips)
4. **Crear Recibos de Salario** → submete el Payroll Entry, lo que crea Y valida (somete) los salary slips automáticamente. Requiere cálculo previo (`salary_slips_calculated = 1`)
5. **Crear Asiento Contable** → botón aparece después de que los slips están validados. Crea el Journal Entry por separado (NO automático como en HRMS original)
6. **Cancelar** → cancela el JV primero, luego los GL entries, luego los salary slips (cancela, NO elimina), y deja el Payroll Entry en estado Cancelado para que el usuario lo pueda eliminar si quiere

## Diferencias clave vs HRMS original

| Aspecto | HRMS | bjobly |
|---------|------|--------|
| Crear salary slips | `on_submit` los crea en borrador | `on_submit` los crea Y somete |
| Journal Entry | Se crea automático al someter slips | Botón separado "Crear Asiento Contable" |
| Días de trabajo | Días calendario reales | Fijos: 30/15/7/1 según frecuencia |
| Cancelar slips | Cancela y **elimina** | Solo **cancela** (preserva para GL) |
| Filtro empleados | Filtra por `payroll_payable_account` | Sin ese filtro |
| Cálculo de impuesto | Estándar | Progresivo por tramos |

## Cálculo de impuesto (ISR) - Detalles de Implementación

El cálculo de ISR en **bjobly** sobrescribe la lógica estándar de HRMS para soportar tramos progresivos específicos de Guatemala.

### Mejoras Recientes (Marzo 2026)

1.  **Corrección de Lógica por Tramos**: Se corrigió un error donde `amount_previusly_taxed` no se actualizaba correctamente, lo que causaba que niveles superiores de ingreso no restaran la base ya tributada en tramos inferiores (previniendo doble tributación).
2.  **Solución de Alcance (Scoping)**: Se sobrescribió completamente `calculate_variable_tax` en `BjoblySalarySlip`. Esto asegura que las llamadas internas de HRMS utilicen la función `calculate_tax_by_tax_slab` definida localmente en el override, en lugar de la versión original del módulo hrms.
3.  **Bug de Condiciones Vacías**: Se reemplazó `str(slab.condition)` por `cstr(slab.condition)`. En Frappe, una condición vacía es `None`; usar `str()` la convertía en el string `"None"`, lo que provocaba que el sistema intentara evaluarla, fallara y saltara el tramo de impuesto (dejando el ISR en 0 para todos).
4.  **Soporte de Tax Relief**: Se integró la validación de `tax_relief_limit` para respetar el mínimo exento definido en el DocType *Income Tax Slab*.

## Mecanismo de override

- **Clases**: `override_doctype_class` en `hooks.py`
- **Custom fields**: `setup.py` crea campos en install y los elimina en uninstall
- **JS**: `doctype_js` y `doctype_list_js` en `hooks.py`

## Regla de días fijos ("Choluteca Rule")

En `BjoblySalarySlip.get_working_days_details()`:
- Mensual → 30 días
- Bimensual → 60 días
- Quincenal → 15 días
- Semanal → 7 días
- Diario → 1 día

`payment_days = días_fijos - (ausencias + licencias_sin_pago)`

## Comandos Útiles (Docker)

Para ejecutar pruebas de lógica dentro del contenedor:
```bash
docker exec bjobly-v16-web-1 bench --site bjobly.localhost execute "from bjobly.overrides.salary_slip import calculate_tax_by_tax_slab; print(calculate_tax_by_tax_slab(500000, frappe.get_doc('Income Tax Slab', 'I.S.R. 2026'), {}, {}))"
```

## Gitignore

Ver [.gitignore](.gitignore) — ignora `__pycache__`, `*.pyc`, `.env`, `node_modules`, `.vscode`, `.idea`, `.aider*`, etc.
