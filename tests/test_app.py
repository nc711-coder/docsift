"""The web API with a fake AI and the in-memory store (no Groq, no database); Postgres when TEST_DATABASE_URL is set."""
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docsift import app as A
from docsift import notify as N
from docsift.store import MemoryStore

ROOT = Path(__file__).resolve().parent.parent


def answer(doc_id):
    return json.loads((ROOT / "samples" / "answers" / f"{doc_id}.json").read_text(encoding="utf-8"))


class FakeAI:
    """Returns the known answer for whichever sample is being processed (optionally changed)."""
    model = "fake"

    def __init__(self):
        self.next, self.changes = None, {}

    def json(self, system, user, schema=None, name="result", max_tokens=0):
        return dict(answer(self.next)["fields"], **self.changes), {"model": "fake", "seconds": 0.1}


@pytest.fixture
def client(monkeypatch):
    events = []
    monkeypatch.setattr(A, "store", MemoryStore())
    monkeypatch.setattr(A, "llm", FakeAI())
    monkeypatch.setattr(A, "limiter", A.RateLimiter())
    monkeypatch.setattr(A, "notify", lambda event, data, wait=False: events.append((event, data)))
    with TestClient(A.app) as c:
        c.events = events
        yield c


def run(c, sample, **changes):
    A.llm.next, A.llm.changes = sample, changes
    r = c.post(f"/api/samples/{sample}")
    assert r.status_code == 200, r.text
    return r.json()


def test_page_and_samples_are_served(client):
    assert "Drop a PDF, JPG or PNG" in client.get("/").text
    s = client.get("/api/samples").json()
    assert len(s) == 60 and {"id", "type", "condition", "file"} <= set(s[0])
    assert client.get("/api/samples/inv-01/file").headers["content-type"] == "application/pdf"


def test_clean_invoice_is_approved_automatically_and_goes_to_the_sheet(client):
    d = run(client, "inv-01")
    assert d["status"] == "approved" and d["fields"]["total"] == answer("inv-01")["fields"]["total"]
    event, data = client.events[-1]
    assert event == "document.approved" and data["sheet"] == "Invoices"
    assert data["row"]["Invoice #"] == answer("inv-01")["fields"]["invoice_number"] and data["row"]["Approved by"] == "auto"
    assert list(data["row"]) == N.COLUMNS["invoice"] and data["link"].endswith("#/doc/" + d["id"])
    assert any(f["name"] == "line_items" and f["kind"] == "rows" for f in d["form"])


def test_doubtful_document_needs_review_then_a_person_fixes_and_approves(client):
    d = run(client, "inv-01", total=99.0)
    assert d["status"] == "needs_review" and any("total" in r for r in d["reasons"])
    assert client.events[-1][0] == "document.needs_review"
    fields = dict(d["fields"], total=answer("inv-01")["fields"]["total"])
    rep = client.post(f"/api/documents/{d['id']}/check", json={"fields": fields}).json()
    assert rep["valid"] and not rep["reasons"]
    ok = client.post(f"/api/documents/{d['id']}/approve", json={"fields": fields}).json()
    assert ok["status"] == "approved" and ok["approved_by"] == "person"
    assert client.events[-1][0] == "document.approved" and client.events[-1][1]["row"]["Approved by"] == "person"


def test_approve_rejects_invalid_or_missing_values(client):
    d = run(client, "rcp-02")
    bad = client.post(f"/api/documents/{d['id']}/approve", json={"fields": dict(d["fields"], total="lots")})
    assert bad.status_code == 422 and "total" in bad.json()["detail"]
    missing = client.post(f"/api/documents/{d['id']}/approve", json={"fields": dict(d["fields"], merchant_name=None)})
    assert missing.status_code == 422 and "Merchant" in missing.json()["detail"]


def test_same_invoice_twice_is_flagged_as_a_possible_duplicate(client):
    first = run(client, "inv-01")
    second = run(client, "inv-01")
    assert first["status"] == "approved" and second["status"] == "needs_review"
    assert second["duplicate_of"] == first["id"] and "duplicate" in second["reasons"][0]


def test_original_file_is_served_for_review_and_unknown_ids_are_404(client):
    data = (ROOT / "samples" / "docs" / "rcp-02.pdf").read_bytes()
    A.llm.next, A.llm.changes = "rcp-02", {}
    d = client.post("/api/documents", files={"file": ("lunch.pdf", data, "application/pdf")}).json()
    f = client.get(f"/api/documents/{d['id']}/file")
    assert f.status_code == 200 and f.content == data
    assert client.get("/api/documents/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/api/documents/not-an-id").status_code == 404


def test_upload_limits(client, monkeypatch):
    r = client.post("/api/documents", files={"file": ("notes.docx", b"hello", "application/msword")})
    assert r.status_code == 415 and "PDF, JPG or PNG" in r.json()["detail"]
    monkeypatch.setattr(A, "MAX_MB", 0.001)
    big = client.post("/api/documents", files={"file": ("x.pdf", b"%PDF-" + b"0" * 5000, "application/pdf")})
    assert big.status_code == 413
    monkeypatch.setattr(A, "MAX_MB", 5)
    monkeypatch.setattr(A, "MAX_PAGES", 1)
    two = client.post("/api/documents", files={"file": ("inv.pdf", (ROOT / "samples" / "docs" / "inv-06.pdf").read_bytes(), "application/pdf")})
    assert two.status_code == 413 and "pages" in two.json()["detail"]


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(A, "limiter", A.RateLimiter(per_hour=1))
    run(client, "inv-01")
    r = client.post("/api/samples/inv-02")
    assert r.status_code == 429 and "last hour" in r.json()["detail"]


def test_ai_outage_is_a_plain_message(client):
    from docsift.llm import LLMError

    class Down:
        def json(self, *a, **k):
            raise LLMError("Groq didn't answer (APITimeoutError)")
    A.llm = Down()
    r = client.post("/api/samples/inv-01")
    assert r.status_code == 503 and "isn't available" in r.json()["detail"] and "Groq" not in r.json()["detail"]


def test_w9_row_and_page_never_hold_the_full_tax_id(client):
    d = run(client, "w9-01")
    tin = answer("w9-01")["meta"]["full_tin"]
    assert tin not in json.dumps(client.events).replace("-", "")
    page = client.get(f"/api/documents/{d['id']}").json()
    assert tin not in json.dumps(page).replace("-", "").replace(" ", "")


# ------------------------------------------------------------------------------------------- Postgres

@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="set TEST_DATABASE_URL to test Postgres")
def test_postgres_store_round_trip():
    from docsift.store import PostgresStore
    s = PostgresStore(os.environ["TEST_DATABASE_URL"])
    s._run("delete from docsift.documents")
    f = answer("inv-01")["fields"]
    result = {"type": "invoice", "fields": f, "needs_review": False, "reasons": []}
    a = s.add("a.pdf", "application/pdf", b"%PDF-1", result, visitor="1.2.3.4")
    doc = s.get(a)
    assert doc["status"] == "approved" and doc["approved_by"] == "auto" and doc["fields"]["total"] == f["total"]
    assert s.file(a) == (b"%PDF-1", "application/pdf") and doc["has_file"]
    assert s.duplicate_of("invoice", f) == a and s.duplicate_of("invoice", f, exclude=a) is None
    s.update(a, fields=dict(f, total=1.0), status="approved", approved_by="person")
    assert s.get(a)["fields"]["total"] == 1.0 and s.get(a)["approved_by"] == "person"
    s._run("update docsift.documents set created_at = now() - interval '2 days' where id = %s", (a,))
    assert s.purge_files() == 1 and s.file(a) is None and s.get(a)["fields"]
    assert s.get("not-a-uuid") is None
