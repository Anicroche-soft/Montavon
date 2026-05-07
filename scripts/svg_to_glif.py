#!/usr/bin/env python3
"""
svg_to_glif.py

Convertit les SVG de sources/svg/ en fichiers .glif dans
sources/montavon.ufo/glyphs/, puis met à jour contents.plist et lib.plist.

Convention de nommage des SVG : uni0041.svg, uni0061.svg, etc.

Usage :
    python3 scripts/svg_to_glif.py
"""

import re
import plistlib
from pathlib import Path
from xml.etree import ElementTree as ET

# --- Chemins ----------------------------------------------------------------

ROOT       = Path(__file__).parent.parent
SVG_DIR    = ROOT / "sources" / "svg"
UFO_DIR    = ROOT / "sources" / "montavon.ufo"
GLYPHS_DIR = UFO_DIR / "glyphs"

# --- Paramètres de la police ------------------------------------------------
# Toutes les valeurs SVG sont en px, mesurées depuis le BAS du cadre Figma.
# Le script calcule automatiquement les valeurs UFO correspondantes.

UNITS_PER_EM = 2048

# Hauteur totale du cadre SVG dans Figma (px)
SVG_HEIGHT = 24

# Positions des lignes de référence, mesurées depuis le BAS du cadre (px)
SVG_ASCENDER  = 24   # haut des capitales et des minuscules hautes
SVG_BASELINE  = 4    # ligne de base
SVG_DESCENDER = 0    # bas des lettres qui descendent (= bas du cadre)

# --- Valeurs UFO calculées --------------------------------------------------
# L'échelle est définie par SVG_HEIGHT → UNITS_PER_EM.
# La baseline SVG devient le zéro UFO.

_scale    = UNITS_PER_EM / SVG_HEIGHT
ASCENDER  = round((SVG_ASCENDER  - SVG_BASELINE) * _scale)
DESCENDER = round((SVG_DESCENDER - SVG_BASELINE) * _scale)


# --- Transformation des coordonnées -----------------------------------------

def tx(x: float) -> int:
    """Coordonnée X SVG → unité UFO."""
    return round(x * _scale)

def ty(y: float) -> int:
    """Coordonnée Y SVG → unité UFO (inversion d'axe + décalage baseline)."""
    return round((SVG_HEIGHT - y - SVG_BASELINE) * _scale)


# --- Nommage des glyphes ----------------------------------------------------

def unicode_from_filename(filename: str) -> str | None:
    """'uni0041.svg' → 'A'. Retourne None si le nom ne correspond pas."""
    match = re.fullmatch(r"uni([0-9A-Fa-f]{4,6})\.svg", filename)
    if not match:
        return None
    return chr(int(match.group(1), 16))


def glyph_name_from_char(char: str) -> str:
    """'A' → 'A', caractères hors ASCII → 'uni0041'."""
    code = ord(char)
    if 0x0021 <= code <= 0x007E:
        return char
    return f"uni{code:04X}"


def safe_filename(glyph_name: str) -> str:
    """
    Nom de fichier UFO-safe : les majuscules sont suffixées d'un underscore.
    'A' → 'A_.glif'  (évite les conflits sur les FS insensibles à la casse)
    """
    result = ""
    for ch in glyph_name:
        result += ch + "_" if ch.isupper() else ch
    return result + ".glif"


# --- Parsing SVG ------------------------------------------------------------

def parse_svg(svg_path: Path) -> tuple[list[ET.Element], float]:
    """
    Retourne (éléments <path>, largeur du viewBox).
    La largeur sert à calculer l'advance width du glyphe.
    """
    tree = ET.parse(svg_path)
    root = tree.getroot()

    # Largeur depuis viewBox ou attribut width
    vb = root.get("viewBox", "")
    if vb:
        parts = vb.strip().split()
        svg_width = float(parts[2]) if len(parts) == 4 else SVG_HEIGHT
    else:
        w_attr = root.get("width", str(SVG_HEIGHT))
        svg_width = float(re.sub(r"[^0-9.]", "", w_attr) or SVG_HEIGHT)

    ns = {"svg": "http://www.w3.org/2000/svg"}
    paths = root.findall(".//svg:path", ns)
    if not paths:
        paths = root.findall(".//{http://www.w3.org/2000/svg}path")
    if not paths:
        paths = root.findall(".//path")

    return paths, svg_width


def tokenize_d(d: str) -> list:
    """Découpe une chaîne SVG path en liste de commandes et nombres."""
    return re.findall(
        r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?",
        d
    )


def parse_svg_d(d: str) -> list[tuple[str, list[float]]]:
    """
    Parse les commandes SVG path.
    Retourne une liste de (commande, [arguments]).
    Gère la répétition implicite des arguments après une commande.
    """
    tokens = tokenize_d(d)
    commands = []
    i = 0
    CMD_CHARS = set("MmLlHhVvCcSsQqTtAaZz")
    # Nombre d'arguments par commande (pour la répétition implicite)
    ARGS_COUNT = {
        "M": 2, "m": 2, "L": 2, "l": 2,
        "H": 1, "h": 1, "V": 1, "v": 1,
        "C": 6, "c": 6, "S": 4, "s": 4,
        "Q": 4, "q": 4, "T": 2, "t": 2,
        "Z": 0, "z": 0,
    }

    while i < len(tokens):
        token = tokens[i]
        if token in CMD_CHARS:
            cmd = token
            i += 1
        else:
            # Répétition implicite : même commande, nouveaux arguments
            # (M implicite devient L, m implicite devient l)
            if commands:
                prev = commands[-1][0]
                cmd = "L" if prev == "M" else "l" if prev == "m" else prev
            else:
                i += 1
                continue

        n = ARGS_COUNT.get(cmd, 0)
        if n == 0:
            commands.append((cmd, []))
            continue

        args = []
        while i < len(tokens) and tokens[i] not in CMD_CHARS:
            args.append(float(tokens[i]))
            i += 1

        # Découpe en groupes si plusieurs répétitions
        for j in range(0, max(len(args), n), n):
            commands.append((cmd, args[j:j+n]))

    return commands


def svg_d_to_contours(d: str) -> list[list[str]]:
    """
    Convertit les commandes SVG path en liste de contours GLIF.
    Chaque sous-chemin (M...Z) devient un contour séparé — indispensable
    pour les glyphes comme B, P, R qui ont des contre-formes (fill-rule evenodd).

    Types de points UFO :
      line     → segment droit (L, H, V)
      curve    → Bézier cubique — précédé de deux points de contrôle sans type
      qcurve   → Bézier quadratique — précédé d'un point de contrôle sans type
    """
    commands = parse_svg_d(d)
    all_contours = []
    points = []
    cx, cy = 0.0, 0.0

    for cmd, args in commands:
        if cmd == "M":
            cx, cy = args[0], args[1]
            points.append(f'<point x="{tx(cx)}" y="{ty(cy)}" type="line"/>')
        elif cmd == "m":
            cx += args[0]; cy += args[1]
            points.append(f'<point x="{tx(cx)}" y="{ty(cy)}" type="line"/>')

        elif cmd == "L":
            cx, cy = args[0], args[1]
            points.append(f'<point x="{tx(cx)}" y="{ty(cy)}" type="line"/>')
        elif cmd == "l":
            cx += args[0]; cy += args[1]
            points.append(f'<point x="{tx(cx)}" y="{ty(cy)}" type="line"/>')

        elif cmd == "H":
            cx = args[0]
            points.append(f'<point x="{tx(cx)}" y="{ty(cy)}" type="line"/>')
        elif cmd == "h":
            cx += args[0]
            points.append(f'<point x="{tx(cx)}" y="{ty(cy)}" type="line"/>')

        elif cmd == "V":
            cy = args[0]
            points.append(f'<point x="{tx(cx)}" y="{ty(cy)}" type="line"/>')
        elif cmd == "v":
            cy += args[0]
            points.append(f'<point x="{tx(cx)}" y="{ty(cy)}" type="line"/>')

        elif cmd == "C":
            x1,y1, x2,y2, x,y = args
            points.append(f'<point x="{tx(x1)}" y="{ty(y1)}"/>')
            points.append(f'<point x="{tx(x2)}" y="{ty(y2)}"/>')
            points.append(f'<point x="{tx(x)}"  y="{ty(y)}"  type="curve"/>')
            cx, cy = x, y
        elif cmd == "c":
            x1,y1 = cx+args[0], cy+args[1]
            x2,y2 = cx+args[2], cy+args[3]
            x, y  = cx+args[4], cy+args[5]
            points.append(f'<point x="{tx(x1)}" y="{ty(y1)}"/>')
            points.append(f'<point x="{tx(x2)}" y="{ty(y2)}"/>')
            points.append(f'<point x="{tx(x)}"  y="{ty(y)}"  type="curve"/>')
            cx, cy = x, y

        elif cmd == "Q":
            x1,y1, x,y = args
            points.append(f'<point x="{tx(x1)}" y="{ty(y1)}"/>')
            points.append(f'<point x="{tx(x)}"  y="{ty(y)}"  type="qcurve"/>')
            cx, cy = x, y
        elif cmd == "q":
            x1,y1 = cx+args[0], cy+args[1]
            x, y  = cx+args[2], cy+args[3]
            points.append(f'<point x="{tx(x1)}" y="{ty(y1)}"/>')
            points.append(f'<point x="{tx(x)}"  y="{ty(y)}"  type="qcurve"/>')
            cx, cy = x, y

        elif cmd in "Zz":
            # Fin du sous-contour : on le sauvegarde et on repart à zéro
            if points:
                all_contours.append(points)
                points = []

    # Sous-contour sans Z final
    if points:
        all_contours.append(points)

    return all_contours


# --- Construction du .glif --------------------------------------------------

def build_glif(char: str, glyph_name: str, paths: list[ET.Element],
               advance_width: int) -> str:
    """Construit le XML d'un fichier .glif (format UFO 3)."""
    unicode_hex = f"{ord(char):04X}"
    contours_xml_parts = []

    for path_el in paths:
        d = path_el.get("d", "").strip()
        if not d:
            continue
        for points in svg_d_to_contours(d):
            if not points:
                continue
            indent = "        "
            points_xml = "\n".join(f"{indent}{p}" for p in points)
            contours_xml_parts.append(f"      <contour>\n{points_xml}\n      </contour>")

    contours_xml = "\n".join(contours_xml_parts)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<glyph name="{glyph_name}" format="2">
  <advance width="{advance_width}"/>
  <unicode hex="{unicode_hex}"/>
  <outline>
{contours_xml}
  </outline>
</glyph>
"""


# --- Mise à jour des plists -------------------------------------------------

def update_contents_plist(mapping: dict[str, str]):
    contents_path = GLYPHS_DIR / "contents.plist"
    existing = {}
    if contents_path.exists():
        with open(contents_path, "rb") as f:
            existing = plistlib.load(f)
    existing.update(mapping)
    with open(contents_path, "wb") as f:
        plistlib.dump(existing, f)


def update_lib_plist(glyph_names: list[str]):
    lib_path = UFO_DIR / "lib.plist"
    existing = {}
    if lib_path.exists():
        with open(lib_path, "rb") as f:
            existing = plistlib.load(f)
    order = existing.get("public.glyphOrder", [])
    for name in glyph_names:
        if name not in order:
            order.append(name)
    existing["public.glyphOrder"] = sorted(order)
    with open(lib_path, "wb") as f:
        plistlib.dump(existing, f)


# --- Programme principal ----------------------------------------------------

def main():
    print(f"[config] SVG {SVG_HEIGHT}px → UFO {UNITS_PER_EM} upm")
    print(f"         ascender  : {SVG_ASCENDER}px depuis le bas → {ASCENDER} ufo")
    print(f"         baseline  : {SVG_BASELINE}px depuis le bas → 0 ufo")
    print(f"         descender : {SVG_DESCENDER}px depuis le bas → {DESCENDER} ufo")
    print()

    # Mise à jour automatique de fontinfo.plist
    fontinfo_path = UFO_DIR / "fontinfo.plist"
    if fontinfo_path.exists():
        with open(fontinfo_path, "rb") as f:
            fontinfo = plistlib.load(f)
        fontinfo["unitsPerEm"]                    = UNITS_PER_EM
        # Métriques typographiques (sTypo*)
        fontinfo["ascender"]                       = ASCENDER
        fontinfo["descender"]                      = DESCENDER
        # Métriques Windows — utilisées par les navigateurs pour la line-box
        fontinfo["openTypeOS2TypoAscender"]        = ASCENDER
        fontinfo["openTypeOS2TypoDescender"]       = DESCENDER
        fontinfo["openTypeOS2TypoLineGap"]         = 0
        fontinfo["openTypeOS2WinAscent"]           = ASCENDER
        fontinfo["openTypeOS2WinDescent"]          = abs(DESCENDER)
        # Métriques hhea — utilisées par macOS et les navigateurs WebKit
        fontinfo["openTypeHheaAscender"]           = ASCENDER
        fontinfo["openTypeHheaDescender"]          = DESCENDER
        fontinfo["openTypeHheaLineGap"]            = 0
        with open(fontinfo_path, "wb") as f:
            plistlib.dump(fontinfo, f)
        print(f"[fontinfo] ascender={ASCENDER}, descender={DESCENDER}")
        print(f"           winAscent={ASCENDER}, winDescent={abs(DESCENDER)} mis à jour\n")

    svg_files = sorted(SVG_DIR.glob("uni*.svg"))
    if not svg_files:
        print(f"Aucun fichier SVG trouvé dans {SVG_DIR}")
        return

    GLYPHS_DIR.mkdir(parents=True, exist_ok=True)

    new_mapping, new_names, converted, skipped = {}, [], [], []

    for svg_path in svg_files:
        char = unicode_from_filename(svg_path.name)
        if char is None:
            skipped.append(svg_path.name)
            continue

        glyph_name = glyph_name_from_char(char)
        filename   = safe_filename(glyph_name)
        glif_path  = GLYPHS_DIR / filename

        paths, svg_width = parse_svg(svg_path)
        advance_width = round(svg_width * _scale)
        glif_content = build_glif(char, glyph_name, paths, advance_width)

        glif_path.write_text(glif_content, encoding="utf-8")
        new_mapping[glyph_name] = filename
        new_names.append(glyph_name)
        converted.append(f"  {svg_path.name} → {filename}  ('{char}', advance={advance_width})")

    update_contents_plist(new_mapping)
    update_lib_plist(new_names)

    print(f"{len(converted)} glyphe(s) converti(s) :")
    for line in converted:
        print(line)

    if skipped:
        print(f"\n{len(skipped)} fichier(s) ignoré(s) (nom non reconnu) :")
        for name in skipped:
            print(f"  {name}")


if __name__ == "__main__":
    main()
