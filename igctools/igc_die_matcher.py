import math
import frappe
import xml.etree.ElementTree as ET


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

    unit = unit.strip().lower()
    if unit in ("in", "inch", "inches"):
        return val * 25.4
    if unit in ("mm", "millimeter", "millimeters", ""):
        return val
    return val


def _parse_svg_bbox_from_root(root):
    width = None
    height = None

    viewbox = root.get("viewBox") or root.get("viewbox")
    if viewbox:
        parts = viewbox.replace(",", " ").split()
        if len(parts) == 4:
            try:
                width = float(parts[2])
                height = float(parts[3])
            except Exception:
                width = None
                height = None

    if width is None or height is None:
        w_attr = root.get("width")
        h_attr = root.get("height")
        w_val = _parse_dim_attr(w_attr) if w_attr else None
        h_val = _parse_dim_attr(h_attr) if h_attr else None
        width = width if width is not None else w_val
        height = height if height is not None else h_val

    return width, height


def _matrix_multiply(m1, m2):
    a1, c1, e1, b1, d1, f1 = m1
    a2, c2, e2, b2, d2, f2 = m2
    a = a1 * a2 + c1 * b2
    c = a1 * c2 + c1 * d2
    e = a1 * e2 + c1 * f2 + e1
    b = b1 * a2 + d1 * b2
    d = b1 * c2 + d1 * d2
    f = b1 * e2 + d1 * f2 + f1
    return [a, c, e, b, d, f]


def _parse_transform(transform_str):
    if not transform_str:
        return [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]

    s = transform_str.strip()
    if not s:
        return [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]

    current = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]

    i = 0
    length = len(s)
    while i < length:
        ch = s[i]
        if ch.isalpha():
            start = i
            while i < length and s[i] != "(":
                i += 1
            name = s[start:i].strip().lower()
            if i >= length or s[i] != "(":
                break
            i += 1
            start_params = i
            depth = 1
            while i < length and depth > 0:
                if s[i] == "(":
                    depth += 1
                elif s[i] == ")":
                    depth -= 1
                i += 1
            params_str = s[start_params : i - 1]
            parts = params_str.replace(",", " ").split()
            nums = []
            for p in parts:
                try:
                    nums.append(float(p))
                except Exception:
                    pass

            local = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
            if name == "translate":
                tx = nums[0] if len(nums) >= 1 else 0.0
                ty = nums[1] if len(nums) >= 2 else 0.0
                local = [1.0, 0.0, tx, 0.0, 1.0, ty]
            elif name == "scale":
                sx = nums[0] if len(nums) >= 1 else 1.0
                sy = nums[1] if len(nums) >= 2 else sx
                local = [sx, 0.0, 0.0, 0.0, sy, 0.0]

            current = _matrix_multiply(current, local)
        else:
            i += 1

    return current


def _apply_matrix(m, x, y):
    a, c, e, b, d, f = m
    x_new = a * x + c * y + e
    y_new = b * x + d * y + f
    return x_new, y_new


def _extract_segments(root):
    segments = []

    def walk(node, parent_matrix):
        transform_str = node.get("transform")
        local_matrix = _parse_transform(transform_str) if transform_str else [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        if parent_matrix is not None:
            matrix = _matrix_multiply(parent_matrix, local_matrix)
        else:
            matrix = local_matrix

        tag = node.tag
        if "}" in tag:
            tag = tag.split("}", 1)[1]

        if tag == "line":
            try:
                x1 = float(node.get("x1", "0") or "0")
                y1 = float(node.get("y1", "0") or "0")
                x2 = float(node.get("x2", "0") or "0")
                y2 = float(node.get("y2", "0") or "0")
            except Exception:
                x1 = y1 = x2 = y2 = 0.0

            x1t, y1t = _apply_matrix(matrix, x1, y1)
            x2t, y2t = _apply_matrix(matrix, x2, y2)

            style_ev = (node.get("ev-style") or "").lower()
            seg_type = "other"
            if "cut" in style_ev:
                seg_type = "cut"
            elif "creas" in style_ev:
                seg_type = "crease"

            dx = x2t - x1t
            dy = y2t - y1t
            length = math.hypot(dx, dy)

            segments.append({
                "x1": x1t,
                "y1": y1t,
                "x2": x2t,
                "y2": y2t,
                "length": length,
                "type": seg_type
            })

        for child in node:
            walk(child, matrix)

    walk(root, None)
    return segments


def _compute_signature(segments, width, height):
    vertical_positions = []
    horizontal_positions = []

    for seg in segments:
        length = seg["length"]
        if length <= 0.1:
            continue

        x1 = seg["x1"]
        x2 = seg["x2"]
        y1 = seg["y1"]
        y2 = seg["y2"]

        dx = abs(x2 - x1)
        dy = abs(y2 - y1)

        if dx < 0.05 and length > 5.0:
            x_mid = 0.5 * (x1 + x2)
            vertical_positions.append(x_mid)
        elif dy < 0.05 and length > 5.0:
            y_mid = 0.5 * (y1 + y2)
            horizontal_positions.append(y_mid)

    vertical_positions.sort()
    horizontal_positions.sort()

    def merge_positions(pos_list):
        if not pos_list:
            return []
        merged = [pos_list[0]]
        tol_merge = 0.5
        for p in pos_list[1:]:
            if abs(p - merged[-1]) <= tol_merge:
                merged[-1] = 0.5 * (merged[-1] + p)
            else:
                merged.append(p)
        return merged

    v_pos = merge_positions(vertical_positions)
    h_pos = merge_positions(horizontal_positions)

    def diffs_from_positions(pos_list):
        diffs = []
        for i in range(len(pos_list) - 1):
            d = pos_list[i + 1] - pos_list[i]
            if d > 0.2:
                diffs.append(d)
        return diffs

    dx_list = diffs_from_positions(v_pos)
    dy_list = diffs_from_positions(h_pos)

    if width and width > 0:
        dx_list = [d / width for d in dx_list]
    if height and height > 0:
        dy_list = [d / height for d in dy_list]

    max_panels = 10
    if len(dx_list) > max_panels:
        dx_list = dx_list[:max_panels]
    if len(dy_list) > max_panels:
        dy_list = dy_list[:max_panels]

    return dx_list, dy_list


def analyze_die_svg(svg_text):
    try:
        root = ET.fromstring(svg_text)
    except Exception:
        return {
            "width": None,
            "height": None,
            "dx_list": [],
            "dy_list": []
        }

    width, height = _parse_svg_bbox_from_root(root)
    segments = _extract_segments(root)

    if (width is None or height is None) and segments:
        xs = []
        ys = []
        for seg in segments:
            xs.append(seg["x1"])
            xs.append(seg["x2"])
            ys.append(seg["y1"])
            ys.append(seg["y2"])
        if xs and ys:
            min_x = min(xs)
            max_x = max(xs)
            min_y = min(ys)
            max_y = max(ys)
            if width is None:
                width = max_x - min_x
            if height is None:
                height = max_y - min_y

    dx_list, dy_list = _compute_signature(segments, width or 0.0, height or 0.0)

    return {
        "width": width,
        "height": height,
        "dx_list": dx_list,
        "dy_list": dy_list
    }


def compare_die_features(cliente, troq, tolerance_mm):
    cw = cliente.get("width")
    ch = cliente.get("height")
    tw = troq.get("width")
    th = troq.get("height")

    if not cw or not ch or not tw or not th:
        return None, False

    dw1 = abs(cw - tw)
    dh1 = abs(ch - th)

    dw2 = abs(cw - th)
    dh2 = abs(ch - tw)

    best_dw = dw1
    best_dh = dh1
    rotated = False

    if dw2 + dh2 < dw1 + dh1:
        best_dw = dw2
        best_dh = dh2
        rotated = True

    tol = tolerance_mm if tolerance_mm is not None else 3.0

    if best_dw > tol or best_dh > tol:
        return None, False

    c_dx = cliente.get("dx_list") or []
    c_dy = cliente.get("dy_list") or []
    t_dx = troq.get("dx_list") or []
    t_dy = troq.get("dy_list") or []

    def signature_distance(a_list, b_list):
        if not a_list and not b_list:
            return 0.0
        n = min(len(a_list), len(b_list))
        dist = 0.0
        for i in range(n):
            dist += abs(a_list[i] - b_list[i])
        penalty_per_missing = 0.5
        if len(a_list) > n:
            dist += (len(a_list) - n) * penalty_per_missing
        if len(b_list) > n:
            dist += (len(b_list) - n) * penalty_per_missing
        return dist

    shape_dx = signature_distance(c_dx, t_dx)
    shape_dy = signature_distance(c_dy, t_dy)
    shape_score = shape_dx + shape_dy

    dim_score = best_dw + best_dh
    total_score = dim_score + shape_score * 10.0

    return {
        "delta_w": best_dw,
        "delta_h": best_dh,
        "rotated": rotated,
        "shape_score": shape_score,
        "total_score": total_score
    }, True


@frappe.whitelist()
def find_similar_dies_from_svg(svg_text, tolerance_mm=3.0, max_results=30):
    if not svg_text:
        return []

    try:
        tol = float(tolerance_mm)
    except Exception:
        tol = 3.0

    try:
        max_res = int(max_results)
    except Exception:
        max_res = 30

    cliente = analyze_die_svg(svg_text)
    cw = cliente.get("width")
    ch = cliente.get("height")

    if not cw or not ch:
        return []

    troqueles = frappe.get_all(
        "Troquel",
        filters={"svg_plano_mecanico_individual": ["is", "set"]},
        fields=["name", "svg_plano_mecanico_individual"],
        limit=500
    )

    candidatos = []

    for t in troqueles:
        svg_t = (t.get("svg_plano_mecanico_individual") or "").trim()
        if not svg_t:
            continue

        troq = analyze_die_svg(svg_t)
        cmp_res, ok = compare_die_features(cliente, troq, tol)
        if not ok:
            continue

        candidatos.append({
            "name": t["name"],
            "score": cmp_res["total_score"],
            "delta_w": cmp_res["delta_w"],
            "delta_h": cmp_res["delta_h"],
            "rotated": cmp_res["rotated"],
            "shape_score": cmp_res["shape_score"],
            "cliente_width": cw,
            "cliente_height": ch,
            "troquel_width": troq.get("width"),
            "troquel_height": troq.get("height")
        })

    candidatos.sort(key=lambda c: c["score"])

    out = []
    count = 0
    for c in candidatos:
        if count >= max_res:
            break
        out.append(c)
        count += 1

    return out
