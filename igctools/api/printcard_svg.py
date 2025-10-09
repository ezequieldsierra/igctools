# apps/igctools/igctools/api/printcard_svg.py
import frappe


def _pdf_file_bytes_from_file_url(file_url: str) -> bytes:
    """Carga bytes del File dado su file_url (soporta público/privado)."""
    if not file_url:
        return b""
    file_doc = frappe.get_doc("File", {"file_url": file_url})
    content = file_doc.get_content()
    return content or b""


def _pdf_first_page_to_svg(pdf_bytes: bytes, text_as_path: bool = True) -> str:
    """Convierte la primera página del PDF a SVG usando PyMuPDF."""
    if not pdf_bytes:
        raise ValueError("PDF vacío.")

    try:
        import fitz  # PyMuPDF
    except Exception:
        # No bloqueamos el guardado si falta la lib; deja rastro en Error Log.
        frappe.log_error("PyMuPDF no está instalado.", "IGCTools: auto SVG")
        return ""

    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
            if pdf.page_count < 1:
                raise ValueError("El PDF no tiene páginas.")
            page = pdf.load_page(0)
            svg = page.get_svg_image(text_as_path=bool(text_as_path))
            return svg or ""
    except Exception as e:
        # Logueamos y devolvemos vacío para no bloquear el guardado
        frappe.log_error(frappe.utils.cstr(e), "IGCTools: error convirtiendo PDF→SVG")
        return ""


def auto_svg_from_printcard(doc, method):
    """
    Hook before_save en Project:
    - Si 'printcard' tiene valor, toma PrintCard.archivo (PDF),
      convierte la pág. 1 a SVG y lo asigna a 'svg_arte' del Project.
    - No hace .save() aquí para evitar recursion/loops.
    - Si hay error, registra en Error Log pero no bloquea el guardado.
    """
    try:
        # Permite saltar el proceso con un flag temporal si algún flujo lo requiere
        if getattr(doc.flags, "skip_auto_svg", False):
            return

        printcard_name = (doc.get("printcard") or "").strip()
        if not printcard_name:
            return  # No hay PrintCard asignado, no hacemos nada

        # Carga el PrintCard vinculado
        pc = frappe.get_doc("PrintCard", printcard_name)
        file_url = (pc.get("archivo") or "").strip()
        if not file_url:
            # Si no hay PDF, puedes limpiar svg_arte si lo prefieres:
            # doc.set("svg_arte", "")
            return

        # (Opcional) evitar recalcular si ya hay SVG y no cambió el PrintCard
        # if doc.get("svg_arte") and not doc.is_new():
        #     old = frappe.db.get_value("Project", doc.name, ["printcard", "svg_arte"], as_dict=True)
        #     if old and old.printcard == printcard_name and old.svg_arte:
        #         return

        pdf_bytes = _pdf_file_bytes_from_file_url(file_url)
        svg = _pdf_first_page_to_svg(pdf_bytes, text_as_path=True)

        # Asigna el SVG al campo del Project (no guardamos aquí)
        if svg:
            doc.set("svg_arte", svg)
        else:
            # Si falló la conversión, decide si limpiar o dejar como está:
            # doc.set("svg_arte", "")
            pass

    except Exception as e:
        # No rompemos el guardado del Project: solo dejamos constancia en Error Log.
        frappe.log_error(frappe.utils.cstr(e), "IGCTools: auto_svg_from_printcard")
        # Si prefieres bloquear, reemplaza por: frappe.throw("Detalle del error…")


@frappe.whitelist()
def pymupdf_status():
    """Ping para confirmar que PyMuPDF está instalado en el server (endpoint de prueba)."""
    try:
        import fitz  # PyMuPDF
        return {"ok": True, "version": getattr(fitz, "__version__", None)}
    except Exception as e:
        return {"ok": False, "error": repr(e)}
