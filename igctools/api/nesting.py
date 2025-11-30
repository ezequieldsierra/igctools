# igctools/api/nesting.py

import math
import json
import pyclipper
import frappe
import xml.etree.ElementTree as ET

SCALE = 1000  # para convertir a enteros (Clipper trabaja mejor con ints)


def _parse_svg_to_paths(svg_str):
    """
    Convierte un SVG sencillo de troquel en lista de paths:
    paths = [ [(x1,y1), (x2,y2), ...], ... ]  en unidades del SVG.
    Soporta <polygon>, <polyline> y <path> con M/L/H/V/Z.
    Devuelve paths, min_y, max_y para poder escalar a mm luego.
    """
    root = ET.fromstring(svg_str)
    # Por si viene con namespace <svg ...>:
    def tag_name(el):
        return el.tag.rsplit('}', 1)[-1].lower()

    paths = []
    min_y = float('inf')
    max_y = float('-inf')

    def add_path(pts):
        nonlocal min_y, max_y
        if len(pts) < 2:
            return
        paths.append(pts)
        for _, y in pts:
            min_y = min(min_y, y)
            max_y = max(max_y, y)

    # polygon / polyline
    for el in root.iter():
        t = tag_name(el)
        if t in ('polygon', 'polyline'):
            pts_attr = el.get('points') or ''
            if not pts_attr.strip():
                continue
            coords = pts_attr.replace(',', ' ').split()
            pts = []
            it = iter(coords)
            for xs, ys in zip(it, it):
                try:
                    x = float(xs)
                    y = float(ys)
                    pts.append((x, y))
                except ValueError:
                    continue
            add_path(pts)

        elif t == 'path':
            d = el.get('d') or ''
            if not d.strip():
                continue
            pts = []
            x = y = 0.0
            start_x = start_y = 0.0
            cmd = None
            token = ''
            nums = []

            def flush_numbers():
                nonlocal nums
                res = nums
                nums = []
                return res

            def read_number(tok):
                try:
                    return float(tok)
                except Exception:
                    return None

            # parse muy sencillo de M/L/H/V/Z
            for ch in d:
                if ch.isalpha():
                    if token:
                        n = read_number(token)
                        if n is not None:
                            nums.append(n)
                        token = ''
                    if cmd is not None and nums:
                        nums = []
                    cmd = ch
                elif ch in ' ,\t\n\r':
                    if token:
                        n = read_number(token)
                        if n is not None:
                            nums.append(n)
                        token = ''
                else:
                    token += ch
            if token:
                n = read_number(token)
                if n is not None:
                    nums.append(n)

            i = 0
            current_cmd = None
            while i < len(nums) or current_cmd in ('Z', 'z'):
                if current_cmd in ('M', 'm', None):
                    if i + 1 >= len(nums):
                        break
                    nx = nums[i]
                    ny = nums[i + 1]
                    i += 2
                    if cmd == 'm':
                        x += nx
                        y += ny
                    else:
                        x = nx
                        y = ny
                    start_x, start_y = x, y
                    pts.append((x, y))
                    current_cmd = 'L'
                elif current_cmd in ('L', 'l'):
                    if i + 1 >= len(nums):
                        break
                    nx = nums[i]
                    ny = nums[i + 1]
                    i += 2
                    if cmd == 'l':
                        x += nx
                        y += ny
                    else:
                        x = nx
                        y = ny
                    pts.append((x, y))
                else:
                    break

            add_path(pts)

    if min_y == float('inf'):
        min_y = 0.0
        max_y = 0.0

    return paths, min_y, max_y


def _rotate_180(paths, min_y, max_y):
    """
    Rota 180° alrededor del centro del bbox.
    En coordenadas del SVG.
    """
    cy = (min_y + max_y) * 0.5

    rotated = []
    for path in paths:
        new_path = []
        for x, y in path:
            # rotar 180° alrededor de (cx,cy):
            # x' = 2*cx - x, y' = 2*cy - y
            # Como no nos importa X para el stepY, se puede dejar igual,
            # pero hacemos la rotación completa para ser correctos.
            nx = x  # si quieres mantener X igual, deja simplemente x
            ny = 2 * cy - y
            new_path.append((nx, ny))
        rotated.append(new_path)
    return rotated


def _paths_to_int(paths):
    out = []
    for path in paths:
        out.append([(int(round(x * SCALE)), int(round(y * SCALE))) for x, y in path])
    return out


def _has_overlap(paths_a, paths_b_shifted):
    pc = pyclipper.Pyclipper()
    pc.AddPaths(paths_a, pyclipper.PT_SUBJECT, True)
    pc.AddPaths(paths_b_shifted, pyclipper.PT_CLIP, True)
    sol = pc.Execute(pyclipper.CT_INTERSECTION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    return bool(sol)


def _min_dy_units(paths_a, paths_b):
    """
    Busca el mínimo desplazamiento dy (en unidades SVG) tal que
    A ∩ (B + dy) == ∅ usando búsqueda binaria.
    """
    paths_a_int = _paths_to_int(paths_a)
    paths_b_int_base = _paths_to_int(paths_b)

    # cota superior: altura del bbox * 1.5
    min_y = min(y for path in paths_a + paths_b for _, y in path)
    max_y = max(y for path in paths_a + paths_b for _, y in path)
    bbox_h = max_y - min_y
    hi = int(math.ceil(bbox_h * 1.5 * SCALE))
    lo = 0

    def shifted(dy_int):
        shifted_paths = []
        for path in paths_b_int_base:
            shifted_paths.append([(x, y + dy_int) for x, y in path])
        return shifted_paths

    # si ya sin desplazar no hay solape, paso mínimo es 0
    if not _has_overlap(paths_a_int, shifted(0)):
        return 0.0

    while hi - lo > 1:  # resolución a 1/SCALE unidades
        mid = (lo + hi) // 2
        if _has_overlap(paths_a_int, shifted(mid)):
            lo = mid
        else:
            hi = mid

    return hi / SCALE


@frappe.whitelist()
def compute_tetebeche_pitch(svg, height_mm, gap_y_mm=0.0, rotation_deg=0):
    """
    API: calcula el paso Y tête-bêche mínimo en mm.
    svg          -> contenido SVG del troquel
    height_mm    -> alto del troquel en mm (el que ya usas en el cliente)
    gap_y_mm     -> gap vertical adicional deseado
    rotation_deg -> 0 ó 90 (por si quieres trabajar con el troquel rotado)
    """
    paths, min_y, max_y = _parse_svg_to_paths(svg)
    if not paths:
        frappe.throw('No se pudieron extraer paths del SVG')

    # Rotación opcional 90° a nivel de SVG, si la necesitas:
    # (para simplificar, aquí asumimos que el troquel ya viene en la orientación
    #  que corresponde a height_mm; si quieres rotar de verdad habría que
    #  permutar x/y y recalcular bbox).

    # versión normal (arriba)
    up_paths = paths
    # versión invertida (abajo, 180°)
    down_paths = _rotate_180(paths, min_y, max_y)

    # distancia mínima en unidades SVG
    dy_units = _min_dy_units(up_paths, down_paths)

    # factor de escala de unidades SVG a mm usando la altura real
    bbox_h_units = max_y - min_y if (max_y > min_y) else 1.0
    factor_mm_per_unit = float(height_mm) / bbox_h_units

    step_y_mm = dy_units * factor_mm_per_unit + float(gap_y_mm)
    return {'step_y_mm': step_y_mm}
