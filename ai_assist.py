"""
Gemini fallback
===============
Optional. Off unless a key is configured, and never used automatically -- the
local pipeline always runs first and this is offered only when it comes up
short, per image, on request.

    python ai_assist.py       check the key, model and a live round trip

Why it is deliberately narrow
-----------------------------
A vision model completes patterns; that is what it is for. Handed a lab row
whose result is unreadable it will often supply a plausible number, and a
plausible number on a blood test is worse than a blank because nothing marks it
as invented. So this module is constrained hard:

  * it transcribes, and is told to return null rather than guess
  * it never computes a high/low flag -- flags stay arithmetic, in medical.py
  * everything it returns is tagged "ai" so the interface and the exports can
    show it as machine-read rather than read off the page

The key is read from the environment or a .env file. It is never written into
the source, and .env is git-ignored.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
ENV_FILE = HERE / ".env"

# Overridable, because model names change faster than this code will.
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
KEY_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

def _load_env_file() -> None:
    """Read .env without needing python-dotenv, and without overriding a real env var."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if name and value and name not in os.environ:
            os.environ[name] = value


def api_key() -> str | None:
    _load_env_file()
    for name in KEY_NAMES:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def available() -> bool:
    """True when both a key and the SDK are present."""
    if not api_key():
        return False
    try:
        import google.genai  # noqa: F401
        return True
    except ImportError:
        return False


def status() -> dict:
    """What the interface shows in Settings."""
    key = api_key()
    try:
        import google.genai  # noqa: F401
        sdk = True
    except ImportError:
        sdk = False
    return {
        "available": bool(key) and sdk,
        "sdk_installed": sdk,
        "key_configured": bool(key),
        "model": DEFAULT_MODEL,
        # Never the key itself -- just enough to confirm which one is loaded.
        "key_hint": f"...{key[-4:]}" if key else "",
    }


def _client():
    from google import genai
    key = api_key()
    if not key:
        raise RuntimeError(
            "No Gemini key found. Put GEMINI_API_KEY=your-key in "
            f"{ENV_FILE}, or set it as an environment variable.")
    return genai.Client(api_key=key)


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #

_RULES = """
You are transcribing a document image. Follow these rules exactly.

1. Copy only what is actually printed in the image. Never infer, complete,
   correct or calculate anything.
2. If a value is missing, cut off, blurred or you are not certain what it says,
   return null for it. A null is correct and useful; a guess is not.
3. Copy identifiers character by character, preserving case exactly. Do not
   tidy them up or expand abbreviations.
4. Return only the JSON described by the schema.
"""

_PAYMENT_PROMPT = _RULES + """
This is a payment receipt or transaction confirmation. Extract the fields in
the schema. The UTR is the bank's Unique Transaction Reference. A payment
gateway id such as one starting "pay_" is a transaction id, not a UTR.
"""

_LAB_PROMPT = _RULES + """
This is a blood test / pathology report. Extract the patient details and one
entry per test row.

For each row give the test name exactly as printed, its result, its unit, and
the reference range printed on the report. Do NOT decide whether a value is
high, low or normal -- that is calculated elsewhere. If a row shows a range but
no result, the result is null.
"""

_PAYMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "utr": {"type": "string", "nullable": True},
        "transaction_id": {"type": "string", "nullable": True},
        "amount": {"type": "string", "nullable": True},
        "date": {"type": "string", "nullable": True},
        "time": {"type": "string", "nullable": True},
        "payee": {"type": "string", "nullable": True},
        "payer": {"type": "string", "nullable": True},
        "status": {"type": "string", "nullable": True},
        "upi_id": {"type": "string", "nullable": True},
        "ifsc": {"type": "string", "nullable": True},
    },
}

_LAB_SCHEMA = {
    "type": "object",
    "properties": {
        "patient_name": {"type": "string", "nullable": True},
        "age_sex": {"type": "string", "nullable": True},
        "patient_id": {"type": "string", "nullable": True},
        "referred_by": {"type": "string", "nullable": True},
        "collected_on": {"type": "string", "nullable": True},
        "reported_on": {"type": "string", "nullable": True},
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "string", "nullable": True},
                    "unit": {"type": "string", "nullable": True},
                    "reference_range": {"type": "string", "nullable": True},
                },
                "required": ["name"],
            },
        },
    },
}


# --------------------------------------------------------------------------- #
# Calls
# --------------------------------------------------------------------------- #

def _generate(image: bytes, mime: str, prompt: str, schema: dict) -> dict:
    from google.genai import types

    # Bound to a name deliberately -- google-genai's Client owns its own
    # transport, and calling _client().models.generate_content(...) as one
    # inline expression lets Python garbage-collect the unnamed Client before
    # the request finishes, closing that transport out from under the call.
    # Confirmed directly: the inline form fails with "Cannot send a request,
    # as the client has been closed" on every call; naming it first does not.
    client = _client()
    response = client.models.generate_content(
        model=DEFAULT_MODEL,
        contents=[
            types.Part.from_bytes(data=image, mime_type=mime),
            prompt,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            # Deterministic: the same page should transcribe the same way twice.
            temperature=0.0,
        ),
    )
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Gemini returned an empty response.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini did not return valid JSON: {exc}") from exc


def _mime_for(data: bytes) -> str:
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


PAYMENT_LABELS = {
    "utr": "UTR / Reference Number", "transaction_id": "Transaction ID",
    "amount": "Amount", "date": "Date", "time": "Time",
    "payee": "Paid To / Beneficiary", "payer": "Paid By / Sender",
    "status": "Status", "upi_id": "UPI ID (VPA)", "ifsc": "IFSC Code",
}


def read_payment(image: bytes) -> list[dict]:
    """Transcribe a receipt. Returns rows shaped like the local extractor's."""
    data = _generate(image, _mime_for(image), _PAYMENT_PROMPT, _PAYMENT_SCHEMA)
    rows = []
    for key, label in PAYMENT_LABELS.items():
        value = (data.get(key) or "").strip()
        if not value:
            continue
        rows.append({
            "key": "txn_id" if key == "transaction_id" else key,
            "label": label,
            "value": value,
            # No confidence: the model does not report one, and inventing a
            # number here would be exactly the false precision this avoids.
            "confidence": 0.0,
            "method": "ai",
            "note": "read by Gemini, not by local OCR -- check against the image",
        })
    return rows


def read_bloodtest(image: bytes) -> dict:
    """
    Transcribe a lab report.

    Flags are computed here by medical.flag_for(), never by the model, so an
    AI-transcribed row is judged by the same arithmetic as a locally read one.
    """
    import medical

    data = _generate(image, _mime_for(image), _LAB_PROMPT, _LAB_SCHEMA)

    meta = {k: (data.get(k) or "").strip()
            for k in ("patient_name", "age_sex", "patient_id",
                      "referred_by", "collected_on", "reported_on")
            if (data.get(k) or "").strip()}

    rows = []
    for item in data.get("rows") or []:
        name = (item.get("name") or "").strip()
        value = (item.get("value") or "").strip()
        if not name or not value:
            continue                      # a row with no result stays absent
        ref = (item.get("reference_range") or "").strip()
        rows.append({
            "key": f"ai::{name.lower()}",
            "name": name,
            "panel": "Read by Gemini",
            "value": value,
            "unit": (item.get("unit") or "").strip(),
            "ref_text": ref,
            "ref_source": "report" if ref else "none",
            "flag": medical.flag_for(value, ref),
            "confidence": 0.0,
            "note": "read by Gemini, not by local OCR -- check against the report",
        })

    return {"meta": meta, "rows": rows}


# --------------------------------------------------------------------------- #
# Self check
# --------------------------------------------------------------------------- #

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    info = status()
    print("Gemini fallback")
    print("---------------")
    print(f"  SDK installed  : {info['sdk_installed']}")
    print(f"  Key configured : {info['key_configured']}"
          + (f"  ({info['key_hint']})" if info['key_hint'] else ""))
    print(f"  Model          : {info['model']}")

    if not info["available"]:
        print()
        if not info["sdk_installed"]:
            print("  Install the SDK:  pip install google-genai")
        if not info["key_configured"]:
            print(f"  Add your key to {ENV_FILE}:")
            print("      GEMINI_API_KEY=your-key-here")
            print("  Get one free at https://aistudio.google.com/apikey")
        return 1

    sample = HERE / "samples" / "sample_receipt.png"
    if not sample.exists():
        print("\n  No sample image; run  python core.py  first.")
        return 1

    print(f"\n  Live round trip on {sample.name} ...")
    try:
        rows = read_payment(sample.read_bytes())
    except Exception as exc:
        print(f"  FAILED: {exc}")
        return 1

    for row in rows:
        print(f"    {row['label']:<26}{row['value']}")
    print(f"\n  OK: {len(rows)} fields returned.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
