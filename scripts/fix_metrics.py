#!/usr/bin/env python3
"""
fix_metrics.py
Corrige les métriques verticales du .ttf après compilation par fontmake.
Fontmake recalcule usWinAscent/hhea depuis les contours — ce script
les force aux valeurs définies dans svg_to_glif.py.
"""

import sys
from pathlib import Path
from fontTools.ttLib import TTFont

# Importer les constantes depuis svg_to_glif
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
from svg_to_glif import ASCENDER, DESCENDER, UNITS_PER_EM

TTF = ROOT / "dist" / "montavon.ttf"

if not TTF.exists():
    print(f"Fichier non trouvé : {TTF}")
    sys.exit(1)

f = TTFont(str(TTF))

os2  = f["OS/2"]
hhea = f["hhea"]
head = f["head"]

head.unitsPerEm     =  UNITS_PER_EM
os2.sTypoAscender   =  ASCENDER
os2.sTypoDescender  =  DESCENDER
os2.sTypoLineGap    =  0
os2.usWinAscent     =  ASCENDER
os2.usWinDescent    =  abs(DESCENDER)
hhea.ascent         =  ASCENDER
hhea.descent        =  DESCENDER
hhea.lineGap        =  0

f.save(str(TTF))

print(f"[fix_metrics] métriques corrigées :")
print(f"  unitsPerEm                   : {UNITS_PER_EM}")
print(f"  sTypoAscender / hhea.ascent  : {ASCENDER}")
print(f"  sTypoDescender / hhea.descent: {DESCENDER}")
print(f"  usWinAscent                  : {ASCENDER}")
print(f"  usWinDescent                 : {abs(DESCENDER)}")