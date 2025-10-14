# apps/igctools/igctools/api/printcard_svg.py
import frappe
import re
from xml.etree import ElementTree as ET

# =======================
# Modo de generación
# =======================
#   "RASTER_WRAPPER"  -> Renderiza PDF->PNG (o JPEG) y genera un SVG mínimo con <image href="file_url">
#   "VECTOR_SIMPLIFIED" -> Convierte a SVG y elimina images/masks/clipPaths/filters/patterns + minificado
#   "VECTOR_RAW" -> SVG crudo de PyMuPDF (puede generar millones de líneas). NO recomendado.
MODE = "RASTER_WRAPPER"

# --- Config RASTER_WRAPPER ---
RASTER_DPI = 180          # 150–200 suele ser perfecto para fichas técnicas
RASTER_FORMAT = "png"     # "png" o "jpeg"
JPEG_QUALITY = 85         # si usas jpeg
RASTER_PRIVATE = 0        # 0 = público, 1 = privado (ajusta según tu uso)
RASTER_FILE_PREFIX = "printcard_preview"

# --- Config VECTOR_SIMPLIFIED ---
TEXT_AS_PATH = False          # Mantener texto como texto reduce bastante
SVG_DECIMAL_PRECISION = 2     # Redondeo de decimales
COMPRESS_WHITESPACE = True
REMOVE_METADATA_TAGS = True
VECTOR_ONLY = True            # elimina images/masks/clipPaths/filters/patterns/defs y atributos relacionados

# =======================
# Utilidades
# =======================
def _pdf_file_bytes_from_file_url(file_url: str) -> bytes:
    if not file_url:
        return b""
    file_doc = frappe.get_doc("File", {"file_url": file_url})
    return file_doc.get_content() or b""

def _strip_metadata(s: str) -> str:
    if not REMOVE_METADATA_TAGS:
        return s
    s = re.sub(r"<!--.*?-->", "", s, flags=re.DOTALL)
    s = re.sub(r"<metadata[^>]*>.*?</metadata>", "", s, flags=re.DOTALL|re.IGNORECASE)
    s = re.sub(r"<desc[^>]*>.*?</desc>", "", s, flags=re.DOTALL|re.IGNORECASE)
    s = re.sub(r"<title[^>]*>.*?</title>", "", s, flags=re.DOTALL|re.IGNORECASE)
    return s

def _minify_numbers(s: str) -> str:
    prec = max(0, int(SVG_DECIMAL_PRECISION))
    def _round_num(m):
        try:
            num = float(m.group(0))
            out = f"{num:.{prec}f}"
            out = re.sub(r"(?<=\d)0+$", "", out)
            out = re.sub(r"\.$", "", out)
            return out
        except Exception:
            return m.group(0)
    return re.sub(r"-?\d+\.\d+", _round_num, s)

def _compress_ws(s: str) -> str:
    if not COMPRESS_WHITESPACE:
        return s
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r">\s+<", "><", s)
    return s.strip()

def _svg_vector_slim(svg_text: str) -> str:
    if not svg_text or not VECTOR_ONLY:
        return svg_text
    try:
        NS_SVG = "http://www.w3.org/2000/svg"
        NS_XLINK = "http://www.w3.org/1999/xlink"

        if "xmlns=" not in svg_text:
            svg_text = svg_text.replace("<svg ", f'<svg xmlns="{NS_SVG}" ', 1)
        if "xmlns:xlink" not in svg_text:
            svg_text = svg_text.replace("<svg ", f'<svg xmlns:xlink="{NS_XLINK}" ', 1)

        root = ET.fromstring(svg_text)
        rm_tags = {
            f"{{{NS_SVG}}}image",
            f"{{{NS_SVG}}}mask",
            f"{{{NS_SVG}}}clipPath",
            f"{{{NS_SVG}}}filter",
            f"{{{NS_SVG}}}pattern",
        }
        rm_attrs = {"clip-path", "mask", "filter"}

        def walk_remove(parent):
            to_delete = []
            for elem in list(parent):
                if elem.tag in rm_tags:
                    to_delete.append(elem); continue
                walk_remove(elem)
            for e in to_delete:
                parent.remove(e)
        walk_remove(root)

        def walk_attrs(elem):
            for a in list(elem.attrib.keys()):
                if a in rm_attrs:
                    del elem.attrib[a]
                if ("href" in a or a.endswith("}href")) and "data:image" in str(elem.attrib.get(a, "")):
                    del elem.attrib[a]
            for ch in list(elem):
                walk_attrs(ch)
        walk_attrs(root)

        svg_out = ET.tostring(root, encoding="unicode")
        return svg_out
    except Exception:
        return svg_text

def _minify_svg(svg_text: str) -> str:
    if not svg_text:
        return svg_text
    s = _strip_metadata(svg_text)
    s = _minify_numbers(s)
    s = re.sub(r"\s*([;,:])\s*", r"\1", s)
    s = _compress_ws(s)
    return s

# =======================
# Generadores
# =======================
def _pdf_first_page_to_svg_vector(pdf_bytes: bytes) -> str:
    try:
        import fitz
    except Exception:
        frappe.log_error("PyMuPDF no está instalado.", "IGCTools: SVG vector")
        return ""
    if not pdf_bytes:
        return ""
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
            if pdf.page_count < 1:
                return ""
            page = pdf.load_page(0)
            svg = page.get_svg_image(text_as_path=bool(TEXT_AS_PATH))
            if not svg:
                return ""
            svg = _svg_vector_slim(svg)
            svg = _minify_svg(svg)
            return svg
    except Exception as e:
        frappe.log_error(frappe.utils.cstr(e), "IGCTools: error PDF→SVG vector")
        return ""

def _pdf_first_page_to_raster_wrapper_svg(pdf_bytes: bytes) -> str:
    """
    Renderiza la página a PNG/JPEG y devuelve un SVG mínimo que la referencia por URL.
    El archivo de imagen se guarda en File y queda muy liviano el SVG.
    """
    try:
        import fitz
    except Exception:
        frappe.log_error("PyMuPDF no está instalado.", "IGCTools: raster wrapper")
        return ""
    if not pdf_bytes:
        return ""

    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
            if pdf.page_count < 1:
                return ""
            page = pdf.load_page(0)

            # Escala por DPI
            scale = float(RASTER_DPI) / 72.0
            mat = fitz.Matrix(scale, scale)
            # Render
            if RASTER_FORMAT.lower() == "jpeg":
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img_bytes = pix.tobytes("jpeg", quality=int(JPEG_QUALITY))
                ext = "jpg"
                mime = "image/jpeg"
            else:
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img_bytes = pix.tobytes("png")
                ext = "png"
                mime = "image/png"

            w, h = pix.width, pix.height

            # Guardar la imagen como File
            file_name = f"{RASTER_FILE_PREFIX}_{frappe.utils.now_datetime().strftime('%Y%m%d%H%M%S')}.{ext}"
            fdoc = frappe.get_doc({
                "doctype": "File",
                "file_name": file_name,
                "is_private": int(RASTER_PRIVATE),
                "content": img_bytes,
                "attached_to_doctype": None,
                "attached_to_name": None,
                "mime_type": mime,
            }).insert(ignore_permissions=True, ignore_if_duplicate=True)
            file_url = fdoc.file_url

            # SVG delgado que referencia la imagen por URL (no base64)
            svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">
  <image href="{file_url}" x="0" y="0" width="{w}" height="{h}" preserveAspectRatio="xMidYMid meet"/>
</svg>'''
            return _compress_ws(svg)
    except Exception as e:
        frappe.log_error(frappe.utils.cstr(e), "IGCTools: error PDF→Raster Wrapper")
        return ""

# =======================
# Hook principal
# =======================
def auto_svg_from_printcard(doc, method):
    """
    before_save en Project:
    - Toma PrintCard.archivo (PDF) y genera:
      * MODO RASTER_WRAPPER: PNG/JPEG + SVG mínimo con <image href="file_url">
      * MODO VECTOR_SIMPLIFIED: SVG vectorial limpiado
      * MODO VECTOR_RAW: SVG crudo (no recomendado)
    - Asigna a doc.svg_arte (no hace .save() aquí)
    """
    try:
        if getattr(doc.flags, "skip_auto_svg", False):
            return

        pc_name = (doc.get("printcard") or "").strip()
        if not pc_name:
            return

        pc = frappe.get_doc("PrintCard", pc_name)
        file_url = (pc.get("archivo") or "").strip()
        if not file_url:
            return

        pdf_bytes = _pdf_file_bytes_from_file_url(file_url)
        if not pdf_bytes:
            return

        if MODE == "RASTER_WRAPPER":
            svg = _pdf_first_page_to_raster_wrapper_svg(pdf_bytes)
        elif MODE == "VECTOR_SIMPLIFIED":
            svg = _pdf_first_page_to_svg_vector(pdf_bytes)
        else:  # VECTOR_RAW
            try:
                import fitz
                with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
                    if pdf.page_count < 1:
                        return
                    page = pdf.load_page(0)
                    svg = page.get_svg_image(text_as_path=True)
            except Exception:
                svg = ""

        if svg:
            doc.set("svg_arte", svg)
    except Exception as e:
        frappe.log_error(frappe.utils.cstr(e), "IGCTools: auto_svg_from_printcard")

# =======================
# Utilidades para lote
# =======================
def _update_one_project_svg(proj_name: str, force: bool = False) -> dict:
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
    if MODE == "RASTER_WRAPPER":
        svg = _pdf_first_page_to_raster_wrapper_svg(pdf_bytes)
    elif MODE == "VECTOR_SIMPLIFIED":
        svg = _pdf_first_page_to_svg_vector(pdf_bytes)
    else:
        try:
            import fitz
            with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
                if pdf.page_count < 1:
                    return {"project": proj_name, "skipped": True, "reason": "no_pages"}
                page = pdf.load_page(0)
                svg = page.get_svg_image(text_as_path=True)
        except Exception:
            svg = ""

    if not svg:
        return {"project": proj_name, "skipped": True, "reason": "svg_empty_or_error"}

    proj.flags.skip_auto_svg = True
    proj.set("svg_arte", svg)
    proj.save(ignore_permissions=True)
    return {"project": proj_name, "updated": True, "bytes": len(svg.encode("utf-8"))}

def _rebuild_job(batch_size: int = 200, force: bool = False, only_empty: bool = True):
    filters = [["printcard", "is", "set"]]
    if only_empty and not force:
        filters.append(["svg_arte", "=", ""])

    total = frappe.db.count("Project", filters=filters)
    done = 0
    start = 0

    while True:
        names = frappe.get_all(
            "Project", filters=filters, fields=["name"],
            start=start, page_length=batch_size, order_by="modified asc",
        )
        if not names:
            break

        for row in names:
            try:
                _update_one_project_svg(row.name, force=force)
            except Exception as e:
                frappe.log_error(frappe.utils.cstr(e),
                                 f"IGCTools: batch SVG failed for {row.name}")
            done += 1

        frappe.db.commit()
        start += batch_size

    return {"ok": True, "total": total, "processed": done, "force": force, "only_empty": only_empty}

@frappe.whitelist()
def rebuild_project_svgs(batch_size: int = 200, force: int = 0, only_empty: int = 1, enqueue: int = 1):
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

@frappe.whitelist()
def pymupdf_status():
    try:
        import fitz
        return {"ok": True, "version": getattr(fitz, "__version__", None)}
    except Exception as e:
        return {"ok": False, "error": repr(e)}
