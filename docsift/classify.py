"""Step 2: which kind of document is it? Invoice, receipt, W-9, or something DocSift doesn't handle.

Clear cases are decided by rules (instant, free, and the same answer every time): "Request for Taxpayer
Identification Number" is a W-9, "Bill to" + "Invoice #" is an invoice, "Subtotal / Total / Visa ****" on short
lines is a receipt. Only when the rules aren't sure does the AI decide. Either way the result says which did it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

TYPES = ("invoice", "receipt", "w9")

SIGNALS = {
    "w9": [(r"\bw-?9\b", 2), (r"taxpayer\s+identification\s+number", 3), (r"backup\s+withholding", 2),
           (r"federal\s+tax\s+classification", 2), (r"social\s+security\s+number", 1)],
    "invoice": [(r"\binvoice\b", 2), (r"\b(bill|sold)\s+to\b|\bclient:", 2), (r"\binvoice\s*(#|no\.?|number)", 2),
                (r"\b(due\s+date|payment\s+due|due:)", 1), (r"\bnet\s+\d{1,2}\b|\bterms\b", 1),
                (r"\b(balance|amount|total)\s+due\b", 1), (r"\bunit\s+price\b|\brate\b|\bqty\b", 1)],
    "receipt": [(r"\bsubtotal\b", 1), (r"\b(change|cash)\b", 1), (r"\bauth(orization)?\s*(code|#)", 2),
                (r"\*{4,}\s*\d{4}|x{4,}\d{4}", 2), (r"thank\s+you", 1), (r"\b(server|table|pump|gal)\b", 1),
                (r"\btip\b", 1), (r"\b(visa|mastercard|amex|discover|debit)\b", 1)],
}


@dataclass
class Classification:
    type: str                  # invoice | receipt | w9 | other
    confident: bool
    by: str                    # "rules" | "ai"
    scores: dict
    reason: str = ""


def rule_scores(text: str) -> dict[str, int]:
    t = text.lower()
    scores = {k: sum(w for pat, w in sigs if re.search(pat, t)) for k, sigs in SIGNALS.items()}
    lines = [l for l in text.splitlines() if l.strip()]
    if lines and sorted(len(l) for l in lines)[len(lines) // 2] <= 45:      # narrow, like a till roll
        scores["receipt"] += 1
    return scores


def classify(text: str, llm=None) -> Classification:
    scores = rule_scores(text)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    (best, top), (_, second) = ranked[0], ranked[1]
    if top >= 4 and top - second >= 3:
        return Classification(best, True, "rules", scores, f"rule score {top} vs {second}")
    if llm is None:
        return Classification(best if top >= 3 else "other", False, "rules", scores, "rules unsure, no AI given")
    data, _ = llm.json(
        "You sort business documents. Answer in JSON: {\"type\": \"invoice\" | \"receipt\" | \"w9\" | \"other\", "
        "\"reason\": \"a few words\"}. invoice = a bill from a business asking to be paid; receipt = proof of a "
        "purchase already paid (shop, restaurant, fuel, parking, taxi); w9 = IRS Form W-9 (taxpayer ID request); "
        "other = anything else.",
        "Document text:\n\n" + text[:6000], name="classification")
    kind = str(data.get("type", "other")).lower().replace("-", "")
    kind = kind if kind in TYPES else "other"
    return Classification(kind, kind != "other", "ai", scores, str(data.get("reason", ""))[:200])
