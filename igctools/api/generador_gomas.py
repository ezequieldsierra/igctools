import math
import frappe
from xml.etree import ElementTree as ET

def _local_name(tag):
    if not tag:
        return ""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag

def _find_first(root, pred):
    for el in root.iter():
        if pred(el):
            return el
    return None

def _find_master_group(svg_root):
    return _find_first(svg_root, lambda el: _local_name(el.tag) == "g" and (el.attrib.get("id", "").startswith("gt_master_svg_")))

def _find_fit_group(master_g):
    for el in master_g.iter():
        if _local_name(el.tag) == "g" and el.attrib.get("id") == "gt_fit_group":
            return el
    return master_g

def _remove_existing_rubber(fit_g):
    gone = []
    for el in list(fit_g):
        if _local_name(el.tag) == "g" and el.attrib.get("id") == "gg_rubber_group":
            gone.append(el)
    for el in gone:
        fit_g.remove(el)

def _float(v, default=None):
    try:
        x = float(str(v).strip())
        if math.isfinite(x):
            return x
        return default
    except Exception:
        return default

def _parse_points(points_str):
    s = (points_str or "").strip()
    if not s:
        return []
    parts = [p for p in s.replace(",", " ").split() if p]
    pts = []
    i = 0
    while i + 1 < len(parts):
        x = _float(parts[i], None)
        y = _float(parts[i + 1], None)
        if x is not None and y is not None:
            pts.append((x, y))
        i += 2
    return pts

def _sample_svg_path_points(d, step_mm=1.0):
    d = (d or "").strip()
    if not d:
        return []
    try:
        from svgpathtools import parse_path
    except Exception:
        return []

    try:
        path = parse_path(d)
    except Exception:
        return []

    try:
        L = float(path.length())
    except Exception:
        return []

    if not math.isfinite(L) or L <= 1e-9:
        return []

    n = max(2, int(math.ceil(L / max(0.2, float(step_mm or 1.0)))))
    pts = []
    for i in range(n + 1):
        t = (i / float(n))
        try:
            p = path.point(t)
        except Exception:
            continue
        try:
            x = float(p.real)
            y = float(p.imag)
        except Exception:
            continue
        if math.isfinite(x) and math.isfinite(y):
            pts.append((x, y))

    out = []
    last = None
    for x, y in pts:
        if last is None:
            out.append((x, y))
            last = (x, y)
            continue
        if (x - last[0]) ** 2 + (y - last[1]) ** 2 > 1e-10:
            out.append((x, y))
            last = (x, y)
    return out

def _shapely_union_lines(line_geoms):
    from shapely.ops import unary_union
    if not line_geoms:
        return None
    try:
        return unary_union(line_geoms)
    except Exception:
        g = None
        for it in line_geoms:
            g = it if g is None else g.union(it)
        return g

def _polygon_to_svg_path(poly, simplify_mm=0.0):
    if simplify_mm and simplify_mm > 0:
        try:
            poly = poly.simplify(float(simplify_mm), preserve_topology=True)
        except Exception:
            pass

    def ring_to_cmds(coords):
        coords = list(coords)
        if not coords:
            return ""
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        d = []
        x0, y0 = coords[0]
        d.append(f"M {x0:.6f} {y0:.6f}")
        for x, y in coords[1:]:
            d.append(f"L {x:.6f} {y:.6f}")
        d.append("Z")
        return " ".join(d)

    d = ring_to_cmds(poly.exterior.coords)
    for hole in poly.interiors:
        d += " " + ring_to_cmds(hole.coords)
    return d.strip()

def _geom_to_paths(geom, simplify_mm=0.0):
    from shapely.geometry import GeometryCollection
    if geom is None:
        return []
    gt = geom.geom_type
    if gt == "Polygon":
        return [_polygon_to_svg_path(geom, simplify_mm=simplify_mm)]
    if gt == "MultiPolygon":
        return [_polygon_to_svg_path(p, simplify_mm=simplify_mm) for p in geom.geoms]
    if gt == "GeometryCollection":
        out = []
        for g in geom.geoms:
            out.extend(_geom_to_paths(g, simplify_mm=simplify_mm))
        return out
    return []

def _extract_cut_lines(fit_g, step_mm=1.0):
    try:
        from shapely.geometry import LineString
    except Exception as e:
        raise RuntimeError(f"Shapely no disponible: {type(e).__name__} {repr(e)}")

    lines = []
    step_mm = float(step_mm or 1.0)

    for el in fit_g.iter():
        if _local_name(el.tag) != "g":
            continue
        if el.attrib.get("data-layer") != "Cut":
            continue

        for ch in list(el):
            tag = _local_name(ch.tag)

            if tag == "line":
                x1 = _float(ch.attrib.get("x1"), None)
                y1 = _float(ch.attrib.get("y1"), None)
                x2 = _float(ch.attrib.get("x2"), None)
                y2 = _float(ch.attrib.get("y2"), None)
                if x1 is None or y1 is None or x2 is None or y2 is None:
                    continue
                if abs(x2 - x1) + abs(y2 - y1) < 1e-9:
                    continue
                lines.append(LineString([(x1, y1), (x2, y2)]))
                continue

            if tag in ("polyline", "polygon"):
                pts = _parse_points(ch.attrib.get("points", ""))
                if len(pts) >= 2:
                    try:
                        lines.append(LineString(pts))
                    except Exception:
                        pass
                    if tag == "polygon" and pts[0] != pts[-1]:
                        try:
                            lines.append(LineString([pts[-1], pts[0]]))
                        except Exception:
                            pass
                continue

            if tag == "path":
                d = (ch.attrib.get("d") or "").strip()
                if not d:
                    continue
                pts = _sample_svg_path_points(d, step_mm=step_mm)
                if len(pts) >= 2:
                    try:
                        lines.append(LineString(pts))
                    except Exception:
                        continue
                continue

    return lines

@frappe.whitelist()
def generar_svg_gomas(tablero_de_troquel, band_width, gap, simplify_mm=0.25, sample_step_mm=1.0, fill="#1f5193", opacity=0.55):
    tablero = (tablero_de_troquel or "").strip()
    if not tablero:
        return {"ok": False, "error": "Falta tablero_de_troquel"}

    try:
        band = float(band_width or 0)
    except Exception:
        band = 0.0
    try:
        gp = float(gap or 0)
    except Exception:
        gp = 0.0

    if band <= 0:
        return {"ok": False, "error": "band_width debe ser > 0"}
    if gp < 0:
        return {"ok": False, "error": "gap no puede ser negativo"}

    svg_raw = frappe.db.get_value("Tablero de Troquel", tablero, "svg_montaje")
    svg_raw = (svg_raw or "").strip()
    if "<svg" not in svg_raw:
        return {"ok": False, "error": "svg_montaje no es válido"}

    try:
        root = ET.fromstring(svg_raw)
    except Exception as e:
        return {"ok": False, "error": f"No pude parsear el SVG: {type(e).__name__} {repr(e)}"}

    master = _find_master_group(root)
    if master is None:
        return {"ok": False, "error": "No encontré gt_master_svg_* en el SVG"}

    fit = _find_fit_group(master)
    _remove_existing_rubber(fit)

    try:
        line_geoms = _extract_cut_lines(fit, step_mm=float(sample_step_mm or 1.0))
    except Exception as e:
        return {"ok": False, "error": f"Error leyendo Cut: {type(e).__name__} {repr(e)}"}

    if not line_geoms:
        g = ET.Element("g", {"id": "gg_rubber_group", "data-layer": "Rubber"})
        fit.append(g)
        return {"ok": True, "svg": ET.tostring(root, encoding="unicode")}

    try:
        from shapely.ops import unary_union
    except Exception as e:
        return {"ok": False, "error": f"Shapely no disponible (ops): {type(e).__name__} {repr(e)}"}

    cut_union = _shapely_union_lines(line_geoms)
    if cut_union is None:
        return {"ok": False, "error": "No pude unir geometría de Cut"}

    try:
        outer = cut_union.buffer(gp + band, cap_style=1, join_style=1)
        inner = cut_union.buffer(gp, cap_style=1, join_style=1)
        rubber = outer.difference(inner)
        try:
            rubber = unary_union(rubber)
        except Exception:
            pass
    except Exception as e:
        return {"ok": False, "error": f"Error haciendo buffers/difference: {type(e).__name__} {repr(e)}"}

    try:
        paths = _geom_to_paths(rubber, simplify_mm=float(simplify_mm or 0))
    except Exception as e:
        return {"ok": False, "error": f"Error convirtiendo a SVG: {type(e).__name__} {repr(e)}"}

    g = ET.Element("g", {
        "id": "gg_rubber_group",
        "data-layer": "Rubber",
        "fill": str(fill or "#1f5193"),
        "fill-rule": "evenodd",
        "opacity": str(opacity if opacity is not None else 0.55),
        "stroke": "none"
    })

    for d in paths:
        if not d:
            continue
        p = ET.Element("path", {"d": d})
        g.append(p)

    fit.append(g)

    return {"ok": True, "svg": ET.tostring(root, encoding="unicode")}
