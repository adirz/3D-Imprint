#!/usr/bin/env python3
"""
Interactive front-end for the print-on-relief effect (print_core.py).

  * Load base (coin) and top (print) images.
  * Drag the print to move it; mouse-wheel to scale it.
  * Tune every parameter with sliders; the preview updates live.
  * Switch the view to Result / Valley / Slope / Keep / Base to see what the
    structure detector is doing.
  * Save renders the full-resolution PNG (and a sidecar .params.json).

Run:  python print_gui.py
"""
import json
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
from PIL import Image

try:
    from PIL import ImageTk
    HAVE_IMAGETK = True
except Exception:                       # fall back to Tk's native PNG loader
    HAVE_IMAGETK = False
    import base64
    import io

import print_core as core

PREVIEW_MAX = 640        # longest preview edge in px (smaller = snappier)
CANVAS_BG = "#2b2b2b"

# default images (edit or just use Load buttons)
HERE = os.path.dirname(os.path.abspath(__file__))
DEF_COIN = os.path.join(HERE, "examples", "coin.png")
DEF_PRINT = os.path.join(HERE, "examples", "fingerprint.png")
DEF_OUT = os.path.join(HERE, "output", "imprint.png")

# (key, label, lo, hi) grouped into labelled sections
SECTIONS = [
    ("Placement", [
        ("PLACE_SCALE", "scale", 0.05, 2.0),
        ("PLACE_DX", "offset x", -1500, 1500),
        ("PLACE_DY", "offset y", -1500, 1500),
        ("PLACE_ROT", "rotation", -180, 180),
        ("PRINT_OPACITY", "opacity", 0.0, 1.0),
    ]),
    ("Relight", [
        ("RELIGHT_AMT", "amount", 0.0, 1.0),
        ("RELIGHT_LO", "shadow clamp", 0.0, 1.0),
        ("RELIGHT_HI", "highlight clamp", 0.0, 1.5),
    ]),
    ("Structure", [
        ("STRUCT_SIGMA", "window scale", 0.5, 12.0),
        ("STRUCT_PCT", "normalise pct", 80.0, 100.0),
    ]),
    ("Valley / groove", [
        ("VALLEY_CUT_LO", "cut start", 0.0, 1.0),
        ("VALLEY_CUT_HI", "cut full", 0.0, 1.0),
        ("VALLEY_POOL", "pool (vs cut)", 0.0, 2.0),
    ]),
    ("Slope / cliff", [
        ("SLOPE_KEEP", "keep below", 0.0, 1.0),
        ("SLOPE_CUT", "cut above", 0.0, 1.0),
        ("SLOPE_SHADE", "slope darken", 0.0, 1.0),
    ]),
    ("Misc", [
        ("CUT_FEATHER", "cut feather", 0.0, 6.0),
        ("DISP_PX", "displace px", 0.0, 30.0),
        ("DISP_BLUR", "displace blur", 0.5, 20.0),
    ]),
]
VIEWS = ["Result", "Valley", "Slope", "Keep", "Base"]


class App:
    def __init__(self, root):
        self.root = root
        root.title("place print")

        self.coin_pil = None          # full-res PIL RGBA base
        self.print_pil = None         # cropped PIL RGBA top
        self.coin_path = None
        self.coin_prev_np = None      # downscaled base for preview
        self.prev_f = 1.0             # preview / full scale factor
        self._base_cache = None
        self._base_key = None
        self._after = None
        self._drag = None
        self._photo = None
        self.ranges = {}
        self.entries = {}
        self.entry_vars = {}

        self.vars = {k: tk.DoubleVar(value=v)
                     for k, v in core.default_params().items()}
        self.view_var = tk.StringVar(value="Result")

        self._build()
        for v in self.vars.values():
            v.trace_add("write", lambda *a: self.schedule())
        self.view_var.trace_add("write", lambda *a: self.schedule())

        self.try_load(DEF_COIN, "coin")
        self.try_load(DEF_PRINT, "print")
        self.render()

    # ----------------------------- UI -------------------------------------
    def _build(self):
        left = ttk.Frame(self.root)
        left.grid(row=0, column=0, sticky="nsew")
        self.canvas = tk.Canvas(left, width=PREVIEW_MAX + 30,
                                height=PREVIEW_MAX + 30, bg=CANVAS_BG,
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        self.canvas.bind("<MouseWheel>", self.on_wheel)      # win/mac
        self.canvas.bind("<Button-4>", self.on_wheel)        # x11 up
        self.canvas.bind("<Button-5>", self.on_wheel)        # x11 down

        right = ttk.Frame(self.root, padding=6)
        right.grid(row=0, column=1, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # top buttons
        bar = ttk.Frame(right)
        bar.pack(fill="x")
        ttk.Button(bar, text="Load base", command=lambda: self.load_dialog("coin")
                   ).pack(side="left")
        ttk.Button(bar, text="Load top", command=lambda: self.load_dialog("print")
                   ).pack(side="left", padx=4)
        ttk.Button(bar, text="Reset", command=self.reset).pack(side="left")
        ttk.Button(bar, text="Save…", command=self.save).pack(side="right")

        # view selector
        vrow = ttk.Frame(right)
        vrow.pack(fill="x", pady=(6, 2))
        ttk.Label(vrow, text="View:").pack(side="left")
        for v in VIEWS:
            ttk.Radiobutton(vrow, text=v, value=v, variable=self.view_var
                            ).pack(side="left")

        self.status = ttk.Label(right, text="", foreground="#888")
        self.status.pack(fill="x", pady=(0, 4))

        # scrollable slider panel
        body = ttk.Frame(right)
        body.pack(fill="both", expand=True)
        sc = tk.Canvas(body, highlightthickness=0, width=300)
        sb = ttk.Scrollbar(body, orient="vertical", command=sc.yview)
        inner = ttk.Frame(sc)
        inner.bind("<Configure>",
                   lambda e: sc.configure(scrollregion=sc.bbox("all")))
        sc.create_window((0, 0), window=inner, anchor="nw")
        sc.configure(yscrollcommand=sb.set)
        sc.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        for title, rows in SECTIONS:
            ttk.Label(inner, text=title, font=("TkDefaultFont", 9, "bold")
                      ).pack(anchor="w", pady=(8, 0))
            for key, label, lo, hi in rows:
                self._slider(inner, key, label, lo, hi)

    def _slider(self, parent, key, label, lo, hi):
        self.ranges[key] = (lo, hi)
        row = ttk.Frame(parent)
        row.pack(fill="x")
        ttk.Label(row, text=label, width=13).pack(side="left")
        ev = tk.StringVar()
        self.entry_vars[key] = ev
        ent = ttk.Entry(row, width=8, textvariable=ev, justify="right")
        ent.pack(side="right")
        ent.bind("<Return>", lambda e, k=key: self._commit_entry(k))
        ent.bind("<FocusOut>", lambda e, k=key: self._commit_entry(k))
        ent.bind("<FocusIn>", lambda e: e.widget.select_range(0, "end"))
        ent.bind("<Escape>", lambda e, k=key: (self._fmt(k), self.canvas.focus_set()))
        self.entries[key] = ent
        s = ttk.Scale(row, from_=lo, to=hi, variable=self.vars[key],
                      command=lambda *_: self._fmt(key))
        s.pack(side="left", fill="x", expand=True, padx=4)
        self._fmt(key)

    def _set_entry_text(self, key, v):
        self.entry_vars[key].set(f"{v:.0f}" if abs(v) >= 100 else f"{v:.3f}")

    def _fmt(self, key):
        ent = self.entries.get(key)
        if ent is not None and self.root.focus_get() is ent:
            return                       # don't clobber while the user is typing
        self._set_entry_text(key, self.vars[key].get())

    def _commit_entry(self, key):
        lo, hi = self.ranges[key]
        try:
            v = float(self.entry_vars[key].get())
        except ValueError:
            self._set_entry_text(key, self.vars[key].get())     # revert
            return
        v = max(lo, min(hi, v))
        if v != self.vars[key].get():
            self.vars[key].set(v)        # moves slider + schedules a render
        self._set_entry_text(key, v)     # show clamped/normalised value

    # ----------------------------- loading --------------------------------
    def load_dialog(self, which):
        path = filedialog.askopenfilename(
            title=f"Load {which}",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.tif *.tiff *.webp"),
                       ("All", "*.*")])
        if path:
            self.try_load(path, which)
            self.render()

    def try_load(self, path, which):
        if not path or not os.path.exists(path):
            return
        try:
            if which == "coin":
                self.coin_pil = Image.open(path).convert("RGBA")
                self.coin_path = path
                self._prepare_preview()
                self._base_key = None
            else:
                self.print_pil = core.load_print(path)
        except Exception as e:
            messagebox.showerror("Load failed", f"{path}\n{e}")

    def _prepare_preview(self):
        w, h = self.coin_pil.size
        self.prev_f = min(1.0, PREVIEW_MAX / max(w, h))
        pw, ph = max(1, int(w * self.prev_f)), max(1, int(h * self.prev_f))
        prev = self.coin_pil.resize((pw, ph), Image.LANCZOS)
        self.coin_prev_np = np.asarray(prev).astype(np.float32) / 255.0

    # ----------------------------- params ---------------------------------
    def params(self):
        return {k: v.get() for k, v in self.vars.items()}

    def scaled(self, P, f):
        Q = dict(P)
        for k in core.PX_PARAMS:
            Q[k] = P[k] * f
        return Q

    def base_for(self, coin_np, sigma, pct):
        key = (id(coin_np), round(sigma, 4), round(pct, 3))
        if key != self._base_key:
            self._base_cache = core.analyze_base(coin_np, sigma, pct)
            self._base_key = key
        return self._base_cache

    # ----------------------------- render ---------------------------------
    def schedule(self):
        for k in self.vars:                 # keep value labels current
            self._fmt(k)
        if self._after is not None:
            self.root.after_cancel(self._after)
        self._after = self.root.after(50, self.render)

    def render(self):
        self._after = None
        if self.coin_prev_np is None or self.print_pil is None:
            self.status.config(text="Load a base and a top image to begin.")
            return
        P = self.params()
        f = self.prev_f
        sigma = max(P["STRUCT_SIGMA"] * f, 0.4)
        base = self.base_for(self.coin_prev_np, sigma, P["STRUCT_PCT"])
        Q = self.scaled(P, f)
        out, diag = core.compose(base, self.print_pil, Q)

        view = self.view_var.get()
        if view == "Result":
            img = Image.fromarray(core.to_uint8(out), "RGBA")
        elif view == "Base":
            img = Image.fromarray(core.to_uint8(self.coin_prev_np), "RGBA")
        else:
            img = Image.fromarray(core.to_uint8(diag[view.lower()]), "L").convert("RGBA")
        self._show(img)
        self.status.config(
            text=f"{img.width}×{img.height} preview  ·  full {self.coin_pil.size[0]}×"
                 f"{self.coin_pil.size[1]}  ·  drag=move  wheel=scale")

    def _show(self, pil_img):
        self._photo = self._photo_from(pil_img)
        self.canvas.delete("img")
        cw = self.canvas.winfo_width() or (PREVIEW_MAX + 30)
        ch = self.canvas.winfo_height() or (PREVIEW_MAX + 30)
        self.canvas.create_image(cw / 2, ch / 2, image=self._photo,
                                 anchor="center", tags="img")
        self._img_size = pil_img.size

    def _photo_from(self, pil_img):
        if HAVE_IMAGETK:
            return ImageTk.PhotoImage(pil_img)
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        return tk.PhotoImage(data=base64.b64encode(buf.getvalue()).decode())

    # ----------------------------- mouse ----------------------------------
    def on_press(self, e):
        self._drag = (e.x, e.y)

    def on_drag(self, e):
        if self._drag is None or self.prev_f == 0:
            return
        dx, dy = e.x - self._drag[0], e.y - self._drag[1]
        self._drag = (e.x, e.y)
        self.vars["PLACE_DX"].set(self.vars["PLACE_DX"].get() + dx / self.prev_f)
        self.vars["PLACE_DY"].set(self.vars["PLACE_DY"].get() + dy / self.prev_f)

    def on_wheel(self, e):
        up = getattr(e, "delta", 0) > 0 or getattr(e, "num", 0) == 4
        factor = 1.05 if up else 1 / 1.05
        self.vars["PLACE_SCALE"].set(
            max(0.02, min(4.0, self.vars["PLACE_SCALE"].get() * factor)))

    # ----------------------------- actions --------------------------------
    def reset(self):
        for k, v in core.default_params().items():
            self.vars[k].set(v)
        self.view_var.set("Result")

    def save(self):
        if self.coin_pil is None or self.print_pil is None:
            messagebox.showwarning("Nothing to save", "Load both images first.")
            return
        os.makedirs(os.path.dirname(DEF_OUT), exist_ok=True)
        path = filedialog.asksaveasfilename(
            title="Save render", defaultextension=".png",
            initialfile=os.path.basename(DEF_OUT),
            initialdir=os.path.dirname(DEF_OUT),
            filetypes=[("PNG", "*.png")])
        if not path:
            return
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.status.config(text="rendering full resolution…")
        self.root.update_idletasks()
        P = self.params()
        coin_np = np.asarray(self.coin_pil).astype(np.float32) / 255.0
        base = core.analyze_base(coin_np, P["STRUCT_SIGMA"], P["STRUCT_PCT"])
        out, _ = core.compose(base, self.print_pil, P)
        Image.fromarray(core.to_uint8(out), "RGBA").save(path)
        with open(os.path.splitext(path)[0] + ".params.json", "w") as fh:
            json.dump({"coin": self.coin_path, **P}, fh, indent=2)
        self.status.config(text=f"saved {path}")


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
