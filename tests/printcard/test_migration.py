import importlib.machinery
import json
import zipfile
from unittest.mock import patch

import pytest
from conftest import ROOT, Bag, ValidationError, frappe

from igctools.printcard import migration


def compatible_site():
	frappe.db.exists.return_value = True
	frappe.get_installed_apps.return_value = ["frappe", "powerpro", "igctools"]
	frappe.get_meta.return_value.get_field.side_effect = lambda name: Bag(
		fieldtype=migration.REQUIRED_FIELDS[name]
	)


def test_sites_without_printcard_are_not_blocked():
	frappe.db.exists.return_value = False
	with patch.object(migration, "source_status") as source:
		migration.assert_source_compatibility()
		source.assert_not_called()


def test_matching_installed_source_is_accepted():
	compatible_site()
	with patch.object(migration, "source_status", return_value={"available": True, "files": []}):
		migration.assert_source_compatibility()


@pytest.mark.parametrize("missing", [False, True])
def test_missing_or_changed_powerpro_source_blocks_migration(missing):
	compatible_site()
	with patch.object(
		migration,
		"source_status",
		return_value={"available": not missing, "files": [{"path": "controller.py", "matches": False}]},
	):
		with pytest.raises(ValidationError, match="versión auditada"):
			migration.assert_source_compatibility()


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
	with patch.object(migration.importlib.util, "find_spec", return_value=spec):
		assert all(row["matches"] for row in migration.source_status()["files"])
		path.write_text("# unexpected installed change\n")
		assert sum(not row["matches"] for row in migration.source_status()["files"]) == 1
