"""The reader and the test documents. OCR tests are skipped when Tesseract isn't installed."""
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from docsift.reader import _tidy, read
from docsift.schemas import MODELS

ROOT = Path(__file__).resolve().parent.parent
ANSWERS = sorted((ROOT / "samples" / "answers").glob("*.json"))
needs_ocr = pytest.mark.skipif(not shutil.which("tesseract") and not Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe").exists(),
                               reason="Tesseract not installed")


def load(doc_id):
    return json.loads((ROOT / "samples" / "answers" / f"{doc_id}.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------------------- the test set

def test_sixty_documents_twenty_per_type():
    kinds = [json.loads(p.read_text(encoding="utf-8"))["type"] for p in ANSWERS]
    assert len(kinds) == 60 and all(kinds.count(k) == 20 for k in ("invoice", "receipt", "w9"))


def test_every_answer_fits_its_schema_and_its_file_exists():
    for p in ANSWERS:
        a = json.loads(p.read_text(encoding="utf-8"))
        MODELS[a["type"]].model_validate(a["fields"])
        assert (ROOT / "samples" / "docs" / a["file"]).exists() and a["printed"]


def test_about_a_third_are_scans_or_photos():
    conds = [json.loads(p.read_text(encoding="utf-8"))["meta"]["condition"] for p in ANSWERS]
    assert 18 <= conds.count("scan") + conds.count("photo") <= 26 and conds.count("photo") >= 8


def test_invoice_totals_add_up():
    for p in ANSWERS:
        a = json.loads(p.read_text(encoding="utf-8"))
        if a["type"] != "invoice":
            continue
        f = a["fields"]
        assert round(sum(i["amount"] for i in f["line_items"]), 2) == f["subtotal"]
        assert round(f["subtotal"] - (f["discount"] or 0) + (f["tax"] or 0) + (f["shipping"] or 0), 2) == f["total"]


def test_w9_tax_ids_are_never_real():
    """SSNs 987-65-4320..4329 are reserved for examples; no EIN starts with 00."""
    for p in ANSWERS:
        a = json.loads(p.read_text(encoding="utf-8"))
        if a["type"] == "w9":
            tin = a["meta"]["full_tin"]
            assert tin.startswith("98765432") if a["fields"]["tin_type"] == "SSN" else tin.startswith("00")
            assert tin[-4:] == a["fields"]["tin_last4"]


# ------------------------------------------------------------------------------------------- reading

def test_text_pdf_is_read_from_its_text_layer():
    a = load("inv-01")
    r = read(ROOT / "samples" / "docs" / a["file"])
    assert r.method == "text" and r.confidence == 100 and r.readable
    assert all(s.replace(" ", "") in r.text.replace(" ", "") for s in a["printed"])


def test_multi_page_invoice_keeps_page_markers():
    r = read(ROOT / "samples" / "docs" / "inv-06.pdf")
    assert len(r.pages) >= 2 and "--- page 2 ---" in r.text


def test_bytes_and_unsupported_types():
    data = (ROOT / "samples" / "docs" / "inv-01.pdf").read_bytes()
    assert read(data, "upload.pdf").method == "text"
    with pytest.raises(ValueError, match="Unsupported"):
        read(b"hello", "notes.docx")


def test_layout_text_is_tidied():
    assert _tidy("      Invoice   #1      \n\n\n\n      Total   $5   \n") == "Invoice   #1\n\nTotal   $5"


@needs_ocr
def test_photo_is_found_flattened_and_read():
    a = load("inv-16")
    r = read(ROOT / "samples" / "docs" / a["file"])
    assert r.method == "ocr" and r.readable and r.confidence > 80
    assert "page found in photo and flattened" in r.pages[0].notes
    assert a["fields"]["invoice_number"] in r.text


@needs_ocr
def test_blank_page_is_flagged_unreadable():
    import io
    buf = io.BytesIO()
    Image.new("RGB", (1200, 1600), "white").save(buf, "PNG")
    r = read(buf.getvalue(), "blank.png")
    assert not r.readable and "almost no text" in r.warnings[0]


@needs_ocr
def test_digits_in_boxes_are_read():
    """A W-9 style tax ID, one digit per box: page OCR garbles it, the box reader gets it."""
    from docsift.reader import _boxed_digits
    img = Image.new("L", (2400, 400), 255)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 44)
    except OSError:
        font = ImageFont.load_default(size=44)
    x = 300
    for group in ("987", "65", "4321"):
        for ch in group:
            d.rectangle([x, 150, x + 64, 222], outline=0, width=3)
            d.text((x + 18, 160), ch, fill=0, font=font)
            x += 64
        x += 50
    assert _boxed_digits(np.asarray(img)) == ["987 65 4321"]
