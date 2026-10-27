"""Events for n8n (or Zapier/Make): an approved document becomes a Google Sheet row; one that needs a person
becomes an email with a link to its review page.

    EVENT_WEBHOOK_URL=https://.../webhook/docsift      where to POST (unset = no events)
    EVENT_WEBHOOK_SECRET=...                           sent as X-Webhook-Secret; n8n rejects requests without it
    PUBLIC_URL=https://docsift-....run.app             the app's address, for links in emails

    {"event": "document.approved", "id": "<unique>", "sent_at": "...",
     "data": {"document_id", "type", "sheet": "Invoices", "row": {"Vendor": ..., "Total": ...}, "link"}}
    {"event": "document.needs_review", ..., "data": {"document_id", "type", "filename", "reasons", "link"}}

The row is built here, in code (columns per type, in order), so the n8n workflow only appends it to the tab named
in "sheet". Sent in a background thread with retries: a slow n8n never delays the person using the page.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("docsift")

SHEETS = {"invoice": "Invoices", "receipt": "Receipts", "w9": "W-9s"}
COLUMNS = {
    "invoice": ["Approved at", "Approved by", "Vendor", "Invoice #", "Invoice date", "Due date", "PO number",
                "Bill to", "Subtotal", "Discount", "Tax", "Shipping", "Total", "Terms", "Line items", "Items",
                "Document"],
    "receipt": ["Approved at", "Approved by", "Merchant", "Date", "Time", "Category", "Subtotal", "Tax", "Tip",
                "Total", "Payment", "Card last 4", "Items", "Document"],
    "w9": ["Approved at", "Approved by", "Name", "Business name", "Tax classification", "LLC code", "Address",
           "City, state, ZIP", "TIN type", "TIN last 4", "Signed", "Date signed", "Document"],
}


def _money(v):
    return "" if v is None else f"{float(v):.2f}"


def sheet_row(kind: str, f: dict, approved_by: str, link: str) -> dict:
    when = datetime.now(ZoneInfo(os.environ.get("TIMEZONE", "America/New_York"))).strftime("%Y-%m-%d %H:%M")
    s = lambda v: "" if v is None else v
    if kind == "invoice":
        items = f.get("line_items") or []
        vals = [when, approved_by, s(f.get("vendor_name")), s(f.get("invoice_number")), s(f.get("invoice_date")),
                s(f.get("due_date")), s(f.get("po_number")), s(f.get("bill_to_name")), _money(f.get("subtotal")),
                _money(f.get("discount")), _money(f.get("tax")), _money(f.get("shipping")), _money(f.get("total")),
                s(f.get("payment_terms")), len(items),
                "; ".join(f"{i.get('description')} {_money(i.get('amount'))}" for i in items)[:2000], link]
    elif kind == "receipt":
        items = f.get("items") or []
        vals = [when, approved_by, s(f.get("merchant_name")), s(f.get("date")), s(f.get("time")),
                s(f.get("category")), _money(f.get("subtotal")), _money(f.get("tax")), _money(f.get("tip")),
                _money(f.get("total")), s(f.get("payment_method")), s(f.get("card_last4")),
                "; ".join(f"{i.get('description')} {_money(i.get('amount'))}" for i in items)[:2000], link]
    else:
        vals = [when, approved_by, s(f.get("name")), s(f.get("business_name")), s(f.get("tax_classification")),
                s(f.get("llc_tax_code")), s(f.get("address")), s(f.get("city_state_zip")), s(f.get("tin_type")),
                s(f.get("tin_last4")), "yes" if f.get("signed") else "no", s(f.get("date_signed")), link]
    return dict(zip(COLUMNS[kind], vals))


def notify(event: str, payload: dict, *, wait: bool = False) -> int | None:
    url = os.environ.get("EVENT_WEBHOOK_URL")
    if not url:
        return None
    body = {"event": event, "id": uuid.uuid4().hex, "sent_at": datetime.now(timezone.utc).isoformat(), "data": payload}
    headers = {"Content-Type": "application/json"}
    if os.environ.get("EVENT_WEBHOOK_SECRET"):
        headers["X-Webhook-Secret"] = os.environ["EVENT_WEBHOOK_SECRET"]

    def send() -> int | None:
        import httpx
        for attempt in range(3):
            try:
                r = httpx.post(url, content=json.dumps(body, default=str), headers=headers,
                               timeout=float(os.environ.get("EVENT_WEBHOOK_TIMEOUT") or 90))
                if r.status_code < 500:
                    if r.status_code >= 400:
                        log.warning("webhook %s rejected: HTTP %s %s", event, r.status_code, r.text[:200])
                    return r.status_code
            except Exception as e:                          # n8n asleep or down: retry
                log.warning("webhook %s attempt %d failed: %s", event, attempt + 1, e)
            time.sleep(2 ** attempt)
        log.error("webhook %s gave up after 3 attempts", event)
        return None

    if wait:
        return send()
    threading.Thread(target=send, name=f"notify-{event}", daemon=True).start()
    return None


if __name__ == "__main__":
    # python -m docsift.notify approved | review   -> a sample event, to test the n8n workflow
    import sys
    from .llm import load_dotenv
    load_dotenv()
    which = (sys.argv[1:] or ["approved"])[0]
    if not os.environ.get("EVENT_WEBHOOK_URL"):
        print("EVENT_WEBHOOK_URL isn't set in .env.")
        raise SystemExit(2)
    sample = {"vendor_name": "TEST Brightline IT Services LLC", "invoice_number": "INV-0000", "invoice_date": "2026-10-27",
              "due_date": "2026-11-26", "subtotal": 100.0, "tax": 6.0, "total": 106.0, "payment_terms": "Net 30",
              "line_items": [{"description": "TEST line", "quantity": 1, "unit_price": 100.0, "amount": 100.0}]}
    if which == "approved":
        event, data = "document.approved", {"document_id": "test", "type": "invoice", "sheet": "Invoices",
                                            "row": sheet_row("invoice", sample, "test", "https://example.com"),
                                            "link": "https://example.com"}
    else:
        event, data = "document.needs_review", {"document_id": "test", "type": "invoice", "filename": "TEST.pdf",
                                                "reasons": ["total: TEST reason"], "link": "https://example.com"}
    status = notify(event, data, wait=True)
    hints = {403: "the secret doesn't match the Header Auth credential", 404: "workflow not published, or wrong URL"}
    print(f"n8n answered HTTP {status}" + (f": {hints[status]}" if status in hints else " (OK)" if status and status < 300 else ""))
