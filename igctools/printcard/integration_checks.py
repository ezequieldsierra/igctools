"""Real Frappe/MariaDB contract checks on a disposable, empty test site only.

Creates representative metadata; does not claim to replace a production clone
or browser acceptance tests. Never run on a site containing PrintCards/Arte.
"""

import base64
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
import frappe
from PIL import Image, ImageDraw

from igctools.printcard import helper, migration

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def field(name, kind="Data", **kwargs):
	return dict(fieldname=name, label=name, fieldtype=kind, **kwargs)


def doctype(name, fields, child=False):
	doc = frappe.get_doc(
		doctype="DocType",
		name=name,
		module="IGCTools",
		custom=1,
		istable=int(child),
		fields=fields,
		permissions=[] if child else [dict(role="All", read=1, write=1, create=1, delete=1)],
	)
	doc.insert()
	return doc


def assert_disposable_site():
	if not frappe.conf.get("allow_tests") or frappe.local.site != "test_site":
		raise RuntimeError(
			"This suite only runs on a disposable site named test_site with allow_tests enabled."
		)


def install_test_schema():
	assert_disposable_site()
	for name in [
		"PrintCard",
		"Arte",
		"PrintCard Canvas",
		"Usuario Aprobacion",
		"Producto del Cliente",
		"Project",
		"PreProIGC Settings",
		"IGC PC Test Change",
	]:
		if frappe.db.exists("DocType", name):
			raise RuntimeError(f"Refusing to replace existing metadata: {name}")
	doctype("Usuario Aprobacion", [field("user", "Link", options="User")], child=True)
	doctype(
		"IGC PC Test Change",
		[
			field(n)
			for n in ["arte", "printcard", "tipo_de_cambio", "version_del_cliente", "notas", "archivo_link"]
		]
		+ [field("numero_version", "Int"), field("fecha", "Date")],
		child=True,
	)
	doctype(
		"Arte",
		[
			field(n)
			for n in [
				"estado",
				"producto",
				"archivo_actual",
				"archivo_printcard_aprobado",
				"producto_aprobado",
				"estado_últ_aprobada",
				"versión_del_cliente",
				"versión_interna_del_aprobada",
			]
		]
		+ [
			field("ultima_version_aprobada", "Data"),
			field("version_actual", "Int"),
			field("cambios", "Table", options="IGC PC Test Change"),
		],
	)
	doctype("Producto del Cliente", [field("nombre_arte"), field("codigo")])
	doctype(
		"PrintCard Canvas",
		[
			field("disabled", "Check"),
			field("orientation", "Select", options="Portrait\nLandscape"),
			field("codigo_html", "Code"),
			field("codigo_css", "Code"),
			field("date_font_color"),
		]
		+ [
			field(n, "Float")
			for n in [
				"ancho_pdf",
				"alto_pdf",
				"ancho_specs",
				"margin_left",
				"margin_right",
				"margin_top",
				"margin_bottom",
				"signature_x_position",
				"signature_y_position",
				"signature_width",
				"signature_height",
				"date_x_position",
				"date_y_position",
				"font_size",
			]
		],
	)
	settings = doctype("PreProIGC Settings", [field("minimum_canvas_margin", "Float")])
	frappe.db.set_value("DocType", settings.name, "issingle", 1)
	frappe.clear_cache(doctype=settings.name)
	frappe.db.set_single_value(settings.name, "minimum_canvas_margin", 0.1)
	source = json.loads((FIXTURES / "printcard_schema.json").read_text())
	fields = []
	for original in source["fields"]:
		if original["fieldtype"] in ["Section Break", "Column Break", "Tab Break", "HTML", "Button"]:
			continue
		entry = {
			k: v
			for k, v in original.items()
			if k in ["fieldname", "label", "fieldtype", "options", "default"]
		}
		if entry["fieldtype"] == "Link":
			entry["fieldtype"] = "Data"
			entry.pop("options", None)
		if entry["fieldname"] == "estado":
			# The public controller also handles this intermediate state.
			entry["options"] += "\nListo para Someter"
		fields.append(entry)
	fields.append(field("svg", "Code"))
	pc = doctype("PrintCard", fields)
	# Native DocType routing is required for override_doctype_class in Frappe 15.
	frappe.db.set_value("DocType", pc.name, {"custom": 0, "autoname": source["autoname"]})
	frappe.clear_cache(doctype="PrintCard")
	frappe.controllers.setdefault(frappe.local.site, {}).pop("PrintCard", None)
	doctype("Project", [field("printcard"), field("svg_arte", "Code")])
	# Disposable schema must exist before the lifecycle transaction.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit
	frappe.clear_cache()


class TestPrintCardIntegration(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		install_test_schema()
		frappe.set_user("Administrator")  # nosemgrep: frappe-setuser

	def test_lifecycle_uses_existing_fields_and_routes(self):
		from frappe.handler import execute_cmd
		from frappe.model.base_document import get_controller

		from igctools.api import printcard_svg

		self.assertEqual(get_controller("PrintCard").__module__, "igctools.printcard.controller")
		migration.verify_activation()
		arte = frappe.get_doc(
			doctype="Arte",
			estado="Borrador",
			version_actual=1,
			cambios=[dict(numero_version=1, tipo_de_cambio="Pendiente de Crear PrintCard")],
		).insert()
		product = frappe.get_doc(
			doctype="Producto del Cliente", nombre_arte="TEST ART", codigo="CODE-1"
		).insert()
		frappe.get_doc(
			doctype="PrintCard Canvas",
			orientation="Landscape",
			ancho_pdf=7,
			alto_pdf=6,
			ancho_specs=1,
			margin_left=0.2,
			margin_right=0.2,
			margin_top=0.2,
			margin_bottom=0.2,
			codigo_html="<h3>PRINTCARD {{ doc.cliente }}</h3>",
			codigo_css="body {font-size:10pt;}",
			signature_x_position=0.1,
			signature_y_position=0.8,
			signature_width=1,
			signature_height=0.3,
			date_x_position=0,
			date_y_position=1,
			date_font_color="#000000",
			font_size=8,
		).insert()
		original = fitz.open()
		for label in ["FIRST ART PAGE", "SECOND ART PAGE"]:
			page = original.new_page(width=216, height=144)
			page.insert_text((20, 30), label)
		payload = original.tobytes()
		original.close()
		file = frappe.get_doc(
			doctype="File", file_name="printcard-test-source.pdf", content=payload, is_private=1
		).insert()
		pc = frappe.get_doc(
			doctype="PrintCard",
			cliente="TEST CUSTOMER",
			producto="SKU1",
			nombre_arte=product.nombre_arte,
			codigo_arte=arte.name,
			version_arte_interna=1,
			version_arte_cliente="V1",
			estado="Borrador",
			archivo=file.file_url,
			usuarios_asignados=[],
		).insert()
		self.assertEqual(pc.version, 1)
		self.assertEqual(pc.archivo, file.file_url)
		file.db_set(
			{"attached_to_doctype": "PrintCard", "attached_to_name": pc.name, "attached_to_field": "archivo"}
		)
		self.assertEqual(printcard_svg._pdf_file_bytes_from_printcard(pc), payload)
		pc.estado = "Listo para Someter"
		pc.save()
		pc.estado = "Pendiente"
		pc.save()
		pc.reload()
		self.assertTrue(pc.printcard_file)
		with self.subTest("SVG preview on save"):
			self.assertTrue(pc.svg, "Existing SVG hook did not produce a preview; see captured errors.")
			self.assertIn("<svg", pc.svg)
		self.assertEqual(pc.codigo, "CODE-1")
		self.assertEqual(Path(helper.get_file_path(file.file_url)).read_bytes(), payload)
		with fitz.open(helper.get_file_path(pc.printcard_file)) as pdf:
			self.assertEqual(len(pdf), 2)
			self.assertIn("FIRST ART PAGE", pdf[0].get_text())
		project = frappe.get_doc(doctype="Project", printcard=pc.name).insert()
		with self.subTest("Project SVG copy"):
			self.assertTrue(project.svg_arte)
			self.assertEqual(project.svg_arte, pc.svg)
		manual = pc.generate_printcard_pdf_on_demand()
		self.assertEqual(manual["printcard_file"], pc.printcard_file)
		frappe.local.form_dict = frappe._dict(printcard=pc.name)
		frappe.local.response = frappe._dict()
		execute_cmd("igcaribe.client.generate_pdf_for_printcard")
		self.assertEqual(frappe.local.response.type, "pdf")
		with fitz.open(stream=frappe.local.response.filecontent, filetype="pdf") as pdf:
			self.assertEqual(len(pdf), 2)
		image = Image.new("RGB", (180, 50), "white")
		ImageDraw.Draw(image).line([(5, 40), (40, 5), (70, 35), (170, 10)], fill="black", width=3)
		buffer = io.BytesIO()
		image.save(buffer, format="PNG")
		signature = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
		pc.firma_cliente = signature
		pc.aprobado = 1
		pc.estado = "Aprobado"
		with patch.object(frappe, "enqueue") as enqueue:
			pc.save()
			self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])
			self.assertEqual(enqueue.call_args.args[0].__module__, "igctools.printcard.helper")
		pc.reload()
		self.assertEqual(pc.estado, "Aprobado")
		helper._sign_pdf_with_base64(pc.name)
		pc.reload()
		self.assertTrue(pc.printcard_file_signed)
		with fitz.open(helper.get_file_path(pc.printcard_file_signed)) as signed:
			self.assertEqual(len(signed), 2)
			for page in signed:
				self.assertTrue(page.get_images())
		self.assertEqual(pc.archivo, file.file_url)
		self.assertEqual(frappe.get_doc("Arte", arte.name).archivo_printcard_aprobado, file.file_url)
		self.assertEqual(Path(helper.get_file_path(file.file_url)).read_bytes(), payload)
		# Read filtering remains assigned-user based for Website Users.
		user = frappe.get_doc(
			doctype="User",
			email="printcard-qa@example.invalid",
			first_name="QA",
			user_type="Website User",
			send_welcome_email=0,
		).insert()
		frappe.set_user(user.name)  # nosemgrep: frappe-setuser -- synthetic user in guarded disposable site
		self.assertEqual(frappe.get_list("PrintCard", pluck="name"), [])
		with self.assertRaises(frappe.PermissionError):
			pc.generate_printcard_pdf_on_demand()
		frappe.set_user("Administrator")  # nosemgrep: frappe-setuser -- restore test runner identity
		pc.append("usuarios_asignados", {"user": user.name})
		pc.save()
		frappe.set_user(user.name)  # nosemgrep: frappe-setuser -- synthetic user in guarded disposable site
		self.assertIn(pc.name, frappe.get_list("PrintCard", pluck="name"))
		frappe.set_user("Administrator")  # nosemgrep: frappe-setuser -- restore test runner identity


def run():
	assert_disposable_site()
	# Nothing can send mail or execute a background task outside this test process.
	with (
		patch.object(frappe, "sendmail"),
		patch.object(frappe, "enqueue"),
		patch.object(frappe, "log_error", wraps=frappe.log_error) as errors,
	):
		result = unittest.TextTestRunner(verbosity=2).run(
			unittest.defaultTestLoader.loadTestsFromTestCase(TestPrintCardIntegration)
		)
		if not result.wasSuccessful():
			for error in errors.call_args_list:
				print("Captured disposable-site error:", error)
	frappe.set_user("Administrator")  # nosemgrep: frappe-setuser -- restore test runner identity
	frappe.db.rollback()
	if not result.wasSuccessful():
		raise RuntimeError("PrintCard integration failed; do not activate the migration.")
