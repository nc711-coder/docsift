"""Step 3: the AI copies the fields off the document into the schema for its type.

    fields, info = extract("invoice", text, llm)    # fields: an Invoice (docsift/schemas.py), or None

What keeps the answer honest:
  - the model gets the JSON schema for the type, and rules for every awkward case (missing values are null,
    US dates, money as plain numbers, last 4 digits only)
  - its answer is tidied ("$1,234.50" -> 1234.5, "03/05/2026" -> 2026-03-05) and checked by Pydantic
  - if it doesn't fit the schema, the model is shown what was wrong and gets one more try
  - card numbers and tax IDs are cut to their last 4 digits whatever the model returned
Whether the values are RIGHT is the next step's job (docsift/validate.py).
"""
from __future__ import annotations

import re
from datetime import date, datetime

from pydantic import BaseModel, ValidationError

from .schemas import MODELS

COMMON = """You copy information from a business document into JSON. The text came from a PDF or from OCR of a scan or \
photo: columns are separated by spaces, and OCR may have misread a few characters.

Rules:
- Only use what the document shows. If a field isn't on the document, use null. Never invent a value.
- Dates: YYYY-MM-DD. Read numeric dates as US month/day/year (03/05/2026 = March 5, 2026).
- Money: a plain number, no $ or commas (1234.5 for $1,234.50).
- Copy names, numbers and descriptions exactly as printed (keep prefixes like "INV-"). Fix only obvious OCR \
slips: O for 0 inside a number, or a common word misspelled by OCR (Bivd -> Blvd, Stret -> Street). Never \
change a digit you can't see clearly.
- Answer with the JSON object only."""

GUIDES = {
    "invoice": """Invoice fields:
- vendor_name / vendor_address: the business that issued the invoice (usually at the top, or "Remit to").
- bill_to_name / bill_to_address: the customer ("Bill to", "Sold to", "Client").
- invoice_number exactly as printed. po_number: the customer's purchase order number, if shown.
- due_date: only if a due date is printed; for "Due on receipt" or no date, null.
- payment_terms as printed, e.g. "Net 30" or "Due on receipt".
- line_items: one per row of the items table, in order, across all pages: description, quantity (hours on a \
service invoice), unit_price, amount.
- subtotal, discount (a positive number), tax (the sales tax amount), shipping, total: as printed; null if \
the line isn't there.""",
    "receipt": """Receipt fields:
- merchant_name: the store or business at the top. merchant_address: its street and city.
- date, and time in 24-hour HH:MM (1:44 PM = 13:44).
- items: each thing bought, in order: description, quantity if printed (gallons for fuel), amount.
- subtotal, tax, tip, total as printed; null if that line isn't there.
- tax_included: true when the receipt says prices include tax (e.g. "PRICE INCLUDES ALL TAXES", \
"INCL. SALES TAX"); then tax is the included tax amount if printed, else null.
- payment_method: one of cash, visa, mastercard, amex, discover, debit, other.
- card_last4: the last 4 digits of the card (from "************1234"); null for cash.
- category: your judgement from the merchant and items: meals, groceries, fuel, office_supplies, travel \
(parking, taxi, hotels, flights) or other.""",
    "w9": """Form W-9 fields:
- name (line 1), business_name (line 2, null if blank), address (line 5), city_state_zip (line 6).
- tax_classification: the box marked with an X on line 3a: individual_sole_proprietor, c_corporation, \
s_corporation, partnership, trust_estate, llc or other. null if no box is marked.
- llc_tax_code: C, S or P, only when the LLC box is marked.
- tin_type: "SSN" if the social security number boxes hold digits, "EIN" if the employer identification \
number boxes do. Lines starting "[digits in boxes]" are the digits read from those boxes (SSN = 3-2-4 digits, \
EIN = 2-7).
- tin_last4: ONLY the last 4 digits of that number. Never write the full number anywhere.
- signed: true if there is a signature (usually the person's name written in the signature area).
- date_signed: the date beside the signature, or null.""",
}

DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y", "%d %b %Y", "%d %B %Y",
                "%b %d %Y", "%B %d %Y"]


def schema_for(kind: str) -> dict:
    return MODELS[kind].model_json_schema()


def prompt(kind: str) -> str:
    return COMMON + "\n\n" + GUIDES[kind]


def extract(kind: str, text: str, llm) -> tuple[BaseModel | None, dict]:
    """Returns (fields or None, info: attempts, model calls, errors)."""
    model = MODELS[kind]
    user = f"Document text:\n\n{text}\n\nReturn the {kind} as JSON."
    info = {"attempts": 0, "calls": [], "errors": []}
    data = None
    for attempt in range(2):
        info["attempts"] += 1
        if attempt and data is not None:
            user += (f"\n\nYour previous answer didn't fit the schema:\n{info['errors'][-1]}\n"
                     f"Previous answer: {str(data)[:3000]}\nReturn corrected JSON.")
        data, call = llm.json(prompt(kind), user, schema_for(kind), name=kind)
        info["calls"].append(call)
        tidy = coerce(kind, data)
        try:
            return model.model_validate(tidy), info
        except ValidationError as e:
            info["errors"].append("; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())[:800])
    return None, info


# ------------------------------------------------------------------------------------------- tidying

def to_number(v):
    if v is None or isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    if s.lower() in ("", "null", "none", "n/a", "-"):
        return None
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-")
    s = re.sub(r"[^\d.]", "", s)
    if not s or s.count(".") > 1:
        return v                                   # leave it for Pydantic to report
    return -float(s) if neg else float(s)


def to_date(v):
    if v is None or isinstance(v, date):
        return v
    s = re.sub(r"\s+", " ", str(v).strip().replace(".", ""))
    if s.lower() in ("", "null", "none", "n/a"):
        return None
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            continue
    return v


def last4(v):
    if v is None:
        return None
    digits = re.sub(r"\D", "", str(v))
    return digits[-4:] if len(digits) >= 4 else None


MONEY = {"invoice": ["subtotal", "discount", "tax", "shipping", "total"],
         "receipt": ["subtotal", "tax", "tip", "total"], "w9": []}
DATES = {"invoice": ["invoice_date", "due_date"], "receipt": ["date"], "w9": ["date_signed"]}


def coerce(kind: str, data: dict) -> dict:
    """Fix the formats models commonly get slightly wrong, before Pydantic checks the shape."""
    d = {k: (None if isinstance(v, str) and v.strip().lower() in ("", "null", "none", "n/a") else v)
         for k, v in (data or {}).items()}
    for k in MONEY[kind]:
        if k in d:
            d[k] = to_number(d[k])
    if kind == "invoice" and d.get("discount"):
        d["discount"] = abs(d["discount"]) if isinstance(d["discount"], (int, float)) else d["discount"]
    for k in DATES[kind]:
        if k in d:
            d[k] = to_date(d[k])
    rows = "line_items" if kind == "invoice" else "items" if kind == "receipt" else None
    if rows and isinstance(d.get(rows), list):
        d[rows] = [{k: (to_number(v) if k in ("quantity", "unit_price", "amount") else v) for k, v in r.items()}
                   for r in d[rows] if isinstance(r, dict)]
    if kind == "receipt":
        d["card_last4"] = last4(d.get("card_last4"))
        if isinstance(d.get("payment_method"), str):
            d["payment_method"] = d["payment_method"].strip().lower()
        if isinstance(d.get("time"), str):
            d["time"] = _time24(d["time"])
        if isinstance(d.get("category"), str):
            d["category"] = d["category"].strip().lower().replace(" ", "_")
    if kind == "w9":
        d["tin_last4"] = last4(d.get("tin_last4"))           # whatever came back, keep only the last 4
        if isinstance(d.get("tin_type"), str):
            d["tin_type"] = d["tin_type"].strip().upper()
        if isinstance(d.get("tax_classification"), str):
            d["tax_classification"] = d["tax_classification"].strip().lower()
    return d


def _time24(s: str) -> str | None:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*([ap])?\.?m?\.?\s*$", s, re.I)
    if not m:
        return s
    h, mm, ap = int(m.group(1)), m.group(2), (m.group(3) or "").lower()
    if ap == "p" and h < 12:
        h += 12
    if ap == "a" and h == 12:
        h = 0
    return f"{h:02d}:{mm}"
