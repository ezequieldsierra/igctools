import re, math
import frappe
from io import BytesIO, StringIO
import cairosvg

try:
    import ezdxf
    from svgpathtools import parse_path
    from svgpathtools import Line as _SP_Line
    from svgpathtools import Arc as _SP_Arc
    from svgpathtools import CubicBezier as _SP_Cubic
    from svgpathtools import QuadraticBezier as _SP_Quad
    _HAS_DXF = True
except Exception:
    _HAS_DXF = False

PX_TO_PT = 72.0 / 96.0
MM_TO_PT = 72.0 / 25.4
CM_TO_PT = 72.0 / 2.54
IN_TO_PT = 72.0
PC_TO_PT = 12.0
PX_PER_IN = 96.0

LAYER_WIDTH_MM = {
    "cut":    0.20,
    "crease": 0.30,
    "guide":  0.15,
}

DXF_LAYER_COLOR = {
    "cut": 1,
    "crease": 3,
    "guide": 8,
}

INTERPRET_UNITLESS_AS = "px"


def _to_pt(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return v
    m = re.match(r'^([+-]?(?:\d+(?:\.\d+)?|\.\d+))(.*)$', v)
    if not m:
        return v
    num, unit = m.group(1), (m.group(2) or "").strip().lower()
    try:
        n = float(num)
    except Exception:
        return v

    if unit == "pt": return f"{n}pt"
    if unit == "mm": return f"{n * MM_TO_PT}pt"
    if unit == "cm": return f"{n * CM_TO_PT}pt"
    if unit in ("in", '"'): return f"{n * IN_TO_PT}pt"
    if unit == "pc": return f"{n * PC_TO_PT}pt"
    if unit == "px": return f"{n * PX_TO_PT}pt"

    if unit == "":
        if INTERPRET_UNITLESS_AS == "px":
            return f"{n * PX_TO_PT}pt"
        return f"{n}pt"

    return v


def _inject_layer_css(svg_text: str, layer_mm: dict) -> str:
    rules = []
    for key, mm_val in layer_mm.items():
        w = f"{mm_val}mm"
        sel = (
            f'g[data-layer="{key.capitalize()}"], '
            f'g[data-layer="{key.lower()}"], '
            f'g[data-layer="{key.upper()}"]'
        )
        rules.append(
            f'''{sel} * {{
  stroke-width: {w} !important;
  vector-effect: none !important;
}}'''
        )
    css = "<style type=\"text/css\"><![CDATA[\n" + "\n".join(rules) + "\n]]></style>"
    return re.sub(r'(<svg\b[^>]*>)', r'\1' + css, svg_text, count=1, flags=re.I | re.S)


def normalize_svg_root(svg_text: str, width_mm: float, height_mm: float) -> str:
    try:
        from lxml import etree
        parser = etree.XMLParser(remove_comments=True, recover=True)
        root = etree.fromstring(svg_text.encode("utf-8"), parser=parser)

        root.attrib["width"] = f"{float(width_mm)}mm"
        root.attrib["height"] = f"{float(height_mm)}mm"

        style = root.attrib.get("style", "")
        if style:
            new_props = []
            for chunk in style.split(";"):
                chunk = chunk.strip()
                if not chunk or ":" not in chunk:
                    continue
                k, v = [t.strip() for t in chunk.split(":", 1)]
                if k.lower() in ("width", "height"):
                    continue
                new_props.append(f"{k}:{v}")
            if new_props:
                root.attrib["style"] = ";".join(new_props)
            elif "style" in root.attrib:
                del root.attrib["style"]

        for el in root.iter():
            if "stroke-width" in el.attrib:
                el.attrib["stroke-width"] = _to_pt(el.attrib["stroke-width"])

            st = el.attrib.get("style")
            if st:
                parts, changed = [], False
                for chunk in st.split(";"):
                    chunk = chunk.strip()
                    if not chunk:
                        continue
                    if ":" not in chunk:
                        parts.append(chunk)
                        continue
                    k, v = [t.strip() for t in chunk.split(":", 1)]
                    kl = k.lower()
                    if kl == "stroke-width":
                        nv = _to_pt(v)
                        parts.append(f"stroke-width:{nv}")
                        changed = changed or (nv != v)
                    elif kl == "stroke-dasharray":
                        if not re.match(r"^\s*(\d+(?:\.\d+)?(?:\s*,\s*\d+(?:\.\d+)?)*|none)\s*$", v, flags=re.I):
                            changed = True
                        else:
                            parts.append(f"{k}:{v}")
                    elif kl == "vector-effect":
                        changed = True
                    else:
                        parts.append(f"{k}:{v}")
                if changed:
                    el.attrib["style"] = ";".join(parts)

            if "stroke-dasharray" in el.attrib:
                v = el.attrib["stroke-dasharray"]
                if not re.match(r"^\s*(\d+(?:\.\d+)?(?:\s*,\s*\d+(?:\.\d+)?)*|none)\s*$", v, flags=re.I):
                    el.attrib.pop("stroke-dasharray", None)
            if "vector-effect" in el.attrib:
                el.attrib.pop("vector-effect", None)

        return etree.tostring(root, encoding="utf-8", xml_declaration=False).decode("utf-8")

    except Exception:
        s = svg_text
        s = re.sub(r'(<svg[^>]*?)\swidth="[^"]*"', r"\1", s, flags=re.I)
        s = re.sub(r'(<svg[^>]*?)\sheight="[^"]*"', r"\1", s, flags=re.I)
        s = re.sub(r"<svg", f'<svg width="{float(width_mm)}mm" height="{float(height_mm)}mm"', s, count=1, flags=re.I)
        s = re.sub(r'stroke-width="\s*([^"]+?)\s*"', lambda m: f'stroke-width="{_to_pt(m.group(1))}"', s, flags=re.I)
        s = re.sub(r'stroke-width\s*:\s*([^;"]+)', lambda m: f'stroke-width:{_to_pt(m.group(1))}', s, flags=re.I)
        s = re.sub(r'\svector-effect="[^"]*"', "", s, flags=re.I)
        s = re.sub(r"vector-effect\s*:\s*[^;]+;?", "", s, flags=re.I)
        s = re.sub(r'\sstroke-dasharray="(?!none)[^"]*"', "", s, flags=re.I)
        s = re.sub(r"stroke-dasharray\s*:\s*(?!none)[^;]+;?", "", s, flags=re.I)
        return s


def _parse_dim_attr(raw):
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None

    val_str = ""
    unit = ""
    for ch in raw:
        if ch.isdigit() or ch in ".-":
            val_str += ch
        else:
            unit += ch

    try:
        val = float(val_str)
    except Exception:
        return None

    u = unit.strip().lower()
    if u in ("in", "inch", "inches", '"', "in."):
        return val * 25.4
    if u in ("mm", "millimeter", "millimeters", ""):
        return val
    if u in ("cm", "centimeter", "centimeters"):
        return val * 10.0
    if u == "px":
        return val * (25.4 / PX_PER_IN)
    return val


def _get_svg_mm_size(svg_text: str) -> tuple[float, float]:
    width_mm = None
    height_mm = None
    text = svg_text or ""

    m = re.search(r'viewBox\s*=\s*"([^"]+)"', text, flags=re.IGNORECASE)
    if m:
        parts = m.group(1).strip().split()
        if len(parts) == 4:
            try:
                width_mm = float(parts[2])
                height_mm = float(parts[3])
            except Exception:
                pass

    if width_mm is None or height_mm is None:
        mw = re.search(r'width\s*=\s*"([^"]+)"', text, flags=re.IGNORECASE)
        mh = re.search(r'height\s*=\s*"([^"]+)"', text, flags=re.IGNORECASE)
        if mw:
            width_mm = _parse_dim_attr(mw.group(1)) or width_mm
        if mh:
            height_mm = _parse_dim_attr(mh.group(1)) or height_mm

    if width_mm is None:
        width_mm = 100.0
    if height_mm is None:
        height_mm = 100.0

    return float(width_mm), float(height_mm)


def _mul(A, B):
    a, b, c, d, e, f = A
    a2, b2, c2, d2, e2, f2 = B
    return (
        a * a2 + c * b2,
        b * a2 + d * b2,
        a * c2 + c * d2,
        b * c2 + d * d2,
        a * e2 + c * f2 + e,
        b * e2 + d * f2 + f,
    )


def _mat_scale(sx, sy):
    return (sx, 0.0, 0.0, sy, 0.0, 0.0)


def _mat_rotate(deg):
    rad = math.radians(float(deg))
    c, s = math.cos(rad), math.sin(rad)
    return (c, s, -s, c, 0.0, 0.0)


def _mat_translate(tx, ty):
    return (1.0, 0.0, 0.0, 1.0, tx, ty)


def _mat_skewX(deg):
    t = math.tan(math.radians(float(deg)))
    return (1.0, 0.0, t, 1.0, 0.0, 0.0)


def _mat_skewY(deg):
    t = math.tan(math.radians(float(deg)))
    return (1.0, t, 0.0, 1.0, 0.0, 0.0)


def _parse_transform_attr(s: str):
    M = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    if not s:
        return M
    for fn, args in re.findall(r"([a-zA-Z]+)\s*\(([^)]*)\)", s):
        fnl = fn.strip().lower()
        nums = [float(x) for x in re.split(r"[ ,]+", args.strip()) if x]
        if fnl == "matrix" and len(nums) == 6:
            m = tuple(nums)
        elif fnl == "translate":
            m = _mat_translate(nums[0] if nums else 0.0, nums[1] if len(nums) > 1 else 0.0)
        elif fnl == "scale":
            sx = nums[0] if nums else 1.0
            sy = nums[1] if len(nums) > 1 else sx
            m = _mat_scale(sx, sy)
        elif fnl == "rotate":
            m = _mat_rotate(nums[0] if nums else 0.0)
        elif fnl == "skewx":
            m = _mat_skewX(nums[0] if nums else 0.0)
        elif fnl == "skewy":
            m = _mat_skewY(nums[0] if nums else 0.0)
        else:
            continue
        M = _mul(M, m)
    return M


def _apply_mat(M, x, y):
    a, b, c, d, e, f = M
    return (a * x + c * y + e, b * x + d * y + f)


def _collect_ancestors_transform(el):
    M = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    cur = el
    try:
        parent = cur.getparent()
    except Exception:
        parent = None
    while parent is not None:
        tf = parent.attrib.get("transform")
        if tf:
            M = _mul(_parse_transform_attr(tf), M)
        try:
            parent = parent.getparent()
        except Exception:
            parent = None
    return M


def _nearest_layer(el):
    cur = el
    while cur is not None:
        dl = cur.attrib.get("data-layer")
        if dl:
            return dl.strip().lower()
        try:
            cur = cur.getparent()
        except Exception:
            cur = None
    return ""


def _ensure_dxf_layers(doc):
    for lname, color in DXF_LAYER_COLOR.items():
        if lname.upper() not in doc.layers:
            doc.layers.add(lname.upper(), color=color)


def _is_in_defs(el):
    cur = el
    try:
        parent = cur.getparent()
    except Exception:
        parent = None
    while parent is not None:
        tag = getattr(parent, "tag", "")
        local = tag.split("}", 1)[-1].lower()
        if local == "defs":
            return True
        try:
            parent = parent.getparent()
        except Exception:
            parent = None
    return False


def _roundN(x, nd=6):
    try:
        return round(float(x), nd)
    except Exception:
        return 0.0


def _remove_bbox_rects(root, viewbox, tol=1e-6):
    from lxml import etree
    minx, miny, vbw, vbh = viewbox
    removed = 0
    for el in list(root.iter()):
        try:
            tag = etree.QName(el).localname.lower()
        except Exception:
            continue
        if tag != "rect":
            continue

        def _f(a, default=None):
            try:
                return float(el.attrib.get(a, default))
            except Exception:
                return None

        x = _f("x", 0.0)
        y = _f("y", 0.0)
        w = _f("width", None)
        h = _f("height", None)
        if w is None or h is None:
            continue

        if (
            abs(x - float(minx)) <= tol
            and abs(y - float(miny)) <= tol
            and abs(w - float(vbw)) <= tol
            and abs(h - float(vbh)) <= tol
        ):
            fill = (el.attrib.get("fill") or "").strip().lower()
            style = (el.attrib.get("style") or "").lower()
            fill_none = (fill in ("", "none", "transparent")) or ("fill:none" in style)
            if not fill_none:
                continue
            try:
                parent = el.getparent()
            except Exception:
                parent = None
            if parent is not None:
                parent.remove(el)
                removed += 1
    return removed


def _canon_pair(a, b):
    return (a, b) if a <= b else (b, a)


def _canon_line_key(x1, y1, x2, y2, nd=6):
    a = (_roundN(x1, nd), _roundN(y1, nd))
    b = (_roundN(x2, nd), _roundN(y2, nd))
    a, b = _canon_pair(a, b)
    return ("L", a, b)


def _canon_arc_key(cx, cy, r, sx, sy, ex, ey, nd=6):
    c = (_roundN(cx, nd), _roundN(cy, nd), _roundN(r, nd))
    s = (_roundN(sx, nd), _roundN(sy, nd))
    e = (_roundN(ex, nd), _roundN(ey, nd))
    k1 = ("A", c, s, e)
    k2 = ("A", c, e, s)
    return min(k1, k2)


def _canon_bezier_key(pts, nd=6):
    p = tuple((_roundN(x, nd), _roundN(y, nd)) for x, y in pts)
    pr = tuple(reversed(p))
    return ("B", min(p, pr))


def _path_signature_geom(d: str, M, nd=6):
    if not _HAS_DXF:
        dd = re.sub(r"\s+", " ", (d or "").strip())
        dd = re.sub(r"\s*,\s*", ",", dd)
        mm = tuple(_roundN(v, nd) for v in (M or (1, 0, 0, 1, 0, 0)))
        return ("RAW", mm, dd)

    try:
        p = parse_path(d)
    except Exception:
        dd = re.sub(r"\s+", " ", (d or "").strip())
        dd = re.sub(r"\s*,\s*", ",", dd)
        mm = tuple(_roundN(v, nd) for v in (M or (1, 0, 0, 1, 0, 0)))
        return ("RAW", mm, dd)

    seg_keys = []

    for seg in p:
        if isinstance(seg, _SP_Line):
            x1, y1 = float(seg.start.real), float(seg.start.imag)
            x2, y2 = float(seg.end.real), float(seg.end.imag)
            x1, y1 = _apply_mat(M, x1, y1)
            x2, y2 = _apply_mat(M, x2, y2)
            seg_keys.append(_canon_line_key(x1, y1, x2, y2, nd=nd))

        elif isinstance(seg, _SP_Arc):
            c = seg.center
            cx, cy = float(c.real), float(c.imag)
            sx, sy = float(seg.start.real), float(seg.start.imag)
            ex, ey = float(seg.end.real), float(seg.end.imag)

            cx, cy = _apply_mat(M, cx, cy)
            sx, sy = _apply_mat(M, sx, sy)
            ex, ey = _apply_mat(M, ex, ey)

            r = float(seg.radius.real) if hasattr(seg.radius, "real") else float(seg.radius)
            a, b, c1, d1, e1, f1 = M
            sxm = math.hypot(a, b)
            sym = math.hypot(c1, d1)
            if abs(sxm - sym) < 1e-9:
                r = r * sxm

            seg_keys.append(_canon_arc_key(cx, cy, r, sx, sy, ex, ey, nd=nd))

        elif isinstance(seg, (_SP_Cubic, _SP_Quad)):
            pts = []
            z0 = seg.start
            z1 = seg.end
            pts.append((float(z0.real), float(z0.imag)))
            if isinstance(seg, _SP_Cubic):
                c1 = seg.control1
                c2 = seg.control2
                pts.append((float(c1.real), float(c1.imag)))
                pts.append((float(c2.real), float(c2.imag)))
            else:
                c1 = seg.control
                pts.append((float(c1.real), float(c1.imag)))
            pts.append((float(z1.real), float(z1.imag)))

            tpts = []
            for x, y in pts:
                X, Y = _apply_mat(M, x, y)
                tpts.append((X, Y))
            seg_keys.append(_canon_bezier_key(tpts, nd=nd))

        else:
            pts = []
            for i in range(0, 33):
                t = i / 32.0
                z = seg.point(t)
                x, y = float(z.real), float(z.imag)
                x, y = _apply_mat(M, x, y)
                pts.append((x, y))
            seg_keys.append(_canon_bezier_key(pts, nd=nd))

    seg_keys.sort()
    return ("G", tuple(seg_keys))


def _dedupe_svg_geometry(svg_text: str, width_mm: float, height_mm: float, nd=6, drop_bbox=True) -> str:
    from lxml import etree
    parser = etree.XMLParser(remove_comments=True, recover=True)
    root = etree.fromstring((svg_text or "").encode("utf-8"), parser=parser)

    vb_attr = root.attrib.get("viewBox")
    if not vb_attr:
        minx, miny, vbw, vbh = 0.0, 0.0, float(width_mm), float(height_mm)
    else:
        vb = [float(x) for x in re.split(r"[ ,]+", vb_attr.strip()) if x]
        if len(vb) == 4:
            minx, miny, vbw, vbh = vb
        else:
            minx, miny, vbw, vbh = 0.0, 0.0, float(width_mm), float(height_mm)
    viewbox = (minx, miny, vbw, vbh)

    if drop_bbox:
        _remove_bbox_rects(root, viewbox, tol=1e-6)

    seen = set()

    for el in list(root.iter()):
        if _is_in_defs(el):
            continue

        try:
            tag = etree.QName(el).localname.lower()
        except Exception:
            continue

        if tag not in ("line", "polyline", "polygon", "path", "circle", "ellipse"):
            continue

        M = _parse_transform_attr(el.attrib.get("transform", ""))
        M = _mul(_collect_ancestors_transform(el), M)

        key = None

        if tag == "line":
            try:
                x1 = float(el.attrib.get("x1", "0"))
                y1 = float(el.attrib.get("y1", "0"))
                x2 = float(el.attrib.get("x2", "0"))
                y2 = float(el.attrib.get("y2", "0"))
                x1, y1 = _apply_mat(M, x1, y1)
                x2, y2 = _apply_mat(M, x2, y2)
                key = ("line",) + _canon_line_key(x1, y1, x2, y2, nd=nd)
            except Exception:
                key = None

        elif tag in ("polyline", "polygon"):
            pts_attr = (el.attrib.get("points") or "").strip()
            if not pts_attr:
                continue
            pts = []
            for pair in re.split(r"\s+", pts_attr):
                if not pair.strip():
                    continue
                parts = pair.split(",")
                if len(parts) != 2:
                    continue
                try:
                    x = float(parts[0]); y = float(parts[1])
                except Exception:
                    continue
                x, y = _apply_mat(M, x, y)
                pts.append((_roundN(x, nd), _roundN(y, nd)))
            if len(pts) < 2:
                continue
            pr = tuple(reversed(pts))
            key = ("poly", tag == "polygon", min(tuple(pts), pr))

        elif tag == "circle":
            try:
                cx = float(el.attrib.get("cx", "0"))
                cy = float(el.attrib.get("cy", "0"))
                r = float(el.attrib.get("r", "0"))
                cx, cy = _apply_mat(M, cx, cy)
                a, b, c1, d1, e1, f1 = M
                sxm = math.hypot(a, b)
                sym = math.hypot(c1, d1)
                if abs(sxm - sym) > 1e-9:
                    key = None
                else:
                    r = r * sxm
                    key = ("circle", _roundN(cx, nd), _roundN(cy, nd), _roundN(r, nd))
            except Exception:
                key = None

        elif tag == "ellipse":
            try:
                cx = float(el.attrib.get("cx", "0"))
                cy = float(el.attrib.get("cy", "0"))
                rx = float(el.attrib.get("rx", "0"))
                ry = float(el.attrib.get("ry", "0"))
                cx, cy = _apply_mat(M, cx, cy)
                a, b, c1, d1, e1, f1 = M
                if abs(b) > 1e-12 or abs(c1) > 1e-12:
                    key = None
                else:
                    sxm = math.hypot(a, b)
                    sym = math.hypot(c1, d1)
                    rx = rx * sxm
                    ry = ry * sym
                    rx, ry = _canon_pair((_roundN(rx, nd),), (_roundN(ry, nd),))
                    key = ("ellipse", _roundN(cx, nd), _roundN(cy, nd), rx[0], ry[0])
            except Exception:
                key = None

        elif tag == "path":
            d = (el.attrib.get("d") or "").strip()
            if not d:
                continue
            key = ("path", _path_signature_geom(d, M, nd=nd))

        if not key:
            continue

        if key in seen:
            try:
                parent = el.getparent()
            except Exception:
                parent = None
            if parent is not None:
                parent.remove(el)
            continue

        seen.add(key)

    return etree.tostring(root, encoding="utf-8", xml_declaration=False).decode("utf-8")


def _vb_to_mm(viewbox, width_mm, height_mm, x, y):
    minx, miny, vbw, vbh = viewbox
    sx = float(width_mm) / float(vbw)
    sy = float(height_mm) / float(vbh)
    X = (x - minx) * sx
    Y = (miny + vbh - y) * sy
    return X, Y


@frappe.whitelist(methods=["POST"])
def export_generador_troquel_pdf(
    docname: str,
    svg: str,
    width_mm: float | None = None,
    height_mm: float | None = None,
    filename: str | None = None,
    is_private: int = 0,
):
    if not svg:
        frappe.throw("SVG requerido.")

    if not width_mm or not height_mm:
        width_mm, height_mm = _get_svg_mm_size(svg)

    svg_clean = normalize_svg_root(svg, width_mm, height_mm)
    svg_clean = _dedupe_svg_geometry(svg_clean, width_mm, height_mm, nd=6, drop_bbox=True)
    svg_clean = _inject_layer_css(svg_clean, LAYER_WIDTH_MM)

    buf = BytesIO()
    cairosvg.svg2pdf(
        bytestring=svg_clean.encode("utf-8"),
        write_to=buf,
        dpi=96,
        background_color="white",
    )
    pdf_bytes = buf.getvalue()
    buf.close()

    if not filename:
        filename = f"{frappe.utils.now_datetime().strftime('%Y%m%d-%H%M%S')}.pdf"
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"

    filedoc = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": filename,
            "is_private": int(is_private or 0),
            "content": pdf_bytes,
            "attached_to_doctype": "Generador de Troquel",
            "attached_to_name": docname,
        }
    )
    filedoc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"file_url": filedoc.file_url, "file_name": filedoc.file_name}


@frappe.whitelist(methods=["POST"])
def export_generador_troquel_svg(
    docname: str,
    svg: str,
    width_mm: float | None = None,
    height_mm: float | None = None,
    filename: str | None = None,
    is_private: int = 0,
):
    if not svg:
        frappe.throw("SVG requerido.")

    if not width_mm or not height_mm:
        width_mm, height_mm = _get_svg_mm_size(svg)

    svg_clean = normalize_svg_root(svg, width_mm, height_mm)
    svg_clean = _dedupe_svg_geometry(svg_clean, width_mm, height_mm, nd=6, drop_bbox=True)
    svg_clean = _inject_layer_css(svg_clean, LAYER_WIDTH_MM)

    if not svg_clean.lstrip().startswith("<?xml"):
        svg_clean = '<?xml version="1.0" encoding="UTF-8"?>\n' + svg_clean

    if not filename:
        filename = f"{frappe.utils.now_datetime().strftime('%Y%m%d-%H%M%S')}.svg"
    if not filename.lower().endswith(".svg"):
        filename += ".svg"

    filedoc = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": filename,
            "is_private": int(is_private or 0),
            "content": svg_clean.encode("utf-8"),
            "attached_to_doctype": "Generador de Troquel",
            "attached_to_name": docname,
        }
    )
    filedoc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"file_url": filedoc.file_url, "file_name": filedoc.file_name}


@frappe.whitelist(methods=["POST"])
def export_generador_troquel_dxf(
    docname: str,
    svg: str,
    width_mm: float | None = None,
    height_mm: float | None = None,
    filename: str | None = None,
    is_private: int = 0,
):
    if not svg:
        frappe.throw("SVG requerido.")

    if not _HAS_DXF:
        frappe.throw("Dependencias DXF no disponibles. Instala ezdxf y svgpathtools.")

    if not width_mm or not height_mm:
        width_mm, height_mm = _get_svg_mm_size(svg)

    from lxml import etree
    parser = etree.XMLParser(remove_comments=True, recover=True)

    svg_clean = normalize_svg_root(svg, width_mm, height_mm)
    svg_clean = _dedupe_svg_geometry(svg_clean, width_mm, height_mm, nd=6, drop_bbox=True)
    root = etree.fromstring(svg_clean.encode("utf-8"), parser=parser)

    vb_attr = root.attrib.get("viewBox")
    if not vb_attr:
        minx, miny, vbw, vbh = 0.0, 0.0, float(width_mm), float(height_mm)
    else:
        vb = [float(x) for x in re.split(r"[ ,]+", vb_attr.strip()) if x]
        if len(vb) != 4:
            frappe.throw("viewBox inválido en SVG.")
        minx, miny, vbw, vbh = vb

    viewbox = (minx, miny, vbw, vbh)

    from ezdxf import units

    doc = ezdxf.new("R2010")
    doc.units = units.MM
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1
    msp = doc.modelspace()

    def lw_from_layer(layer_name: str) -> int:
        mm = LAYER_WIDTH_MM.get(layer_name, 0.20)
        return max(0, min(211, int(round(mm * 100))))

    _ensure_dxf_layers(doc)

    seen_ent = set()

    def _k(*parts):
        return tuple(parts)

    def _add_line(layer_u, lweight, x1, y1, x2, y2):
        kk = _k("L", layer_u) + _canon_line_key(x1, y1, x2, y2, nd=6) + (int(lweight),)
        if kk in seen_ent:
            return
        seen_ent.add(kk)
        msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": layer_u, "lineweight": lweight})

    def _add_circle(layer_u, lweight, cx, cy, r):
        kk = _k("C", layer_u, _roundN(cx, 6), _roundN(cy, 6), _roundN(r, 6), int(lweight))
        if kk in seen_ent:
            return
        seen_ent.add(kk)
        msp.add_circle((cx, cy), r, dxfattribs={"layer": layer_u, "lineweight": lweight})

    def _add_arc(layer_u, lweight, cx, cy, r, start_deg, end_deg):
        a1 = _roundN(start_deg, 6)
        a2 = _roundN(end_deg, 6)
        kk1 = _k("A", layer_u, _roundN(cx, 6), _roundN(cy, 6), _roundN(r, 6), a1, a2, int(lweight))
        kk2 = _k("A", layer_u, _roundN(cx, 6), _roundN(cy, 6), _roundN(r, 6), a2, a1, int(lweight))
        kk = min(kk1, kk2)
        if kk in seen_ent:
            return
        seen_ent.add(kk)
        msp.add_arc((cx, cy), r, start_deg, end_deg, dxfattribs={"layer": layer_u, "lineweight": lweight})

    def _add_ellipse(layer_u, lweight, cx, cy, major_x, major_y, ratio):
        kk = _k("E", layer_u, _roundN(cx, 6), _roundN(cy, 6), _roundN(major_x, 6), _roundN(major_y, 6), _roundN(ratio, 9), int(lweight))
        if kk in seen_ent:
            return
        seen_ent.add(kk)
        msp.add_ellipse((cx, cy), (major_x, major_y), ratio=ratio, dxfattribs={"layer": layer_u, "lineweight": lweight})

    def _add_spline(layer_u, lweight, fit_pts):
        pts = tuple((_roundN(x, 6), _roundN(y, 6)) for x, y in (fit_pts or []))
        pr = tuple(reversed(pts))
        kk = _k("S", layer_u, min(pts, pr), int(lweight))
        if kk in seen_ent:
            return
        seen_ent.add(kk)
        msp.add_spline(fit_points=list(fit_pts), dxfattribs={"layer": layer_u, "lineweight": lweight})

    for el in root.iter():
        if _is_in_defs(el):
            continue

        tag = etree.QName(el).localname.lower()
        if tag not in ("line", "path", "circle", "ellipse", "polyline", "polygon"):
            continue

        layer = (_nearest_layer(el) or "cut").lower()
        if layer == "guide":
            continue

        layer_u = layer.upper()
        if layer_u not in doc.layers:
            doc.layers.add(layer_u, color=DXF_LAYER_COLOR.get(layer, 7))

        M = _parse_transform_attr(el.attrib.get("transform", ""))
        M = _mul(_collect_ancestors_transform(el), M)

        lweight = lw_from_layer(layer)

        if tag == "line":
            try:
                x1 = float(el.attrib.get("x1", "0"))
                y1 = float(el.attrib.get("y1", "0"))
                x2 = float(el.attrib.get("x2", "0"))
                y2 = float(el.attrib.get("y2", "0"))
                x1, y1 = _apply_mat(M, x1, y1)
                x2, y2 = _apply_mat(M, x2, y2)
                X1, Y1 = _vb_to_mm(viewbox, width_mm, height_mm, x1, y1)
                X2, Y2 = _vb_to_mm(viewbox, width_mm, height_mm, x2, y2)
                _add_line(layer_u, lweight, X1, Y1, X2, Y2)
            except Exception:
                continue

        elif tag == "circle":
            try:
                cx = float(el.attrib.get("cx", "0"))
                cy = float(el.attrib.get("cy", "0"))
                r = float(el.attrib.get("r", "0"))
                cx, cy = _apply_mat(M, cx, cy)
                a, b, c1, d1, e1, f1 = M
                sxm = math.hypot(a, b)
                sym = math.hypot(c1, d1)
                if abs(sxm - sym) > 1e-9:
                    continue
                r = r * sxm
                CX, CY = _vb_to_mm(viewbox, width_mm, height_mm, cx, cy)
                R = r * (float(width_mm) / float(vbw))
                _add_circle(layer_u, lweight, CX, CY, R)
            except Exception:
                continue

        elif tag == "ellipse":
            try:
                cx = float(el.attrib.get("cx", "0"))
                cy = float(el.attrib.get("cy", "0"))
                rx = float(el.attrib.get("rx", "0"))
                ry = float(el.attrib.get("ry", "0"))
                cx, cy = _apply_mat(M, cx, cy)
                a, b, c1, d1, e1, f1 = M
                if abs(b) > 1e-12 or abs(c1) > 1e-12:
                    continue
                sxm = math.hypot(a, b)
                sym = math.hypot(c1, d1)
                rx = rx * sxm
                ry = ry * sym
                CX, CY = _vb_to_mm(viewbox, width_mm, height_mm, cx, cy)
                RX = rx * (float(width_mm) / float(vbw))
                RY = ry * (float(height_mm) / float(vbh))
                major = max(RX, RY)
                if major <= 0:
                    continue
                ratio = (min(RX, RY) / major) if major else 1.0
                if RX >= RY:
                    _add_ellipse(layer_u, lweight, CX, CY, major, 0.0, ratio)
                else:
                    _add_ellipse(layer_u, lweight, CX, CY, 0.0, major, ratio)
            except Exception:
                continue

        elif tag in ("polyline", "polygon"):
            pts_attr = (el.attrib.get("points") or "").strip()
            if not pts_attr:
                continue
            pts = []
            for pair in re.split(r"\s+", pts_attr):
                if not pair.strip():
                    continue
                parts = pair.split(",")
                if len(parts) != 2:
                    continue
                try:
                    x = float(parts[0]); y = float(parts[1])
                except Exception:
                    continue
                x, y = _apply_mat(M, x, y)
                X, Y = _vb_to_mm(viewbox, width_mm, height_mm, x, y)
                pts.append((X, Y))
            if len(pts) < 2:
                continue
            for i in range(len(pts) - 1):
                _add_line(layer_u, lweight, pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1])
            if tag == "polygon":
                _add_line(layer_u, lweight, pts[-1][0], pts[-1][1], pts[0][0], pts[0][1])

        elif tag == "path":
            d = (el.attrib.get("d") or "").strip()
            if not d:
                continue
            try:
                p = parse_path(d)
            except Exception:
                continue

            for seg in p:
                if isinstance(seg, _SP_Line):
                    x1, y1 = float(seg.start.real), float(seg.start.imag)
                    x2, y2 = float(seg.end.real), float(seg.end.imag)
                    x1, y1 = _apply_mat(M, x1, y1)
                    x2, y2 = _apply_mat(M, x2, y2)
                    X1, Y1 = _vb_to_mm(viewbox, width_mm, height_mm, x1, y1)
                    X2, Y2 = _vb_to_mm(viewbox, width_mm, height_mm, x2, y2)
                    _add_line(layer_u, lweight, X1, Y1, X2, Y2)

                elif isinstance(seg, _SP_Arc):
                    c = seg.center
                    cx, cy = float(c.real), float(c.imag)
                    sx, sy = float(seg.start.real), float(seg.start.imag)
                    ex, ey = float(seg.end.real), float(seg.end.imag)

                    cx, cy = _apply_mat(M, cx, cy)
                    sx, sy = _apply_mat(M, sx, sy)
                    ex, ey = _apply_mat(M, ex, ey)

                    CX, CY = _vb_to_mm(viewbox, width_mm, height_mm, cx, cy)
                    SX, SY = _vb_to_mm(viewbox, width_mm, height_mm, sx, sy)
                    EX, EY = _vb_to_mm(viewbox, width_mm, height_mm, ex, ey)

                    r = float(seg.radius.real) if hasattr(seg.radius, "real") else float(seg.radius)
                    a, b, c1, d1, e1, f1 = M
                    sxm = math.hypot(a, b)
                    sym = math.hypot(c1, d1)
                    if abs(sxm - sym) > 1e-9:
                        fit = []
                        for i in range(0, 33):
                            t = i / 32.0
                            z = seg.point(t)
                            x, y = float(z.real), float(z.imag)
                            x, y = _apply_mat(M, x, y)
                            X, Y = _vb_to_mm(viewbox, width_mm, height_mm, x, y)
                            fit.append((X, Y))
                        _add_spline(layer_u, lweight, fit)
                        continue

                    R = (r * sxm) * (float(width_mm) / float(vbw))

                    def ang(x, y):
                        return math.degrees(math.atan2(y - CY, x - CX))

                    a1 = ang(SX, SY)
                    a2 = ang(EX, EY)

                    if math.hypot(SX - EX, SY - EY) < 1e-6:
                        _add_circle(layer_u, lweight, CX, CY, R)
                    else:
                        _add_arc(layer_u, lweight, CX, CY, R, a1, a2)

                else:
                    fit = []
                    for i in range(0, 25):
                        t = i / 24.0
                        z = seg.point(t)
                        x, y = float(z.real), float(z.imag)
                        x, y = _apply_mat(M, x, y)
                        X, Y = _vb_to_mm(viewbox, width_mm, height_mm, x, y)
                        fit.append((X, Y))
                    _add_spline(layer_u, lweight, fit)

    text_buf = StringIO()
    doc.write(text_buf)
    dxf_text = text_buf.getvalue()
    text_buf.close()
    out = dxf_text.encode("utf-8")

    if not filename:
        filename = f"{frappe.utils.now_datetime().strftime('%Y%m%d-%H%M%S')}.dxf"
    if not filename.lower().endswith(".dxf"):
        filename += ".dxf"

    filedoc = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": filename,
            "is_private": int(is_private or 0),
            "content": out,
            "attached_to_doctype": "Generador de Troquel",
            "attached_to_name": docname,
        }
    )
    filedoc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"file_url": filedoc.file_url, "file_name": filedoc.file_name}

@frappe.whitelist(methods=["POST"])
def export_generador_troquel_dxf_v2(
    docname: str,
    svg: str,
    width_mm: float | None = None,
    height_mm: float | None = None,
    filename: str | None = None,
    is_private: int = 0,
):
    if not svg:
        frappe.throw("SVG requerido.")

    if not _HAS_DXF:
        frappe.throw("Dependencias DXF no disponibles. Instala ezdxf y svgpathtools.")

    if not width_mm or not height_mm:
        width_mm, height_mm = _get_svg_mm_size(svg)

    from lxml import etree
    parser = etree.XMLParser(remove_comments=True, recover=True)
    root = etree.fromstring(svg.encode("utf-8"), parser=parser)

    vb_attr = root.attrib.get("viewBox")
    if not vb_attr:
        minx, miny, vbw, vbh = 0.0, 0.0, float(width_mm), float(height_mm)
    else:
        vb = [float(x) for x in re.split(r"[ ,]+", vb_attr.strip()) if x]
        if len(vb) != 4:
            frappe.throw("viewBox inválido en SVG.")
        minx, miny, vbw, vbh = vb

    viewbox = (minx, miny, vbw, vbh)

    from ezdxf import units
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.units = units.MM
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1
    msp = doc.modelspace()

    def lw_from_layer(layer_name: str) -> int:
        mm = LAYER_WIDTH_MM.get(layer_name, 0.20)
        return max(0, min(211, int(round(mm * 100))))

    def _norm_hex(h: str) -> str:
        s = (h or "").strip().lower()
        if not s:
            return ""
        if s.startswith("#") and len(s) == 4:
            return f"#{s[1]}{s[1]}{s[2]}{s[2]}{s[3]}{s[3]}"
        if s.startswith("#") and len(s) == 7:
            return s
        return ""

    def _parse_rgb(s: str):
        m = re.match(r"^rgba?\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)(?:\s*,\s*([0-9.]+))?\s*\)\s*$", (s or "").strip().lower())
        if not m:
            return None
        r = int(round(float(m.group(1))))
        g = int(round(float(m.group(2))))
        b = int(round(float(m.group(3))))
        a = m.group(4)
        if a is not None and float(a) <= 0:
            return None
        r = max(0, min(255, r))
        g = max(0, min(255, g))
        b = max(0, min(255, b))
        return (r, g, b)

    def _nearest_style_attr(el, key: str) -> str:
        cur = el
        while cur is not None:
            st = (cur.attrib.get("style") or "").strip()
            if st:
                for part in st.split(";"):
                    if ":" not in part:
                        continue
                    k, v = part.split(":", 1)
                    if k.strip().lower() == key:
                        return (v or "").strip()
            try:
                cur = cur.getparent()
            except Exception:
                cur = None
        return ""

    def _nearest_attr(el, key: str) -> str:
        cur = el
        while cur is not None:
            v = cur.attrib.get(key)
            if v is not None and str(v).strip() != "":
                return str(v).strip()
            try:
                cur = cur.getparent()
            except Exception:
                cur = None
        return ""

    def _stroke_rgb_for_el(el):
        s = _nearest_attr(el, "stroke")
        if not s:
            s = _nearest_style_attr(el, "stroke")
        s = (s or "").strip().lower()
        if not s or s in ("none", "transparent", "currentcolor"):
            return None
        if s.startswith("#"):
            hx = _norm_hex(s)
            if not hx:
                return None
            return (int(hx[1:3], 16), int(hx[3:5], 16), int(hx[5:7], 16))
        if s.startswith("rgb"):
            return _parse_rgb(s)
        if s == "red":
            return (255, 0, 0)
        if s == "black":
            return (0, 0, 0)
        if s == "white":
            return (255, 255, 255)
        if s == "blue":
            return (0, 0, 255)
        if s == "green":
            return (0, 128, 0)
        return None

    def _dxf_truecolor_int(rgb):
        r, g, b = rgb
        return (int(r) << 16) | (int(g) << 8) | int(b)

    _ensure_dxf_layers(doc)

    for el in root.iter():
        if _is_in_defs(el):
            continue

        tag = etree.QName(el).localname.lower()
        if tag not in ("line", "polyline", "polygon", "path", "circle", "ellipse", "rect"):
            continue

        layer = _nearest_layer(el) or "cut"
        if layer.lower() == "guide":
            continue

        layer_upper = layer.upper()
        if layer_upper not in doc.layers:
            doc.layers.add(layer_upper, color=DXF_LAYER_COLOR.get(layer, 7))

        M = _parse_transform_attr(el.attrib.get("transform", ""))
        M = _mul(_collect_ancestors_transform(el), M)

        lweight = lw_from_layer(layer)
        dxfattribs = {"layer": layer_upper, "lineweight": lweight}

        rgb = _stroke_rgb_for_el(el)
        if rgb is not None:
            dxfattribs["true_color"] = _dxf_truecolor_int(rgb)
            dxfattribs["color"] = 256

        if tag == "line":
            try:
                x1 = float(el.attrib.get("x1", "0"))
                y1 = float(el.attrib.get("y1", "0"))
                x2 = float(el.attrib.get("x2", "0"))
                y2 = float(el.attrib.get("y2", "0"))
                x1, y1 = _apply_mat(M, x1, y1)
                x2, y2 = _apply_mat(M, x2, y2)
                X1, Y1 = _vb_to_mm(viewbox, width_mm, height_mm, x1, y1)
                X2, Y2 = _vb_to_mm(viewbox, width_mm, height_mm, x2, y2)
                msp.add_line((X1, Y1), (X2, Y2), dxfattribs=dxfattribs)
            except Exception:
                continue

        elif tag in ("polyline", "polygon"):
            pts_attr = (el.attrib.get("points", "") or "").strip()
            if not pts_attr:
                continue
            pts = []
            for pair in re.split(r"\s+", pts_attr):
                if not pair.strip():
                    continue
                parts = pair.split(",")
                if len(parts) != 2:
                    continue
                try:
                    x = float(parts[0])
                    y = float(parts[1])
                except Exception:
                    continue
                x, y = _apply_mat(M, x, y)
                X, Y = _vb_to_mm(viewbox, width_mm, height_mm, x, y)
                pts.append((X, Y))
            if len(pts) < 2:
                continue
            is_closed = (tag == "polygon")
            msp.add_lwpolyline(pts, format="xy", dxfattribs={**dxfattribs, "closed": is_closed})

        elif tag == "rect":
            ok = _dxf_add_rect_el(msp, el, viewbox, width_mm, height_mm, M, dxfattribs)
            if not ok:
                continue

        elif tag == "circle":
            ok = _dxf_add_circle_el(msp, el, viewbox, width_mm, height_mm, M, dxfattribs)
            if not ok:
                continue

        elif tag == "ellipse":
            ok = _dxf_add_ellipse_el_as_arcchain(
                msp, el, viewbox, width_mm, height_mm, M, dxfattribs, chord_mm=0.15
            )
            if not ok:
                continue

        elif tag == "path":
            d = (el.attrib.get("d", "") or "").strip()
            if not d:
                continue

            try:
                p = parse_path(d)
            except Exception:
                continue

            try:
                subs = p.continuous_subpaths()
            except Exception:
                subs = [p]

            for sp in subs:
                if not sp or len(sp) == 0:
                    continue

                for seg in sp:
                    if seg is None:
                        continue

                    cls = seg.__class__.__name__.lower()

                    if cls == "line":
                        z0 = seg.start
                        z1 = seg.end
                        x0, y0 = float(z0.real), float(z0.imag)
                        x1, y1 = float(z1.real), float(z1.imag)
                        x0, y0 = _apply_mat(M, x0, y0)
                        x1, y1 = _apply_mat(M, x1, y1)
                        X0, Y0 = _vb_to_mm(viewbox, width_mm, height_mm, x0, y0)
                        X1, Y1 = _vb_to_mm(viewbox, width_mm, height_mm, x1, y1)
                        msp.add_line((X0, Y0), (X1, Y1), dxfattribs=dxfattribs)
                        continue

                    if cls == "arc":
                        ok = _dxf_add_arc_from_segment_3pt(msp, seg, viewbox, width_mm, height_mm, M, dxfattribs)
                        if ok:
                            continue

                    _dxf_add_arcchain_from_segment(
                        msp, seg, viewbox, width_mm, height_mm, M, dxfattribs, chord_mm=0.20
                    )

    text_buf = StringIO()
    doc.write(text_buf)
    dxf_text = text_buf.getvalue()
    text_buf.close()
    out = dxf_text.encode("utf-8")

    if not filename:
        filename = f"{frappe.utils.now_datetime().strftime('%Y%m%d-%H%M%S')}.dxf"
    if not filename.lower().endswith(".dxf"):
        filename += ".dxf"

    filedoc = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": filename,
            "is_private": int(is_private or 0),
            "content": out,
            "attached_to_doctype": "Generador de Troquel",
            "attached_to_name": docname,
        }
    )
    filedoc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"file_url": filedoc.file_url, "file_name": filedoc.file_name}


def _dxf_add_rect_el(msp, el, viewbox, width_mm, height_mm, M, dxfattribs):
    try:
        x = float(el.attrib.get("x", "0") or 0.0)
        y = float(el.attrib.get("y", "0") or 0.0)
        w = float(el.attrib.get("width", "0") or 0.0)
        h = float(el.attrib.get("height", "0") or 0.0)
        if w <= 0 or h <= 0:
            return False
        pts_u = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
        pts = []
        for (px, py) in pts_u:
            px, py = _apply_mat(M, px, py)
            X, Y = _vb_to_mm(viewbox, width_mm, height_mm, px, py)
            pts.append((X, Y))
        if len(pts) >= 2:
            msp.add_lwpolyline(pts, format="xy", dxfattribs={**dxfattribs, "closed": True})
            return True
        return False
    except Exception:
        return False


def _dxf_add_circle_el(msp, el, viewbox, width_mm, height_mm, M, dxfattribs):
    try:
        cx = float(el.attrib.get("cx", "0") or 0.0)
        cy = float(el.attrib.get("cy", "0") or 0.0)
        r = float(el.attrib.get("r", "0") or 0.0)
        if r <= 0:
            return False

        sx, sy = _mat_scales(M)
        if abs(sx - sy) <= max(1e-6, 0.002 * max(abs(sx), abs(sy))):
            x, y = _apply_mat(M, cx, cy)
            X, Y = _vb_to_mm(viewbox, width_mm, height_mm, x, y)
            vbw = float(viewbox[2])
            scale_u_to_mm = (float(width_mm) / float(vbw)) if float(vbw) else 1.0
            R = abs(r * sx) * scale_u_to_mm
            if R > 0:
                msp.add_circle((X, Y), R, dxfattribs=dxfattribs)
                return True

        return _dxf_add_ellipse_like_poly(msp, cx, cy, r, r, viewbox, width_mm, height_mm, M, dxfattribs)
    except Exception:
        return False


def _dxf_add_ellipse_el_as_arcchain(msp, el, viewbox, width_mm, height_mm, M, dxfattribs, chord_mm=0.15):
    try:
        cx = float(el.attrib.get("cx", "0") or 0.0)
        cy = float(el.attrib.get("cy", "0") or 0.0)
        rx = float(el.attrib.get("rx", "0") or 0.0)
        ry = float(el.attrib.get("ry", "0") or 0.0)
        if rx <= 0 or ry <= 0:
            return False

        steps = max(72, int(math.ceil((2.0 * math.pi * max(rx, ry)) / max(0.05, chord_mm))))
        pts = []
        for i in range(steps + 1):
            t = (2.0 * math.pi * i) / steps
            x = cx + rx * math.cos(t)
            y = cy + ry * math.sin(t)
            x, y = _apply_mat(M, x, y)
            X, Y = _vb_to_mm(viewbox, width_mm, height_mm, x, y)
            pts.append((X, Y))

        _arcfit_poly_to_arcs(msp, pts, dxfattribs, max_err_mm=0.03)
        return True
    except Exception:
        return False


def _dxf_add_ellipse_like_poly(msp, cx, cy, rx, ry, viewbox, width_mm, height_mm, M, dxfattribs):
    try:
        steps = 180
        pts = []
        for i in range(steps + 1):
            t = (2.0 * math.pi * i) / steps
            x = cx + rx * math.cos(t)
            y = cy + ry * math.sin(t)
            x, y = _apply_mat(M, x, y)
            X, Y = _vb_to_mm(viewbox, width_mm, height_mm, x, y)
            if not pts or abs(X - pts[-1][0]) > 1e-6 or abs(Y - pts[-1][1]) > 1e-6:
                pts.append((X, Y))
        if len(pts) >= 2:
            msp.add_lwpolyline(pts, format="xy", dxfattribs={**dxfattribs, "closed": True})
            return True
        return False
    except Exception:
        return False


def _mat_scales(M):
    a, b, c, d, e, f = M
    sx = math.hypot(a, b)
    sy = math.hypot(c, d)
    if sx <= 1e-12:
        sx = 0.0
    if sy <= 1e-12:
        sy = 0.0
    return sx, sy


def _dxf_add_arc_from_segment_3pt(msp, seg, viewbox, width_mm, height_mm, M, dxfattribs):
    try:
        z0 = seg.point(0.0)
        z1 = seg.point(0.5)
        z2 = seg.point(1.0)

        def pt_mm(z):
            x, y = float(z.real), float(z.imag)
            x, y = _apply_mat(M, x, y)
            return _vb_to_mm(viewbox, width_mm, height_mm, x, y)

        p0 = pt_mm(z0)
        pm = pt_mm(z1)
        p2 = pt_mm(z2)

        cxcy = _circumcenter(p0, pm, p2)
        if not cxcy:
            return False
        cx, cy = cxcy

        r0 = math.hypot(p0[0] - cx, p0[1] - cy)
        r1 = math.hypot(pm[0] - cx, pm[1] - cy)
        r2 = math.hypot(p2[0] - cx, p2[1] - cy)

        if r0 < 1e-6:
            return False
        if max(abs(r0 - r1), abs(r0 - r2), abs(r1 - r2)) > max(0.03, r0 * 0.0025):
            return False

        a0 = _ang_deg(cx, cy, p0[0], p0[1])
        am = _ang_deg(cx, cy, pm[0], pm[1])
        a2 = _ang_deg(cx, cy, p2[0], p2[1])

        ccw = _is_between_ccw(a0, a2, am)
        if ccw:
            start_angle = a0
            end_angle = a2
        else:
            start_angle = a2
            end_angle = a0

        msp.add_arc((cx, cy), r0, start_angle, end_angle, dxfattribs=dxfattribs)
        return True
    except Exception:
        return False


def _dxf_add_arcchain_from_segment(msp, seg, viewbox, width_mm, height_mm, M, dxfattribs, chord_mm=0.20):
    try:
        try:
            L = seg.length(error=1e-3)
        except Exception:
            L = 0.0

        n = max(24, int(math.ceil(max(L, 10.0) / max(0.05, chord_mm))))
        pts = []
        for i in range(n + 1):
            t = i / n
            z = seg.point(t)
            x, y = float(z.real), float(z.imag)
            x, y = _apply_mat(M, x, y)
            X, Y = _vb_to_mm(viewbox, width_mm, height_mm, x, y)
            if not pts or abs(X - pts[-1][0]) > 1e-6 or abs(Y - pts[-1][1]) > 1e-6:
                pts.append((X, Y))

        if len(pts) < 3:
            if len(pts) == 2:
                msp.add_line(pts[0], pts[1], dxfattribs=dxfattribs)
            return

        _arcfit_poly_to_arcs(msp, pts, dxfattribs, max_err_mm=0.03)
    except Exception:
        return


def _arcfit_poly_to_arcs(msp, pts, dxfattribs, max_err_mm=0.03):
    if len(pts) < 3:
        return
    i = 0
    n = len(pts)
    while i < n - 1:
        if i + 2 >= n:
            msp.add_line(pts[i], pts[i + 1], dxfattribs=dxfattribs)
            break

        best_j = None
        best_arc = None
        max_j = min(n - 1, i + 80)

        for j in range(i + 2, max_j + 1):
            p0 = pts[i]
            pm = pts[(i + j) // 2]
            p2 = pts[j]
            c = _circumcenter(p0, pm, p2)
            if not c:
                continue
            cx, cy = c
            r = math.hypot(p0[0] - cx, p0[1] - cy)
            if r < 1e-6:
                continue

            ok = True
            for k in range(i + 1, j):
                pk = pts[k]
                dr = abs(math.hypot(pk[0] - cx, pk[1] - cy) - r)
                if dr > max_err_mm:
                    ok = False
                    break
            if not ok:
                continue

            a0 = _ang_deg(cx, cy, p0[0], p0[1])
            am = _ang_deg(cx, cy, pm[0], pm[1])
            a2 = _ang_deg(cx, cy, p2[0], p2[1])
            ccw = _is_between_ccw(a0, a2, am)
            if ccw:
                sa, ea = a0, a2
            else:
                sa, ea = a2, a0

            best_j = j
            best_arc = (cx, cy, r, sa, ea)

        if best_j is not None and best_arc is not None:
            cx, cy, r, sa, ea = best_arc
            msp.add_arc((cx, cy), r, sa, ea, dxfattribs=dxfattribs)
            i = best_j
        else:
            msp.add_line(pts[i], pts[i + 1], dxfattribs=dxfattribs)
            i += 1


def _circumcenter(p1, p2, p3):
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    d = 2.0 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        return None
    x1s = x1 * x1 + y1 * y1
    x2s = x2 * x2 + y2 * y2
    x3s = x3 * x3 + y3 * y3
    ux = (x1s * (y2 - y3) + x2s * (y3 - y1) + x3s * (y1 - y2)) / d
    uy = (x1s * (x3 - x2) + x2s * (x1 - x3) + x3s * (x2 - x1)) / d
    return (ux, uy)


def _ang_deg(cx, cy, px, py):
    return (math.degrees(math.atan2(py - cy, px - cx)) + 360.0) % 360.0


def _is_between_ccw(a0, a1, am):
    a0 = a0 % 360.0
    a1 = a1 % 360.0
    am = am % 360.0
    if a0 <= a1:
        return a0 <= am <= a1
    return (am >= a0) or (am <= a1)


def _ensure_dxf_layers(doc):
    for lname, color in DXF_LAYER_COLOR.items():
        up = lname.upper()
        if up not in doc.layers:
            doc.layers.add(up, color=color)


def _get_svg_mm_size(svg: str):
    m = re.search(r'viewBox\s*=\s*"([^"]+)"', svg or "", flags=re.I)
    if m:
        parts = [p for p in re.split(r"[ ,]+", m.group(1).strip()) if p]
        if len(parts) == 4:
            try:
                return float(parts[2]), float(parts[3])
            except Exception:
                pass
    return 100.0, 100.0


def _is_in_defs(el):
    cur = el
    try:
        parent = cur.getparent()
    except Exception:
        parent = None
    while parent is not None:
        tag = getattr(parent, "tag", "")
        local = tag.split("}", 1)[-1].lower()
        if local == "defs":
            return True
        try:
            parent = parent.getparent()
        except Exception:
            parent = None
    return False


def _nearest_layer(el):
    cur = el
    while cur is not None:
        dl = cur.attrib.get("data-layer")
        if dl:
            return dl.strip().lower()
        try:
            cur = cur.getparent()
        except Exception:
            cur = None
    return ""


def _parse_transform_attr(s: str):
    M = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    if not s:
        return M
    for fn, args in re.findall(r"([a-zA-Z]+)\s*\(([^)]*)\)", s):
        fnl = fn.strip().lower()
        nums = [float(x) for x in re.split(r"[ ,]+", args.strip()) if x]
        if fnl == "matrix" and len(nums) == 6:
            m = tuple(nums)
        elif fnl == "translate":
            tx = nums[0] if nums else 0.0
            ty = nums[1] if len(nums) > 1 else 0.0
            m = (1.0, 0.0, 0.0, 1.0, tx, ty)
        elif fnl == "scale":
            sx = nums[0] if nums else 1.0
            sy = nums[1] if len(nums) > 1 else sx
            m = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        else:
            continue
        M = _mul(M, m)
    return M


def _collect_ancestors_transform(el):
    M = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    cur = el
    while True:
        try:
            parent = cur.getparent()
        except Exception:
            parent = None
        if parent is None:
            break
        t = _parse_transform_attr(parent.attrib.get("transform", ""))
        M = _mul(t, M)
        cur = parent
    return M


def _mul(A, B):
    a, b, c, d, e, f = A
    a2, b2, c2, d2, e2, f2 = B
    return (
        a * a2 + c * b2,
        b * a2 + d * b2,
        a * c2 + c * d2,
        b * c2 + d * d2,
        a * e2 + c * f2 + e,
        b * e2 + d * f2 + f,
    )


def _apply_mat(M, x, y):
    a, b, c, d, e, f = M
    return (a * x + c * y + e, b * x + d * y + f)


def _vb_to_mm(viewbox, width_mm, height_mm, x, y):
    minx, miny, vbw, vbh = viewbox
    sx = float(width_mm) / float(vbw) if float(vbw) else 1.0
    sy = float(height_mm) / float(vbh) if float(vbh) else 1.0
    X = (x - minx) * sx
    Ysvg = (y - miny) * sy
    Y = float(height_mm) - Ysvg
    return (X, Y)
