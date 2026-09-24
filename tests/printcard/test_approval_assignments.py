"""Customer approver selection must not require access before submission."""

from unittest.mock import MagicMock

import pytest
from conftest import Bag, assign, frappe

from igctools.printcard.controller import PrintCard
from igctools.printcard.portal_access import VISIBLE_STATES, has_permission

CLIENT = "client@example.test"
STAFF = "staff@example.test"


@pytest.fixture(autouse=True)
def reset_assignment_permission_check():
	yield
	assign._add.side_effect = None


def card(state, previous_state, users, previous_users):
	doc = PrintCard(
		name="PC1",
		doctype="PrintCard",
		estado=state,
		usuarios_asignados=[Bag(user=user) for user in users],
	)
	doc.get_doc_before_save = MagicMock(
		return_value=Bag(
			estado=previous_state,
			usuarios_asignados=[Bag(user=user) for user in previous_users],
		)
	)
	frappe.get_all.return_value = [CLIENT] if CLIENT in users + previous_users else []
	frappe.db.get_value.side_effect = lambda doctype, user, field: (
		"Website User" if user == CLIENT else "System User"
	)
	frappe.db.exists.side_effect = lambda doctype, filters: filters["user"] in users

	def assign_with_native_permission_check(args, ignore_permissions=False):
		for user in args["assign_to"]:
			if has_permission(doc, user) is False:
				raise PermissionError("Document sharing disabled: recipient cannot read")

	assign._add.side_effect = assign_with_native_permission_check
	return doc


@pytest.mark.parametrize("state", ["Borrador", "Listo para Someter", "Reemplazado"])
def test_selecting_customer_before_submission_keeps_selection_without_assignment(state):
	doc = card(state, state, [CLIENT], [])
	doc.check_for_changes_on_usuarios_asignados()
	assert [row.user for row in doc.usuarios_asignados] == [CLIENT]
	assert has_permission(doc, CLIENT) is False
	assign._add.assert_not_called()
	assign.remove.assert_not_called()


@pytest.mark.parametrize("previous", ["Borrador", "Listo para Someter"])
def test_submission_assigns_preselected_customer_once(previous):
	doc = card("Pendiente", previous, [CLIENT], [CLIENT])
	doc.check_for_changes_on_usuarios_asignados()
	assign._add.assert_called_once()
	assert assign._add.call_args.args[0]["assign_to"] == [CLIENT]
	assert has_permission(doc, CLIENT) is None
	assign.remove.assert_not_called()
	assign._add.reset_mock()
	doc.get_doc_before_save.return_value.estado = "Pendiente"
	doc.check_for_changes_on_usuarios_asignados()
	assign._add.assert_not_called()


@pytest.mark.parametrize("state", VISIBLE_STATES)
def test_new_customer_on_visible_card_is_assigned_normally(state):
	doc = card(state, state, [CLIENT], [])
	doc.check_for_changes_on_usuarios_asignados()
	assign._add.assert_called_once()
	assign.remove.assert_not_called()


@pytest.mark.parametrize("state", ["Borrador", "Listo para Someter"])
def test_staff_assignments_work_while_customer_assignments_are_deferred(state):
	doc = card(state, state, [CLIENT, STAFF], [])
	doc.check_for_changes_on_usuarios_asignados()
	assign._add.assert_called_once()
	assert assign._add.call_args.args[0]["assign_to"] == [STAFF]


@pytest.mark.parametrize("state", ["Borrador", "Listo para Someter", "Reemplazado"])
def test_return_to_internal_state_removes_customer_assignment_only(state):
	doc = card(state, "Pendiente", [CLIENT, STAFF], [CLIENT, STAFF])
	doc.check_for_changes_on_usuarios_asignados()
	assign._add.assert_not_called()
	assign.remove.assert_called_once_with("PrintCard", "PC1", CLIENT, ignore_permissions=True)
	assert [row.user for row in doc.usuarios_asignados] == [CLIENT, STAFF]


@pytest.mark.parametrize("state", ["Borrador", "Listo para Someter", "Pendiente"])
def test_removed_customer_cleans_up_any_previous_assignment(state):
	doc = card(state, state, [], [CLIENT])
	doc.check_for_changes_on_usuarios_asignados()
	assign._add.assert_not_called()
	assign.remove.assert_called_once_with("PrintCard", "PC1", CLIENT, ignore_permissions=True)


@pytest.mark.parametrize("state", ["Aprobado", "Rechazado"])
def test_approval_outcome_does_not_reassign_unchanged_customer(state):
	doc = card(state, "Pendiente", [CLIENT], [CLIENT])
	doc.check_for_changes_on_usuarios_asignados()
	assign._add.assert_not_called()
	assign.remove.assert_not_called()


def test_first_insert_keeps_existing_behavior():
	doc = card("Borrador", "Borrador", [CLIENT], [])
	doc.get_doc_before_save.return_value = None
	doc.check_for_changes_on_usuarios_asignados()
	assign._add.assert_not_called()
	assign.remove.assert_not_called()
