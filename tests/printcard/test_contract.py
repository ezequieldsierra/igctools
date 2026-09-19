import ast
import hashlib
import json
import zipfile
from unittest.mock import MagicMock, patch

import pytest
from conftest import LEGACY, NEW, ROOT, Bag, assign, frappe


def normalized(text):
	tree = ast.parse(
		text.replace(
			"from powerpro.controllers.printcard import (", "from igctools.printcard.helper import ("
		)
		.replace(
			"from powerpro.controllers.pdf_manager import pdf_manipulator as pdf_manager",
			"from igctools.printcard import pdf_manipulator as pdf_manager",
		)
		.replace(
			"from powerpro.controllers.pdf_manager import signature_helper as signature_helper",
			"from igctools.printcard import signature_helper as signature_helper",
		)
	)
	# Ignore docstring indentation and trailing line whitespace removed by pre-commit.
	for node in ast.walk(tree):
		if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
			if ast.get_docstring(node, clean=False) is not None:
				node.body.pop(0)
		if isinstance(node, ast.Constant) and isinstance(node.value, str) and "\n" in node.value:
			node.value = "\n".join(line.rstrip() for line in node.value.split("\n"))
	return ast.dump(tree, include_attributes=False)


@pytest.mark.parametrize("filename", list(NEW))
def test_migrated_runtime_has_no_unreviewed_behavior_changes(filename):
	manifest = json.loads((ROOT / "igctools/printcard/origin.json").read_text())
	with zipfile.ZipFile(ROOT / "tests/fixtures/printcard_powerpro.zip") as archive:
		old = archive.read(filename)
	assert hashlib.sha256(old).hexdigest() == manifest["files"][filename]["sha256"]
	assert normalized(old.decode()) == normalized((ROOT / "igctools/printcard" / filename).read_text())


def card(module, **changes):
	values = dict(
		name="ACME - [PC]A1.v1.2",
		doctype="PrintCard",
		cliente="ACME",
		codigo_arte="A1",
		version_arte_interna=1,
		version=2,
		version_arte_cliente="V2",
		estado="Pendiente",
		producto="SKU1",
		nombre_arte="Product A",
		archivo="/files/source.pdf",
		printcard_file="/files/card.pdf",
		usuarios_asignados=[],
		codigo="PC-A",
	)
	values.update(changes)
	doc = module.PrintCard(**values)
	arte = Bag(cambios=[], flags=Bag(), **{"versión_del_cliente": "V2"})
	arte.db_update = MagicMock()
	arte.notify_update = MagicMock()
	doc._arte = arte
	doc.get_doc_before_save = MagicMock(
		return_value=Bag(
			estado="Listo para Someter",
			producto="SKU1",
			archivo="/files/source.pdf",
			version_arte_cliente="V2",
			usuarios_asignados=[],
		)
	)
	doc.is_latest_version = MagicMock(return_value=True)
	doc.db_set = MagicMock()
	return doc, arte


@pytest.mark.parametrize(
	"module", [LEGACY["controller.py"], NEW["controller.py"]], ids=["powerpro", "igctools"]
)
@pytest.mark.parametrize(
	"previous,current,generated",
	[
		("Borrador", "Pendiente", True),
		("Listo para Someter", "Pendiente", True),
		("Pendiente", "Pendiente", False),
		("Pendiente", "Aprobado", False),
		("Pendiente", "Rechazado", False),
		("Aprobado", "Reemplazado", False),
	],
)
def test_submit_generates_once_only_on_original_transitions(module, previous, current, generated):
	doc, _ = card(module, estado=current)
	doc.get_doc_before_save.return_value.estado = previous
	frappe.db.get_value.return_value = "CODE-42"
	with patch.object(module, "generate_pdf_for_printcard", return_value="/files/generated.pdf") as generate:
		doc.update_arte_changes()
		assert generate.call_count == int(generated)
	assert doc.archivo == "/files/source.pdf"
	assert doc.printcard_file == ("/files/generated.pdf" if generated else "/files/card.pdf")
	assert doc.codigo == "CODE-42"


@pytest.mark.parametrize("module", [LEGACY["controller.py"], NEW["controller.py"]])
@pytest.mark.parametrize(
	"previous,current,expected",
	[
		("Pendiente", "Aprobado", 1),
		("Aprobado", "Aprobado", 0),
		("Pendiente", "Rechazado", 0),
		("Aprobado", "Reemplazado", 0),
	],
)
def test_approval_queues_signature_once(module, previous, current, expected):
	doc, _ = card(module, estado=current)
	doc.get_doc_before_save.return_value.estado = previous
	with patch.object(module, "sign_pdf_with_base64") as sign:
		doc.sign_pdf_if_approved()
		assert sign.call_count == expected


@pytest.mark.parametrize("module", [LEGACY["controller.py"], NEW["controller.py"]])
def test_admin_regeneration_and_permissions(module):
	doc, _ = card(module)
	with patch.object(module, "generate_pdf_for_printcard", return_value="/files/manual.pdf") as generate:
		result = doc.generate_printcard_pdf_on_demand()
		doc.db_set.assert_called_once_with("printcard_file", "/files/manual.pdf")
		assert result["printcard_file"] == "/files/manual.pdf"
		frappe.session.user = "client@example.test"
		frappe.get_roles.return_value = ["Cliente Aprobaciones"]
		with pytest.raises(PermissionError):
			doc.generate_printcard_pdf_on_demand()
		assert generate.call_count == 1


@pytest.mark.parametrize("module", [LEGACY["controller.py"], NEW["controller.py"]])
def test_versions_arte_and_replacement(module):
	doc, arte = card(module, estado="Aprobado")
	frappe.db.sql.return_value = [(2,)]
	doc.set_version()
	assert doc.version == 3
	frappe.get_all.return_value = ["older-one", "older-two"]
	doc.update_arte_status()
	assert arte.archivo_printcard_aprobado == "/files/source.pdf"
	assert arte.producto_aprobado == "SKU1"
	assert arte.ultima_version_aprobada == 1
	assert frappe.db.set_value.call_count == 2
	for call in frappe.db.set_value.call_args_list:
		assert call.args[2:] == ("estado", "Reemplazado")
	doc.producto, doc.archivo, doc.version_arte_cliente = "SKU2", "/files/new.pdf", "V3"
	doc.update_arte_fields()
	assert arte.producto == "SKU2"
	assert arte.archivo_actual == "/files/new.pdf"
	assert arte["versión_del_cliente"] == "V3"
	doc.save_arte()
	arte.db_update.assert_called_once()
	assert arte.flags.ignore_permissions is True


@pytest.mark.parametrize("module", [LEGACY["controller.py"], NEW["controller.py"]])
def test_assignment_diff_and_last_record_deletion(module):
	doc, arte = card(module, usuarios_asignados=[Bag(user="new@example.test")])
	doc.get_doc_before_save.return_value.usuarios_asignados = [Bag(user="old@example.test")]
	doc.check_for_changes_on_usuarios_asignados()
	assign._add.assert_called_once()
	assign.remove.assert_called_once_with("PrintCard", doc.name, "old@example.test", ignore_permissions=True)
	frappe.db.count.return_value = 1
	doc.revert_art_on_printcard_trash()
	assert arte.estado == "PrintCard por Crear"
	assert arte.archivo_printcard_aprobado is None
	assert arte.ultima_version_aprobada is None


@pytest.mark.parametrize("module", [LEGACY["controller.py"], NEW["controller.py"]])
def test_new_document_history_and_lifecycle_order(module):
	doc, arte = card(module, estado="Borrador")
	doc.update_change_log()
	assert len(arte.cambios) == 1
	assert arte.cambios[0].printcard == doc.name
	assert arte.cambios[0].archivo_link.endswith("/files/source.pdf")
	assert arte.archivo_actual == "/files/source.pdf"
	order = []
	for name in ("update_arte_status", "update_arte_fields", "update_arte_changes", "save_arte"):
		setattr(doc, name, lambda name=name: order.append(name))
	doc.before_save()
	assert order == ["update_arte_status", "update_arte_fields", "update_arte_changes", "save_arte"]


def test_permission_condition_remains_identical():
	for kind in ["Website User", "System User"]:
		frappe.db.get_value.return_value = kind
		old = LEGACY["permissions.py"].printcard_query_conditions("client@example.test")
		new = NEW["permissions.py"].printcard_query_conditions("client@example.test")
		assert old == new
		assert ("tabUsuario Aprobacion" in new) == (kind == "Website User")


def test_list_api_keeps_legacy_order_and_signature_job_is_after_commit():
	frappe.db.get_list.return_value = [
		Bag(name="P1", version_arte_interna=1, version=1),
		Bag(name="P2", version_arte_interna=2, version=1),
	]
	for modules in (LEGACY, NEW):
		assert [x.name for x in modules["client.py"].get_printcard_list("A1")] == ["P2", "P1"]
		modules["helper.py"].sign_pdf_with_base64("P1")
		assert frappe.enqueue.call_args.kwargs == {"printcard_id": "P1", "enqueue_after_commit": True}
	assert frappe.enqueue.call_args.args[0].__module__.startswith("igctools.")


def test_all_legacy_api_routes_are_direct_and_controller_is_independent():
	from igctools import hooks

	assert hooks.override_doctype_class["PrintCard"] == "igctools.printcard.controller.PrintCard"
	assert hooks.override_doctype_class["Job Card"] == "igctools.overrides.job_card.JobCard"
	assert len(hooks.override_whitelisted_methods) == 7
	for target in hooks.override_whitelisted_methods.values():
		assert target.startswith("igctools.printcard.")
	assert NEW["controller.py"].PrintCard.__bases__[0].__name__ == "Document"
	assert (
		hooks.doc_events["PrintCard"]["before_save"]
		== "igctools.api.printcard_svg.before_save_printcard_set_svg"
	)
	assert hooks.doc_events["Project"]["before_save"] == "igctools.api.printcard_svg.auto_svg_from_printcard"
