from pathlib import Path

import pytest
from conftest import NEW, frappe, utils

from igctools.printcard.file_safety import confined_file_path


@pytest.mark.parametrize("private", [False, True])
def test_existing_public_and_private_file_paths_are_preserved(tmp_path, private):
	root = tmp_path / ("private" if private else "public") / "files"
	root.mkdir(parents=True)
	utils.get_files_path = lambda is_private=False: str(root)
	url = ("/private" if private else "") + "/files/Arte #1 con espacios.pdf"
	assert NEW["helper.py"].get_file_path(url) == str(root / "Arte #1 con espacios.pdf")


@pytest.mark.parametrize("suffix", ["../outside.pdf", "sub/../../outside.pdf"])
def test_file_traversal_is_rejected(tmp_path, suffix):
	root = tmp_path / "files"
	root.mkdir()
	with pytest.raises(PermissionError):
		confined_file_path(str(root / suffix), str(root))


def test_symlink_to_outside_files_directory_is_rejected(tmp_path):
	root = tmp_path / "files"
	root.mkdir()
	(root / "linked").symlink_to(tmp_path, target_is_directory=True)
	with pytest.raises(PermissionError):
		confined_file_path(str(root / "linked" / "outside.pdf"), str(root))


def test_art_identifiers_are_passed_as_sql_parameters():
	doc = NEW["controller.py"].PrintCard(codigo_arte="ART' OR 1=1 --", version_arte_interna=3)
	frappe.db.sql.return_value = [(2,)]
	doc.set_version()
	query, values = frappe.db.sql.call_args.args
	assert doc.codigo_arte not in query
	assert values == (doc.codigo_arte, 3)
	assert doc.version == 3
