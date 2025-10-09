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
        if getattr(doc.flags, "skip_auto_svg", False):
            return

        printcard_name = (doc.get("printcard") or "").strip()
        if not printcard_name:
            return  # No hay PrintCard asignado

        # Carga el PrintCard vinculado
        pc = frappe.get_doc("PrintCard", printcard_name)
        file_url = (pc.get("archivo") or "").strip()
        if not file_url:
            # Si no hay PDF, opcionalmente podrías limpiar svg_arte:
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
            # doc.set("svg_arte", "")
            pass

    except Exception as e:
        # No rompemos el guardado del Project: solo dejamos constancia en Error Log.
        frappe.log_error(frappe.utils.cstr(e), "IGCTools: auto_svg_from_printcard")
        # Si prefieres bloquear, reemplaza por: frappe.throw("Detalle del error…")


# ---------- UTILIDADES PARA LOTE (Projects existentes) ----------

def _update_one_project_svg(proj_name: str, force: bool = False) -> dict:
    """Genera y guarda svg_arte para un Project. Si force=False y ya hay svg_arte, lo deja tal cual."""
    proj = frappe.get_doc("Project", proj_name)
    pc_name = (proj.get("printcard") or "").strip()
    if not pc_name:
        return {"project": proj_name, "skipped": True, "reason": "no_printcard"}

    if (proj.get("svg_arte") or "") and not force:
        return {"project": proj_name, "skipped": True, "reason": "has_svg"}

    pc = frappe.get_doc("PrintCard", pc_name)
    file_url = (pc.get("archivo") or "").strip()
    if not file_url:
        return {"project": proj_name, "skipped": True, "reason": "no_pdf"}

    pdf_bytes = _pdf_file_bytes_from_file_url(file_url)
    svg = _pdf_first_page_to_svg(pdf_bytes, text_as_path=True)
    if not svg:
        return {"project": proj_name, "skipped": True, "reason": "svg_empty_or_error"}

    # Evitar disparar hooks recursivamente
    proj.flags.skip_auto_svg = True
    proj.set("svg_arte", svg)
    proj.save(ignore_permissions=True)
    return {"project": proj_name, "updated": True, "bytes": len(svg.encode("utf-8"))}


def _rebuild_job(batch_size: int = 200, force: bool = False, only_empty: bool = True):
    """Job en background: recorre Projects con printcard y llena svg_arte."""
    filters = [["printcard", "is", "set"]]
    if only_empty and not force:
        filters.append(["svg_arte", "=", ""])

    total = frappe.db.count("Project", filters=filters)
    done = 0
    start = 0

    while True:
        names = frappe.get_all(
            "Project",
            filters=filters,
            fields=["name"],
            start=start,
            page_length=batch_size,
            order_by="modified asc",
        )
        if not names:
            break

        for row in names:
            try:
                _update_one_project_svg(row.name, force=force)
            except Exception as e:
                frappe.log_error(frappe.utils.cstr(e), f"IGCTools: batch SVG failed for {row.name}")
            done += 1

        frappe.db.commit()
        start += batch_size

    return {"ok": True, "total": total, "processed": done, "force": force, "only_empty": only_empty}


@frappe.whitelist()
def rebuild_project_svgs(batch_size: int = 200, force: int = 0, only_empty: int = 1, enqueue: int = 1):
    """
    Reconstruye svg_arte de Project en lote.
    - batch_size: tamaño de página (default 200)
    - force: 1 = recalcula aunque ya exista svg_arte
    - only_empty: 1 = procesa solo donde svg_arte está vacío
    - enqueue: 1 = encola como background job; 0 = ejecuta inline (cuidado con timeout)
    """
    # Seguridad mínima: necesita permiso de escritura en Project
    if not frappe.has_permission(doctype="Project", ptype="write"):
        frappe.throw("Permisos insuficientes")

    force_b = bool(int(force))
    only_empty_b = bool(int(only_empty))
    enqueue_b = bool(int(enqueue))

    if enqueue_b:
        job = frappe.enqueue(
            "igctools.api.printcard_svg._rebuild_job",
            queue="long",
            job_name="IGCTools: Rebuild Project SVGs",
            timeout=60 * 60,
            batch_size=int(batch_size),
            force=force_b,
            only_empty=only_empty_b,
        )
        return {"enqueued": True, "job_name": job.get_id()}
    else:
        return _rebuild_job(batch_size=int(batch_size), force=force_b, only_empty=only_empty_b)


# ---------- PING / DIAGNÓSTICO ----------

@frappe.whitelist()
def pymupdf_status():
    """Ping para confirmar que PyMuPDF está instalado en el server (endpoint de prueba)."""
    try:
        import fitz  # PyMuPDF
        return {"ok": True, "version": getattr(fitz, "__version__", None)}
    except Exception as e:
        return {"ok": False, "error": repr(e)}
