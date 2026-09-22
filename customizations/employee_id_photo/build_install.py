"""Build an auditable restricted-Python installer for the IGCARIBE System Console.

Usage: python build_install.py /absolute/path/install_employee_id_photo.txt
The generated installer contains no credentials and only changes this feature's records.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def field(name, label, kind="Data", **kwargs):
    return {"fieldname": name, "label": label, "fieldtype": kind, **kwargs}


def build():
    job = {
        "doctype": "DocType", "name": "Foto ID Laboral Version", "module": "IGCTools",
        "custom": 1, "autoname": "hash", "track_changes": 1, "allow_rename": 0,
        "title_field": "employee", "sort_field": "creation", "sort_order": "DESC",
        "fields": [
            field("employee", "Empleado", "Link", options="Employee", reqd=1, in_list_view=1),
            field("status", "Estado", "Select", options="En cola\nGenerando\nAjustando\nLista\nError", reqd=1, in_list_view=1),
            field("mode", "Modo", "Select", options="generate\nedit", reqd=1),
            field("adjustment", "Ajuste solicitado", "Small Text"),
            field("request_key", "Solicitud", unique=1, reqd=1),
            field("requested_by", "Solicitado por", "Link", options="User", reqd=1),
            field("source_snapshot", "Referencias originales", "Long Text", hidden=1),
            field("input_photo", "Foto de partida", "Attach Image"),
            field("raw_image", "Imagen generada", "Attach Image"),
            field("final_image", "Foto normalizada", "Attach Image"),
            field("normalization", "Control de formato", "Long Text", hidden=1),
            field("model", "Modelo de imágenes"),
            field("style_version", "Versión del estilo"),
            field("error_message", "Detalle", "Small Text"),
        ],
        # Other HR users use the API, which checks Employee read/write permissions
        # and their HR role. The audit table itself is administered by System Manager.
        "permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 0, "report": 1, "export": 0, "share": 0, "print": 0}],
    }
    employee = [
        field("custom_foto_id_tab", "Foto de ID Laboral", "Tab Break", insert_after="connections_tab"),
        field("custom_foto_id_panel", "Foto de ID Laboral", "HTML", insert_after="custom_foto_id_tab"),
        field("custom_foto_id_laboral", "Foto de ID Laboral", "Attach Image", insert_after="custom_foto_id_panel", read_only=1, hidden=1, no_copy=1),
        field("custom_foto_id_version", "Versión Foto ID", insert_after="custom_foto_id_laboral", read_only=1, hidden=1, no_copy=1),
    ]
    config = [
        field("custom_id_foto_seccion", "Foto de ID Laboral", "Section Break", insert_after="ultima_prueba"),
        field("custom_id_foto_habilitado", "Habilitar Foto de ID Laboral", "Check", insert_after="custom_id_foto_seccion", default="1"),
        field("custom_id_foto_modelo", "Modelo de imágenes para ID Laboral", "Select", insert_after="custom_id_foto_habilitado", options="gpt-image-2\ngpt-image-2-2026-04-21\ngpt-image-2.5-sunburst\ngpt-image-2.5-flare", default="gpt-image-2", description="Utiliza la Clave API del Servicio de esta configuración. Modelo independiente de la lectura de facturas."),
        field("custom_id_foto_color", "Color fijo del polo", insert_after="custom_id_foto_modelo", default="#16263F", read_only=1, description="Azul marino corporativo. Se normaliza automáticamente en el archivo final."),
    ]
    custom_fields = [{"doctype": "Custom Field", "dt": dt, **f} for dt, fields in [("Employee", employee), ("Configuracion Factura de Compra", config)] for f in fields]
    server = {"doctype": "Server Script", "name": "IGC - Employee - Foto ID Laboral API V1", "script_type": "API", "api_method": "igc_employee_id_photo", "allow_guest": 0, "disabled": 0, "script": (ROOT / "server.py").read_text()}
    client = {"doctype": "Client Script", "name": "IGC - Employee - Foto ID Laboral V1", "dt": "Employee", "view": "Form", "enabled": 1, "script": (ROOT / "client.js").read_text()}
    payload = {"job": job, "fields": custom_fields, "scripts": [server, client]}
    # Frappe sanitizes HTML-like text in console audit documents. JSON Unicode
    # escapes transport literal markup losslessly; decoded scripts are still
    # validated normally by their native Server Script / Client Script DocTypes.
    transport_json = json.dumps(payload, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    code = "payload = json.loads(" + repr(transport_json) + ")\n"
    code += """
if frappe.session.user != 'Administrator':
    roles = frappe.get_all('Has Role', filters={'parent':frappe.session.user,'parenttype':'User'}, pluck='role')
    if 'System Manager' not in roles:
        frappe.throw('Esta instalación requiere System Manager.', frappe.PermissionError)
if not frappe.db.exists('DocType', payload['job']['name']):
    frappe.get_doc(payload['job']).insert()
else:
    existing = frappe.get_doc('DocType', payload['job']['name'])
    if not existing.custom or not any(f.fieldname == 'style_version' for f in existing.fields):
        frappe.throw('Existe un DocType con el mismo nombre que no pertenece a esta función.')
for spec in payload['fields']:
    name = spec['dt'] + '-' + spec['fieldname']
    if not frappe.db.exists('Custom Field', name):
        frappe.get_doc(spec).insert()
    else:
        current = frappe.get_doc('Custom Field', name)
        if current.fieldtype != spec['fieldtype']:
            frappe.throw('Tipo de campo incompatible: ' + name)
for spec in payload['scripts']:
    if frappe.db.exists(spec['doctype'], spec['name']):
        current = frappe.get_doc(spec['doctype'], spec['name'])
        if 'IGC Employee ID Photo' not in current.script:
            frappe.throw('Existe un script ajeno con el mismo nombre: ' + spec['name'])
        if current.script != spec['script']:
            backup = frappe.get_doc({'doctype':spec['doctype'],'name':'BACKUP - Foto ID ' + frappe.utils.now_datetime().strftime('%Y%m%d%H%M%S%f'),'script':current.script})
            if spec['doctype'] == 'Client Script':
                backup.dt = 'Employee'
                backup.view = 'Form'
                backup.enabled = 0
            else:
                backup.script_type = 'API'
                backup.disabled = 1
                backup.api_method = 'igc_id_backup_' + frappe.utils.now_datetime().strftime('%Y%m%d%H%M%S%f')
            backup.insert()
        for key in spec:
            if key not in ['doctype','name']:
                current.set(key,spec[key])
        current.save()
    else:
        frappe.get_doc(spec).insert()
config = frappe.get_doc('Configuracion Factura de Compra')
if not config.get('custom_id_foto_modelo'):
    config.custom_id_foto_modelo = 'gpt-image-2'
config.custom_id_foto_habilitado = 1
config.custom_id_foto_color = '#16263F'
config.save()
print({'installed':'IGC Employee ID Photo 1.0.0','employee_tab':'Foto de ID Laboral','image_model':config.custom_id_foto_modelo,'color':'#16263F','credential':'existing server-side config; never exported'})
"""
    return code


if __name__ == "__main__":
    Path(sys.argv[1]).write_text(build())
