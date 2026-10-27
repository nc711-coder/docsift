"""Step 4: check every value, score how sure we are of each field, and decide: approve, or send to a person.

Two kinds of check:
  rules    the numbers must agree with each other (items add up to the subtotal; subtotal - discount + tax +
           shipping = total; quantity x price = amount), dates must be real and in order, formats must fit
  found    each value the AI copied must actually be on the page (in the text the reader got). A value that
           isn't there was misread by OCR, or invented: either way a person should look.

    report = check("invoice", fields, text)
    report.needs_review     True/False
    report.reasons          plain-English reasons for review
    report.confidence       {"total": 1.0, "vendor_name": 0.92, "due_date": 0.3, ...}
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from difflib import SequenceMatcher

from .schemas import REQUIRED

CENT = 0.011                     # amounts agree if within a cent (rounding on the document)
FOUND, FUZZY_OK = 1.0, 0.88      # exact match / smallest fuzzy similarity that counts as found
NOT_FOUND = 0.3

# Fields a client relies on: if one of these is doubtful, the document goes to review.
KEY_FIELDS = {
    "invoice": ["vendor_name", "invoice_number", "invoice_date", "due_date", "subtotal", "tax", "total"],
    "receipt": ["merchant_name", "date", "total", "tax", "tip", "card_last4"],
    "w9": ["name", "business_name", "tin_last4", "tin_type", "tax_classification", "signed", "date_signed"],
}
# Values that are judged rather than copied (no exact text to find): only the rules check them.
JUDGED = {"currency", "category", "tax_included", "payment_method", "tin_type", "tax_classification",
          "llc_tax_code", "signed"}


@dataclass
class Issue:
    field: str
    problem: str
    severity: str = "error"          # error -> review; warning -> noted only
    kind: str = "doubt"              # doubt: a value may be wrong; incomplete: the document itself is missing
                                     # something (unsigned W-9): extraction is right, but a person must follow up


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)
    confidence: dict[str, float] = field(default_factory=dict)
    needs_review: bool = False
    reasons: list[str] = field(default_factory=list)
    doubts: list[str] = field(default_factory=list)        # the reasons that question a value

    def add(self, f, problem, severity="error", kind="doubt"):
        self.issues.append(Issue(f, problem, severity, kind))


# ------------------------------------------------------------------------------------------- "is it on the page?"

def squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def alnum(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", (s or "").lower())


class Page:
    """The document text, prepared for lookups. `exact`: the text came from the PDF itself (no OCR), so a copied
    value must be on the page character for character; after OCR, near matches are allowed for letters only."""

    def __init__(self, text: str, exact: bool = False):
        self.raw = text or ""
        self.exact = exact
        self.flat = squash(self.raw)
        self.letters = alnum(self.raw)
        self.digits = re.sub(r"[,$]", "", self.flat)          # "$1,234.50" -> "1234.50"

    def money(self, v: float) -> float:
        forms = {f"{v:.2f}", f"{v:,.2f}"} | ({f"{v:.0f}"} if v == int(v) else set())
        return FOUND if any(squash(f).replace(",", "") in self.digits for f in forms) else NOT_FOUND

    def date(self, d: date) -> float:
        forms = [d.isoformat(), f"{d:%m/%d/%Y}", f"{d:%m/%d/%y}", f"{d:%B} {d.day}, {d.year}",
                 f"{d.day:02d} {d:%b} {d.year}", f"{d.day} {d:%b} {d.year}", f"{d:%b} {d.day:02d} {d.year}",
                 f"{d:%b} {d.day} {d.year}", f"{d:%b} {d.day}, {d.year}", f"{d.month}/{d.day}/{d.year}"]
        return FOUND if any(squash(f) in self.flat for f in forms) else NOT_FOUND

    def text(self, s: str) -> float:
        raw, s = s, alnum(s)
        if not s or s in self.letters:
            return FOUND
        # an address split over lines or columns: every part must be there ("5353 Industrial Pkwy" + "Tulsa, OK")
        parts = [alnum(p) for p in re.split(r",|\n", raw) if len(alnum(p)) >= 3]
        if len(parts) > 1 and all(p in self.letters for p in parts):
            return FOUND
        if self.exact:
            return NOT_FOUND
        # numbers inside the value must be there exactly, even after OCR ("Net 30", ZIP codes, street numbers)
        if any(d not in self.flat for d in re.findall(r"\d{2,}", raw)):
            return NOT_FOUND
        if len(parts) > 1:                     # judge each part separately, the weakest decides
            return min(self._near(p) for p in parts)
        return self._near(s)

    def _near(self, s: str) -> float:
        if s in self.letters:
            return FOUND
        # OCR slips ("Copperleaf" read as "Copperteaf"): best similarity of any same-length stretch of the page
        n, best = len(s), 0.0
        if n < 4 or n > 200:
            return NOT_FOUND
        step = max(1, n // 6)
        for i in range(0, max(1, len(self.letters) - n + 1), step):
            r = SequenceMatcher(None, s, self.letters[i:i + n]).ratio()
            if r > best:
                best = r
                if r > 0.97:
                    break
        # refine around the best rough position
        return round(best, 2) if best >= FUZZY_OK else NOT_FOUND


def _found(page: Page, value, name: str = "") -> float:
    if value is None:
        return 0.9                       # "not on the document": nothing to look up
    if isinstance(value, bool):
        return 0.9
    if isinstance(value, date):
        return page.date(value)
    if name == "time" and re.fullmatch(r"\d\d:\d\d", str(value)):
        h, m = map(int, str(value).split(":"))
        forms = [f"{h:02d}:{m:02d}", f"{h}:{m:02d}", f"{(h - 1) % 12 + 1}:{m:02d}{'pm' if h >= 12 else 'am'}"]
        return FOUND if any(f in page.flat for f in forms) else NOT_FOUND
    if isinstance(value, float):
        return page.money(value)
    return page.text(str(value))


# ------------------------------------------------------------------------------------------- rules per type

def _close(a, b) -> bool:
    return abs((a or 0) - (b or 0)) <= CENT


def _plausible_date(r: Report, name: str, d: date | None, future_days: int = 400):
    if d and not (date(2000, 1, 1) <= d <= date.today() + timedelta(days=future_days)):
        r.add(name, f"{d.isoformat()} isn't a plausible date")


def _rules_invoice(f, r: Report, page=None):
    items = f.line_items
    if not items:
        r.add("line_items", "no line items found")
    for i, it in enumerate(items, 1):
        if it.quantity is not None and it.unit_price is not None and not _close(it.quantity * it.unit_price, it.amount):
            # a little slack: unit prices are often rounded on the page
            if abs(it.quantity * it.unit_price - it.amount) > max(CENT, 0.005 * abs(it.amount)):
                r.add(f"line_items[{i}]", f"{it.quantity:g} x {it.unit_price:.2f} = "
                                          f"{it.quantity * it.unit_price:.2f}, not {it.amount:.2f}")
    items_sum = round(sum(it.amount for it in items), 2)
    if f.subtotal is not None and items and not _close(items_sum, f.subtotal):
        r.add("subtotal", f"line items add up to {items_sum:.2f}, but the subtotal is {f.subtotal:.2f}")
    base = f.subtotal if f.subtotal is not None else items_sum
    expected = round(base - (f.discount or 0) + (f.tax or 0) + (f.shipping or 0), 2)
    if f.total is not None and not _close(expected, f.total):
        r.add("total", f"subtotal - discount + tax + shipping = {expected:.2f}, but the total is {f.total:.2f}")
    if f.total is not None and f.total <= 0:
        r.add("total", "the total isn't positive")
    _plausible_date(r, "invoice_date", f.invoice_date, future_days=60)
    _plausible_date(r, "due_date", f.due_date)
    if f.due_date and f.invoice_date and f.due_date < f.invoice_date:
        r.add("due_date", "the due date is before the invoice date")
    if f.due_date is None and f.payment_terms and re.search(r"net\s*\d+", f.payment_terms, re.I):
        r.add("due_date", f"terms are '{f.payment_terms}' but no due date was found")


def _rules_receipt(f, r: Report, page=None):
    items_sum = round(sum(it.amount for it in f.items), 2)
    if not f.items:
        r.add("items", "no items found", "warning")
    if f.subtotal is not None and f.items and not _close(items_sum, f.subtotal):
        r.add("subtotal", f"items add up to {items_sum:.2f}, but the subtotal is {f.subtotal:.2f}")
    if f.tax_included:
        base = f.subtotal if f.subtotal is not None else items_sum
        expected = round(base + (f.tip or 0), 2)
    else:
        base = f.subtotal if f.subtotal is not None else items_sum
        expected = round(base + (f.tax or 0) + (f.tip or 0), 2)
    if f.total is not None and (f.items or f.subtotal is not None) and not _close(expected, f.total):
        r.add("total", f"the parts add up to {expected:.2f}, but the total is {f.total:.2f}")
    if f.payment_method and f.payment_method not in ("cash", "other") and not f.card_last4:
        r.add("card_last4", "paid by card but no card digits found")
    if f.payment_method == "cash" and f.card_last4:
        r.add("card_last4", "paid in cash but card digits were given")
    if f.time and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", f.time):
        r.add("time", f"'{f.time}' isn't a time")
    _plausible_date(r, "date", f.date, future_days=1)


W9_BOXES = {"individual_sole_proprietor": r"(?i:individual)", "c_corporation": r"(?<![A-Za-z])C\s+corporation",
            "s_corporation": r"(?<![A-Za-z])S\s+corporation", "partnership": r"(?i:partner)",
            "trust_estate": r"(?i:trus\w*\s*/?\s*estate|trust)", "llc": r"LLC\.", "other": r"(?i:other\s*\(see)"}
# a tick before the option's label: "X Partnership", "[X] C corporation", "(x) LLC."
_TICK = r"(?:\[\s*[xX]\s*\]|\(\s*[xX]\s*\)|(?<![A-Za-z0-9])[xX](?![A-Za-z]))\s*"


def ticked_boxes(text: str) -> list[str]:
    """Which line-3a options have an X before them on the page (both PDFs and OCR show the tick as an X)."""
    return [k for k, label in W9_BOXES.items() if re.search(_TICK + "(?:" + label + ")", text)]


def boxed_numbers(text: str) -> list[tuple[str, str | None]]:
    """(digits, "SSN"/"EIN"/None) for each "[digits in boxes]" line the reader added."""
    out = []
    for m in re.finditer(r"\[digits in boxes\]\s*([\d ]+?)\s*\(([\d+]+) digits(?::\s*(SSN|EIN))?", text):
        out.append((re.sub(r"\D", "", m.group(1)), m.group(3)))
    return out


def _rules_w9(f, r: Report, page: "Page"):
    ticks = ticked_boxes(page.raw)
    chosen = f.tax_classification.value if f.tax_classification else None
    if len(ticks) > 1:
        r.add("tax_classification", f"more than one box looks ticked ({', '.join(ticks)})")
    elif ticks and chosen and ticks[0] != chosen:
        r.add("tax_classification", f"the tick on the page is next to {ticks[0]}, not {chosen}")
    elif chosen and not ticks:
        r.add("tax_classification", f"no tick found next to {chosen} on the page")
    numbers = [n for n in boxed_numbers(page.raw) if len(n[0]) >= 7]
    if numbers and f.tin_last4:
        digits, layout = numbers[0]
        # OCR sometimes drops a leading 0 (harmless: the last 4 still check out); extra digits mean a misread
        if len(digits) > 9:
            r.add("tin_last4", f"the boxes read as {len(digits)} digits, not 9: OCR misread some")
        elif not digits.endswith(f.tin_last4):
            r.add("tin_last4", f"the boxes end in {digits[-4:]}, not {f.tin_last4}")
        if layout and f.tin_type and layout != f.tin_type:
            r.add("tin_type", f"the digits are in the {layout} boxes, not {f.tin_type}")
    if f.llc_tax_code and f.tax_classification and f.tax_classification.value != "llc":
        r.add("llc_tax_code", "an LLC tax code was given but the LLC box isn't marked")
    if f.tax_classification is None:
        r.add("tax_classification", "no tax classification box is marked", kind="incomplete")
    if f.tax_classification and f.tax_classification.value == "individual_sole_proprietor" and f.tin_type == "EIN":
        r.add("tin_type", "sole proprietors can use an EIN, but most give an SSN: check", "warning")
    if f.signed and f.date_signed is None:
        r.add("date_signed", "signed, but no date found")
    if f.signed is False:
        r.add("signed", "the form isn't signed", kind="incomplete")
    _plausible_date(r, "date_signed", f.date_signed, future_days=1)


RULES = {"invoice": _rules_invoice, "receipt": _rules_receipt, "w9": _rules_w9}


# ------------------------------------------------------------------------------------------- putting it together

# Printed labels that mean a value should have been found
EXPECTED_IF = {"invoice": {"payment_terms": r"\bnet\s*\d+\b|due\s+(on|upon)\s+receipt",
                           "po_number": r"\b(customer\s+)?p\.?o\.?\s*(#|no\.?|number)?\s*[:\s]\s*[A-Z0-9-]{3,}",
                           # a totals line: the label, then only an amount ("Shipping    $82.57")
                           "tax": r"^\s*(sales\s+)?tax\b[^\n]*?\$?-?[\d,]+\.\d\d\s*$",
                           "discount": r"^\s*discount\b[^\n]*?-?\$?[\d,]+\.\d\d\s*$",
                           "shipping": r"^\s*(shipping|freight)\b\W*\$?[\d,]+\.\d\d\s*$"},
               "receipt": {"tip": r"^\s*tip\b", "subtotal": r"\bsub\s*total\b"},
               "w9": {}}


def check(kind: str, fields, text: str, readable: bool = True, exact: bool = False,
          unsure: dict[str, float] | None = None) -> Report:
    """`exact`: the text is the PDF's own (not OCR), so every copied value must be on the page exactly.
    `unsure`: OCR words Tesseract scored low (word -> confidence); a value made from one is doubtful."""
    r = Report()
    page = Page(text, exact=exact)
    data = fields.__dict__
    for name, value in data.items():
        if name in JUDGED or isinstance(value, list):
            r.confidence[name] = 0.9 if value is not None else 0.8
            continue
        r.confidence[name] = _found(page, value, name)
        if r.confidence[name] == NOT_FOUND and value is not None:
            # On a real PDF every value must be there exactly: anything else is a copying mistake. After OCR,
            # side details (an address) can differ by a misread letter, so only key fields send it to review.
            # A number that isn't on the page (a ZIP code, "Net 30") is never just an OCR slip of the AI's: review.
            sev = "error" if (name in KEY_FIELDS[kind] or exact or re.search(r"\d", _show(value))) else "warning"
            r.add(name, f"'{_show(value)}' isn't on the page (misread, or not there)", sev)
        elif not exact and value is not None and FUZZY_OK <= r.confidence[name] < FOUND and \
                (name in KEY_FIELDS[kind] or "address" in name or name == "city_state_zip"):
            # After OCR the page says something slightly different (the AI may have fixed an OCR slip, or made
            # one). For names and addresses a person should take the two-second look.
            r.add(name, f"'{_show(value)}' only roughly matches the page (OCR); check it")
        if unsure and isinstance(value, str) and r.confidence[name] == FOUND:
            shaky = [w for w in re.findall(r"[0-9A-Za-z]{3,}", value) if w.lower() in unsure]
            if shaky:
                r.confidence[name] = 0.5
                w = shaky[0]
                sev = "error" if (name in KEY_FIELDS[kind] or "address" in name or name == "city_state_zip"
                                  or name.endswith("name") or re.search(r"\d", value)) else "warning"
                r.add(name, f"OCR was unsure of '{w}' (confidence {unsure[w.lower()]:.0f}); check it", sev)
        if value is None and name in EXPECTED_IF[kind] and re.search(EXPECTED_IF[kind][name], text, re.I | re.M):
            r.confidence[name] = NOT_FOUND
            r.add(name, "the document seems to show one, but none was extracted")
    rows = data.get("line_items") or data.get("items") or []
    row_conf = []
    for i, it in enumerate(rows, 1):
        c = min(page.text(it.description), page.money(it.amount))
        row_conf.append(c)
        if c == NOT_FOUND:
            r.add(f"{'line_items' if kind == 'invoice' else 'items'}[{i}]",
                  f"'{it.description}' / {it.amount:.2f} isn't on the page", "warning")
    if rows:
        r.confidence["line_items" if kind == "invoice" else "items"] = round(min(row_conf), 2)

    for name in REQUIRED[kind]:
        if data.get(name) is None:
            r.confidence[name] = 0.0
            r.add(name, "not found on the document (needed)")
    RULES[kind](fields, r, page)
    for i in r.issues:                     # a field with a broken rule is doubtful whatever the lookup said
        base = i.field.split("[")[0]
        if i.severity == "error" and i.kind == "doubt" and base in r.confidence:
            r.confidence[base] = min(r.confidence[base], 0.4)

    if not readable:
        r.doubts.append("the page was hard to read (low OCR confidence)")
    r.doubts += [f"{i.field}: {i.problem}" for i in r.issues if i.severity == "error" and i.kind == "doubt"]
    r.reasons = r.doubts + [f"{i.field}: {i.problem}" for i in r.issues if i.severity == "error" and i.kind != "doubt"]
    r.needs_review = bool(r.reasons)
    return r


def _show(v):
    return v.isoformat() if isinstance(v, date) else f"{v:.2f}" if isinstance(v, float) else str(v)
