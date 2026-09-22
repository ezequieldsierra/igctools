"""Local tests for boundary-sensitive helpers; production integration is tested via the form."""
import ast
import base64
import json
import random
import types
import unittest
from pathlib import Path


class ServerHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = ast.parse(Path(__file__).with_name("server.py").read_text())
        definitions = [node for node in source.body if isinstance(node, (ast.FunctionDef, ast.Assign))]
        definitions = definitions[:next(i for i, node in enumerate(definitions) if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "action")]
        cls.env = {"json": json}
        exec(compile(ast.Module(body=definitions, type_ignores=[]), "server-helpers", "exec"), cls.env)

    def test_binary_references_survive_encoding(self):
        rng = random.Random(101)
        for size in [0, 1, 2, 3, 4, 512, 32769]:
            content = bytes(rng.randrange(256) for _ in range(size))
            self.assertEqual(self.env["encode64"](content), base64.b64encode(content).decode())

    def test_external_and_non_file_references_fail_before_reading(self):
        def fail(message, *args):
            raise ValueError(message)
        self.env["frappe"] = types.SimpleNamespace(throw=fail)
        for url in ["https://example.org/person.jpg", "http://localhost/private.jpg", "../../site_config.json", "/api/method/login"]:
            with self.assertRaises(ValueError):
                self.env["local_file"](url, types.SimpleNamespace(name="Test"))

    def test_non_hr_employee_cannot_generate(self):
        self.env["frappe"] = types.SimpleNamespace(session=types.SimpleNamespace(user="employee@example.org"), get_all=lambda *args, **kwargs: ["Employee", "Employee Self Service"])
        self.assertFalse(self.env["may_generate"]())
        self.env["frappe"].get_all = lambda *args, **kwargs: ["Encargado Gestión Humana"]
        self.assertTrue(self.env["may_generate"]())

    def test_edit_selects_employee_attachment_among_duplicate_file_urls(self):
        employee = types.SimpleNamespace(name="Employee Test")
        def lookup(doctype, filters, field):
            return "employee-file" if filters.get("attached_to_doctype") == "Employee" and filters.get("attached_to_name") == employee.name else "version-file"
        files = {
            "employee-file": types.SimpleNamespace(attached_to_doctype="Employee", attached_to_name=employee.name, file_name="portrait.png", get_content=lambda: b"png-test"),
            "version-file": types.SimpleNamespace(attached_to_doctype="Foto ID Laboral Version", attached_to_name="version"),
        }
        def fail(message, *args):
            raise ValueError(message)
        self.env["frappe"] = types.SimpleNamespace(db=types.SimpleNamespace(get_value=lookup), get_doc=lambda doctype, name: files[name], throw=fail, PermissionError=PermissionError)
        self.assertEqual(self.env["local_file"]("/private/files/portrait.png", employee, True), {"image_url":"data:image/png;base64,cG5nLXRlc3Q="})

    def test_edit_keeps_original_reference_requirement(self):
        prompt = self.env["build_prompt"]("Soltar el pelo", True)
        self.assertIn("CURRENT corporate portrait", prompt)
        self.assertIn("original identity references", prompt)
        self.assertIn("#16263F", prompt)
        self.assertIn("uppermost button undone", prompt)


if __name__ == "__main__":
    unittest.main()
