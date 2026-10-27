"""Email the owner when a visitor got an error instead of a result, and why (through n8n, like reviews).

    ALERT_EVERY_MINUTES=30   at most one email per kind of problem in this window; the next one says how many
                             similar failures happened in between (a Groq outage sends 2 emails an hour, not 200)

Sending never raises: an alert must never turn one failure into two.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger("docsift")

KNOWN = [   # (words in the error, kind, what happened, likely fix)
    ("api key", "api_key", "Groq rejected the API key", "Check GROQ_API_KEY in deploy.env.yaml, then redeploy."),
    ("not set", "api_key", "The Groq API key isn't set", "Add GROQ_API_KEY to deploy.env.yaml, then redeploy."),
    ("doesn't offer the model", "model", "The AI model isn't available any more",
     "Set GROQ_MODEL in deploy.env.yaml to a current model, then redeploy."),
    ("usage limit", "rate_limit", "Groq's usage limit was reached",
     "Check the Groq console (Settings > Limits and Billing)."),
    ("malformed json", "bad_json", "The AI kept producing malformed answers", "Usually random; if it repeats, try another GROQ_MODEL."),
    ("didn't answer", "ai_down", "Groq didn't answer", "Usually a short outage: check https://groqstatus.com."),
    ("tesseract", "ocr", "Tesseract (OCR) isn't working on the server", "The Docker image should include it: rebuild and redeploy."),
]


def explain(e: BaseException) -> tuple[str, str, str]:
    text = str(e).lower()
    if (type(e).__module__ or "").startswith("psycopg"):
        return ("database", "The database couldn't be reached",
                "Supabase pauses free projects after a week without activity: open the dashboard and click Restore project.")
    for words, kind, what, fix in KNOWN:
        if words in text:
            return kind, what, fix
    return "bug", "The app hit an unexpected error", "A bug or a setup problem: the details below show where."


class Alerter:
    def __init__(self, every_minutes: float | None = None, clock=time.time, send=None):
        self.window = 60 * (every_minutes if every_minutes is not None else float(os.environ.get("ALERT_EVERY_MINUTES") or 30))
        self.clock, self._send = clock, send
        self.last: dict[str, float] = {}
        self.skipped: dict[str, int] = {}
        self.lock = threading.Lock()

    def report(self, e: BaseException, filename: str = "", where: str = "upload") -> bool:
        try:
            kind, what, fix = explain(e)
            with self.lock:
                now = self.clock()
                if now - self.last.get(kind, -1e12) < self.window:
                    self.skipped[kind] = self.skipped.get(kind, 0) + 1
                    return False
                self.last[kind] = now
                skipped = self.skipped.pop(kind, 0)
            tb = "".join(traceback.format_exception(type(e), e, e.__traceback__)[-5:]) if e.__traceback__ else ""
            data = {"kind": kind, "headline": what, "fix": fix, "error": f"{type(e).__name__}: {e}"[:1200],
                    "traceback": tb[-2000:], "filename": filename, "where": where,
                    "when": datetime.now(ZoneInfo(os.environ.get("TIMEZONE", "America/New_York"))).strftime("%a %b %d, %I:%M %p"),
                    "similar_failures_not_emailed": skipped, "version": os.environ.get("K_REVISION", "local"),
                    "logs_command": f"gcloud run services logs read {os.environ.get('K_SERVICE', 'docsift')} --region us-west1 --limit 50"}
            if self._send:
                self._send("docsift.error", data)
            else:
                from .notify import notify
                notify("docsift.error", data)
            return True
        except Exception:
            log.exception("could not send the failure alert")
            return False


alerter = Alerter()
