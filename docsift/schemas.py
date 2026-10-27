"""The fields DocSift extracts, one model per document type.

The same models serve three jobs:
  - the known answers for the test documents (samples/answers/*.json)
  - the shape the AI must fill in (day 2: its JSON schema is sent with the request)
  - checking what the AI returned before anything is validated or saved

Conventions, so answers compare cleanly:
  money      a number with 2 decimals, no currency sign (1234.5 means $1,234.50)
  dates      YYYY-MM-DD, whatever the document printed ("March 5, 2026" -> 2026-03-05)
  missing    None (null), never "" or "N/A"
  required   the fields a document can't do without (an invoice's number, total...) still accept None here, so
             one unreadable value doesn't discard everything else: docsift/validate.py flags it for review
  sensitive  W-9 TINs and card numbers: last 4 digits only, never the full number
"""
from __future__ import annotations

import datetime as dt
from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

DOC_TYPES = ("invoice", "receipt", "w9")


def _money(v):
    return None if v is None else round(float(v), 2)


class LineItem(BaseModel):
    description: str
    quantity: float | None = None          # hours for service invoices
    unit_price: float | None = None
    amount: float

    @field_validator("quantity", "unit_price", "amount")
    @classmethod
    def _cents(cls, v):
        return _money(v)


class Invoice(BaseModel):
    vendor_name: str | None = None         # required (see above)
    vendor_address: str | None = None
    invoice_number: str | None = None      # required
    invoice_date: date | None = None       # required
    due_date: date | None = None           # None when the invoice says "due on receipt" or gives no date
    po_number: str | None = None
    bill_to_name: str | None = None
    bill_to_address: str | None = None
    currency: str = "USD"
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: float | None = None
    discount: float | None = None          # a positive number: 25.0 means $25 off
    tax: float | None = None
    shipping: float | None = None
    total: float | None = None             # required
    payment_terms: str | None = None       # "Net 30", "Due on receipt"

    @field_validator("subtotal", "discount", "tax", "shipping", "total")
    @classmethod
    def _cents(cls, v):
        return _money(v)


class ExpenseCategory(str, Enum):
    meals = "meals"
    groceries = "groceries"
    fuel = "fuel"
    office_supplies = "office_supplies"
    travel = "travel"
    other = "other"


class ReceiptItem(BaseModel):
    description: str
    quantity: float | None = None
    amount: float

    @field_validator("quantity", "amount")
    @classmethod
    def _cents(cls, v):
        return _money(v)


class Receipt(BaseModel):
    merchant_name: str | None = None       # required
    merchant_address: str | None = None
    date: dt.date | None = None            # required (dt.date: the field's name hides the type)
    time: str | None = None                # 24-hour HH:MM
    items: list[ReceiptItem] = Field(default_factory=list)
    subtotal: float | None = None
    tax: float | None = None
    tax_included: bool = False             # prices already include tax ("incl. tax 0.84")
    tip: float | None = None
    total: float | None = None             # required
    payment_method: Literal["cash", "visa", "mastercard", "amex", "discover", "debit", "other"] | None = None
    card_last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    category: ExpenseCategory = ExpenseCategory.other    # not printed: judged from the merchant and items

    @field_validator("subtotal", "tax", "tip", "total")
    @classmethod
    def _cents(cls, v):
        return _money(v)


class TaxClassification(str, Enum):
    individual = "individual_sole_proprietor"
    c_corp = "c_corporation"
    s_corp = "s_corporation"
    partnership = "partnership"
    trust = "trust_estate"
    llc = "llc"
    other = "other"


class W9(BaseModel):
    name: str | None = None                # line 1, required
    business_name: str | None = None       # line 2
    tax_classification: TaxClassification | None = None     # None when no box is checked
    llc_tax_code: Literal["C", "S", "P"] | None = None      # only for LLCs
    address: str | None = None             # line 5, required
    city_state_zip: str | None = None      # line 6, required
    tin_type: Literal["SSN", "EIN"] | None = None             # required
    tin_last4: str | None = Field(default=None, pattern=r"^\d{4}$")   # required; never the full number
    signed: bool | None = None             # required
    date_signed: date | None = None


REQUIRED = {"invoice": ["vendor_name", "invoice_number", "invoice_date", "total"],
            "receipt": ["merchant_name", "date", "total"],
            "w9": ["name", "address", "city_state_zip", "tin_type", "tin_last4", "signed"]}

MODELS: dict[str, type[BaseModel]] = {"invoice": Invoice, "receipt": Receipt, "w9": W9}
