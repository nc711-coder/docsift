"""The web app: upload a document, review what was extracted, fix and approve.

    uvicorn docsift.app:app --reload          # then open http://127.0.0.1:8000

    POST /api/documents              upload (multipart "file"): read, classify, extract, validate, save
    POST /api/samples/{id}           the same for a built-in sample document (one click on the demo page)
    GET  /api/samples                the samples
    GET  /api/documents/{id}         fields, confidence, reasons, status, and the form to edit them with
    GET  /api/documents/{id}/file    the original (for the review page; deleted after 24 hours)
    POST /api/documents/{id}/check   re-check edited fields (no saving): what's still doubtful
    POST /api/documents/{id}/approve save the (edited) fields as approved -> Google Sheet via n8n
    GET  /health

Limits for a public demo: PDF/JPG/PNG, MAX_UPLOAD_MB (5), MAX_PAGES (3), UPLOADS_PER_HOUR_PER_VISITOR (20),
UPLOADS_PER_DAY (200). Errors come back as one plain sentence.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from .extract import coerce
from .llm import LLMError, load_dotenv
from .notify import SHEETS, notify, sheet_row
from .schemas import MODELS, REQUIRED
from .store import open_store
from .validate import check

load_dotenv()
log = logging.getLogger("docsift")
ROOT = Path(__file__).resolve().parent.parent
WEB, SAMPLES = ROOT / "web", ROOT / "samples"
MAX_MB = float(os.environ.get("MAX_UPLOAD_MB") or 5)
MAX_PAGES = int(os.environ.get("MAX_PAGES") or 3)
TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

store = None
llm = None                     # tests put a fake AI here


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store
    if store is None:
        store = open_store()
        log.warning("documents are stored in %s", store.kind)
    yield


app = FastAPI(title="DocSift", version="0.3.0", lifespan=lifespan)


# ------------------------------------------------------------------------------------------- limits

class RateLimiter:
    def __init__(self, per_hour=0, per_day=0, clock=time.time):
        self.per_hour, self.per_day, self.clock = per_hour, per_day, clock
        self.recent, self.day, self.today = defaultdict(deque), None, 0
        self.lock = threading.Lock()

    def check(self, visitor: str) -> str | None:
        now = self.clock()
        with self.lock:
            if int(now // 86400) != self.day:
                self.day, self.today = int(now // 86400), 0
            if self.per_day and self.today >= self.per_day:
                return "The demo has processed its limit of documents for today. Please try again tomorrow."
            q = self.recent[visitor]
            while q and q[0] < now - 3600:
                q.popleft()
            if self.per_hour and len(q) >= self.per_hour:
                return "You've uploaded a lot of documents in the last hour. Please wait a little."
            q.append(now)
            self.today += 1
        return None


limiter = RateLimiter(int(os.environ.get("UPLOADS_PER_HOUR_PER_VISITOR") or 20),
                      int(os.environ.get("UPLOADS_PER_DAY") or 200))


def visitor_id(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def page_count(data: bytes) -> int:
    import pypdfium2 as pdfium
    try:
        return len(pdfium.PdfDocument(data))
    except Exception:
        raise HTTPException(400, "That PDF couldn't be opened. Is it damaged or password-protected?")


# ------------------------------------------------------------------------------------------- processing

def _llm():
    global llm
    if llm is None:
        from .llm import GroqJSON
        llm = GroqJSON()
    return llm


def process_and_save(data: bytes, filename: str, request: Request, sample: str | None = None) -> dict:
    from .pipeline import process
    ext = Path(filename).suffix.lower()
    if ext not in TYPES:
        raise HTTPException(415, "Please upload a PDF, JPG or PNG.")
    if len(data) > MAX_MB * 1024 * 1024:
        raise HTTPException(413, f"That file is over {MAX_MB:g} MB. Please upload a smaller one.")
    if not data:
        raise HTTPException(400, "That file is empty.")
    if ext == ".pdf" and page_count(data) > MAX_PAGES:
        raise HTTPException(413, f"Please upload documents of up to {MAX_PAGES} pages.")
    try:
        result = process(data, filename, llm=_llm()).to_dict()
    except LLMError as e:
        log.error("AI failed: %s", e)
        raise HTTPException(503, "The AI service isn't available right now. Please try again in a minute.")
    except RuntimeError as e:                          # Tesseract missing
        log.error("reader failed: %s", e)
        raise HTTPException(500, str(e))
    except Exception:
        log.exception("processing %s failed", filename)
        raise HTTPException(500, "Something went wrong reading that document. Please try another one.")
    dup = store.duplicate_of(result["type"], result.get("fields"))
    if dup:
        why = "possible duplicate: a document with the same " + \
              ("vendor and invoice number" if result["type"] == "invoice" else "merchant, date and total") + \
              " was processed before"
        result["reasons"].insert(0, why)
        result["doubts"].insert(0, why)
        result["needs_review"] = True
        result["duplicate_of"] = dup
    doc_id = store.add(filename, TYPES[ext], data, result, visitor=visitor_id(request), sample=sample)
    store.purge_files()
    doc = store.get(doc_id)
    link = _link(request, doc_id)
    if doc["status"] == "approved":
        notify("document.approved", {"document_id": doc_id, "type": doc["type"], "sheet": SHEETS[doc["type"]],
                                     "row": sheet_row(doc["type"], doc["fields"], "auto", link), "link": link})
    else:
        notify("document.needs_review", {"document_id": doc_id, "type": doc["type"], "filename": filename,
                                         "reasons": result["reasons"], "link": link})
    return view(doc)


def _link(request: Request, doc_id: str) -> str:
    base = os.environ.get("PUBLIC_URL") or str(request.base_url)
    return f"{base.rstrip('/')}/#/doc/{doc_id}"


# ------------------------------------------------------------------------------------------- what the page needs

LABELS = {
    "vendor_name": "Vendor", "vendor_address": "Vendor address", "invoice_number": "Invoice number",
    "invoice_date": "Invoice date", "due_date": "Due date", "po_number": "PO number", "bill_to_name": "Bill to",
    "bill_to_address": "Bill-to address", "currency": "Currency", "subtotal": "Subtotal", "discount": "Discount",
    "tax": "Tax", "shipping": "Shipping", "total": "Total", "payment_terms": "Payment terms",
    "merchant_name": "Merchant", "merchant_address": "Merchant address", "date": "Date", "time": "Time",
    "tax_included": "Prices include tax", "tip": "Tip", "payment_method": "Payment method",
    "card_last4": "Card last 4", "category": "Category", "name": "Name (line 1)",
    "business_name": "Business name (line 2)", "tax_classification": "Tax classification (3a)",
    "llc_tax_code": "LLC tax code", "address": "Address (line 5)", "city_state_zip": "City, state, ZIP (line 6)",
    "tin_type": "TIN type", "tin_last4": "TIN last 4", "signed": "Signed", "date_signed": "Date signed",
    "line_items": "Line items", "items": "Items",
}


def form_spec(kind: str) -> list[dict]:
    """The fields of a type as the page should show them: label, input kind, choices, required."""
    schema = MODELS[kind].model_json_schema()
    defs = schema.get("$defs", {})
    out = []
    for name, prop in schema["properties"].items():
        options = None
        kinds = [p for p in prop.get("anyOf", [prop]) if p.get("type") != "null"]
        p = kinds[0] if kinds else prop
        if "$ref" in p:
            p = defs[p["$ref"].split("/")[-1]]
        if p.get("type") == "array":
            item = defs[p["items"]["$ref"].split("/")[-1]]
            kind_ = "rows"
            options = [c for c in item["properties"]]
        elif "enum" in p:
            kind_, options = "choice", p["enum"]
        elif p.get("format") == "date":
            kind_ = "date"
        elif p.get("type") in ("number", "integer"):
            kind_ = "money" if name not in ("quantity",) else "number"
        elif p.get("type") == "boolean":
            kind_ = "bool"
        else:
            kind_ = "text"
        out.append({"name": name, "label": LABELS.get(name, name.replace("_", " ").capitalize()), "kind": kind_,
                    "options": options, "required": name in REQUIRED[kind]})
    return out


def view(doc: dict) -> dict:
    r = doc["result"]
    return {"id": str(doc["id"]), "status": doc["status"], "type": doc["type"], "approved_by": doc["approved_by"],
            "filename": doc["filename"], "content_type": doc["content_type"], "has_file": doc["has_file"],
            "sample": doc.get("sample"), "created_at": str(doc["created_at"]),
            "fields": doc["fields"], "confidence": r.get("confidence", {}), "reasons": r.get("reasons", []),
            "doubts": r.get("doubts", []), "issues": r.get("issues", []), "duplicate_of": r.get("duplicate_of"),
            "reading": {k: v for k, v in r.get("reading", {}).items() if k != "unsure"},
            "seconds": r.get("seconds", {}), "classified_by": r.get("classified_by"),
            "form": form_spec(doc["type"]) if doc["type"] in MODELS else []}


def _get(doc_id: str) -> dict:
    doc = store.get(doc_id)
    if not doc:
        raise HTTPException(404, "That document wasn't found. Uploads are kept for a limited time.")
    return doc


def _recheck(doc: dict, fields: dict) -> tuple[BaseModel | None, dict]:
    """Validate edited fields the same way the pipeline did, against the (masked) text that was read."""
    kind = doc["type"]
    try:
        model = MODELS[kind].model_validate(coerce(kind, fields))
    except ValidationError as e:
        problems = [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()]
        return None, {"valid": False, "problems": problems, "reasons": problems, "confidence": {}, "issues": []}
    reading = doc["result"].get("reading", {})
    rep = check(kind, model, doc["result"].get("text", ""), readable=True, exact=reading.get("method") == "text",
                unsure=reading.get("unsure"))
    return model, {"valid": True, "problems": [], "reasons": rep.reasons, "confidence": rep.confidence,
                   "issues": [i.__dict__ for i in rep.issues], "fields": model.model_dump(mode="json")}


# ------------------------------------------------------------------------------------------- routes

@app.get("/health")
def health():
    return {"status": "ok", "store": store.kind if store else None}


@app.post("/api/documents")
async def upload(request: Request, file: UploadFile = File(...)):
    blocked = limiter.check(visitor_id(request))
    if blocked:
        raise HTTPException(429, blocked)
    data = await file.read(int(MAX_MB * 1024 * 1024) + 1)
    import anyio
    return await anyio.to_thread.run_sync(process_and_save, data, file.filename or "upload", request)


def samples() -> list[dict]:
    rows = list(csv.DictReader(io.StringIO((SAMPLES / "index.csv").read_text(encoding="utf-8"))))
    return [{"id": r["id"], "type": r["type"], "condition": r["condition"], "file": r["file"],
             "tricks": [t for t in r["tricks"].split("+") if t]} for r in rows]


@app.get("/api/samples")
def list_samples():
    return samples()


@app.get("/api/samples/{sample_id}/file")
def sample_file(sample_id: str):
    s = next((s for s in samples() if s["id"] == sample_id), None)
    if not s:
        raise HTTPException(404, "No such sample.")
    path = SAMPLES / "docs" / s["file"]
    return Response(path.read_bytes(), media_type=TYPES[path.suffix.lower()])


@app.post("/api/samples/{sample_id}")
def run_sample(sample_id: str, request: Request):
    s = next((s for s in samples() if s["id"] == sample_id), None)
    if not s:
        raise HTTPException(404, "No such sample.")
    blocked = limiter.check(visitor_id(request))
    if blocked:
        raise HTTPException(429, blocked)
    path = SAMPLES / "docs" / s["file"]
    return process_and_save(path.read_bytes(), path.name, request, sample=sample_id)


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: str):
    return view(_get(doc_id))


@app.get("/api/documents/{doc_id}/file")
def get_file(doc_id: str):
    f = store.file(doc_id)
    if not f:
        raise HTTPException(404, "The original file is no longer kept (originals are deleted after 24 hours).")
    data, ctype = f
    return Response(data, media_type=ctype, headers={"Content-Disposition": "inline", "Cache-Control": "private, max-age=3600"})


class Edit(BaseModel):
    fields: dict


@app.post("/api/documents/{doc_id}/check")
def recheck(doc_id: str, body: Edit):
    doc = _get(doc_id)
    if doc["type"] not in MODELS:
        raise HTTPException(400, "This document's type isn't supported, so there's nothing to check.")
    return _recheck(doc, body.fields)[1]


@app.post("/api/documents/{doc_id}/approve")
def approve(doc_id: str, body: Edit, request: Request):
    doc = _get(doc_id)
    if doc["type"] not in MODELS:
        raise HTTPException(400, "Only invoices, receipts and W-9s can be approved.")
    model, report = _recheck(doc, body.fields)
    if model is None:
        raise HTTPException(422, "Some values aren't valid: " + "; ".join(report["problems"]))
    missing = [LABELS.get(n, n) for n in REQUIRED[doc["type"]] if getattr(model, n) is None]
    if missing:
        raise HTTPException(422, "Please fill in: " + ", ".join(missing) + ".")
    fields = model.model_dump(mode="json")
    already = doc["status"] == "approved"
    store.update(doc_id, fields=fields, status="approved", approved_by="person")
    link = _link(request, doc_id)
    if not already or fields != doc["fields"]:
        notify("document.approved", {"document_id": doc_id, "type": doc["type"], "sheet": SHEETS[doc["type"]],
                                     "row": sheet_row(doc["type"], fields, "person", link), "link": link})
    return view(store.get(doc_id)) | {"remaining": report["reasons"]}


# Last, so the API routes win: everything else is the web page.
app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
