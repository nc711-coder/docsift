"""Classify, extract, validate and the pipeline, with a scripted fake AI (no Groq calls)."""
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest

from docsift.classify import classify
from docsift.extract import coerce, extract
from docsift.llm import GroqJSON, LLMError, parse_json
from docsift.pipeline import analyse, mask
from docsift.reader import read
from docsift.schemas import MODELS
from docsift.validate import check

ROOT = Path(__file__).resolve().parent.parent


def answer(doc_id):
    return json.loads((ROOT / "samples" / "answers" / f"{doc_id}.json").read_text(encoding="utf-8"))


class Scripted:
    """A fake AI: returns the given answers in order and records what it was asked."""

    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def json(self, system, user, schema=None, name="result", max_tokens=0):
        self.asked.append({"system": system, "user": user, "name": name})
        return self.answers.pop(0), {"model": "fake", "seconds": 0}


# ------------------------------------------------------------------------------------------- classify

def test_rules_classify_clean_documents_without_the_ai():
    for doc_id in ("inv-01", "inv-04", "rcp-02", "rcp-08", "w9-01"):
        a = answer(doc_id)
        c = classify(read(ROOT / "samples" / "docs" / a["file"]).text, llm=None)
        assert (c.type, c.confident, c.by) == (a["type"], True, "rules")


def test_unclear_document_goes_to_the_ai_and_other_means_review():
    llm = Scripted({"type": "other", "reason": "a lease agreement"})
    c = classify("This lease is made between the landlord and the tenant for the premises.", llm)
    assert c.type == "other" and c.by == "ai" and not c.confident and llm.asked[0]["name"] == "classification"


# ------------------------------------------------------------------------------------------- extract

def test_formats_are_tidied_before_checking():
    d = coerce("receipt", {"merchant_name": "X", "date": "03/05/2026", "total": "$1,234.50", "time": "1:44 PM",
                           "card_last4": "************3683", "payment_method": "VISA", "category": "Office Supplies",
                           "items": [{"description": "A", "amount": "$4.00"}]})
    assert (d["date"], d["total"], d["time"], d["card_last4"], d["payment_method"], d["category"]) == \
        ("2026-03-05", 1234.5, "13:44", "3683", "visa", "office_supplies")
    assert d["items"][0]["amount"] == 4.0
    assert coerce("invoice", {"discount": "-70.82"})["discount"] == 70.82


def test_full_tax_id_from_the_ai_is_cut_to_last_four():
    f = answer("w9-01")["fields"]
    fields, _ = extract("w9", "text", Scripted(dict(f, tin_last4="987-65-4321")))
    assert fields.tin_last4 == "4321"


def test_answer_that_doesnt_fit_gets_one_retry_with_the_error():
    good = answer("inv-01")["fields"]
    bad = dict(good, total="lots", invoice_date="someday")
    llm = Scripted(bad, good)
    fields, info = extract("invoice", "text", llm)
    assert fields.total == good["total"] and info["attempts"] == 2
    assert "total" in llm.asked[1]["user"] and "didn't fit the schema" in llm.asked[1]["user"]
    fields, info = extract("invoice", "text", Scripted(bad, bad))
    assert fields is None and info["errors"]


# ------------------------------------------------------------------------------------------- validate

def fields_of(doc_id, **changes):
    a = answer(doc_id)
    return MODELS[a["type"]].model_validate(dict(a["fields"], **changes)), a


def text_of(a):
    return read(ROOT / "samples" / "docs" / a["file"]).text


def test_correct_invoice_is_approved():
    f, a = fields_of("inv-01")
    rep = check("invoice", f, text_of(a), exact=True)
    assert not rep.needs_review and rep.confidence["total"] == 1.0


def test_math_errors_are_caught():
    f, a = fields_of("inv-03", total=999.99)
    rep = check("invoice", f, text_of(a), exact=True)
    assert rep.needs_review and any("total" in r and "but the total is 999.99" in r for r in rep.reasons)
    a = answer("inv-04")
    items = a["fields"]["line_items"][1:]                      # the AI missed a line
    f = MODELS["invoice"].model_validate(dict(a["fields"], line_items=items))
    assert any("line items add up to" in r for r in check("invoice", f, text_of(a), exact=True).reasons)


def test_a_value_not_on_the_page_is_caught():
    f, a = fields_of("inv-01", invoice_number="CL-673-62")
    rep = check("invoice", f, text_of(a), exact=True)
    assert any("invoice_number" in r and "isn't on the page" in r for r in rep.reasons)


def test_typos_count_on_real_pdfs_but_ocr_slips_are_allowed():
    f, a = fields_of("inv-01")
    vendor = f.vendor_name
    typo = vendor[:3] + ("x" if vendor[3] != "x" else "y") + vendor[4:]
    text = text_of(a)
    assert check("invoice", f.model_copy(update={"vendor_name": typo}), text, exact=True).needs_review
    # after OCR the page itself may hold the slip: the AI's corrected spelling is still accepted
    ocr_text = text.replace(vendor, typo)
    assert not check("invoice", f, ocr_text, exact=False).needs_review
    # ...but a changed number never is
    assert check("invoice", f.model_copy(update={"payment_terms": "Net 80"}), ocr_text).needs_review


def test_dropped_value_is_caught_when_the_page_shows_one():
    f, a = fields_of("inv-01", payment_terms=None)
    assert any("payment_terms" in r for r in check("invoice", f, text_of(a), exact=True).reasons)


def test_shipping_labels_item_is_not_mistaken_for_a_shipping_line():
    rep = check("invoice", *(lambda f, a: (f, "Shipping labels, 1000 ct   12   $30.53   $366.36\n" + text_of(a)))(*fields_of("inv-01")),
                exact=False)
    assert not any("shipping" in r for r in rep.reasons)


def test_receipt_time_is_found_in_12_hour_form():
    f, a = fields_of("rcp-02")
    assert check("receipt", f, text_of(a), exact=True).confidence["time"] == 1.0


def test_unsigned_w9_is_incomplete_not_doubtful():
    a = next(answer(f"w9-{i:02d}") for i in range(1, 21) if not answer(f"w9-{i:02d}")["fields"]["signed"])
    f = MODELS["w9"].model_validate(a["fields"])
    rep = check("w9", f, text_of(a), exact=a["meta"]["condition"] == "clean")
    assert rep.needs_review and "signed: the form isn't signed" in rep.reasons and not rep.doubts


# ------------------------------------------------------------------------------------------- pipeline + privacy

def test_pipeline_end_to_end_and_nothing_leaks_the_tax_id():
    a = answer("w9-01")
    r = read(ROOT / "samples" / "docs" / a["file"])
    res = analyse(r, Scripted(dict(a["fields"]))).to_dict()
    assert res["type"] == "w9" and res["fields"]["tin_last4"] == a["fields"]["tin_last4"]
    tin = a["meta"]["full_tin"]
    assert tin not in json.dumps(res).replace("-", "").replace(" ", "")


def test_mask():
    assert mask("[digits in boxes] 987 65 4321") == "[digits in boxes] *****4321"
    assert mask("EIN 00-1234567") == "EIN *****4567"
    assert mask("0 0 - 5 3 5 8 4  6 6") == "*****8466"                 # a clean PDF's digit boxes
    assert mask("Signed 05/02/2026, ZIP 93721") == "Signed 05/02/2026, ZIP 93721"
    assert mask("Invoice # 202612345", tax_ids=False) == "Invoice # 202612345"
    assert mask("card 4111 1111 1111 1234", tax_ids=False) == "card ****1234"


# ------------------------------------------------------------------------------------------- the Groq client

def fake_client(*behaviours):
    calls = []

    def create(**kw):
        calls.append(kw)
        b = behaviours[len(calls) - 1]
        if isinstance(b, Exception):
            raise b
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=b), finish_reason="stop")],
                               usage=SimpleNamespace(total_tokens=42))
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


def err(cls, status, msg):
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    if cls is openai.APITimeoutError:
        return cls(request=req)
    return cls(msg, response=httpx.Response(status, request=req), body={"error": {"message": msg}})


def test_json_reply_is_parsed_and_typography_cleaned(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "x")
    client, calls = fake_client('```json\n{"time": "9:00\\u202fAM", "n": 1}\n```')
    data, info = GroqJSON(client=client).json("s", "u", {"type": "object"})
    assert data == {"time": "9:00 AM", "n": 1} and info["tokens"] == 42
    assert calls[0]["response_format"]["type"] == "json_schema" and "temperature" not in calls[0]


def test_schema_mode_falls_back_to_json_mode(monkeypatch):
    client, calls = fake_client(err(openai.BadRequestError, 400, "response_format json_schema is not supported"), '{"a": 1}')
    llm = GroqJSON(client=client)
    assert llm.json("s", "u", {"type": "object"})[0] == {"a": 1}
    assert calls[1]["response_format"] == {"type": "json_object"} and llm.schema_mode is False


def test_fallback_model_on_timeout_and_plain_errors(monkeypatch):
    monkeypatch.delenv("GROQ_FALLBACK_MODEL", raising=False)
    client, calls = fake_client(err(openai.APITimeoutError, 0, ""), '{"ok": true}')
    data, info = GroqJSON(client=client).json("s", "u")
    assert data == {"ok": True} and info["model"] == "openai/gpt-oss-20b" and calls[0]["model"] == "openai/gpt-oss-120b"
    client, _ = fake_client(err(openai.AuthenticationError, 401, "bad key"))
    with pytest.raises(LLMError, match="GROQ_API_KEY"):
        GroqJSON(client=client).json("s", "u")


def test_missing_key_is_explained(monkeypatch, tmp_path):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr("docsift.llm.ROOT", tmp_path)
    with pytest.raises(LLMError, match="GROQ_API_KEY is not set"):
        GroqJSON().client


def test_parse_json_finds_the_object():
    assert parse_json('Here you go: {"a": [1, 2]} hope it helps') == {"a": [1, 2]}
