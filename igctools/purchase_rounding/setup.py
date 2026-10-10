"""Idempotent schema installation. Policies remain disabled until configured."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from igctools.purchase_rounding.policy import METHODS, PREFIX, PURCHASE_DOCTYPES, TAX_MODES


def install():
	if "erpnext" not in frappe.get_installed_apps():
		return
	fields = []

	def add(name, label, fieldtype, **kwargs):
		fields.append({"fieldname": PREFIX + name, "label": label, "fieldtype": fieldtype, **kwargs})

	add(
		"rounding_section",
		"Redondeo en compras",
		"Section Break",
		insert_after="number_format",
		collapsible=1,
	)
	add(
		"rounding_enabled",
		"Activar politica de redondeo en compras",
		"Check",
		default="0",
		insert_after=PREFIX + "rounding_section",
		description="Aplica al recalcular borradores y documentos nuevos. Los documentos confirmados conservan sus valores.",
	)
	previous = PREFIX + "rounding_enabled"
	for part, label, default in (
		("rate", "Decimales del precio unitario", "4"),
		("amount", "Decimales del importe por linea", "2"),
		("tax", "Decimales del impuesto", "2"),
		("total", "Decimales de los totales", "2"),
	):
		add(
			part + "_precision",
			label,
			"Select",
			options="\n".join(str(i) for i in range(10)),
			default=default,
			insert_after=previous,
		)
		previous = PREFIX + part + "_precision"
	add(
		"rounding_method",
		"Metodo de redondeo",
		"Select",
		options="\n".join(METHODS),
		default="Sistema",
		insert_after=previous,
		description="Commercial Rounding redondea los empates alejandose de cero: 2.345 a 2.35 con dos decimales.",
	)
	add(
		"tax_rounding",
		"Calcular impuestos",
		"Select",
		options="\n".join(TAX_MODES),
		default="Por subtotal",
		insert_after=PREFIX + "rounding_method",
		description="Por linea redondea cada impuesto antes de sumarlo. Por subtotal suma las bases de cada tasa y redondea el impuesto acumulado.",
	)
	custom_fields = {
		"Currency": fields,
		"Supplier": [
			{
				"fieldname": PREFIX + "tax_rounding",
				"label": "Redondeo de impuestos en compras",
				"fieldtype": "Select",
				"options": "Heredar\n" + "\n".join(TAX_MODES),
				"default": "Heredar",
				"insert_after": "default_currency",
				"description": "Heredar utiliza la politica de la moneda. Solo aplica si esa politica esta activada.",
			}
		],
	}
	for doctype in PURCHASE_DOCTYPES:
		custom_fields[doctype] = [
			{
				"fieldname": "custom_igc_use_native_rounding",
				"label": "Usar calculo original de redondeo",
				"fieldtype": "Check",
				"default": "0",
				"insert_after": "currency",
				"description": "Excepcion para este documento: usa las precisiones y el redondeo generales del sistema.",
			},
			{
				"fieldname": "custom_igc_rounding_policy",
				"label": "Politica de redondeo aplicada",
				"fieldtype": "Small Text",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"insert_after": "custom_igc_use_native_rounding",
			},
		]
	create_custom_fields(custom_fields, update=True)
