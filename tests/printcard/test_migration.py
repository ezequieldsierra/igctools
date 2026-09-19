import importlib.machinery
import json
import runpy
import sys
import types
import zipfile
from unittest.mock import MagicMock, patch

import pytest
from conftest import ROOT, Bag, ValidationError, frappe

from igctools.printcard import compatibility, migration


def compatible_site():
	frappe.db.exists.return_value = True
	frappe.get_installed_apps.return_value = ["frappe", "powerpro", "igctools"]
	frappe.get_meta.return_value.get_field.side_effect = lambda name: Bag(
		fieldtype=migration.REQUIRED_FIELDS[name]
	)


@pytest.fixture(autouse=True)
def source_fixture():
	with patch.object(
		migration, "source_status", return_value={"available": True, "compatible": True, "files": []}
	):
		yield


@pytest.fixture
def runtime(monkeypatch):
	base = types.ModuleType("frappe.model.base_document")
	legacy = type("PrintCard", (), {"__module__": "powerpro.controllers.printcard.printcard"})
	base.get_controller = MagicMock(return_value=legacy)
	monkeypatch.setitem(sys.modules, base.__name__, base)
	monkeypatch.setattr(frappe, "override_whitelisted_method", lambda name: name, raising=False)
	monkeypatch.setattr(frappe, "get_hooks", lambda key: {}, raising=False)
	return base


def test_sites_without_printcard_are_not_blocked():
	frappe.db.exists.return_value = False
	with patch.object(migration, "source_status") as source:
		migration.assert_source_compatibility()
		source.assert_not_called()


def test_matching_installed_source_is_accepted():
	compatible_site()
	migration.assert_source_compatibility()


@pytest.mark.parametrize("missing", [False, True])
def test_missing_or_changed_source_preserves_legacy_engine_without_blocking_migration(missing, runtime):
	compatible_site()
	with patch.object(
		migration,
		"source_status",
		return_value={
			"available": not missing,
			"compatible": False,
			"files": [{"path": "controller.py", "matches": False}],
		},
	):
		migration.assert_source_compatibility()
		migration.verify_activation()
		result = migration.status()
		assert result["controller"].startswith("powerpro.")
		assert result["activation_blocked"] is True
		assert result["layers_enabled"] is False
		assert result["legacy_mode"] == "all_pdfs"
		frappe.get_meta.assert_not_called()


@pytest.mark.parametrize("stale", ["controller", "route", "permissions"])
def test_mismatched_source_cannot_keep_any_new_runtime_hook(stale, runtime, monkeypatch):
	compatible_site()
	if stale == "controller":
		runtime.get_controller.return_value = type(
			"PrintCard", (), {"__module__": "igctools.printcard.controller"}
		)
	elif stale == "route":
		monkeypatch.setattr(
			frappe,
			"override_whitelisted_method",
			lambda name: "igctools.printcard.helper.generate_pdf_for_printcard",
		)
	else:
		monkeypatch.setattr(
			frappe,
			"get_hooks",
			lambda key: {"PrintCard": ["igctools.printcard.permissions.printcard_query_conditions"]},
		)
	with patch.object(migration, "source_status", return_value={"compatible": False}):
		with pytest.raises(ValidationError, match="reiniciar procesos"):
			migration.assert_source_compatibility()


def test_unknown_source_omits_all_three_overrides_and_preserves_other_hooks():
	with patch.object(compatibility, "source_status", return_value={"compatible": False}):
		hooks = runpy.run_path(str(ROOT / "igctools/hooks.py"))
	assert hooks["override_doctype_class"] == {"Job Card": "igctools.overrides.job_card.JobCard"}
	assert hooks["override_whitelisted_methods"] == {}
	assert hooks["permission_query_conditions"] == {}
	assert (
		hooks["doc_events"]["PrintCard"]["before_save"]
		== "igctools.api.printcard_svg.before_save_printcard_set_svg"
	)
	assert hooks["before_request"] == ["igctools.mcp_auth.before_request"]


def test_schema_ownership_is_not_silently_removed():
	compatible_site()
	frappe.get_installed_apps.return_value = ["frappe", "igctools"]
	with pytest.raises(ValidationError, match="conservar PowerPro"):
		migration.assert_source_compatibility()


def test_incompatible_existing_field_blocks_migration():
	compatible_site()
	frappe.get_meta.return_value.get_field.side_effect = lambda name: Bag(fieldtype="Data")
	with pytest.raises(ValidationError, match="archivo"):
		migration.assert_source_compatibility()


def test_provenance_hashes_detect_actual_installed_file_change(tmp_path):
	manifest = json.loads((ROOT / "igctools/printcard/origin.json").read_text())
	with zipfile.ZipFile(ROOT / "tests/fixtures/printcard_powerpro.zip") as archive:
		for filename, entry in manifest["files"].items():
			path = tmp_path / entry["source"]
			path.parent.mkdir(parents=True, exist_ok=True)
			path.write_bytes(archive.read(filename))
	spec = importlib.machinery.ModuleSpec("powerpro", loader=None, is_package=True)
	spec.submodule_search_locations = [str(tmp_path / "powerpro")]
	with patch.object(compatibility.importlib.util, "find_spec", return_value=spec):
		assert compatibility.source_status()["compatible"]
		assert compatibility.runtime_hooks()[0]["PrintCard"] == compatibility.CONTROLLER
		# Reproduce the three filenames from the actual failed migration.
		for filename in ["controller.py", "helper.py", "permissions.py"]:
			changed = tmp_path / manifest["files"][filename]["source"]
			changed.write_text(changed.read_text() + "\n# different installed source\n")
		report = compatibility.source_status()
		assert not report["compatible"]
		assert sum(not row["matches"] for row in report["files"]) == 3
		assert all(row["actual_sha256"] and row["expected_sha256"] for row in report["files"])
		assert compatibility.runtime_hooks() == ({}, {}, {})
