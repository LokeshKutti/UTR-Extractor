"""
Supabase logging
================
Optional. Off unless SUPABASE_URL and SUPABASE_SERVICE_KEY are configured --
see .env.example and supabase_schema.sql. Every successful extraction
(payment or blood test) is logged as one row via a plain REST call to
Supabase's PostgREST endpoint. No SDK: httpx is already a dependency and the
whole exchange is a single POST.

Logging is supplementary history, not something the user is waiting on --
log_extraction() never raises, so a slow or unreachable database can only add
a little latency, never break an extraction that otherwise succeeded.

The key must be the *service_role* key, not the anon/public key -- it is only
ever read here, in the backend, and must never be sent to the frontend.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).parent
ENV_FILE = HERE / ".env"


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


def _config() -> tuple[str, str] | None:
    _load_env_file()
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        return None
    return url, key


def available() -> bool:
    """True when a Supabase project is actually configured."""
    return _config() is not None


def log_extraction(kind: str, filename: str, meta: dict[str, Any],
                    rows: list[dict[str, Any]] | None = None) -> None:
    """Log one extraction to the `extractions` table. Never raises."""
    cfg = _config()
    if cfg is None:
        return
    url, key = cfg
    try:
        resp = httpx.post(
            f"{url}/rest/v1/extractions",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            json={"kind": kind, "filename": filename, "meta": meta, "rows": rows or []},
            timeout=5.0,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"  [db] log_extraction failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    if not available():
        print("Not configured -- set SUPABASE_URL and SUPABASE_SERVICE_KEY in .env")
        sys.exit(1)
    print("Configured. Sending a test row...")
    log_extraction("test", "db.py self-test", {"note": "hello from db.py"})
    print("Sent (check the `extractions` table in your Supabase dashboard).")
