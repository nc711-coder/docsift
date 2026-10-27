"""Make the synthetic test documents and their known answers.

    python samples/generate.py          # writes samples/docs/ and samples/answers/ (same output every time)

60 documents: 20 invoices, 20 receipts, 20 W-9 forms. Every company, person, address and number is invented,
every page says SAMPLE, and the W-9 tax IDs use number ranges that are never issued (SSNs 987-65-432x are
reserved for examples; EINs never start with 00).

For each document:
  samples/docs/<id>.pdf or .jpg     the document: a clean PDF, a scan (image-only PDF) or a phone photo (JPG)
  samples/answers/<id>.json         {"type": ..., "fields": {...the correct extraction...},
                                     "printed": [...strings exactly as printed...], "meta": {...}}

"printed" lists the key values as they appear on the page ("$1,234.50", "March 5, 2026"). The reading check
(evals/reading.py) uses it to measure how much of each document the reader got back as text.
"""
from __future__ import annotations

import io
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from reportlab.lib.colors import Color, HexColor, black, white
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parent
DOCS, ANSWERS = ROOT / "docs", ROOT / "answers"
SEED = 2026
SAMPLE_NOTE = "SAMPLE - synthetic test document generated for DocSift. Not a real record."

# ------------------------------------------------------------------------------------------- invented data

CITIES = [("Arlington", "VA", "22201"), ("Columbus", "OH", "43215"), ("Tempe", "AZ", "85281"),
          ("Madison", "WI", "53703"), ("Raleigh", "NC", "27601"), ("Boise", "ID", "83702"),
          ("Portland", "ME", "04101"), ("Tulsa", "OK", "74103"), ("Eugene", "OR", "97401"),
          ("Savannah", "GA", "31401"), ("Lansing", "MI", "48933"), ("Fresno", "CA", "93721")]
STREETS = ["Maple Ave", "Industrial Pkwy", "Commerce Dr", "Harbor St", "Ridge Rd", "Elm St", "Lakeview Blvd",
           "Mill Rd", "Union St", "Cedar Ln", "Foundry Way", "Summit Ave", "Orchard St", "Bridge St"]
FIRST = ["Maria", "James", "Priya", "Daniel", "Aisha", "Tomas", "Grace", "Kevin", "Lena", "Omar", "Rachel",
         "Victor", "Hannah", "Marcus", "Sofia", "Ethan", "Nadia", "Caleb", "Yuki", "Andre"]
LAST = ["Alvarez", "Brennan", "Chowdhury", "Dalton", "Esposito", "Fairchild", "Goldberg", "Haddad", "Iwata",
        "Jensen", "Kowalski", "Lindqvist", "Mbeki", "Navarro", "Okafor", "Pellegrini", "Quinlan", "Rasmussen"]

VENDORS = {   # industry -> (company names, catalog of (description, low price, high price, unit))
    "it": (["Brightline IT Services LLC", "Northgate Systems Inc.", "Copperleaf Tech Partners"],
           [("Managed IT support - monthly", 450, 1800, "month"), ("On-site network troubleshooting", 95, 160, "hour"),
            ("Firewall license renewal (12 months)", 300, 900, "each"), ("Laptop setup and imaging", 75, 140, "each"),
            ("Microsoft 365 Business seats", 12.5, 22, "seat"), ("Cloud backup storage 1 TB", 18, 45, "each"),
            ("Help desk tickets (overage)", 35, 60, "seat")]),
    "office": (["Paperwell Office Supply Co.", "Staplewood Business Products"],
               [("Copy paper, letter, 10 reams", 42, 58, "each"), ("Black toner cartridge HY", 89, 145, "each"),
                ("Ballpoint pens, box of 50", 9, 16, "each"), ("Ergonomic office chair", 210, 390, "each"),
                ("Filing cabinet, 3-drawer", 160, 260, "each"), ("Sticky notes, 24 pads", 11, 19, "each"),
                ("Desk organizer set", 18, 34, "each"), ("Shipping labels, 1000 ct", 24, 38, "each")]),
    "marketing": (["Lanternfish Creative Studio", "Bold Arrow Marketing Group"],
                  [("Brand strategy workshop", 1200, 3500, "each"), ("Social media management - month", 900, 2400, "month"),
                   ("Copywriting", 65, 120, "hour"), ("Graphic design", 70, 130, "hour"),
                   ("Paid ads management fee", 400, 1500, "each"), ("Photography session (half day)", 600, 1100, "each")]),
    "construction": (["Ironside Building Supply", "Graniteview Materials Inc."],
                     [("2x4x8 kiln-dried stud", 3.85, 6.4, "each"), ("Drywall sheet 4x8 1/2in", 11, 16, "each"),
                      ("Concrete mix 80 lb", 5.5, 8.9, "each"), ("Exterior screws 5 lb box", 28, 44, "each"),
                      ("Rebar #4 20 ft", 9, 15, "each"), ("Plywood 3/4in sheet", 38, 62, "each"),
                      ("Equipment rental - mini excavator (day)", 280, 420, "each")]),
    "cleaning": (["Sparkfield Commercial Cleaning", "ClearCourse Janitorial LLC"],
                 [("Nightly office cleaning - month", 900, 2600, "month"), ("Carpet deep clean", 0.22, 0.4, "sq ft"),
                  ("Window washing, exterior", 180, 420, "each"), ("Restroom supplies restock", 60, 140, "each"),
                  ("Floor strip and wax", 0.35, 0.6, "sq ft")]),
}
CUSTOMERS = ["Harborview Dental Group", "Pinecrest Property Management", "Summit Ridge Logistics",
             "Willow & Finch Bakery", "Redwood Family Law PLLC", "Bluewater Veterinary Clinic",
             "Northstar Charter School", "Copper Kettle Restaurants", "Meridian Physical Therapy"]


def person(rng):
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


def address(rng):
    city, st, zp = rng.choice(CITIES)
    street = f"{rng.randint(12, 9800)} {rng.choice(STREETS)}"
    if rng.random() < 0.3:
        street += f", Suite {rng.randint(100, 450)}"
    return street, f"{city}, {st} {zp}"


def money(x, sign=True):
    return ("$" if sign else "") + f"{x:,.2f}"


DATE_FORMATS = {
    "us": lambda d: d.strftime("%m/%d/%Y"),
    "iso": lambda d: d.isoformat(),
    "long": lambda d: f"{d:%B} {d.day}, {d.year}",
    "short": lambda d: f"{d.day:02d} {d:%b} {d.year}",
}


def rand_date(rng, start=date(2026, 1, 5), end=date(2026, 10, 20)):
    return start + timedelta(days=rng.randint(0, (end - start).days))


# ------------------------------------------------------------------------------------------- invoices

INVOICE_LAYOUTS = ["classic", "band", "minimal", "remit", "service"]


def invoice_data(rng, n, layout, tricks):
    industry = {"service": rng.choice(["marketing", "it"]), "remit": rng.choice(["construction", "office"])}.get(
        layout, rng.choice(list(VENDORS)))
    names, catalog = VENDORS[industry]
    vendor = rng.choice(names)
    v_street, v_city = address(rng)
    customer = rng.choice(CUSTOMERS)
    c_street, c_city = address(rng)
    count = 26 if "multi_page" in tricks else rng.randint(2, 7)
    items = []
    for _ in range(count):
        desc, lo, hi, unit = rng.choice(catalog)
        price = round(rng.uniform(lo, hi), 2)
        qty = (rng.choice([1, 1.5, 2, 2.5, 3, 4, 6, 7.5, 8, 10]) if unit == "hour" else
               rng.choice([250, 400, 800, 1200]) if unit == "sq ft" else
               rng.choice([1, 1, 1, 2, 3]) if unit == "month" or price >= 150 else
               rng.choice([4, 5, 8, 10, 12, 15, 20]) if unit == "seat" else rng.choice([1, 2, 3, 4, 5, 10, 12, 24]))
        items.append({"description": desc, "quantity": qty, "unit_price": price, "amount": round(qty * price, 2)})
    subtotal = round(sum(i["amount"] for i in items), 2)
    discount = round(subtotal * rng.choice([0.05, 0.1]), 2) if "discount" in tricks else None
    rate = 0.0 if "no_tax" in tricks else rng.choice([0.0575, 0.06, 0.0725, 0.08, 0.0875])
    tax = round((subtotal - (discount or 0)) * rate, 2) if rate else None
    shipping = round(rng.uniform(15, 85), 2) if (industry in ("office", "construction") and rng.random() < 0.6) else None
    total = round(subtotal - (discount or 0) + (tax or 0) + (shipping or 0), 2)
    inv_date = rand_date(rng)
    if "due_on_receipt" in tricks:
        terms, due = "Due on receipt", None
    else:
        days = rng.choice([15, 30, 30, 45])
        terms, due = f"Net {days}", inv_date + timedelta(days=days)
    number = rng.choice([f"INV-{rng.randint(1000, 9999)}", f"{rng.randint(2026000, 2026999)}",
                         f"{vendor[:2].upper()}-{rng.randint(100, 999)}-{rng.randint(10, 99)}"])
    po = f"PO-{rng.randint(40000, 49999)}" if rng.random() < 0.45 else None
    fields = {"vendor_name": vendor, "vendor_address": f"{v_street}, {v_city}", "invoice_number": number,
              "invoice_date": inv_date.isoformat(), "due_date": due.isoformat() if due else None, "po_number": po,
              "bill_to_name": customer, "bill_to_address": f"{c_street}, {c_city}", "currency": "USD",
              "line_items": items, "subtotal": subtotal, "discount": discount, "tax": tax, "shipping": shipping,
              "total": total, "payment_terms": terms}
    return fields, {"v_street": v_street, "v_city": v_city, "c_street": c_street, "c_city": c_city,
                    "rate": rate, "hourly": industry in ("marketing", "it") and layout == "service",
                    "email": f"billing@{vendor.split()[0].lower()}.example", "phone": f"(555) 01{rng.randint(10, 99)}-{rng.randint(1000, 9999)}"}


def draw_invoice(f, x, layout, fmt):
    """Draw one invoice. Returns PDF bytes and the strings printed for key values."""
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=letter)
    W, H = letter
    d = DATE_FORMATS[fmt]
    printed = {"vendor_name": f["vendor_name"], "invoice_number": f["invoice_number"],
               "invoice_date": d(date.fromisoformat(f["invoice_date"])), "bill_to_name": f["bill_to_name"],
               "total": money(f["total"])}
    if f["due_date"]:
        printed["due_date"] = d(date.fromisoformat(f["due_date"]))
    if f["po_number"]:
        printed["po_number"] = f["po_number"]
    accent = HexColor(["#1F4E79", "#7A2E2E", "#2E5E3E", "#4B3B8F", "#8A5A00"][INVOICE_LAYOUTS.index(layout)])

    def footer():
        c.setFont("Helvetica", 7)
        c.setFillColor(Color(0.45, 0.45, 0.45))
        c.drawCentredString(W / 2, 24, SAMPLE_NOTE)
        c.setFillColor(black)

    # ---- header
    if layout == "band":
        c.setFillColor(accent)
        c.rect(0, H - 110, W, 110, stroke=0, fill=1)
        c.setFillColor(white)
        c.setFont("Helvetica-Bold", 20)
        c.drawString(48, H - 56, f["vendor_name"])
        c.setFont("Helvetica", 9)
        c.drawString(48, H - 74, f"{x['v_street']}, {x['v_city']}")
        c.drawString(48, H - 87, f"{x['phone']}  |  {x['email']}")
        c.setFont("Helvetica-Bold", 26)
        c.drawRightString(W - 48, H - 60, "INVOICE")
        c.setFillColor(black)
        y = H - 140
        boxes = [("Invoice #", f["invoice_number"]), ("Date", printed["invoice_date"]),
                 ("Due", printed.get("due_date", "On receipt"))] + ([("PO", f["po_number"])] if f["po_number"] else [])
        bw = (W - 96) / len(boxes)
        for i, (k, v) in enumerate(boxes):
            bx = 48 + i * bw
            c.setStrokeColor(Color(0.8, 0.8, 0.8))
            c.rect(bx, y - 34, bw - 8, 40, stroke=1, fill=0)
            c.setFont("Helvetica", 8)
            c.drawString(bx + 8, y - 8, k.upper())
            c.setFont("Helvetica-Bold", 10.5)
            c.drawString(bx + 8, y - 25, v)
        y -= 70
        c.setFont("Helvetica-Bold", 9)
        c.drawString(48, y, "BILL TO")
        c.setFont("Helvetica", 10)
        for i, line in enumerate([f["bill_to_name"], x["c_street"], x["c_city"]]):
            c.drawString(48, y - 15 - 13 * i, line)
        y -= 75
    elif layout == "remit":
        c.setFont("Helvetica-Bold", 15)
        c.drawString(48, H - 60, f["vendor_name"])
        c.setFont("Helvetica-Bold", 18)
        c.setFillColor(accent)
        c.drawRightString(W - 48, H - 60, "SALES INVOICE")
        c.setFillColor(black)
        y = H - 100
        c.setFont("Helvetica-Bold", 9)
        c.drawString(48, y, "REMIT TO")
        c.drawString(W / 2, y, "SOLD TO")
        c.setFont("Helvetica", 10)
        for i, (a, b) in enumerate(zip([f["vendor_name"], x["v_street"], x["v_city"]],
                                       [f["bill_to_name"], x["c_street"], x["c_city"]])):
            c.drawString(48, y - 15 - 13 * i, a)
            c.drawString(W / 2, y - 15 - 13 * i, b)
        y -= 80
        heads = ["Invoice No.", "Invoice Date", "Terms", "Due Date", "Customer PO"]
        vals = [f["invoice_number"], printed["invoice_date"], f["payment_terms"], printed.get("due_date", "-"),
                f["po_number"] or "-"]
        cw = (W - 96) / 5
        c.setFillColor(Color(0.92, 0.92, 0.92))
        c.rect(48, y - 4, W - 96, 16, stroke=0, fill=1)
        c.setFillColor(black)
        for i, (h, v) in enumerate(zip(heads, vals)):
            c.setFont("Helvetica-Bold", 8.5)
            c.drawString(52 + i * cw, y, h)
            c.setFont("Helvetica", 9.5)
            c.drawString(52 + i * cw, y - 18, v)
        y -= 50
    else:   # classic, minimal, service: vendor block left, title + meta right
        title = {"classic": "INVOICE", "minimal": "Invoice", "service": "Invoice for services"}[layout]
        c.setFont("Helvetica-Bold" if layout != "minimal" else "Helvetica", 16 if layout != "minimal" else 13)
        c.drawString(48, H - 60, f["vendor_name"])
        c.setFont("Helvetica", 9)
        lines = [x["v_street"], x["v_city"], x["phone"]] if layout != "minimal" else [f"{x['v_street']}, {x['v_city']}"]
        for i, line in enumerate(lines):
            c.drawString(48, H - 76 - 12 * i, line)
        c.setFont("Helvetica-Bold", 22 if layout == "classic" else 14)
        c.setFillColor(accent if layout == "classic" else black)
        c.drawRightString(W - 48, H - 60, title)
        c.setFillColor(black)
        meta = [("Invoice number" if layout == "minimal" else "Invoice #" if layout == "classic" else "Invoice No.",
                 f["invoice_number"]), ("Date" if layout != "service" else "Issued", printed["invoice_date"])]
        if layout == "minimal" or f["due_date"]:
            meta.append(("Due" if layout != "service" else "Payment due", printed.get("due_date", "Upon receipt")))
        if f["po_number"]:
            meta.append(("PO number", f["po_number"]))
        for i, (k, v) in enumerate(meta):
            c.setFont("Helvetica", 9)
            c.drawRightString(W - 150, H - 86 - 14 * i, k + ":")
            c.setFont("Helvetica-Bold" if layout == "classic" else "Helvetica", 9.5)
            c.drawRightString(W - 48, H - 86 - 14 * i, v)
        y = H - 160
        c.setFont("Helvetica-Bold", 9)
        c.drawString(48, y, "Bill to:" if layout != "service" else "Client:")
        c.setFont("Helvetica", 10)
        for i, line in enumerate([f["bill_to_name"], x["c_street"], x["c_city"]]):
            c.drawString(48, y - 15 - 13 * i, line)
        y -= 75

    # ---- line items
    hourly = x["hourly"]
    cols = ([("Description", 48, "l"), ("Hours", 360, "r"), ("Rate", 440, "r"), ("Amount", W - 48, "r")] if hourly
            else [("Item", 48, "l"), ("Qty", 360, "r"), ("Unit price", 450, "r"), ("Amount", W - 48, "r")])

    def head(y):
        if layout in ("classic", "remit", "service"):
            c.setFillColor(accent if layout == "classic" else Color(0.9, 0.9, 0.9))
            c.rect(44, y - 5, W - 88, 17, stroke=0, fill=1)
            c.setFillColor(white if layout == "classic" else black)
        c.setFont("Helvetica-Bold", 9)
        for name, cx, al in cols:
            (c.drawString if al == "l" else c.drawRightString)(cx, y, name)
        c.setFillColor(black)
        if layout == "minimal":
            c.line(48, y - 5, W - 48, y - 5)
        return y - 20

    y = head(y)
    for n, it in enumerate(f["line_items"]):
        if y < 120:                                   # continue on the next page
            c.setFont("Helvetica-Oblique", 8.5)
            c.drawRightString(W - 48, y - 4, "Continued on next page")
            footer()
            c.showPage()
            c.setFont("Helvetica-Bold", 11)
            c.drawString(48, H - 56, f"{f['vendor_name']}  -  Invoice {f['invoice_number']} (continued)")
            y = head(H - 90)
        if layout == "band" and n % 2:
            c.setFillColor(Color(0.95, 0.95, 0.97))
            c.rect(44, y - 4, W - 88, 15, stroke=0, fill=1)
            c.setFillColor(black)
        c.setFont("Helvetica", 9.5)
        qty = f"{it['quantity']:g}"
        c.drawString(48, y, it["description"])
        c.drawRightString(cols[1][1], y, qty)
        c.drawRightString(cols[2][1], y, money(it["unit_price"], sign=layout != "minimal"))
        c.drawRightString(W - 48, y, money(it["amount"], sign=layout != "minimal"))
        if layout in ("classic", "remit"):
            c.setStrokeColor(Color(0.85, 0.85, 0.85))
            c.line(44, y - 5, W - 44, y - 5)
            c.setStrokeColor(black)
        y -= 17

    # ---- totals
    y -= 10
    rows = [("Subtotal", f["subtotal"])]
    if f["discount"]:
        rows.append((f"Discount", -f["discount"]))
    if f["tax"]:
        rows.append((f"Sales tax ({x['rate'] * 100:g}%)", f["tax"]))
    if f["shipping"]:
        rows.append(("Shipping", f["shipping"]))
    total_label = {"classic": "TOTAL", "band": "Total due", "minimal": "Amount due", "remit": "BALANCE DUE",
                   "service": "Total"}[layout]
    rows.append((total_label, f["total"]))
    for k, v in rows:
        if y < 70:
            footer()
            c.showPage()
            y = H - 80
        last = k == total_label
        c.setFont("Helvetica-Bold" if last else "Helvetica", 11 if last else 9.5)
        c.drawRightString(450, y, k)
        txt = ("-" + money(-v)) if v < 0 else money(v)
        c.drawRightString(W - 48, y, txt)
        y -= 17 if not last else 22
    printed["subtotal"] = money(f["subtotal"])
    for k in ("tax", "shipping"):
        if f[k]:
            printed[k] = money(f[k])
    if f["discount"]:
        printed["discount"] = money(f["discount"])
    c.setFont("Helvetica", 8.5)
    c.drawString(48, max(y - 10, 60), f"Terms: {f['payment_terms']}. Questions? {x['email']}")
    printed["payment_terms"] = f["payment_terms"]
    footer()
    c.showPage()
    c.save()
    printed_list = list(printed.values()) + [i["description"] for i in f["line_items"]] + \
        [money(i["amount"], sign=layout != "minimal") for i in f["line_items"]]
    return buf.getvalue(), printed_list


# ------------------------------------------------------------------------------------------- receipts

RECEIPT_STYLES = ["grocery", "restaurant", "fuel", "office", "parking"]
MERCHANTS = {"grocery": ["FRESHWAY MARKET #214", "GREEN BASKET FOODS", "VALLEY HARVEST GROCERY"],
             "restaurant": ["The Copper Spoon", "Luna Taqueria", "Harbor Grill & Oyster Bar"],
             "fuel": ["QUIKFUEL #0882", "SUNRISE GAS & GO"],
             "office": ["PAPERWELL OFFICE SUPPLY", "DESKPOINT STORE 117"],
             "parking": ["CITYPARK GARAGE 4", "METRO CAB CO."]}
GROCERY = [("BANANAS", 0.59, 2.9), ("WHOLE MILK GAL", 3.49, 4.99), ("SOURDOUGH LOAF", 4.25, 6.5),
           ("CHEDDAR 8OZ", 3.99, 5.49), ("COFFEE BEANS 12OZ", 8.99, 13.99), ("PAPER TOWELS 6PK", 7.99, 11.49),
           ("EGGS LG DOZEN", 2.99, 4.79), ("SPINACH 5OZ", 2.99, 4.29), ("CHICKEN BREAST", 6.5, 12.8),
           ("SPARKLING WATER 8PK", 4.49, 6.99), ("DISH SOAP", 2.79, 4.49), ("APPLES GALA 3LB", 3.99, 5.99)]
MENU = [("Fish Tacos", 13, 17), ("Caesar Salad", 9, 13), ("Burger & Fries", 14, 19), ("Iced Tea", 3, 4),
        ("Craft Lager", 6, 8.5), ("Clam Chowder", 7, 10), ("Grilled Salmon", 22, 29), ("Churros", 6, 8)]
SUPPLIES = [("TONER CART 26A", 79.99, 119.99), ("COPY PAPER CASE", 44.99, 59.99), ("BINDER 2IN 4PK", 12.99, 18.99),
            ("USB-C CABLE 6FT", 9.99, 19.99), ("GEL PENS 12PK", 8.49, 14.99), ("LABEL MAKER TAPE", 15.99, 24.99)]
CARDS = [("visa", "VISA"), ("mastercard", "MASTERCARD"), ("amex", "AMEX"), ("discover", "DISCOVER"), ("debit", "DEBIT")]


def receipt(rng, style, tricks):
    """Returns (fields, lines, printed) - lines are monospace text, 40 columns."""
    merchant = rng.choice(MERCHANTS[style])
    street, city = address(rng)
    when = rand_date(rng)
    hh, mm = rng.randint(7, 21), rng.randint(0, 59)
    W = 40

    def lr(left, right):
        return left[: W - len(right) - 1].ljust(W - len(right)) + right

    items, lines = [], [merchant.center(W), street.center(W), city.center(W),
                        f"({rng.randint(200, 989)}) 555-01{rng.randint(10, 99)}".center(W), ""]
    fmt = rng.choice(["us", "us_time12", "caps"])
    date_str = {"us": f"{when:%m/%d/%Y} {hh:02d}:{mm:02d}",
                "us_time12": f"{when:%m/%d/%y} {(hh - 1) % 12 + 1}:{mm:02d} {'PM' if hh >= 12 else 'AM'}",
                "caps": f"{when:%b} {when.day:02d} {when.year}  {hh:02d}:{mm:02d}".upper()}[fmt]
    lines += [date_str, ""]
    tax_included = "tax_included" in tricks
    tip = None
    if style == "grocery":
        for _ in range(rng.randint(5, 11)):
            name, lo, hi = rng.choice(GROCERY)
            amt = round(rng.uniform(lo, hi), 2)
            items.append({"description": name, "quantity": None, "amount": amt})
            lines.append(lr(name, f"{amt:.2f} F"))
        category = "groceries"
        rate = 0.02
    elif style == "restaurant":
        lines += [f"Server: {rng.choice(FIRST)}   Table {rng.randint(1, 30)}", ""]
        for _ in range(rng.randint(2, 6)):
            name, lo, hi = rng.choice(MENU)
            q = rng.choice([1, 1, 2])
            amt = round(q * round(rng.uniform(lo, hi), 2), 2)
            items.append({"description": name, "quantity": q, "amount": amt})
            lines.append(lr(f"{q} {name}", f"{amt:.2f}"))
        category, rate = "meals", 0.08
    elif style == "fuel":
        gal = round(rng.uniform(6, 16), 3)
        ppg = round(rng.uniform(2.89, 3.99), 3)
        amt = round(gal * ppg, 2)
        lines += [f"PUMP # {rng.randint(1, 12)}", lr("REGULAR UNLEADED", ""), lr(f"  {gal:.3f} GAL @ {ppg:.3f}/GAL", f"{amt:.2f}")]
        items.append({"description": "REGULAR UNLEADED", "quantity": gal, "amount": amt})
        category, rate, tax_included = "fuel", 0.0, True
    elif style == "office":
        for _ in range(rng.randint(2, 5)):
            name, lo, hi = rng.choice(SUPPLIES)
            q = rng.choice([1, 1, 2, 3])
            amt = round(q * round(rng.uniform(lo, hi), 2), 2)
            items.append({"description": name, "quantity": q, "amount": amt})
            lines.append(lr(f"{name}", ""))
            lines.append(lr(f"  {q} @ {amt / q:.2f}", f"{amt:.2f}"))
        category, rate = "office_supplies", 0.07
    else:  # parking / taxi
        if merchant.startswith("CITYPARK"):
            hrs = rng.randint(2, 9)
            amt = float(min(hrs * 6, 38))
            lines += [f"ENTRY {hh - 1 if hh > 7 else hh}:{mm:02d}  EXIT {hh:02d}:{mm:02d}", lr(f"PARKING {hrs} HR", f"{amt:.2f}")]
            items.append({"description": f"PARKING {hrs} HR", "quantity": None, "amount": amt})
        else:
            amt = round(rng.uniform(14, 48), 2)
            lines += [f"CAB #{rng.randint(100, 999)}  DRIVER {rng.randint(1000, 9999)}", lr("FARE", f"{amt:.2f}")]
            items.append({"description": "FARE", "quantity": None, "amount": amt})
        category, rate, tax_included = "travel", 0.0, True

    subtotal = round(sum(i["amount"] for i in items), 2)
    lines.append("-" * W)
    if tax_included:
        if style == "fuel":
            tax = None
            lines.append("PRICE INCLUDES ALL TAXES".center(W))
        else:
            tax = round(subtotal - subtotal / 1.06, 2)
            lines.append(lr("INCL. SALES TAX", f"{tax:.2f}"))
        total, sub_field = subtotal, None
    else:
        tax = round(subtotal * rate, 2)
        lines.append(lr("SUBTOTAL", f"{subtotal:.2f}"))
        lines.append(lr(f"TAX {rate * 100:g}%", f"{tax:.2f}"))
        total, sub_field = round(subtotal + tax, 2), subtotal
    if style in ("restaurant",) or (style == "parking" and merchant.startswith("METRO")):
        tip = round(total * rng.choice([0.15, 0.18, 0.2]), 2)
        lines.append(lr("TIP", f"{tip:.2f}"))
        total = round(total + tip, 2)
    lines.append(lr("TOTAL", f"${total:.2f}"))
    lines.append("")
    if "cash" in tricks:
        paid = float(math.ceil(total / 5) * 5) + (5 if rng.random() < 0.5 else 0)
        lines += [lr("CASH", f"{paid:.2f}"), lr("CHANGE", f"{paid - total:.2f}")]
        method, last4 = "cash", None
    else:
        method, label = rng.choice(CARDS)
        last4 = f"{rng.randint(0, 9999):04d}"
        lines += [lr(f"{label} ************{last4}", f"{total:.2f}"), f"AUTH CODE {rng.randint(100000, 999999)}"]
    lines += ["", "THANK YOU - PLEASE COME AGAIN".center(W), "", "SAMPLE TEST RECEIPT".center(W),
              "not a real transaction".center(W)]
    fields = {"merchant_name": merchant, "merchant_address": f"{street}, {city}", "date": when.isoformat(),
              "time": f"{hh:02d}:{mm:02d}", "items": items, "subtotal": sub_field, "tax": tax,
              "tax_included": tax_included, "tip": tip, "total": total, "payment_method": method,
              "card_last4": last4, "category": category}
    printed = [merchant, date_str.split()[0], f"{total:.2f}"] + [i["description"] for i in items] + \
              [f"{i['amount']:.2f}" for i in items] + ([f"{tip:.2f}"] if tip else []) + \
              ([f"{tax:.2f}"] if tax else []) + ([last4] if last4 else [])
    return fields, lines, printed


def draw_receipt(lines):
    buf = io.BytesIO()
    W, lh = 80 * 72 / 25.4, 10.5
    H = 30 + lh * len(lines)
    c = Canvas(buf, pagesize=(W, H))
    c.setFont("Courier", 8.6)
    for i, line in enumerate(lines):
        c.drawString(10, H - 20 - lh * i, line)
    c.showPage()
    c.save()
    return buf.getvalue()


# ------------------------------------------------------------------------------------------- W-9 forms

W9_BOXES = [("individual_sole_proprietor", "Individual/sole proprietor"), ("c_corporation", "C corporation"),
            ("s_corporation", "S corporation"), ("partnership", "Partnership"), ("trust_estate", "Trust/estate"),
            ("llc", "LLC. Enter the tax classification (C, S, P)"), ("other", "Other (see instructions)")]
BIZ_SUFFIX = {"c_corporation": "Inc.", "s_corporation": "Corp.", "partnership": "Partners", "llc": "LLC",
              "trust_estate": "Family Trust", "other": "Cooperative"}


def w9(rng, tricks):
    name = person(rng)
    cls = rng.choice([c for c, _ in W9_BOXES])
    biz = None
    if cls != "individual_sole_proprietor" or rng.random() < 0.4:
        biz = f"{rng.choice(LAST)} {rng.choice(['Consulting', 'Landscaping', 'Design', 'Electric', 'Bookkeeping', 'Catering'])} {BIZ_SUFFIX.get(cls, 'Services')}".strip()
    llc_code = rng.choice(["C", "S", "P"]) if cls == "llc" else None
    tin_type = "SSN" if cls == "individual_sole_proprietor" else "EIN"
    tin = f"98765432{rng.randint(0, 9)}" if tin_type == "SSN" else f"00{rng.randint(1000000, 9999999)}"
    street, city = address(rng)
    signed = "unsigned" not in tricks
    dsigned = rand_date(rng) if signed else None
    checked = None if "no_box" in tricks else cls
    fields = {"name": name, "business_name": biz, "tax_classification": checked,
              "llc_tax_code": llc_code if checked == "llc" else None, "address": street, "city_state_zip": city,
              "tin_type": tin_type, "tin_last4": tin[-4:], "signed": signed,
              "date_signed": dsigned.isoformat() if dsigned else None}
    return fields, {"tin": tin, "llc_code": llc_code, "date_fmt": rng.choice(["us", "long"])}


def draw_w9(f, x, hand):
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=letter)
    W, H = letter
    L, R = 40, W - 40
    fill_font = ("Courier-Bold", 11) if hand else ("Helvetica", 10.5)
    # watermark
    c.saveState()
    c.setFillColor(Color(0.88, 0.88, 0.88))
    c.setFont("Helvetica-Bold", 70)
    c.translate(W / 2, H / 2)
    c.rotate(35)
    c.drawCentredString(0, 0, "SAMPLE")
    c.restoreState()
    # header
    c.setLineWidth(1.2)
    c.line(L, H - 40, R, H - 40)
    c.setFont("Helvetica", 8)
    c.drawString(L, H - 52, "Form")
    c.setFont("Helvetica-Bold", 22)
    c.drawString(L + 24, H - 62, "W-9")
    c.setFont("Helvetica", 7)
    c.drawString(L, H - 76, "(Rev. March 2024)")
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(W / 2, H - 56, "Request for Taxpayer")
    c.drawCentredString(W / 2, H - 70, "Identification Number and Certification")
    c.setFont("Helvetica", 7)
    c.drawRightString(R, H - 56, "Give form to the")
    c.drawRightString(R, H - 66, "requester. Do not")
    c.drawRightString(R, H - 76, "send to the IRS.")
    c.line(L, H - 84, R, H - 84)
    c.setFont("Helvetica-Oblique", 6.5)
    c.drawString(L, H - 94, "Synthetic SAMPLE for software testing. Layout simplified; not an official IRS form.")

    def line_box(y, label, value, h=28):
        c.setLineWidth(0.6)
        c.rect(L, y - h, R - L, h)
        c.setFont("Helvetica", 7.5)
        c.drawString(L + 4, y - 9, label)
        if value:
            c.setFont(*fill_font)
            c.drawString(L + 10, y - 23, value)
        return y - h

    y = line_box(H - 102, "1  Name of entity/individual. An entry is required.", f["name"])
    y = line_box(y, "2  Business name/disregarded entity name, if different from above.", f["business_name"] or "")
    # 3a checkboxes
    h3 = 70
    c.rect(L, y - h3, R - L, h3)
    c.setFont("Helvetica", 7.5)
    c.drawString(L + 4, y - 9, "3a  Check the appropriate box for federal tax classification of the entity/individual "
                               "whose name is entered on line 1. Check only one of the following seven boxes.")
    positions = [(L + 10, y - 26), (L + 150, y - 26), (L + 260, y - 26), (L + 370, y - 26), (L + 460, y - 26),
                 (L + 10, y - 44), (L + 10, y - 62)]
    for (key, label), (bx, by) in zip(W9_BOXES, positions):
        c.rect(bx, by - 2, 9, 9)
        c.setFont("Helvetica", 8)
        c.drawString(bx + 14, by, label)
        if f["tax_classification"] == key:
            c.setFont("Helvetica-Bold", 10)
            c.drawString(bx + 1.2, by - 0.5, "X")
    if f["llc_tax_code"]:
        c.setFont(*fill_font)
        c.drawString(L + 300, y - 44, f["llc_tax_code"])
    c.line(L + 296, y - 46, L + 330, y - 46)
    y -= h3
    y = line_box(y, "5  Address (number, street, and apt. or suite no.). See instructions.", f["address"])
    y = line_box(y, "6  City, state, and ZIP code", f["city_state_zip"])
    # Part I TIN
    y -= 14
    c.setFillColor(black)
    c.rect(L, y - 12, 40, 14, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(L + 4, y - 8, "Part I")
    c.setFillColor(black)
    c.drawString(L + 50, y - 8, "Taxpayer Identification Number (TIN)")
    y -= 24
    c.setFont("Helvetica", 7.5)
    c.drawString(L, y - 6, "Enter your TIN in the appropriate box. For individuals, this is generally your social")
    c.drawString(L, y - 16, "security number (SSN). For other entities, it is your employer identification number (EIN).")

    def digit_boxes(x0, y0, groups, digits):
        k = 0
        xx = x0
        for gi, g in enumerate(groups):
            for _ in range(g):
                c.rect(xx, y0, 16, 18)
                if digits:
                    c.setFont(*fill_font)
                    c.drawCentredString(xx + 8, y0 + 5, digits[k])
                k += 1
                xx += 16
            if gi < len(groups) - 1:
                c.setFont("Helvetica-Bold", 10)
                c.drawCentredString(xx + 6, y0 + 5, "-")
                xx += 12

    c.setFont("Helvetica-Bold", 8)
    c.drawString(R - 210, y - 4, "Social security number")
    digit_boxes(R - 210, y - 28, [3, 2, 4], x["tin"] if f["tin_type"] == "SSN" else None)
    c.drawString(R - 210, y - 44, "or")
    c.drawString(R - 210, y - 58, "Employer identification number")
    digit_boxes(R - 210, y - 82, [2, 7], x["tin"] if f["tin_type"] == "EIN" else None)
    y -= 100
    # Part II
    c.setFillColor(black)
    c.rect(L, y - 12, 40, 14, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(L + 4, y - 8, "Part II")
    c.setFillColor(black)
    c.drawString(L + 50, y - 8, "Certification")
    c.setFont("Helvetica", 7.5)
    text = ["Under penalties of perjury, I certify that:",
            "1. The number shown on this form is my correct taxpayer identification number; and",
            "2. I am not subject to backup withholding; and",
            "3. I am a U.S. citizen or other U.S. person; and",
            "4. The FATCA code(s) entered on this form (if any) indicating that I am exempt from FATCA reporting is correct."]
    for i, t in enumerate(text):
        c.drawString(L, y - 28 - 11 * i, t)
    y -= 100
    c.setLineWidth(1.2)
    c.line(L, y, R, y)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(L, y - 22, "Sign")
    c.drawString(L, y - 34, "Here")
    c.setFont("Helvetica", 7.5)
    c.drawString(L + 50, y - 14, "Signature of U.S. person")
    c.drawString(R - 170, y - 14, "Date")
    c.setLineWidth(0.6)
    c.line(L + 50, y - 40, R - 190, y - 40)
    c.line(R - 170, y - 40, R, y - 40)
    date_str = None
    if f["signed"]:
        c.setFont("Times-Italic", 20)
        c.drawString(L + 70, y - 36, f["name"])
        date_str = DATE_FORMATS[x["date_fmt"]](date.fromisoformat(f["date_signed"]))
        c.setFont(*fill_font)
        c.drawString(R - 160, y - 34, date_str)
    c.setFont("Helvetica", 7)
    c.setFillColor(Color(0.45, 0.45, 0.45))
    c.drawCentredString(W / 2, 24, SAMPLE_NOTE)
    c.showPage()
    c.save()
    printed = [f["name"], f["address"], f["city_state_zip"], f["tin_last4"]] + \
              ([f["business_name"]] if f["business_name"] else []) + ([date_str] if date_str else [])
    return buf.getvalue(), printed


# ------------------------------------------------------------------------------------------- scans and photos

def render(pdf_bytes, dpi):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(pdf_bytes)
    return [pdf[i].render(scale=dpi / 72).to_pil().convert("RGB") for i in range(len(pdf))]


def scan(img, rng):
    """A flatbed scan: grey, a little tilted, noisy, slightly soft, 200 dpi."""
    g = img.convert("L")
    g = g.rotate(rng.uniform(-2.2, 2.2), resample=Image.BICUBIC, expand=True, fillcolor=255)
    a = np.asarray(g).astype(np.float32)
    a = a * rng.uniform(0.85, 0.97) + rng.uniform(5, 25)                     # duller contrast, grey paper
    a += np.random.default_rng(rng.randint(0, 10**6)).normal(0, rng.uniform(4, 9), a.shape)
    specks = np.random.default_rng(rng.randint(0, 10**6)).random(a.shape) < 0.0004
    a[specks] = 30
    g = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.7)))
    return g


def photo(img, rng, receipt=False):
    """A phone photo: on a desk, at an angle, uneven light, a bit blurry, JPEG."""
    import cv2
    src = np.asarray(img.convert("RGB"))
    h, w = src.shape[:2]
    pad = int(max(h, w) * (0.12 if receipt else 0.1))
    bg_color = rng.choice([(120, 95, 70), (150, 150, 145), (90, 100, 115), (175, 160, 140)])
    canvas = np.zeros((h + 2 * pad, w + 2 * pad, 3), np.uint8)
    canvas[:] = bg_color
    noise = np.random.default_rng(rng.randint(0, 10**6)).normal(0, 9, canvas.shape)
    canvas = np.clip(canvas + noise, 0, 255).astype(np.uint8)
    canvas[pad:pad + h, pad:pad + w] = src
    H2, W2 = canvas.shape[:2]
    j = lambda s: rng.uniform(-s, s) * min(H2, W2)
    corners = np.float32([[pad, pad], [pad + w, pad], [pad + w, pad + h], [pad, pad + h]])
    moved = np.float32([[pad + j(0.035), pad + j(0.035)], [pad + w + j(0.035), pad + j(0.035)],
                        [pad + w + j(0.035), pad + h + j(0.035)], [pad + j(0.035), pad + h + j(0.035)]])
    M = cv2.getPerspectiveTransform(corners, moved)
    warped = cv2.warpPerspective(canvas, M, (W2, H2), borderValue=bg_color)
    # rotate the whole photo a little
    R = cv2.getRotationMatrix2D((W2 / 2, H2 / 2), rng.uniform(-4, 4), 1.0)
    warped = cv2.warpAffine(warped, R, (W2, H2), borderValue=bg_color)
    # uneven light: brighter on one side, a soft shadow on the other
    gx = np.linspace(rng.uniform(0.72, 0.85), rng.uniform(1.0, 1.08), W2)[None, :, None]
    gy = np.linspace(rng.uniform(0.9, 1.05), rng.uniform(0.8, 0.95), H2)[:, None, None]
    lit = np.clip(warped.astype(np.float32) * gx * gy, 0, 255).astype(np.uint8)
    lit = cv2.GaussianBlur(lit, (0, 0), rng.uniform(0.8, 1.4))
    # a phone photo of a page is ~1600-2400 px on the long side
    scale = rng.uniform(1900, 2400) / max(lit.shape[:2])
    lit = cv2.resize(lit, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return Image.fromarray(lit)


# ------------------------------------------------------------------------------------------- the 60 documents

def plan(rng):
    """(id, type, layout, condition, tricks) for every document."""
    out = []
    conds = {"invoice": ["clean"] * 13 + ["scan"] * 4 + ["photo"] * 3,
             "receipt": ["clean"] * 10 + ["scan"] * 4 + ["photo"] * 6,
             "w9": ["clean"] * 13 + ["scan"] * 5 + ["photo"] * 2}
    for c in conds.values():
        rng.shuffle(c)
    inv_tricks = {2: ["discount"], 5: ["multi_page"], 8: ["due_on_receipt"], 11: ["no_tax"], 14: ["discount"],
                  17: ["due_on_receipt", "no_tax"], 19: ["multi_page"]}
    for i, tr in inv_tricks.items():          # a photo is one page: multi-page invoices are clean or scanned
        if "multi_page" in tr and conds["invoice"][i] == "photo":
            j = next(k for k, c in enumerate(conds["invoice"]) if c == "clean" and k not in inv_tricks)
            conds["invoice"][i], conds["invoice"][j] = "clean", "photo"
    for i in range(20):
        layout = INVOICE_LAYOUTS[i % 5]
        tricks = inv_tricks.get(i, [])
        if "multi_page" in tricks:
            layout = "classic"
        out.append((f"inv-{i + 1:02d}", "invoice", layout, conds["invoice"][i], tricks))
    rc_tricks = {3: ["cash"], 7: ["tax_included"], 12: ["cash"], 16: ["tax_included"]}
    for i in range(20):
        out.append((f"rcp-{i + 1:02d}", "receipt", RECEIPT_STYLES[i % 5], conds["receipt"][i], rc_tricks.get(i, [])))
    w9_tricks = {4: ["unsigned"], 9: ["no_box"], 15: ["unsigned"], 18: ["handwritten_font"]}
    for i in range(20):
        t = w9_tricks.get(i, [])
        out.append((f"w9-{i + 1:02d}", "w9", "handwritten_font" if "handwritten_font" in t or i % 3 == 0 else "typed",
                    conds["w9"][i], t))
    return out


def main():
    from docsift.schemas import MODELS
    rng = random.Random(SEED)
    DOCS.mkdir(parents=True, exist_ok=True)
    ANSWERS.mkdir(parents=True, exist_ok=True)
    for p in list(DOCS.iterdir()) + list(ANSWERS.iterdir()):
        p.unlink()
    rows = []
    for doc_id, kind, layout, cond, tricks in plan(rng):
        if kind == "invoice":
            fields, extra = invoice_data(rng, doc_id, layout, tricks)
            pdf, printed = draw_invoice(fields, extra, layout, rng.choice(list(DATE_FORMATS)))
        elif kind == "receipt":
            fields, lines, printed = receipt(rng, layout, tricks)
            pdf = draw_receipt(lines)
        else:
            fields, extra = w9(rng, tricks)
            pdf, printed = draw_w9(fields, extra, hand=layout == "handwritten_font")
        MODELS[kind].model_validate(fields)                  # the answer must fit the schema
        if cond == "clean":
            path = DOCS / f"{doc_id}.pdf"
            path.write_bytes(pdf)
        elif cond == "scan":
            pages = [scan(p, rng) for p in render(pdf, 200)]
            path = DOCS / f"{doc_id}.pdf"
            pages[0].save(path, "PDF", resolution=200, save_all=True, append_images=pages[1:])
        else:
            pages = render(pdf, 220 if kind == "receipt" else 170)
            path = DOCS / f"{doc_id}.jpg"
            photo(pages[0], rng, receipt=kind == "receipt").save(path, "JPEG", quality=rng.randint(68, 82))
            if len(pages) > 1:          # a photo is one page; keep the multi-page invoices as scans instead
                raise ValueError(f"{doc_id}: multi-page document can't be a photo")
        answer = {"id": doc_id, "type": kind, "file": path.name, "fields": fields, "printed": printed,
                  "meta": {"layout": layout, "condition": cond, "tricks": tricks,
                           **({"full_tin": extra["tin"]} if kind == "w9" else {})}}
        (ANSWERS / f"{doc_id}.json").write_text(json.dumps(answer, indent=2) + "\n", encoding="utf-8")
        rows.append(f"{doc_id},{kind},{layout},{cond},{'+'.join(tricks)},{path.name}")
    (ROOT / "index.csv").write_text("id,type,layout,condition,tricks,file\n" + "\n".join(rows) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} documents to {DOCS}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT.parent))
    main()
