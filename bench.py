"""
ID accuracy benchmark
=====================
Generates receipts with known IDs, degrades them the way real screenshots are
degraded (small, blurred, recompressed, low contrast, noisy), and measures how
often the exact ID string comes back out.

    python bench.py                 measure the current default
    python bench.py --mode fast     force single-pass
    python bench.py --mode high     force the ensemble
    python bench.py --compare       run every mode and print a table

Exact match only. A UTR that is one character wrong is wrong, so partial credit
would flatter the numbers and hide the failures that actually matter.
"""

from __future__ import annotations

import argparse
import io
import random
import sys
import time
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

import core

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DIGITS = "0123456789"
ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
BASE62 = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
BANKS = ["SBIN", "HDFC", "ICIC", "UTIB", "KKBK", "PUNB"]
NAMES = ["Rajesh Kumar", "Anita Sharma", "Vikram Rao", "Meera Nair",
         "Sanjay Gupta", "Priya Menon", "Arjun Reddy", "Kavita Iyer"]


@dataclass
class Case:
    name: str
    image: Image.Image
    truth: dict[str, str]


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    names = ["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]
    for n in names:
        try:
            return ImageFont.truetype(f"C:/Windows/Fonts/{n}", size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def make_cases(count: int, seed: int = 7) -> list[Case]:
    """Build receipts whose IDs we know exactly."""
    rng = random.Random(seed)
    cases: list[Case] = []

    for i in range(count):
        kind = i % 4
        if kind == 0:                                     # UPI / IMPS
            utr = "".join(rng.choice(DIGITS) for _ in range(12))
        elif kind == 1:                                   # NEFT
            utr = rng.choice(BANKS) + "".join(rng.choice(ALNUM) for _ in range(12))
        elif kind == 2:                                   # RTGS
            utr = rng.choice(BANKS) + "".join(rng.choice(ALNUM) for _ in range(18))
        else:                                             # digit-heavy, confusable
            utr = "".join(rng.choice("0158693") for _ in range(12))

        txn = "pay_" + "".join(rng.choice(BASE62) for _ in range(14))
        payee = rng.choice(NAMES)
        amount = f"{rng.randint(1, 99):,},{rng.randint(0, 999):03d}.00"

        w, h = 760, 620
        img = Image.new("RGB", (w, h), "#ffffff")
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, w, 120], fill="#0e7a4f")
        d.text((44, 38), "Payment Successful", font=_font(34, True), fill="#ffffff")

        rows = [("Paid To", payee), ("Amount", f"INR {amount}"),
                ("UTR No.", utr), ("Payment ID", txn), ("Status", "Success")]
        y = 175
        for label, value in rows:
            d.text((44, y), label, font=_font(22), fill="#6b7684")
            d.text((300, y - 2), value, font=_font(24, True), fill="#111820")
            d.line([(44, y + 46)], fill="#e4e8ee")
            y += 82

        cases.append(Case(f"receipt{i+1}", img,
                          {"utr": utr, "txn_id": txn, "payee": payee}))
    return cases


# --------------------------------------------------------------------------- #
# Degradations -- what real screenshots actually suffer from
# --------------------------------------------------------------------------- #

def _shrink(img, factor=0.45):
    return img.resize((int(img.width * factor), int(img.height * factor)), Image.LANCZOS)


def _jpeg(img, quality=28):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def _noise(img, amount=22):
    import numpy as np
    arr = np.asarray(img).astype(np.int16)
    rng = np.random.default_rng(3)
    arr = arr + rng.normal(0, amount, arr.shape)
    return Image.fromarray(arr.clip(0, 255).astype("uint8"))


DEGRADATIONS = {
    "clean":       lambda im: im,
    "small":       _shrink,
    "blur":        lambda im: im.filter(ImageFilter.GaussianBlur(1.1)),
    "jpeg":        _jpeg,
    "lowcontrast": lambda im: ImageEnhance.Contrast(im).enhance(0.42),
    "noisy":       _noise,
    "small+jpeg":  lambda im: _jpeg(_shrink(im, 0.5), 34),
}

ID_FIELDS = ["utr", "txn_id"]


# --------------------------------------------------------------------------- #
# Measurement
# --------------------------------------------------------------------------- #

def evaluate(mode: str, cases: list[Case], verbose: bool = False,
             degradations: dict | None = None) -> dict:
    degradations = degradations or DEGRADATIONS
    hits = {f: 0 for f in ID_FIELDS}
    top1 = {f: 0 for f in ID_FIELDS}
    total = {f: 0 for f in ID_FIELDS}
    by_degradation: dict[str, list[int]] = {k: [0, 0] for k in degradations}
    misses: list[str] = []
    started = time.perf_counter()

    for idx, case in enumerate(cases, start=1):
        for dname, degrade in degradations.items():
            img = degrade(case.image)
            try:
                result = core.read_image(img, engine="auto", accuracy=mode)
                found = core.extract_fields(result, enabled=("utr", "txn_id", "payee"))
            except Exception as exc:
                misses.append(f"{case.name}/{dname}: ERROR {exc}")
                for f in ID_FIELDS:
                    total[f] += 1
                    by_degradation[dname][1] += 1
                continue

            for fkey in ID_FIELDS:
                total[fkey] += 1
                by_degradation[dname][1] += 1
                got = found.get(fkey, [])
                values = [m.value for m in got]

                # Two separate numbers, deliberately. top1 is what the tool
                # actually asserts; offered is only what the user could reach by
                # clicking an alternate. Merging them would let a pile of
                # speculative candidates masquerade as accuracy.
                if values and values[0] == case.truth[fkey]:
                    top1[fkey] += 1
                if case.truth[fkey] in values:
                    hits[fkey] += 1
                    by_degradation[dname][0] += 1
                else:
                    misses.append(
                        f"  {case.name}/{dname:<11} {fkey:<7} "
                        f"want {case.truth[fkey]!r} got {values or '[]'}")

        done = sum(sum(v) for v in [[by_degradation[k][1]] for k in degradations])
        print(f"    [{mode}] {idx}/{len(cases)} receipts, "
              f"{time.perf_counter() - started:.0f}s elapsed", flush=True)

    overall_hits = sum(hits.values())
    overall_total = sum(total.values())
    return {
        "mode": mode,
        "hits": hits,
        "top1": top1,
        "total": total,
        "rate": overall_hits / overall_total if overall_total else 0.0,
        "top1_rate": sum(top1.values()) / overall_total if overall_total else 0.0,
        "by_degradation": by_degradation,
        "misses": misses,
    }


def print_report(rep: dict) -> None:
    print(f"\n=== mode: {rep['mode']} ===")
    print(f"  {'field':<8} {'top-1 (asserted)':>18} {'among candidates':>18}")
    for f in ID_FIELDS:
        d = rep["total"][f]
        print(f"  {f:<8} {rep['top1'][f]:>8}/{d:<3} {rep['top1'][f]/d:>6.1%} "
              f"{rep['hits'][f]:>8}/{d:<3} {rep['hits'][f]/d:>6.1%}")
    d = sum(rep["total"].values())
    print(f"  {'OVERALL':<8} {sum(rep['top1'].values()):>8}/{d:<3} {rep['top1_rate']:>6.1%} "
          f"{sum(rep['hits'].values()):>8}/{d:<3} {rep['rate']:>6.1%}")
    print("  by degradation:")
    for k, (n, d) in rep["by_degradation"].items():
        bar = "#" * round((n / d if d else 0) * 24)
        print(f"    {k:<12} {n:>3}/{d:<3} {n/d if d else 0:>6.1%}  {bar}")
    if rep["misses"]:
        print("  misses:")
        for m in rep["misses"][:14]:
            print(m)
        if len(rep["misses"]) > 14:
            print(f"    ... and {len(rep['misses']) - 14} more")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default=None)
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--count", type=int, default=8)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--degradations", default="",
                    help="comma list; default is all of "
                         + ",".join(DEGRADATIONS))
    args = ap.parse_args()

    if not core.available_engines():
        print("No OCR engine installed.")
        return 1

    chosen = DEGRADATIONS
    if args.degradations:
        want = [d.strip() for d in args.degradations.split(",") if d.strip()]
        chosen = {k: v for k, v in DEGRADATIONS.items() if k in want}
        unknown = set(want) - set(DEGRADATIONS)
        if unknown:
            print(f"Unknown degradation(s): {', '.join(sorted(unknown))}")
            return 1
    if not chosen:
        print("No degradations selected.")
        return 1

    cases = make_cases(args.count)
    print(f"{len(cases)} receipts x {len(chosen)} degradations "
          f"x {len(ID_FIELDS)} id fields "
          f"= {len(cases) * len(chosen) * len(ID_FIELDS)} checks per mode")

    modes = ["fast", "balanced", "high"] if args.compare else [args.mode or "fast"]
    reports = []
    for m in modes:
        rep = evaluate(m, cases, verbose=args.verbose, degradations=chosen)
        print_report(rep)
        reports.append(rep)

    if len(reports) > 1:
        print("\n=== summary ===")
        base = reports[0]["top1_rate"]
        for r in reports:
            delta = (f"{(r['top1_rate'] - base) * 100:+.1f} pts"
                     if r is not reports[0] else "baseline")
            print(f"  {r['mode']:<10} top-1 {r['top1_rate']:>6.1%}   "
                  f"candidates {r['rate']:>6.1%}   {delta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
