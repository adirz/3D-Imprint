#!/usr/bin/env python3
"""
Core of the print-on-relief effect, shared by the batch script (place_print.py)
and the GUI (print_gui.py). One implementation, two front-ends.

The idea: read the base in black-and-white and, over a small window, take the
gradient (steepness) and the Hessian's peak eigenvalue (curvature along the
steepest direction). That tells a groove (dark line between bright shoulders)
from a shaded slope (bright one side, shallow the other). The top image (a
bloody print) is cut in grooves and at cliffs, kept-but-darkened on slopes.

Functions:
    default_params()                 -> dict of every knob
    load_rgba(path)                  -> HxWx4 float32 base image
    load_print(path)                 -> PIL RGBA cropped to its content
    analyze_base(coin, sigma, pct)   -> dict of cacheable base-derived maps
    compose(base, print_pil, P)      -> (HxWx4 float result, diag dict)
"""
import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, map_coordinates

# Params measured in pixels -- the GUI scales these for its downscaled preview.
PX_PARAMS = ("STRUCT_SIGMA", "PLACE_DX", "PLACE_DY",
             "DISP_PX", "DISP_BLUR", "CUT_FEATHER")


def default_params():
    return dict(
        PLACE_SCALE=0.60, PLACE_DX=0.0, PLACE_DY=-10.0, PLACE_ROT=0.0,
        PRINT_OPACITY=0.88,
        RELIGHT_LO=0.35, RELIGHT_HI=1.0, RELIGHT_AMT=0.90,
        STRUCT_SIGMA=2.5, STRUCT_PCT=99.0,
        VALLEY_CUT_LO=0.30, VALLEY_CUT_HI=0.70, VALLEY_POOL=0.0,
        SLOPE_KEEP=0.55, SLOPE_CUT=0.85, SLOPE_SHADE=0.25,
        CUT_FEATHER=1.0,
        DISP_PX=6.0, DISP_BLUR=4.0,
    )


def load_rgba(path):
    return np.asarray(Image.open(path).convert("RGBA")).astype(np.float32) / 255.0


def load_print(path):
    """Open the top image and crop it to its non-transparent bounding box."""
    pr = Image.open(path).convert("RGBA")
    pa = np.asarray(pr)[..., 3]
    ys, xs = np.where(pa > 10)
    if len(xs):
        pr = pr.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    return pr


def analyze_base(coin, sigma, pct):
    """Everything derived from the base + structure scale (cache on these)."""
    sigma = max(float(sigma), 0.4)
    H, W = coin.shape[:2]
    rgb, a = coin[..., :3], coin[..., 3]
    opaque = a > 0.04
    lum = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    ref = float(np.median(lum[opaque])) if opaque.any() else 0.5

    # Flatten the transparent exterior so the rim isn't read as a giant cliff.
    lum_s = np.where(opaque, lum, ref).astype(np.float32)
    Lx = gaussian_filter(lum_s, sigma, order=[0, 1])   # axis0=y(rows) axis1=x
    Ly = gaussian_filter(lum_s, sigma, order=[1, 0])
    grad = np.hypot(Lx, Ly)
    Lxx = gaussian_filter(lum_s, sigma, order=[0, 2])
    Lyy = gaussian_filter(lum_s, sigma, order=[2, 0])
    Lxy = gaussian_filter(lum_s, sigma, order=[1, 1])

    # Peak (most-positive) Hessian eigenvalue: big in a luminance valley, ~0 on
    # a flat or straight slope, negative on a ridge. Peak-only (not Laplacian)
    # makes a thin line score like a round pit.
    half_tr = 0.5 * (Lxx + Lyy)
    disc = np.sqrt(np.maximum((0.5 * (Lxx - Lyy)) ** 2 + Lxy * Lxy, 0.0))
    lam_hi = half_tr + disc

    def _norm(x):
        if not opaque.any():
            return np.clip(x, 0, 1)
        return np.clip(x / (np.percentile(x[opaque], pct) + 1e-6), 0.0, 1.0)

    valley = _norm(np.maximum(lam_hi, 0.0))
    slope = _norm(grad)
    return dict(H=H, W=W, rgb=rgb, a=a, opaque=opaque, lum=lum, ref=ref,
                valley=valley, slope=slope)


def _place(print_pil, base, P):
    H, W = base["H"], base["W"]
    ys, xs = np.where(base["opaque"])
    if not len(xs):
        return np.zeros((H, W, 3), np.float32), np.zeros((H, W), np.float32)
    cx, cy = xs.mean(), ys.mean()
    coin_w = xs.max() - xs.min()

    target_w = max(1.0, P["PLACE_SCALE"] * coin_w)
    s = target_w / print_pil.width
    pw, ph = max(1, int(print_pil.width * s)), max(1, int(print_pil.height * s))
    pr = print_pil.resize((pw, ph), Image.LANCZOS)
    pr = pr.rotate(P["PLACE_ROT"], expand=True, resample=Image.BICUBIC)

    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    px = int(round(cx + P["PLACE_DX"] - pr.width / 2))
    py = int(round(cy + P["PLACE_DY"] - pr.height / 2))
    canvas.paste(pr, (px, py), pr)
    blood = np.asarray(canvas).astype(np.float32) / 255.0
    return blood[..., :3], blood[..., 3] * P["PRINT_OPACITY"]


def compose(base, print_pil, P):
    """Return (result HxWx4 float, diag dict of valley/slope/keep maps)."""
    H, W = base["H"], base["W"]
    rgb, a, opaque = base["rgb"], base["a"], base["opaque"]
    lum, ref = base["lum"], base["ref"]
    valley, slope = base["valley"], base["slope"]
    blood_rgb, blood_a = _place(print_pil, base, P)

    # displace blood along the shading gradient at relief edges (minor)
    if P["DISP_PX"] > 0:
        lb = gaussian_filter(lum, max(P["DISP_BLUR"], 0.4))
        gy, gx = np.gradient(lb)
        mag = np.hypot(gx, gy); mag[mag == 0] = 1
        dx = (gx / mag) * np.clip(np.abs(gx) * P["DISP_PX"] * 4, 0, P["DISP_PX"])
        dy = (gy / mag) * np.clip(np.abs(gy) * P["DISP_PX"] * 4, 0, P["DISP_PX"])
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        coords = np.array([yy + dy, xx + dx])
        blood_rgb = np.stack([map_coordinates(blood_rgb[..., c], coords,
                              order=1, mode="constant") for c in range(3)], -1)
        blood_a = map_coordinates(blood_a, coords, order=1, mode="constant")

    # relight: blood inherits coin local shading (darker on shaded faces)
    shade = np.clip(lum / ref, P["RELIGHT_LO"], P["RELIGHT_HI"])
    shade = 1 + P["RELIGHT_AMT"] * (shade - 1)
    blood_rgb = np.clip(blood_rgb * shade[..., None], 0, 1)
    if P["SLOPE_SHADE"] > 0:
        blood_rgb = blood_rgb * (1 - P["SLOPE_SHADE"] * slope)[..., None]
    if P["VALLEY_POOL"] > 0:
        pool = P["VALLEY_POOL"] * valley
        blood_rgb = blood_rgb * (1 - 0.5 * pool)[..., None]
        blood_a = np.clip(blood_a + pool, 0, 1)

    # structure mask: cut the print in grooves and at cliffs
    cv = np.clip((valley - P["VALLEY_CUT_LO"]) /
                 (P["VALLEY_CUT_HI"] - P["VALLEY_CUT_LO"] + 1e-6), 0, 1)
    cs = np.clip((slope - P["SLOPE_KEEP"]) /
                 (P["SLOPE_CUT"] - P["SLOPE_KEEP"] + 1e-6), 0, 1)
    cut = gaussian_filter(np.maximum(cv, cs), max(P["CUT_FEATHER"], 0.0)) \
        if P["CUT_FEATHER"] > 0 else np.maximum(cv, cs)
    keep = np.clip(1.0 - cut, 0, 1)
    blood_a = blood_a * keep * opaque

    out_rgb = rgb * (1 - blood_a[..., None]) + blood_rgb * blood_a[..., None]
    out = np.dstack([np.clip(out_rgb, 0, 1), a])
    return out, dict(valley=valley, slope=slope, keep=keep,
                     shade=np.clip(lum / ref / 2, 0, 1))


def to_uint8(arr):
    return (np.clip(arr, 0, 1) * 255).astype(np.uint8)
