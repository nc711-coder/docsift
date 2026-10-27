"""The whole path for one document: read -> classify -> extract -> validate.

    from docsift.pipeline import process
    result = process("samples/docs/inv-16.jpg")
    result.type, result.fields, result.needs_review, result.reasons, result.confidence

Nothing returned contains a full tax ID or card number: the text kept for the review page is masked too.
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .classify import classify
from .extract import extract
from .reader import read
from .validate import check


@dataclass
class Result:
    type: str
    classified_by: str
    fields: dict | None
    needs_review: bool
    reasons: list[str]                                 # why it needs review (empty = approved)
    doubts: list[str] = field(default_factory=list)    # the reasons that question a value (vs an incomplete document)
    confidence: dict[str, float] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    reading: dict = field(default_factory=dict)        # method, OCR confidence, pages, warnings
    text: str = ""                                     # masked
    seconds: dict = field(default_factory=dict)
    model_calls: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


_CARD = re.compile(r"\b(?:\d[ -]?){9,15}(\d{4})\b")
# 9 digits with any spaces or hyphens between them: "987-65-4321", "00 8836261", "0 0 - 5 3 5 8 4  6 6"
_TAX_ID = re.compile(r"(?<![\d/])(?:\d[ \t-]{0,3}){8}\d(?![\d/])")


def mask(text: str, tax_ids: bool = True) -> str:
    """Hide tax IDs and card numbers, keeping the last 4 digits: 987-65-4321 -> *****4321. Tax-ID patterns are
    only applied to W-9s (`tax_ids`), so a 9-digit invoice number elsewhere isn't masked by mistake."""
    if tax_ids:
        text = _TAX_ID.sub(lambda m: "*****" + re.sub(r"\D", "", m.group(0))[-4:], text)
    return _CARD.sub(r"****\1", text)


def process(source, filename: str | None = None, llm=None) -> Result:
    if llm is None:
        from .llm import GroqJSON
        llm = GroqJSON()
    seconds = {}
    t = time.perf_counter()
    r = read(source, filename)
    seconds["read"] = round(time.perf_counter() - t, 2)
    return analyse(r, llm, seconds)


def analyse(r, llm, seconds: dict | None = None) -> Result:
    """Everything after reading (the evals reuse cached reading results through this)."""
    seconds = dict(seconds or {})
    reading = {"method": r.method, "ocr_confidence": r.confidence, "pages": len(r.pages), "readable": r.readable,
               "warnings": r.warnings, "notes": [n for p in r.pages for n in p.notes],
               # kept so edits can be re-checked later; long digit runs dropped (they could be part of a tax ID)
               "unsure": {w: c for w, c in r.unsure.items() if not re.fullmatch(r"\d{5,}", w)}}

    t = time.perf_counter()
    c = classify(r.text, llm)
    seconds["classify"] = round(time.perf_counter() - t, 2)
    if c.type == "other":
        why = [f"not an invoice, receipt or W-9 ({c.reason})"]
        return Result("other", c.by, None, True, why, why, reading=reading, text=mask(r.text), seconds=seconds)
    masked = mask(r.text, tax_ids=c.type == "w9")

    t = time.perf_counter()
    fields, info = extract(c.type, r.text, llm)
    seconds["extract"] = round(time.perf_counter() - t, 2)
    if fields is None:
        why = ["the AI's answer didn't fit the form: " + info["errors"][-1]]
        return Result(c.type, c.by, None, True, why, why, reading=reading, text=masked, seconds=seconds, model_calls=info["calls"])

    t = time.perf_counter()
    rep = check(c.type, fields, r.text, readable=r.readable, exact=r.method == "text", unsure=r.unsure)
    seconds["validate"] = round(time.perf_counter() - t, 2)
    unsure = [] if c.confident else [f"document type unsure ({c.reason})"]
    reasons, doubts = rep.reasons + unsure, rep.doubts + unsure
    seconds["total"] = round(sum(seconds.values()), 2)
    return Result(c.type, c.by, fields.model_dump(mode="json"), bool(reasons), reasons, doubts, rep.confidence,
                  [asdict(i) for i in rep.issues], reading, masked, seconds, info["calls"])


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) != 2:
        print("usage: python -m docsift.pipeline <file>")
        raise SystemExit(2)
    from .llm import LLMError
    try:
        res = process(Path(sys.argv[1]))
    except (LLMError, RuntimeError, ValueError) as e:
        print(f"Couldn't process it: {e}")
        raise SystemExit(1)
    out = res.to_dict()
    out.pop("text")
    print(json.dumps(out, indent=2))
