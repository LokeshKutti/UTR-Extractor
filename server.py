"""
UTR / Payment Detail Extractor -- HTTP API
==========================================
FastAPI backend. All the real work lives in core.py; this file only exposes it
over HTTP and serves the frontend in web/.

    python server.py                start on http://127.0.0.1:8000
    python server.py --port 9000    pick a different port

Endpoints
---------
    GET  /                  the interface (web/index.html)
    GET  /api/config        available OCR engines and field definitions
    POST /api/extract       upload images -> extracted fields as JSON
    POST /api/export        edited results -> a downloadable document

The export step deliberately takes the *edited* values back from the browser
rather than re-reading server state, so a correction made in the interface is
the value that lands in the document.
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import sys
import threading
import time
from collections import defaultdict
from typing import Any

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

import ai_assist
import core
import medical
from core import (BUILTIN_FIELDS, DEFAULT_ENABLED, ExtractionRecord, Match,
                  NoEngineAvailable, available_engines, build_csv, build_docx,
                  build_json, build_pdf, build_txt, classify_utr, extract_fields,
                  load_image, make_custom_rule, read_document)
from medical import AnalyteResult, BloodReport

WEB_DIR = core.HERE / "web"
INDEX = WEB_DIR / "index.html"

# Guards against a browser sending something enormous or absurd.
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGES_PER_REQUEST = 40
THUMBNAIL_WIDTH = 520

# False for local dev and the desktop build; a public deployment (see
# DEPLOY.md) sets this explicitly so the interface can tell visitors their
# upload is leaving their machine, which is only true in that one context.
PUBLIC_DEPLOYMENT = os.environ.get("PUBLIC_DEPLOYMENT", "").strip() == "1"

# Set when the frontend is hosted separately from this API (e.g. a static
# site on Vercel calling a Render-hosted backend) -- same-origin deployments,
# including local dev and the desktop build, never need this. A comma
# separated list of exact origins, e.g. "https://my-app.vercel.app". Vercel's
# per-deploy preview URLs are matched by the regex below instead, since they
# change on every push and can't be listed individually up front.
_ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGIN", "").split(",") if o.strip()]

app = FastAPI(title="UTR / Payment Detail Extractor", docs_url="/api/docs")

if _ALLOWED_ORIGINS:
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOWED_ORIGINS,
        allow_origin_regex=r"https://.*\.vercel\.app",
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )


def _client_ip(request: Request) -> str:
    """
    The visitor's real address, not the reverse proxy's.

    Render (like every PaaS that terminates TLS at its own edge) sits in
    front of the app, so request.client.host is the platform's internal
    proxy address for every single visitor -- keying the rate limit on that
    would silently turn a per-IP cap into one shared budget for the entire
    site. X-Forwarded-For's first hop is the actual client; only trust it
    when a deployment mode that implies a proxy is actually in front of us.
    """
    if PUBLIC_DEPLOYMENT:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# A locally-run copy has exactly one trusted user, so this never mattered
# before. A publicly reachable copy has no login at all, so a per-IP cap on
# the expensive (OCR-driven) routes is the only thing between an open upload
# form and someone scripting it into an unusable or costly server. In-memory
# and best-effort by design -- it does not need to be precise, only to make
# unattended hammering pointless; a restart or a second instance behind a
# load balancer simply resets it.
_RATE_LIMITED_PREFIXES = ("/api/extract", "/api/bloodtest/extract", "/api/ai/")
_RATE_LIMIT_WINDOW_S = 60.0
_RATE_LIMIT_MAX_REQUESTS = 20
_rate_lock = threading.Lock()
_rate_buckets: dict[str, list[float]] = defaultdict(list)


@app.middleware("http")
async def _rate_limit(request: Request, call_next):
    if request.method == "POST" and request.url.path.startswith(_RATE_LIMITED_PREFIXES):
        client = _client_ip(request)
        now = time.monotonic()
        cutoff = now - _RATE_LIMIT_WINDOW_S
        with _rate_lock:
            bucket = _rate_buckets[client]
            while bucket and bucket[0] < cutoff:
                bucket.pop(0)
            if len(bucket) >= _RATE_LIMIT_MAX_REQUESTS:
                return JSONResponse(
                    {"error": "Too many requests from this address -- wait a "
                              "minute and try again."},
                    status_code=429,
                )
            bucket.append(now)
    return await call_next(request)


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #

class ExportField(BaseModel):
    """One field as the browser currently shows it, edits included."""
    key: str
    label: str
    value: str
    confidence: float = 0.0
    method: str = "manual"
    note: str = ""


class ExportRecord(BaseModel):
    filename: str
    engine: str = ""
    ocr_confidence: float = 0.0
    elapsed: float = 0.0
    full_text: str = ""
    fields: list[ExportField] = Field(default_factory=list)


class ExportRequest(BaseModel):
    format: str
    records: list[ExportRecord]
    include_raw_text: bool = False


class LabRow(BaseModel):
    """One analyte row as the interface currently shows it, edits included."""
    key: str = ""
    name: str
    panel: str = "Other"
    value: str = ""
    unit: str = ""
    ref_text: str = ""
    ref_source: str = "report"
    flag: str = "unknown"
    note: str = ""


class LabReportPayload(BaseModel):
    filename: str
    engine: str = ""
    ocr_confidence: float = 0.0
    elapsed: float = 0.0
    full_text: str = ""
    meta: dict[str, str] = Field(default_factory=dict)
    rows: list[LabRow] = Field(default_factory=list)


class LabExportRequest(BaseModel):
    format: str
    reports: list[LabReportPayload]
    include_raw_text: bool = False


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _match_to_dict(m: Match) -> dict[str, Any]:
    return {
        "key": m.key,
        "label": m.label,
        "value": m.value,
        "raw_value": m.raw_value,
        "confidence": round(m.confidence, 3),
        "method": m.method,
        "note": m.note,
        "context": m.context,
    }


def _thumbnail(data: bytes) -> tuple[str, str]:
    """
    Shrink an upload to a data: URI for the preview pane.

    Sending the original back would mean pushing multi-megabyte screenshots
    over the wire a second time for no visual gain.
    """
    try:
        img = core.load_pages(data)[0]
        if img.width > THUMBNAIL_WIDTH:
            ratio = THUMBNAIL_WIDTH / img.width
            img = img.resize((THUMBNAIL_WIDTH, int(img.height * ratio)))
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=78)
        return ("data:image/jpeg;base64,"
                + base64.b64encode(buffer.getvalue()).decode()), ""
    except Exception as exc:
        # Returning "" alone made a systematic failure look like "no preview
        # available" forever. The reason travels with it so the interface can
        # say what went wrong.
        return "", f"Preview unavailable: {exc}"


def _record_from_payload(rec: ExportRecord) -> ExtractionRecord:
    """
    Rebuild a core record from what the browser sent back.

    Fields arrive as a flat list because the interface lets the user reorder and
    edit them; they are regrouped by key here so the exporters see the same
    shape they always do.
    """
    grouped: dict[str, list[Match]] = {}
    for f in rec.fields:
        if not f.value.strip():
            continue          # cleared in the interface means "leave it out"
        grouped.setdefault(f.key, []).append(
            Match(key=f.key, label=f.label, value=f.value,
                  confidence=f.confidence, method=f.method, note=f.note)
        )

    return ExtractionRecord(
        filename=rec.filename,
        fields=grouped,
        full_text=rec.full_text,
        engine=rec.engine,
        ocr_confidence=rec.ocr_confidence,
        elapsed=rec.elapsed,
    )


EXPORTERS = {
    "docx": (build_docx, "utr_extract.docx",
             "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "pdf": (build_pdf, "utr_extract.pdf", "application/pdf"),
    "csv": (build_csv, "utr_extract.csv", "text/csv"),
    "json": (build_json, "utr_extract.json", "application/json"),
    "txt": (build_txt, "utr_extract.txt", "text/plain"),
}


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

@app.get("/")
def index() -> FileResponse:
    if not INDEX.exists():
        raise HTTPException(500, f"Interface missing: {INDEX}")
    return FileResponse(INDEX)


@app.get("/api/config")
def config() -> JSONResponse:
    """Everything the interface needs to draw its settings panel."""
    return JSONResponse({
        "engines": available_engines(),
        "default_enabled": DEFAULT_ENABLED,
        "fields": [
            {"key": r.key, "label": r.label, "multi": r.multi}
            for r in BUILTIN_FIELDS
        ],
        "max_images": MAX_IMAGES_PER_REQUEST,
        "max_image_mb": MAX_IMAGE_BYTES // (1024 * 1024),
        "accepts_pdf": True,
        "max_pdf_pages": core.MAX_PDF_PAGES,
        # The two document kinds are wholly separate: different endpoints,
        # different result shapes, different sensible defaults.
        "doc_types": [
            {"key": "payment", "label": "Payment receipts",
             "hint": "UTR, transaction id, amount, payer and payee.",
             "default_accuracy": "auto"},
            {"key": "bloodtest", "label": "Blood test reports",
             "hint": "Patient details and every test row with its reference range.",
             "default_accuracy": "auto"},
        ],
        "lab_disclaimer": medical.DISCLAIMER,
        # The interface's own "everything stays on your machine" line is only
        # true for the desktop build. Set PUBLIC_DEPLOYMENT=1 on a hosted copy
        # (see Dockerfile / DEPLOY.md) so it tells visitors the truth instead.
        "deployment": {
            "public": PUBLIC_DEPLOYMENT,
            "notice": (
                "This copy runs on a shared server, not your own device -- "
                "uploads leave your computer to get here. Files are read into "
                "memory to extract results and are never written to disk or "
                "stored; closing the tab discards them."
            ) if PUBLIC_DEPLOYMENT else "",
        },
        # Whether the optional Gemini fallback is usable. The key itself
        # never leaves the server -- only whether one is configured.
        "ai": ai_assist.status(),
        # Labels state what was actually measured. On the bundled benchmark the
        # extra reads did not improve the top answer -- they only produce more
        # candidates -- so Fast is the honest default.
        "accuracy_modes": [
            {"key": "auto", "label": "Auto (default)",
             "hint": "Checks the image for blur or low resolution first, and "
                     "reads it more times only if that looks warranted."},
            {"key": "fast", "label": "Fast — 2 reads",
             "hint": "~3s per image. Scored best on top-1 in the benchmark."},
            {"key": "balanced", "label": "Thorough — 4 reads",
             "hint": "~11s. More candidates on poor scans; no measured gain "
                     "on the top answer."},
            {"key": "high", "label": "Maximum — 6 reads",
             "hint": "~18s. Most OCR candidates. Use when a scan is genuinely "
                     "bad and Fast returns nothing usable."},
        ],
    })


@app.post("/api/extract")
def extract(
    images: list[UploadFile] = File(...),
    engine: str = Form("auto"),
    preprocess: str = Form("auto"),
    accuracy: str = Form("auto"),
    fields: str = Form(",".join(DEFAULT_ENABLED)),
    custom_label: str = Form(""),
    custom_keywords: str = Form(""),
    custom_pattern: str = Form(""),
) -> JSONResponse:
    """OCR each uploaded image and return its extracted fields."""
    if not images:
        raise HTTPException(400, "No images uploaded.")
    if len(images) > MAX_IMAGES_PER_REQUEST:
        raise HTTPException(
            400, f"Too many images at once (limit {MAX_IMAGES_PER_REQUEST})."
        )

    enabled = tuple(f for f in fields.split(",") if f.strip())
    custom_rules = []
    if custom_label.strip():
        try:
            custom_rules.append(
                make_custom_rule(custom_label, custom_keywords, custom_pattern))
        except core.UnsafePattern as exc:
            raise HTTPException(400, str(exc))

    results: list[dict[str, Any]] = []

    for upload in images:
        payload = upload.file.read()
        name = upload.filename or "image"

        if len(payload) > MAX_IMAGE_BYTES:
            results.append({
                "filename": name,
                "error": f"Too large (limit {MAX_IMAGE_BYTES // (1024*1024)} MB).",
            })
            continue

        try:
            ocr = read_document(payload, engine=engine, preprocess_mode=preprocess,
                                accuracy=accuracy, auto_base="fast")
            found = extract_fields(ocr, enabled=enabled, custom_rules=custom_rules)
        except NoEngineAvailable as exc:
            raise HTTPException(503, str(exc))
        except Exception as exc:
            # One unreadable file must not sink the whole batch.
            results.append({"filename": name, "error": str(exc)})
            continue

        thumbnail, thumbnail_error = _thumbnail(payload)
        utr = next(iter(found.get("utr", [])), None)
        results.append({
            "filename": name,
            "engine": ocr.engine,
            "ocr_confidence": round(ocr.mean_conf, 3),
            "elapsed": round(ocr.elapsed, 3),
            "full_text": ocr.text,
            "thumbnail": thumbnail,
            "thumbnail_error": thumbnail_error,
            "quality": ocr.quality,
            "accuracy_used": ocr.accuracy_used,
            "escalated": ocr.escalated,
            "utr_kind": classify_utr(utr.value) if utr else "",
            "fields": [
                {"key": key, "label": hits[0].label,
                 "matches": [_match_to_dict(m) for m in hits]}
                for key, hits in found.items()
            ],
        })

    return JSONResponse({"results": results})


# --------------------------------------------------------------------------- #
# Blood test reports -- deliberately separate endpoints
# --------------------------------------------------------------------------- #
#
# Payments and lab reports do not share a response shape, and mixing a
# transaction id into a table of analytes would be worse than useless. They get
# their own routes so neither schema has to carry the other's fields.

@app.post("/api/bloodtest/extract")
def bloodtest_extract(
    images: list[UploadFile] = File(...),
    engine: str = Form("auto"),
    preprocess: str = Form("auto"),
    # Lab reports default to more reads than payments do. Measured on the
    # bundled sample, the extra passes lift row recall from 11/12 to 12/12 --
    # a faint row either parses or it does not, so more attempts genuinely help.
    accuracy: str = Form("auto"),
) -> JSONResponse:
    """OCR each uploaded lab report and return its rows."""
    if not images:
        raise HTTPException(400, "No images uploaded.")
    if len(images) > MAX_IMAGES_PER_REQUEST:
        raise HTTPException(400, f"Too many images (limit {MAX_IMAGES_PER_REQUEST}).")

    results: list[dict[str, Any]] = []

    for upload in images:
        payload = upload.file.read()
        name = upload.filename or "report"

        if len(payload) > MAX_IMAGE_BYTES:
            results.append({"filename": name,
                            "error": f"Too large (limit "
                                     f"{MAX_IMAGE_BYTES // (1024*1024)} MB)."})
            continue

        try:
            ocr = read_document(payload, engine=engine, preprocess_mode=preprocess,
                                accuracy=accuracy, auto_base="balanced")
            report = medical.extract_report(ocr, filename=name)
        except NoEngineAvailable as exc:
            raise HTTPException(503, str(exc))
        except Exception as exc:
            results.append({"filename": name, "error": str(exc)})
            continue

        thumbnail, thumbnail_error = _thumbnail(payload)
        labels = {r.key: r.label for r in medical.META_RULES}
        results.append({
            "filename": name,
            "engine": report.engine,
            "ocr_confidence": round(report.ocr_confidence, 3),
            "elapsed": round(report.elapsed, 3),
            "full_text": report.full_text,
            "thumbnail": thumbnail,
            "thumbnail_error": thumbnail_error,
            "quality": ocr.quality,
            "accuracy_used": ocr.accuracy_used,
            "escalated": ocr.escalated,
            "meta": [{"key": k, "label": labels.get(k, k), "value": v}
                     for k, v in report.meta.items() if v],
            "rows": [{
                "key": r.key, "name": r.name, "panel": r.panel,
                "value": r.value, "unit": r.unit, "ref_text": r.ref_text,
                "ref_source": r.ref_source, "flag": r.flag,
                "confidence": r.confidence, "note": r.note,
            } for r in report.results],
            "out_of_range": len(report.out_of_range),
            "panels": report.panels,
        })

    return JSONResponse({"results": results, "disclaimer": medical.DISCLAIMER})


@app.post("/api/bloodtest/export")
def bloodtest_export(request: LabExportRequest) -> Response:
    """Turn the current (edited) lab rows into a downloadable document."""
    if request.format not in medical.EXPORTERS:
        raise HTTPException(400, f"Unknown format '{request.format}'.")
    if not request.reports:
        raise HTTPException(400, "Nothing to export.")

    builder, default_name, mime = medical.EXPORTERS[request.format]

    reports = [
        BloodReport(
            filename=payload.filename, meta=payload.meta,
            full_text=payload.full_text, engine=payload.engine,
            ocr_confidence=payload.ocr_confidence, elapsed=payload.elapsed,
            # Flags are recomputed here, never taken from the request. The
            # browser's flag was right for the value it was computed from, and
            # that value may since have been edited -- a document carrying a
            # stale HIGH beside a corrected number is worse than one carrying no
            # verdict at all. Rows the tool does not recognise stay unjudged.
            results=[
                AnalyteResult(
                    key=row.key, name=row.name, panel=row.panel,
                    value=row.value, numeric=None, unit=row.unit,
                    ref_text=row.ref_text, ref_source=row.ref_source,
                    flag=("unknown" if row.key.startswith("other::")
                          else medical.flag_for(row.value, row.ref_text)),
                    note=row.note,
                )
                for row in payload.rows if row.value.strip()
            ],
        )
        for payload in request.reports
    ]

    try:
        blob = builder(reports) if request.format == "csv" \
            else builder(reports, request.include_raw_text)
    except Exception as exc:
        raise HTTPException(500, f"Could not build the {request.format}: {exc}")

    if len(reports) == 1:
        stem = reports[0].filename.rsplit(".", 1)[0][:40] or "blood_report"
        name = f"{stem}.{request.format}"
    else:
        name = default_name

    return Response(content=blob, media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# --------------------------------------------------------------------------- #
# Gemini fallback -- opt in, per image
# --------------------------------------------------------------------------- #
#
# Never called automatically. The local pipeline runs first on every upload;
# these routes exist so a person can decide, for one image at a time, that it is
# worth sending that page to Google.

@app.post("/api/ai/extract")
def ai_extract(images: list[UploadFile] = File(...)) -> JSONResponse:
    """Transcribe one receipt with Gemini."""
    return _ai_call(images, lambda data: {"rows": ai_assist.read_payment(data)})


@app.post("/api/ai/bloodtest")
def ai_bloodtest(images: list[UploadFile] = File(...)) -> JSONResponse:
    """Transcribe one lab report with Gemini."""
    return _ai_call(images, ai_assist.read_bloodtest)


def _ai_call(images: list[UploadFile], handler) -> JSONResponse:
    if not ai_assist.available():
        info = ai_assist.status()
        missing = ("the google-genai package" if not info["sdk_installed"]
                   else "a Gemini API key")
        raise HTTPException(400, f"AI reading is not set up: {missing} is missing.")
    if len(images) != 1:
        raise HTTPException(400, "Send one image at a time to the AI reader.")

    payload = images[0].file.read()
    if len(payload) > MAX_IMAGE_BYTES:
        raise HTTPException(400, f"Too large (limit "
                                 f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB).")
    try:
        result = handler(payload)
    except Exception as exc:
        raise HTTPException(502, f"Gemini could not read this image: {exc}")

    result["source"] = "gemini"
    result["model"] = ai_assist.DEFAULT_MODEL
    return JSONResponse(result)


@app.post("/api/export")
def export(request: ExportRequest) -> Response:
    """Turn the current (edited) results into a downloadable document."""
    if request.format not in EXPORTERS:
        raise HTTPException(400, f"Unknown format '{request.format}'.")
    if not request.records:
        raise HTTPException(400, "Nothing to export.")

    builder, default_name, mime = EXPORTERS[request.format]
    records = [_record_from_payload(r) for r in request.records]

    try:
        if request.format == "csv":
            blob = builder(records)
        else:
            blob = builder(records, request.include_raw_text)
    except Exception as exc:
        raise HTTPException(500, f"Could not build the {request.format}: {exc}")

    if len(records) == 1:
        stem = records[0].filename.rsplit(".", 1)[0][:40] or "utr_extract"
        name = f"{stem}.{request.format}"
    else:
        name = default_name

    return Response(
        content=blob,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(description="UTR Extractor server")
    parser.add_argument("--host", default="127.0.0.1")
    # Cloud hosts (Render, Railway, etc.) assign a port at start-up and pass it
    # in via $PORT rather than a fixed number -- the env var is only a
    # fallback default, so a local `python server.py` is unaffected.
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    parser.add_argument("--reload", action="store_true", help="auto-reload on edit")
    args = parser.parse_args()

    engines = available_engines()
    if not engines:
        print("WARNING: no OCR engine found. Install one with:")
        print("    pip install rapidocr-onnxruntime\n")
    else:
        print(f"OCR engines: {', '.join(engines)}")

    print(f"Open http://{args.host}:{args.port}\n")
    uvicorn.run("server:app" if args.reload else app,
                host=args.host, port=args.port, reload=args.reload,
                log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
