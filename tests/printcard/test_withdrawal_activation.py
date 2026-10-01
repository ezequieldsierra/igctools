"""Only activate the exact preconfigured action once the compatible controller is installed."""

import hashlib
import sys
import types
from unittest.mock import MagicMock

import pytest
from conftest import Bag, frappe

from igctools.patches import enable_printcard_withdrawal as patch


@pytest.fixture
def setup_activation(monkeypatch):
	module = types.ModuleType("frappe.model.base_document")
	controller = types.SimpleNamespace(__module__="igctools.printcard.controller")
	module.get_controller = lambda name: controller
	monkeypatch.setitem(sys.modules, module.__name__, module)
	docs = {
		("Server Script", "guard"): Bag(script="guard", disabled=1, save=MagicMock()),
		("Server Script", "api"): Bag(script="api", disabled=1, save=MagicMock()),
		("Client Script", "client"): Bag(script="client", enabled=0, save=MagicMock()),
	}
	monkeypatch.setattr(
		patch,
		"EXPECTED",
		[(dt, name, hashlib.sha256(doc.script.encode()).hexdigest()) for (dt, name), doc in docs.items()],
	)
	frappe.db.exists.return_value = True
	frappe.get_doc.side_effect = lambda dt, name: docs[(dt, name)]
	return docs, controller


def test_enable_exact_preconfigured_action(setup_activation):
	docs, _ = setup_activation
	patch.execute()
	client = docs[("Client Script", "client")]
	assert client.enabled == 1
	assert docs[("Server Script", "guard")].disabled == 0
	assert docs[("Server Script", "api")].disabled == 0
	client.save.assert_called_once()
	patch.execute()
	client.save.assert_called_once()


@pytest.mark.parametrize("change", ["edited", "partially_active", "other_controller"])
def test_preserve_site_customizations_and_partial_activation(setup_activation, change):
	docs, controller = setup_activation
	if change == "edited":
		docs[("Server Script", "guard")].script = "changed"
	elif change == "partially_active":
		docs[("Server Script", "api")].disabled = 0
	else:
		controller.__module__ = "powerpro.controllers.printcard"
	patch.execute()
	client = docs[("Client Script", "client")]
	assert client.enabled == 0
	client.save.assert_not_called()
