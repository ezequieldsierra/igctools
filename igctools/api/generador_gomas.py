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

def _remove_existing_rubber_anywhere(svg_root):
    gone = []
    for el in svg_root.iter():
        if _local_name(el.tag) == "g" and el.attrib.get("id") == "gg_rubber_group":
            gone.append(el)
    for g in gone:
        parent = _find_parent(svg_root, g)
        if parent is not None:
            parent.remove(g)

def _find_parent(root, child):
    for el in root.iter():
        for ch in list(el):
            if ch is child:
                return el
    return None

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

def _extract_cut_lines_from_fit(fit_g, step_mm=1.0):
    try:
        from shapely.geometry import LineString
    except Exception as e:
        raise RuntimeError(f"Shapely no disponible: {type(e).__name__} {repr(e)}")

    lines = []
    dbg = {"cut_groups": 0, "line": 0, "poly": 0, "path": 0, "path_sampled": 0}
    step_mm = float(step_mm or 1.0)

    for el in fit_g.iter():
        if _local_name(el.tag) != "g":
            continue
        if el.attrib.get("data-layer") != "Cut":
            continue
        dbg["cut_groups"] += 1

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
                dbg["line"] += 1
                continue

            if tag in ("polyline", "polygon"):
                pts = _parse_points(ch.attrib.get("points", ""))
                if len(pts) >= 2:
                    try:
                        lines.append(LineString(pts))
                        dbg["poly"] += 1
                    except Exception:
                        pass
                    if tag == "polygon" and pts[0] != pts[-1]:
                        try:
                            lines.append(LineString([pts[-1], pts[0]]))
                            dbg["poly"] += 1
                        except Exception:
                            pass
                continue

            if tag == "path":
                d = (ch.attrib.get("d") or "").strip()
                if not d:
                    continue
                dbg["path"] += 1
                pts = _sample_svg_path_points(d, step_mm=step_mm)
                if len(pts) >= 2:
                    dbg["path_sampled"] += 1
                    try:
                        lines.append(LineString(pts))
                    except Exception:
                        continue
                continue

    return lines, dbg

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
        fn = s[i:j]
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

        fn = fn.lower()
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

def _apply_mat_to_lines(lines, m):
    from shapely.affinity import affine_transform
    a,b,c,d,e,f = m
    return [affine_transform(g, [a,c,b,d,e,f]) for g in lines]

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

def _safe_voronoi_cells(points, envelope_poly):
    try:
        from shapely.geometry import MultiPoint
        from shapely.ops import voronoi_diagram
        mp = MultiPoint(points)
        vc = voronoi_diagram(mp, envelope=envelope_poly, edges=False)
        geoms = list(vc.geoms) if hasattr(vc, "geoms") else []
        cells = {}
        for poly in geoms:
            owner = None
            for idx, pt in enumerate(points):
                if poly.contains(pt) or poly.touches(pt):
                    owner = idx
                    break
            if owner is None:
                c = poly.representative_point()
                best_i = 0
                best_d = c.distance(points[0])
                for i in range(1, len(points)):
                    d = c.distance(points[i])
                    if d < best_d:
                        best_d = d
                        best_i = i
                owner = best_i
            cells[owner] = poly if owner not in cells else cells[owner].union(poly)
        return cells
    except Exception:
        return None

@frappe.whitelist()
def generar_svg_gomas(tablero_de_troquel, band_width, gap, simplify_mm=0.25, sample_step_mm=1.0, fill="#1f5193", opacity=0.55):
    t0 = time.time()

    tablero = (tablero_de_troquel or "").strip()
    if not tablero:
        return {"ok": False, "error": "Falta tablero_de_troquel"}

    band = _float(band_width, 0.0) or 0.0
    gp = _float(gap, 0.0) or 0.0

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

    _remove_existing_rubber_anywhere(root)

    master = _find_master_group(root)
    if master is None:
        return {"ok": False, "error": "No encontré gt_master_svg_* en el SVG"}

    fit = _find_fit_group(master)

    try:
        master_lines, dbg = _extract_cut_lines_from_fit(fit, step_mm=float(sample_step_mm or 1.0))
    except Exception as e:
        return {"ok": False, "error": f"Error leyendo Cut: {type(e).__name__} {repr(e)}"}

    if not master_lines:
        out_svg = ET.tostring(root, encoding="unicode")
        return {"ok": True, "svg": out_svg, "debug": {**dbg, "instances": 0, "polys": 0, "svg_len": len(out_svg), "ms": int((time.time()-t0)*1000)}}

    vb0 = _parse_viewbox(root.attrib.get("viewBox"))
    if vb0 is None:
        out_svg = ET.tostring(root, encoding="unicode")
        return {"ok": True, "svg": out_svg, "debug": {**dbg, "instances": 0, "polys": 0, "svg_len": len(out_svg), "ms": int((time.time()-t0)*1000)}}

    from shapely.geometry import box, Point
    from shapely.ops import unary_union

    vx, vy, vw, vh = vb0
    envelope = box(vx, vy, vx+vw, vy+vh)

    nest = _find_nest_group(root)

    instances = []
    if nest is not None:
        for ch in list(nest):
            if _local_name(ch.tag) != "use":
                continue
            tr = ch.attrib.get("transform", "") or ""
            m = _parse_transform(tr)
            instances.append(m)

    if not instances:
        instances = [(1,0,0,1,0,0)]

    rubber_list = []
    centroids = []
    per_inst_dbg = []

    cap_style = 2
    join_style = 2

    for m in instances:
        inst_lines = _apply_mat_to_lines(master_lines, m)
        cut_union = _shapely_union_lines(inst_lines)
        if cut_union is None:
            continue

        try:
            outer = cut_union.buffer(gp + band, cap_style=cap_style, join_style=join_style)
            inner = cut_union.buffer(gp, cap_style=cap_style, join_style=join_style)
            rubber = outer.difference(inner)
            rubber = unary_union(rubber)
        except Exception:
            continue

        if rubber.is_empty:
            continue

        try:
            c = cut_union.centroid
        except Exception:
            b = cut_union.bounds
            c = Point((b[0]+b[2])/2.0, (b[1]+b[3])/2.0)

        centroids.append(c)
        rubber_list.append(rubber)

    if not rubber_list:
        out_svg = ET.tostring(root, encoding="unicode")
        return {"ok": True, "svg": out_svg, "debug": {**dbg, "instances": len(instances), "polys": 0, "svg_len": len(out_svg), "ms": int((time.time()-t0)*1000)}}

    cells = _safe_voronoi_cells(centroids, envelope)

    clipped = []
    if cells:
        for i, rb in enumerate(rubber_list):
            cell = cells.get(i)
            if cell is None:
                clipped.append(rb.intersection(envelope))
            else:
                clipped.append(rb.intersection(cell))
    else:
        clipped = [rb.intersection(envelope) for rb in rubber_list]

    all_rubber = unary_union([g for g in clipped if g is not None and not g.is_empty])

    if all_rubber.is_empty:
        out_svg = ET.tostring(root, encoding="unicode")
        return {"ok": True, "svg": out_svg, "debug": {**dbg, "instances": len(instances), "polys": 0, "svg_len": len(out_svg), "ms": int((time.time()-t0)*1000)}}

    paths = _geom_to_paths(all_rubber, simplify_mm=float(simplify_mm or 0))

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

    try:
        minx, miny, maxx, maxy = all_rubber.bounds
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
        **dbg,
        "instances": int(len(instances)),
        "polys": int(len(paths)),
        "svg_len": int(len(out_svg)),
        "ms": int((time.time()-t0)*1000),
        "voronoi": bool(cells is not None),
        "cap_style": cap_style,
        "join_style": join_style
    }

    return {"ok": True, "svg": out_svg, "debug": debug}
