import math
import time
import frappe
from xml.etree import ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

def _q(tag):
    return "{%s}%s" % (SVG_NS, tag)

def _local_name(tag):
    if not tag:
        return ""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag

def _float(v, default=None):
    try:
        x = float(str(v).strip())
        if math.isfinite(x):
            return x
        return default
    except Exception:
        return default

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

def _find_nest_group(svg_root):
    return _find_first(svg_root, lambda el: _local_name(el.tag) == "g" and (el.attrib.get("id", "").startswith("gt_nest_")))

def _find_parent(root, child):
    for el in root.iter():
        for ch in list(el):
            if ch is child:
                return el
    return None

def _remove_existing_rubber_anywhere(svg_root):
    gone = []
    for el in svg_root.iter():
        if _local_name(el.tag) == "g" and el.attrib.get("id") == "gg_rubber_group":
            gone.append(el)
    for g in gone:
        parent = _find_parent(svg_root, g)
        if parent is not None:
            parent.remove(g)

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

def _extract_lines_by_layer(fit_g, layer_name, step_mm=1.0):
    try:
        from shapely.geometry import LineString
    except Exception as e:
        raise RuntimeError(f"Shapely no disponible: {type(e).__name__} {repr(e)}")

    lines = []
    step_mm = float(step_mm or 1.0)

    for el in fit_g.iter():
        if _local_name(el.tag) != "g":
            continue
        if el.attrib.get("data-layer") != layer_name:
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

def _mat_mul(m1, m2):
    a1,b1,c1,d1,e1,f1 = m1
    a2,b2,c2,d2,e2,f2 = m2
    return (
        a1*a2 + c1*b2,
        b1*a2 + d1*b2,
        a1*c2 + c1*d2,
        b1*c2 + d1*d2,
        a1*e2 + c1*f2 + e1,
        b1*e2 + d1*f2 + f1
    )

def _mat_translate(tx, ty):
    return (1,0,0,1,tx,ty)

def _mat_scale(sx, sy):
    return (sx,0,0,sy,0,0)

def _mat_rotate(deg):
    r = math.radians(deg)
    cs = math.cos(r)
    sn = math.sin(r)
    return (cs, sn, -sn, cs, 0, 0)

def _parse_transform(transform_str):
    s = (transform_str or "").strip()
    if not s:
        return (1,0,0,1,0,0)

    m = (1,0,0,1,0,0)
    i = 0
    n = len(s)

    while i < n:
        while i < n and s[i].isspace():
            i += 1
        if i >= n:
            break

        j = i
        while j < n and s[j].isalpha():
            j += 1
        fn = s[i:j].lower()
        i = j

        while i < n and s[i].isspace():
            i += 1
        if i >= n or s[i] != "(":
            break
        i += 1

        k = i
        depth = 1
        while k < n and depth > 0:
            if s[k] == "(":
                depth += 1
            elif s[k] == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1

        args_str = s[i:k]
        i = k + 1

        args = []
        for p in args_str.replace(",", " ").split():
            v = _float(p, None)
            if v is not None:
                args.append(v)

        if fn == "translate":
            tx = args[0] if len(args) > 0 else 0.0
            ty = args[1] if len(args) > 1 else 0.0
            m = _mat_mul(m, _mat_translate(tx, ty))
        elif fn == "scale":
            sx = args[0] if len(args) > 0 else 1.0
            sy = args[1] if len(args) > 1 else sx
            m = _mat_mul(m, _mat_scale(sx, sy))
        elif fn == "rotate":
            ang = args[0] if len(args) > 0 else 0.0
            if len(args) >= 3:
                cx, cy = args[1], args[2]
                m = _mat_mul(m, _mat_translate(cx, cy))
                m = _mat_mul(m, _mat_rotate(ang))
                m = _mat_mul(m, _mat_translate(-cx, -cy))
            else:
                m = _mat_mul(m, _mat_rotate(ang))
        elif fn == "matrix" and len(args) >= 6:
            m = _mat_mul(m, (args[0],args[1],args[2],args[3],args[4],args[5]))

    return m

def _apply_mat_to_geoms(geoms, m):
    from shapely.affinity import affine_transform
    a,b,c,d,e,f = m
    return [affine_transform(g, [a,c,b,d,e,f]) for g in geoms]

def _union_lines(line_geoms):
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

def _parse_viewbox(vb):
    vb = str(vb or "").strip()
    if not vb:
        return None
    parts = [p for p in vb.replace(",", " ").split() if p]
    if len(parts) != 4:
        return None
    x = _float(parts[0], None)
    y = _float(parts[1], None)
    w = _float(parts[2], None)
    h = _float(parts[3], None)
    if x is None or y is None or w is None or h is None:
        return None
    return (x, y, w, h)

def _set_viewbox_and_size(root_svg, x, y, w, h):
    root_svg.set("viewBox", f"{x:.6f} {y:.6f} {w:.6f} {h:.6f}")
    root_svg.set("width", f"{w:.6f}mm")
    root_svg.set("height", f"{h:.6f}mm")

def _geom_to_paths(geom, simplify_mm=0.0):
    from shapely.geometry import GeometryCollection
    if geom is None:
        return []
    if simplify_mm and simplify_mm > 0:
        try:
            geom = geom.simplify(float(simplify_mm), preserve_topology=True)
        except Exception:
            pass
    gt = geom.geom_type
    if gt == "Polygon":
        return [_poly_to_path(geom)]
    if gt == "MultiPolygon":
        return [_poly_to_path(p) for p in geom.geoms]
    if gt == "GeometryCollection":
        out = []
        for g in geom.geoms:
            out.extend(_geom_to_paths(g, simplify_mm=0.0))
        return out
    return []

def _poly_to_path(poly):
    def ring(coords):
        coords = list(coords)
        if not coords:
            return ""
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        d = []
        x0,y0 = coords[0]
        d.append(f"M {x0:.6f} {y0:.6f}")
        for x,y in coords[1:]:
            d.append(f"L {x:.6f} {y:.6f}")
        d.append("Z")
        return " ".join(d)
    d = ring(poly.exterior.coords)
    for hole in poly.interiors:
        d += " " + ring(hole.coords)
    return d.strip()

@frappe.whitelist()
def generar_svg_gomas(tablero_de_troquel, band_width, gap, simplify_mm=0.25, sample_step_mm=1.0, fill="#1f5193", opacity=0.55, avoid_crease_mm=1.0):
    t0 = time.time()

    tablero = (tablero_de_troquel or "").strip()
    if not tablero:
        return {"ok": False, "error": "Falta tablero_de_troquel"}

    band = _float(band_width, 0.0) or 0.0
    gp = _float(gap, 0.0) or 0.0
    avc = _float(avoid_crease_mm, 0.0) or 0.0

    if band <= 0:
        return {"ok": False, "error": "band_width debe ser > 0"}
    if gp < 0:
        return {"ok": False, "error": "gap no puede ser negativo"}
    if avc < 0:
        avc = 0.0

    svg_raw = frappe.db.get_value("Tablero de Troquel", tablero, "svg_montaje")
    svg_raw = (svg_raw or "").strip()
    if "<svg" not in svg_raw:
        return {"ok": False, "error": "svg_montaje no es válido"}

    try:
        root = ET.fromstring(svg_raw)
    except Exception as e:
        return {"ok": False, "error": f"No pude parsear el SVG: {type(e).__name__} {repr(e)}"}

    _remove_existing_rubber_anywhere(root)

    master = _find_master_group(root)
    if master is None:
        return {"ok": False, "error": "No encontré gt_master_svg_* en el SVG"}

    fit = _find_fit_group(master)

    try:
        cut_lines_master = _extract_lines_by_layer(fit, "Cut", step_mm=float(sample_step_mm or 1.0))
        crease_lines_master = _extract_lines_by_layer(fit, "Crease", step_mm=float(sample_step_mm or 1.0))
    except Exception as e:
        return {"ok": False, "error": f"Error leyendo layers: {type(e).__name__} {repr(e)}"}

    if not cut_lines_master:
        out_svg = ET.tostring(root, encoding="unicode")
        return {"ok": True, "svg": out_svg, "debug": {"instances": 0, "polys": 0, "svg_len": len(out_svg), "ms": int((time.time()-t0)*1000)}}

    vb0 = _parse_viewbox(root.attrib.get("viewBox"))
    if vb0 is None:
        out_svg = ET.tostring(root, encoding="unicode")
        return {"ok": True, "svg": out_svg, "debug": {"instances": 0, "polys": 0, "svg_len": len(out_svg), "ms": int((time.time()-t0)*1000)}}

    from shapely.ops import unary_union
    from shapely.geometry import box

    nest = _find_nest_group(root)

    instances = []
    if nest is not None:
        for ch in list(nest):
            if _local_name(ch.tag) != "use":
                continue
            m = _parse_transform(ch.attrib.get("transform", "") or "")
            instances.append(m)

    if not instances:
        instances = [(1,0,0,1,0,0)]

    cap_style = 2
    join_style = 2

    rubber_all = []
    region_all = []

    for m in instances:
        cut_inst = _apply_mat_to_geoms(cut_lines_master, m)
        cut_u = _union_lines(cut_inst)
        if cut_u is None or cut_u.is_empty:
            continue

        crease_u = None
        if crease_lines_master:
            crease_inst = _apply_mat_to_geoms(crease_lines_master, m)
            crease_u = _union_lines(crease_inst)

        try:
            outer = cut_u.buffer(gp + band, cap_style=cap_style, join_style=join_style)
            inner = cut_u.buffer(gp, cap_style=cap_style, join_style=join_style)
            rb = outer.difference(inner)
            rb = unary_union(rb)
        except Exception:
            continue

        if rb.is_empty:
            continue

        if crease_u is not None and (not crease_u.is_empty) and avc > 0:
            try:
                forbid = crease_u.buffer(avc, cap_style=cap_style, join_style=join_style)
                rb = rb.difference(forbid)
                rb = unary_union(rb)
            except Exception:
                pass

        if rb.is_empty:
            continue

        try:
            region = cut_u.buffer(gp + band + 0.01, cap_style=cap_style, join_style=join_style)
            region = region.envelope
        except Exception:
            region = None

        if region is not None and (not region.is_empty):
            try:
                rb = rb.intersection(region)
            except Exception:
                pass

        if rb.is_empty:
            continue

        rubber_all.append(rb)
        if region is not None and (not region.is_empty):
            region_all.append(region)

    if not rubber_all:
        out_svg = ET.tostring(root, encoding="unicode")
        return {"ok": True, "svg": out_svg, "debug": {"instances": len(instances), "polys": 0, "svg_len": len(out_svg), "ms": int((time.time()-t0)*1000)}}

    try:
        rb_all = unary_union(rubber_all)
    except Exception:
        rb_all = rubber_all[0]
        for g in rubber_all[1:]:
            rb_all = rb_all.union(g)

    paths = _geom_to_paths(rb_all, simplify_mm=float(simplify_mm or 0))

    g = ET.Element(_q("g"), {
        "id": "gg_rubber_group",
        "data-layer": "Rubber",
        "fill": str(fill or "#1f5193"),
        "fill-rule": "evenodd",
        "opacity": str(opacity if opacity is not None else 0.55),
        "stroke": "none"
    })

    for d in paths:
        if d:
            g.append(ET.Element(_q("path"), {"d": d}))

    root.append(g)

    vx, vy, vw, vh = vb0
    try:
        minx, miny, maxx, maxy = rb_all.bounds
        pad = float(gp + band + 2.0)
        x1 = min(vx, minx - pad)
        y1 = min(vy, miny - pad)
        x2 = max(vx + vw, maxx + pad)
        y2 = max(vy + vh, maxy + pad)
        _set_viewbox_and_size(root, x1, y1, (x2 - x1), (y2 - y1))
    except Exception:
        pass

    out_svg = ET.tostring(root, encoding="unicode")

    debug = {
        "instances": int(len(instances)),
        "cut_master": int(len(cut_lines_master)),
        "crease_master": int(len(crease_lines_master)),
        "polys": int(len(paths)),
        "svg_len": int(len(out_svg)),
        "ms": int((time.time()-t0)*1000),
        "avoid_crease_mm": avc,
        "cap_style": cap_style,
        "join_style": join_style
    }

    return {"ok": True, "svg": out_svg, "debug": debug}
