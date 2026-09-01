# Document Extractor

Two separate extractors sharing one OCR engine and one interface:

| Mode | Pulls out | Module |
|---|---|---|
| **Payment receipts** | UTR, transaction id, amount, payer, payee, status | `core.py` |
| **Blood test reports** | Patient details and every test row with its reference range | `medical.py` |

Pick the mode with the switcher at the top of the page. **The two never mix** —
separate endpoints, separate result shapes, separate exports, and switching
modes clears whatever was on screen. A transaction id will never appear in a
table of blood test results.

---

# Payment receipts

Upload a payment screenshot or receipt image. The tool reads it, pulls out the
UTR and other transaction details, shows them in the sidebar, and hands you a
document to download.

**Python API + vanilla-JS frontend.** No Node, no npm, no build step, and no
system binaries — the OCR engine ships as a pip wheel. Everything runs locally.

---

## Quick start

Double-click **`run.bat`**, or from a terminal:

```bash
cd E:\utr-extractor && .venv\Scripts\python.exe server.py
```

Then open <http://127.0.0.1:8000>.

```bash
cd E:\utr-extractor && .venv\Scripts\python.exe server.py --port 9000 --reload
```

`--reload` restarts the server when you edit the Python.

### First-time setup (already done on this machine)

```bash
py -3.13 -m venv .venv && .venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Check everything works

```bash
cd E:\utr-extractor && .venv\Scripts\python.exe core.py
```

Draws a synthetic receipt, runs it through OCR and extraction, writes all five
export formats into `samples/`, and asserts the values came back right.

---

## Layout

| File | Role |
|---|---|
| `core.py` | OCR, field extraction, document builders, self test. No web code. |
| `server.py` | FastAPI app — the HTTP API and static hosting. |
| `web/index.html` | The whole interface: HTML, CSS and JS in one file. |
| `requirements.txt` | Dependencies. |
| `run.bat` | Double-click launcher. |
| `samples/` | Written by the self test; safe to delete. |

`core.py` is split into four banner-marked sections: **OCR**, **EXTRACTION**,
**EXPORT**, **SELF TEST**. To add a field permanently, add a `FieldRule` to
`BUILTIN_FIELDS` in the extraction section — the frontend picks it up
automatically from `/api/config`, no JS changes needed.

### API

| Endpoint | Purpose |
|---|---|
| `GET /` | The interface. |
| `GET /api/config` | Available OCR engines and field definitions. |
| `POST /api/extract` | Multipart image upload → extracted fields as JSON. |
| `POST /api/export` | Current (edited) values → a downloadable document. |
| `GET /api/docs` | Auto-generated API docs, courtesy of FastAPI. |

Export takes the values back *from the browser* rather than re-reading server
state, so a correction made in the interface is exactly what lands in the file.

---

## What it pulls out

| Field | Notes |
|---|---|
| **UTR / Reference Number** | 12-digit UPI/IMPS, 16-char NEFT, 22-char RTGS, and generic bank refs. The format is identified for you. |
| **Transaction ID** | Txn ID, UPI ref, order ID, payment ID — including gateway ids like `pay_TR6FFa1mQcOkwM`. |
| **Amount** | Handles `₹`, `Rs.`, `INR`, and Indian comma grouping (`1,20,500.00`). |
| **Date** and **Time** | `18/08/2026`, `18 Aug 2026`, `Aug 18, 2026`, `2026-08-18`. |
| **Paid To / Beneficiary**, **Paid By / Sender** | |
| **Status** | Success, Failed, Pending, Processing, Declined… |
| **UPI ID (VPA)** | `name@okhdfcbank`. Email addresses are excluded. |
| **IFSC Code**, **Account Number**, **Payment Mode** | Off by default — switch them on under Settings. |

### Extracting something else

Under **Custom field** in the sidebar, give it a **label**, some **label
keywords** (the text printed next to the value on the image), and optionally a
**regex** the value must match. Keywords alone search by label, a regex alone
scans the whole image, both together does label-first then falls back to
scanning.

---

## How extraction works

Four strategies run per field, strongest first, and each hit carries a
confidence so you can see what to trust:

| Strategy | Looks like | Trust |
|---|---|---|
| `inline` | `UTR No: 312845967103` — label and value in one text box | highest |
| `same-line` | `[UTR No] [312845967103]` — value in the box beside the label | high |
| `next-line` | `UTR No` with the value directly below | medium |
| `scan` | a bare `312845967103` with no label anywhere | lowest |

Every score is also scaled by the OCR engine's own confidence for that box, so
a blurry screenshot honestly reports lower numbers rather than pretending.

**Confidence is a hint, not a guarantee.** Anything marked LOW is worth a
glance — that is what the editable value boxes are for.

Details worth knowing:

- **Bad rupee glyphs.** OCR often misreads `₹` as `7`, `3`, or a CJK character.
  Money-shaped numbers are therefore matched on their own too, just trusted a
  little less than ones with a clean currency prefix.
- **Gateway IDs keep their case.** Razorpay/Stripe ids such as
  `pay_TR6FFa1mQcOkwM` are case-sensitive and the prefix is part of the id, so
  they are never upper-cased the way a bank reference is. The prefix also makes
  them safe to spot with no label at all, which is common on gateway receipts.
- **Digit repair.** In a mostly-numeric reference, stray letters are mapped back
  to the digits they were probably meant to be (`O`→`0`, `I`→`1`, `S`→`5`).
  The change is recorded in the export notes.
- **Phone numbers.** A bare 12-digit token shaped like `91XXXXXXXXXX` is flagged
  rather than silently reported as a UTR.

## Measured ID accuracy

`bench.py` generates receipts with known IDs, degrades them (small, JPEG,
low-contrast, small+JPEG), and checks whether the exact string comes back.

```bash
cd E:\utr-extractor && .venv\Scripts\python.exe bench.py --compare --count 4
```

Last run, 40 ID checks per mode:

| Mode | Top-1 (asserted) | Among candidates | Time/image |
|---|---|---|---|
| Fast — 2 reads | **75.0%** | 95.0% | 3.0s |
| Thorough — 4 reads | 72.5% | 95.0% | 11.0s |
| Maximum — 6 reads | 72.5% | 95.0% | 18.2s |

Broken down by field, the picture is much clearer than the totals suggest:

| Field | Top-1 | Among candidates |
|---|---|---|
| **UTR / bank reference** | **100%** | **100%** |
| Gateway ID (`pay_…`) | 45–55% | 90% |

Two columns, not one, on purpose. **Top-1** is what the tool asserts.
**Among candidates** is only what you can reach by clicking an alternate. Blending
them would let a pile of speculative suggestions pose as accuracy.

### What this means in practice

- **Bank UTRs are solved.** 100% under every degradation, in every mode.
- **Reading the image more times does not help.** Thorough and Maximum scored no
  better than Fast — and slightly worse on top-1. Every miss is the *same*
  glyph confusion in every pass, and voting can only resolve disagreement.
  That is why Fast is the default; the slower modes remain for genuinely bad
  scans where you want more raw OCR candidates to choose from.
- **What did help was offering the alternative reading** (77.5% → 95%).

### The remaining 5%

Cases where OCR dropped a character outright rather than misreading one:

```
want pay_KtJ0RlgLKOmxgJ   (18 chars)
got  pay_KtoRIgLKOmxgJ    (17 chars -- the J is simply gone)
```

Glyph substitution cannot recover a character that was never read. Retry such
an image at **Maximum**, or type the ID in directly.

## Fixing a bad read

Every value in the results table is an editable box — type over anything that
came out wrong and it flows straight into the downloads, marked as a manual
correction. Where more than one candidate was found, the alternates appear as
**also found:** chips; click one to use it instead.

---

## Downloads

| Format | Good for |
|---|---|
| **Word (.docx)** | A formatted report — one table per image. |
| **PDF** | The same report, fixed layout, for sharing or filing. |
| **CSV** | Opens in Excel. One row per extracted value. |
| **JSON** | Feeding another script or system. |
| **Plain text** | Quick copy-paste. |

Upload several images at once and you get a batch summary table plus a single
document covering all of them; **This image only** narrows it to the one you
are looking at. **Append full OCR text** adds everything the engine read, which
helps when a field did not match and you want to see why.

---

## When a field does not match

1. Open **Full OCR text** on that image to see what was actually read. If the
   value is not in there, it is an OCR problem, not an extraction problem.
2. Try **Image cleanup → aggressive** in Settings. It grayscales and thresholds
   the image, which helps faint or low-contrast screenshots.
3. Crop the screenshot to just the receipt — background clutter costs accuracy.
4. Still stuck? Add a **Custom field** with the exact label text from the image.

---

---

# Blood test reports

A separate module (`medical.py`) because a lab report is a different shape of
document. A payment receipt is label-and-value — `UTR No: 3128…`. A lab report
is a **table**: every row is `test, result, unit, reference range`. So the
parser works row-wise instead.

```bash
cd E:\utr-extractor && .venv\Scripts\python.exe medical.py
```

That draws a sample lab report, extracts it, writes all five formats into
`samples/`, and checks the flags.

## What it reads

**Patient details** — name, patient/UHID, age & sex, referring doctor,
laboratory, sample collected, reported on, sample type.

**Test rows** — around 50 analytes across Complete Blood Count, Differential
Count, Diabetes, Lipid Profile, Liver Function, Kidney Function, Electrolytes,
Thyroid Profile, Vitamins, Iron Studies and Inflammation. Results are grouped
under their panel in the table.

## The reference range comes from your report

Ranges vary by laboratory, method, age and sex, so **the range printed on the
report always wins.** A built-in typical-adult range is used only when the
report prints none — and when that happens the row is marked
*"not printed on report"* and the export lists exactly which tests it applied to.

A value is marked HIGH or LOW purely by comparing it with that range. That is
arithmetic, not interpretation.

> **This tool transcribes; it does not interpret.** Nothing here diagnoses
> anything or suggests what to do about a result. Check anything that matters
> against the original document and with the doctor who ordered the test.

Edit any result and its flag clears rather than showing a verdict computed from
a value you have since changed.

## Built against real reports, not a tidy template

Every lab prints differently. The parser was tested against five actual
reports — a Thyrocare printout, JOTHI, St. Vincent, Jeyasurya and Mount
Hospitals — arriving as both clean scans and phone photos. What that forced:

| Real-world mess | How it is handled |
|---|---|
| `Mg%`, `MG%`, `mgs/dl`, `mg/dL` for the same unit | all recognised |
| Lab typos — `CHOLESTROL`, `TRIGLERICIDS` | listed as aliases; a typo printed on thousands of reports is not worth being pedantic about |
| `Bl.Sugar (F)`, `BLOOD SUGAR(P.P)`, `Fasting Blood Sugar :` | all map to the same test |
| A method column between name and result (`GOD-POD`, `H.P.L.C`) | skipped |
| Ranges as `60-120`, `UPTO 150`, `( 70 to 110 )`, `Desirable: <200` | all parsed |
| OCR reading `Bl.Sugar` as `BI.Sugar` | names are re-matched with look-alike letters folded, so the row is still found |
| HbA1c control bands (`Non-Diabetes 4.0 to 6.0`) sitting under the results | recognised as interpretation tables and never reported as results |

Across those five layouts: **21 of 21 test rows read, every patient name found.**

### Ranges that differ by sex are not judged

`SERUM HDL 44 MG% men 30-70 women 30-85` has two ranges. Picking the first
would give a confident verdict against possibly the wrong one, so the value and
both ranges are shown with **no flag** and a note saying why. Same for any row
where nothing is printed and there is no sensible fallback.

### Nothing is silently dropped

A fixed dictionary will always miss something. Any row that *looks* like a
measurement but whose test is unknown is still surfaced, under **Other rows**,
named exactly as printed and never flagged. So a report can be read for what is
actually in it rather than only for what was anticipated.

## A missing result stays missing

The parser strips the reference range out of a row *before* looking for the
result. On the bundled sample, OCR failed to read HDL's value at all and the row
came through as `HDL Cholesterol mg/dL > 40` — so the parser reported nothing
rather than mistaking the range bound `40` for the patient's reading.

## Accuracy here behaves differently from payments

Unlike payment IDs, extra reads **do** help lab reports — a faint row either
parses or it does not, so more attempts genuinely recover rows:

| Mode | Rows found | Time |
|---|---|---|
| Fast | 11 / 12 | 4.1s |
| **Thorough (default)** | **12 / 12** | 12.4s |
| Maximum | 12 / 12 | 17.6s |

Thorough reaches full recall, so it is the default for this mode; Maximum costs
40% more for nothing on this sample. Payment mode still defaults to Fast.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/bloodtest/extract` | Lab report images → patient details and rows |
| `POST /api/bloodtest/export` | Current (edited) rows → a document |

## Adding a test

Append an `Analyte` to `ANALYTES` in `medical.py` with its aliases, unit and
fallback range. The parser and every export pick it up; no interface changes.

---

## Running it as a public website instead

The exe above runs entirely on one person's PC and never touches a network.
Putting this behind a real URL that anyone can open is a different trade-off
-- uploaded documents leave the visitor's device to get processed -- so it's
covered separately in [DEPLOY.md](DEPLOY.md), including what changes to make
that honest in the interface itself, and how to actually host it.

## Notes

**Auto accuracy.** The default `Auto` setting reads every image at its normal
tier first (Fast for receipts, Thorough for lab reports), then checks the
read's own confidence and word count. A thin result — low confidence, or
nothing read at all — escalates to the next tier and re-reads, repeating up to
Maximum if needed. The per-image caption says so when it happens: *"image came
back thin at first, so it was read six ways."*

A blur/resolution pre-check was tried first, to pick the starting tier before
OCR ran at all, and was dropped after it produced a real false positive: a
small but perfectly clean crop got misclassified as blurry, because the
measurement itself required upscaling the image to a common reference size,
and that upscaling manufactured the appearance of blur through interpolation
smoothing. Reacting to the OCR engine's own confidence sidesteps that problem
entirely — it reflects what the engine actually saw, not a proxy statistic
computed on pixels beforehand — and correctly stayed quiet on that same small
crop (0.858 confidence on the 3 words it contained) while still catching
genuinely unreadable input (a synthetic pure-noise test came back at 0.000
confidence and correctly escalated to Maximum). The blur/resolution reading is
kept only as explanatory colour in the caption, never as the trigger.

**PDFs.** Images and PDFs are both accepted. PDF pages render at 200 DPI and
merge into one document, so a report split across sheets extracts as a single
report. Capped at 15 pages.

**Photographs.** Before anything else the page is straightened (projection-
profile deskew), and the `high` accuracy mode adds a pass that cancels the
lighting gradient a phone camera leaves behind. Thresholding is Otsu — chosen
from each image's own histogram rather than a fixed cut that suited scans and
ruined dim photos.

**Batching.** Uploads go up in groups of four rather than one long request, so
results appear as they land and **Stop** keeps whatever already arrived.
Stopping mid-group discards that group — the server cannot hand back a
half-finished request.

**Flags are recomputed on export.** The document never carries the browser's
verdict; the server re-derives high/low/normal from the value and range in the
payload. Edit a result and the exported flag follows the new number, or drops
to no verdict if the range no longer settles it.

**Custom patterns are checked.** A regex that nests one repeat inside another
(`(a+)+`) is refused with an explanation instead of hanging the process —
Python's `re` has no timeout and cannot be interrupted.

**Dependency pin.** `rapidocr-onnxruntime` is pinned `>=1.2`, not `>=1.3`. The
1.3+ line caps at Python &lt;3.13, so on Python 3.13 pip would find no
installable version at all. 1.2.3 is the newest that works here.

**Swapping the OCR engine.** The app auto-detects alternatives if you install
them and lists them under Settings:

```bash
cd E:\utr-extractor && .venv\Scripts\python.exe -m pip install easyocr
```

`easyocr` is heavier (it pulls in torch). `pytesseract` also works but needs the
Tesseract binary installed separately, which is why it is not the default.

**Privacy.** The server binds to `127.0.0.1`, so nothing is reachable from
outside your machine. Images are processed in memory and no network calls are
made. Pass `--host 0.0.0.0` only if you deliberately want others on your
network to reach it.
