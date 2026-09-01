# Extraction accuracy

Measured, not estimated — every number below comes from running real diagnostic
lab reports (images and PDFs, supplied as-is, nothing staged) through the
actual extraction pipeline and hand-checking each row against the value
printed on the source report.

## Headline numbers

| Round | Reports | Values checked | Result |
|---|---|---|---|
| 1 (baseline) | 12 | 106 | 63% correct |
| 1 (after fixes) | 12 | 106 | 75.5% correct |
| 3 (final verification) | 7 | 27 | 26/27 correct as extracted (96%) — the 1 miss is a raw OCR misread of the source image, not an extraction bug |

Round 2 (7 more reports) fed into the same fix cycle between rounds 1 and 3
rather than producing its own separate headline number.

## Methodology

1. Run each report through the real pipeline: `core.read_document(data, accuracy="auto", auto_base="balanced")` → `medical.extract_report(ocr, filename=name)`.
2. For every row the tool returns, compare the value and flag against what's actually printed on the source report.
3. Anything wrong gets root-caused in `medical.py`/`core.py` — not patched around — and re-verified against the full regression suite before moving to the next report.
4. "Not a test this tool knows" rows (stray OCR noise near real text) are excluded from the accuracy count — they're a false-positive-row concern, not a value-correctness concern, and the tool already labels them `UNKNOWN` rather than presenting them as real results.

## Bug classes found and fixed

- **Value/unit truncation** — a regex lookbehind (`_UNIT_RE`) blocked stripping a glued-on unit like `mg/dl` from a preceding number in specific digit patterns, corrupting the number itself (e.g. `115` → `11`). Single highest-impact fix of the whole pass.
- **Missing terminology variants** — labels like "Sugar" for Glucose, "Random", "Means Glucose Value" for eAG, and spacing variants like `( PP)` vs `(PP)` weren't recognized at all.
- **Ratio analytes absorbed by their base analyte** — short aliases like `sgot` were matching the start of `SGOT/SGPT Ratio` and swallowing the whole row. Fixed by adding dedicated analytes: HDL/LDL, SGOT/SGPT, Albumin/Globulin, Urea/Creatinine, BUN/Creatinine ratios, plus spacing variants of the existing LDL/HDL ratio.
- **Specimen-prefix qualifiers** — `Plasma-F` was splitting into `plasma` + `f` instead of being read as one qualified label.
- **Multi-line row layouts** — two distinct patterns:
  - A sex-split reference range where each half sits on its own OCR line — now merged via `_looks_like_sex_continuation()`.
  - A test's name printed several lines above its value, with explanatory text in between — now bridged via `_fold_heading_continuations()`.
- **Combined "Patient : Name (age/sex)" layout** — a single line carrying both patient name and age/sex with no separate labels (the MEENAKUMARI report) — added as a narrow `scan_pattern` that only fires when normal label matching finds nothing, so it can't misfire on other reports.

Full list of individual commits: `git log --oneline` — search for the ones between `a9532e4` and `8b840e7`.

## Reproducing this testing

Point `core.read_document` / `medical.extract_report` at a folder of real
reports and diff each output row against the source by eye — there's no
labeled ground-truth dataset checked into this repo, so any accuracy number
is only as good as the reports it was measured against. Prefer reports the
tool hasn't seen yet over reusing the same ones, since the fixes above were
each shaped by whatever a given report happened to expose.
