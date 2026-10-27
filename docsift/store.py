"""Where processed documents live: Postgres (Supabase) when DATABASE_URL is set, otherwise in memory.

    store = open_store()                       # Postgres if DATABASE_URL is set, else in memory (lost on restart)
    doc_id = store.add(filename, content_type, file_bytes, result_dict, visitor)
    store.get(doc_id)                          # {"id", "status", "type", "fields", "result", ...}
    store.update(doc_id, fields=..., status="approved", approved_by="person")
    store.file(doc_id)                         # (bytes, content_type) of the original, for the review page
    store.duplicate_of("invoice", fields, doc_id)   # an earlier document with the same vendor + invoice number

Originals are kept 24 hours (FILE_HOURS) for the review page, then deleted; the extracted fields stay. The text
kept with a result is already masked (no full tax IDs or card numbers).
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

FILE_HOURS = float(os.environ.get("FILE_HOURS") or 24)
STATUSES = ("approved", "needs_review", "failed")

SCHEMA = """
create schema if not exists docsift;
create table if not exists docsift.documents (
    id            uuid primary key,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    visitor       text,
    filename      text,
    content_type  text,
    file          bytea,                       -- the original upload; deleted after FILE_HOURS
    sample        text,                        -- set when a built-in sample was used
    type          text,                        -- invoice | receipt | w9 | other
    status        text not null check (status in ('approved', 'needs_review', 'failed')),
    approved_by   text,                        -- auto | person
    fields        jsonb,                       -- the current values (after any edits)
    result        jsonb not null               -- what the pipeline returned (masked text, reasons, confidence)
);
create index if not exists documents_invoice_key on docsift.documents
    (lower(fields->>'vendor_name'), (fields->>'invoice_number')) where type = 'invoice';
create index if not exists documents_created on docsift.documents (created_at);
"""


def _now():
    return datetime.now(timezone.utc)


def _norm(s) -> str:
    return " ".join(str(s or "").lower().split())


def duplicate_key(kind: str, f: dict | None):
    """What makes two documents 'the same' bill: an invoice's vendor + number; a receipt's merchant + date + total."""
    if not f:
        return None
    if kind == "invoice" and f.get("vendor_name") and f.get("invoice_number"):
        return ("invoice", _norm(f["vendor_name"]), _norm(f["invoice_number"]))
    if kind == "receipt" and f.get("merchant_name") and f.get("date") and f.get("total") is not None:
        return ("receipt", _norm(f["merchant_name"]), str(f["date"]), f"{float(f['total']):.2f}")
    return None


class MemoryStore:
    """Same interface as PostgresStore, kept in a dict: for tests and for trying the app without a database."""

    kind = "memory"

    def __init__(self):
        self.docs: dict[str, dict] = {}
        self.lock = threading.Lock()

    def add(self, filename, content_type, data: bytes, result: dict, visitor=None, sample=None) -> str:
        doc_id = str(uuid.uuid4())
        status = "failed" if result.get("fields") is None and result.get("type") != "other" else \
            "needs_review" if result.get("needs_review") else "approved"
        with self.lock:
            self.docs[doc_id] = {"id": doc_id, "created_at": _now(), "updated_at": _now(), "visitor": visitor,
                                 "filename": filename, "content_type": content_type, "file": data, "sample": sample,
                                 "type": result.get("type"), "status": status,
                                 "approved_by": "auto" if status == "approved" else None,
                                 "fields": result.get("fields"), "result": result}
        return doc_id

    def get(self, doc_id: str) -> dict | None:
        d = self.docs.get(str(doc_id))
        return {k: v for k, v in d.items() if k != "file"} | {"has_file": d["file"] is not None} if d else None

    def file(self, doc_id: str):
        d = self.docs.get(str(doc_id))
        return (d["file"], d["content_type"]) if d and d["file"] is not None else None

    def update(self, doc_id: str, **cols) -> None:
        with self.lock:
            self.docs[str(doc_id)].update(cols, updated_at=_now())

    def duplicate_of(self, kind: str, fields: dict, exclude: str | None = None) -> str | None:
        key = duplicate_key(kind, fields)
        if not key:
            return None
        for d in self.docs.values():
            if d["id"] != exclude and d["status"] != "failed" and duplicate_key(d["type"], d["fields"]) == key:
                return d["id"]
        return None

    def purge_files(self) -> int:
        cutoff = _now() - timedelta(hours=FILE_HOURS)
        n = 0
        with self.lock:
            for d in self.docs.values():
                if d["file"] is not None and d["created_at"] < cutoff:
                    d["file"] = None
                    n += 1
        return n


class PostgresStore:
    kind = "postgres"

    def __init__(self, url: str):
        import psycopg
        from psycopg.rows import dict_row
        self.url = url
        self._connect = lambda: psycopg.connect(url, autocommit=True, row_factory=dict_row,
                                                prepare_threshold=None)   # None: works with Supabase's pooler
        self.conn = self._connect()
        self.lock = threading.Lock()
        self.conn.execute(SCHEMA)

    def _run(self, sql, args=()):
        import psycopg
        with self.lock:
            try:
                return self.conn.execute(sql, args)
            except psycopg.OperationalError:              # the pooler dropped an idle connection: reconnect once
                self.conn = self._connect()
                return self.conn.execute(sql, args)

    def add(self, filename, content_type, data, result, visitor=None, sample=None) -> str:
        doc_id = str(uuid.uuid4())
        status = "failed" if result.get("fields") is None and result.get("type") != "other" else \
            "needs_review" if result.get("needs_review") else "approved"
        self._run("""insert into docsift.documents (id, visitor, filename, content_type, file, sample, type, status,
                     approved_by, fields, result) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                  (doc_id, visitor, filename, content_type, data, sample, result.get("type"), status,
                   "auto" if status == "approved" else None, json.dumps(result.get("fields")), json.dumps(result)))
        return doc_id

    def get(self, doc_id):
        try:
            uuid.UUID(str(doc_id))
        except ValueError:
            return None
        row = self._run("""select id::text, created_at, updated_at, visitor, filename, content_type, sample, type,
                           status, approved_by, fields, result, file is not null as has_file
                           from docsift.documents where id = %s""", (doc_id,)).fetchone()
        return dict(row) if row else None

    def file(self, doc_id):
        row = self._run("select file, content_type from docsift.documents where id = %s and file is not null",
                        (doc_id,)).fetchone()
        return (bytes(row["file"]), row["content_type"]) if row else None

    def update(self, doc_id, **cols):
        allowed = {"fields", "status", "approved_by", "result", "type"}
        sets, args = [], []
        for k, v in cols.items():
            if k not in allowed:
                raise ValueError(k)
            sets.append(f"{k} = %s")
            args.append(json.dumps(v) if k in ("fields", "result") else v)
        self._run(f"update docsift.documents set {', '.join(sets)}, updated_at = now() where id = %s",
                  (*args, doc_id))

    def duplicate_of(self, kind, fields, exclude=None):
        key = duplicate_key(kind, fields)
        if not key:
            return None
        if kind == "invoice":
            row = self._run("""select id::text from docsift.documents where type = 'invoice' and status <> 'failed'
                               and lower(fields->>'vendor_name') = %s and lower(fields->>'invoice_number') = %s
                               and id <> coalesce(%s::uuid, '00000000-0000-0000-0000-000000000000') limit 1""",
                            (key[1], key[2], exclude)).fetchone()
        else:
            row = self._run("""select id::text from docsift.documents where type = 'receipt' and status <> 'failed'
                               and lower(fields->>'merchant_name') = %s and fields->>'date' = %s
                               and (fields->>'total')::numeric = %s::numeric
                               and id <> coalesce(%s::uuid, '00000000-0000-0000-0000-000000000000') limit 1""",
                            (key[1], key[2], key[3], exclude)).fetchone()
        return row["id"] if row else None

    def purge_files(self) -> int:
        cur = self._run("""update docsift.documents set file = null
                           where file is not null and created_at < now() - %s * interval '1 hour'""",
                        (FILE_HOURS,))
        return cur.rowcount


def open_store():
    url = os.environ.get("DATABASE_URL")
    return PostgresStore(url) if url else MemoryStore()
