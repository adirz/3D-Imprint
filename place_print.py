#!/usr/bin/env python3
"""
Batch front-end: place a (bloody) thumbprint onto a bumpy surface, deciding
where the print survives from the surface's *relief structure* (grooves &
cliffs cut, shaded slopes kept-but-darker) rather than its raw brightness.

The algorithm lives in print_core.py; this file just sets paths + knobs and
writes the result. To tune interactively instead, run:  python print_gui.py

Tune PARAMS and re-run.
"""
import os
from PIL import Image
import print_core as core

HERE = os.path.dirname(os.path.abspath(__file__))

# ----------------------------- PARAMS --------------------------------------
COIN  = os.path.join(HERE, "examples", "coin.png")          # base / surface
PRINT = os.path.join(HERE, "examples", "fingerprint.png")   # top / decal
OUT   = os.path.join(HERE, "output", "imprint.png")

P = core.default_params()
P.update(
    PLACE_SCALE=0.60,     # print width as fraction of coin opaque-width
    PLACE_DX=0.0,         # px offset of print centre from coin centroid (+right)
    PLACE_DY=-10.0,       # px offset (+down)
    PLACE_ROT=0.0,        # degrees, CCW positive
    PRINT_OPACITY=0.88,   # global blood opacity

    RELIGHT_LO=0.35,      # clamp on shading multiplier (shadows don't go black)
    RELIGHT_HI=1.0,       # clamp (highlights don't blow blood out)
    RELIGHT_AMT=0.90,     # 0=ignore coin shading, 1=full modulation

    STRUCT_SIGMA=2.5,     # window scale for the derivative kernels (px)
    STRUCT_PCT=99.0,      # percentile used to normalise structure maps to ~[0,1]

    VALLEY_CUT_LO=0.30,   # valley strength below this -> print fully kept
    VALLEY_CUT_HI=0.70,   # valley strength above this -> print fully cut
    VALLEY_POOL=0.0,      # >0: darken+thicken blood in grooves (raise CUT_* too)

    SLOPE_KEEP=0.55,      # slope below this -> gentle face, fully kept
    SLOPE_CUT=0.85,       # slope above this -> cliff, print fully cut off
    SLOPE_SHADE=0.25,     # extra darkening of blood on (kept) steep slopes 0..1

    CUT_FEATHER=1.0,      # blur (px) on the cut lines so they aren't jagged
    DISP_PX=6.0,          # max displacement amplitude (px)
    DISP_BLUR=4.0,        # blur of coin lum before taking gradient (sets scale)
)
# ---------------------------------------------------------------------------

coin = core.load_rgba(COIN)
print_pil = core.load_print(PRINT)
base = core.analyze_base(coin, P["STRUCT_SIGMA"], P["STRUCT_PCT"])
out, diag = core.compose(base, print_pil, P)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
Image.fromarray(core.to_uint8(out), "RGBA").save(OUT)

# diagnostics -- inspect these to tune the thresholds
D = os.path.dirname(OUT)
for name in ("shade", "valley", "slope", "keep"):
    Image.fromarray(core.to_uint8(diag[name]), "L").save(os.path.join(D, "diag_" + name + ".png"))
print("wrote", OUT)
