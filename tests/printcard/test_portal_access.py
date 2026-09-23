import runpy
import sqlite3
from unittest.mock import patch

import pytest
from conftest import ROOT, Bag, frappe

from igctools.printcard import portal_access


@pytest.mark.parametrize("state", ["Borrador", "Listo para Someter", "Reemplazado", "", None])
def test_customer_cannot_read_internal_documents_even_when_assigned(state):
	frappe.db.get_value.return_value = "Website User"
	frappe.db.exists.return_value = True
	assert portal_access.has_permission(Bag(name="PC1", estado=state), "client@example.test") is False
	frappe.db.exists.assert_not_called()


@pytest.mark.parametrize("state", portal_access.VISIBLE_STATES)
@pytest.mark.parametrize("assigned", [True, False])
def test_published_documents_still_require_assignment_and_native_permissions(state, assigned):
	frappe.db.get_value.return_value = "Website User"
	frappe.db.exists.return_value = assigned
	result = portal_access.has_permission(Bag(name="PC1", estado=state), "client@example.test")
	assert result is (None if assigned else False)
	frappe.db.exists.assert_called_once_with(
		"Usuario Aprobacion", {"parent": "PC1", "parenttype": "PrintCard", "user": "client@example.test"}
	)


@pytest.mark.parametrize("state", ["Borrador", "Listo para Someter", *portal_access.VISIBLE_STATES])
def test_internal_staff_keep_native_permissions(state):
	frappe.db.get_value.return_value = "System User"
	assert portal_access.has_permission(Bag(name="PC1", estado=state), "staff@example.test") is None
	assert portal_access.query_conditions("staff@example.test") == ""
	frappe.db.exists.assert_not_called()


def test_guests_are_denied():
	assert portal_access.has_permission(Bag(name="PC1", estado="Pendiente"), "Guest") is False
	assert portal_access.query_conditions("Guest") == "1=0"


def test_query_filters_real_rows_and_uses_session_user_by_default():
	frappe.session.user = "client@example.test"
	frappe.db.get_value.return_value = "Website User"
	with sqlite3.connect(":memory:") as db:
		db.execute("CREATE TABLE tabPrintCard (estado TEXT)")
		db.executemany(
			"INSERT INTO tabPrintCard VALUES (?)",
			[
				(state,)
				for state in [
					"Borrador",
					"Listo para Someter",
					"Reemplazado",
					"",
					None,
					*portal_access.VISIBLE_STATES,
				]
			],
		)
		rows = db.execute(
			"SELECT estado FROM tabPrintCard WHERE " + portal_access.query_conditions()
		).fetchall()
	assert [row[0] for row in rows] == list(portal_access.VISIBLE_STATES)
	frappe.db.get_value.assert_called_once_with("User", "client@example.test", "user_type")


def test_status_filter_is_combined_with_existing_approval_user_filter():
	with patch("igctools.printcard.compatibility.source_status", return_value={"compatible": True}):
		hooks = runpy.run_path(str(ROOT / "igctools/hooks.py"))
	assert hooks["permission_query_conditions"]["PrintCard"] == [
		"igctools.printcard.permissions.printcard_query_conditions",
		"igctools.printcard.portal_access.query_conditions",
	]
	assert hooks["has_permission"]["PrintCard"] == "igctools.printcard.portal_access.has_permission"
