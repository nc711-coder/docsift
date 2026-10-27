# DocSift: AI document extraction

Reads invoices, receipts and W-9 forms (text PDFs, scans or phone photos), extracts their fields into checked,
structured data, and sends anything doubtful to a person for review instead of guessing.

> Work in progress. Day 1: the test set and the reader are done. Next: classify + extract + validate,
> then a review page + Google Sheet, then the live demo.

## Day 1 results: reading

Before any AI is involved, the text has to come off the page. On the 60 test documents, **98% of the key values
come back as text** (amounts, dates, names, invoice numbers, as printed):

| Documents | Values found | Notes |
| --- | --- | --- |
| Text PDFs (36) | 100% | read from the PDF's own text, instantly |
| Scans (13) | 96% | rendered at 300 dpi, denoised, straightened, OCR |
| Phone photos (11) | 94% | page found in the photo and flattened first |

Full table by type: [evals/reports/day1-reading.md](evals/reports/day1-reading.md).

What measuring changed:
- **Tesseract's page mode.** Reading the page as one block (`--psm 6`) instead of the default found 82% of values
  on the hardest documents instead of 60%. Typed answers inside a form's boxes were skipped entirely before.
- **Denoise before enlarging.** Scanner grain turned into stray "letters" once the image was enlarged for OCR.
  Removing it first took scanned receipts from 70% to 91% of values read (OCR confidence 66 → 92).
- **A reader for digits in boxes.** A W-9's tax ID is one digit per box, and page OCR reads the box walls as
  letters. The reader finds rows of small equal boxes, cuts out their insides and reads them as digits only:
  `[digits in boxes] 987 65 4321`.
- **Tried and dropped:** erasing all ruled lines (helped forms, wrecked receipts: Courier's tall strokes look like
  lines); a faster denoise setting (20% faster, 7 points worse on scanned forms).

## The test set

`samples/` holds 60 synthetic documents with known answers: 20 invoices (5 layouts, multi-page, discounts, no tax,
"due on receipt"), 20 receipts (grocery, restaurant with tip, fuel, office supplies, parking/taxi; cash and card;
tax included), 20 W-9 forms (all 7 tax classes, SSN or EIN, unsigned, no box checked). About a third are scans
(tilted, grainy) or phone photos (on a desk, at an angle, uneven light).

Everything is invented and every page says SAMPLE. W-9 tax IDs use numbers that are never issued (SSNs
987-65-4320 to 4329 are reserved for examples; EINs never start with 00).

```
python samples/generate.py        # rebuild the documents (same output every time)
python -m evals.reading           # how much of each document the reader gets back
python -m evals.reading --show w9-14     # what was read from one document
```

Each `samples/answers/<id>.json` has the correct extraction (`fields`, in the shape of `docsift/schemas.py`),
the key values as printed (`printed`), and how the document was made (`meta`).

## Setup (Windows)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Scans and photos need **Tesseract**: install it from https://github.com/UB-Mannheim/tesseract/wiki (default
folder). The reader finds it there; elsewhere, set `TESSERACT_CMD` to the full path of `tesseract.exe`.

```powershell
python -m pytest -q               # tests
```

## Layout

```
docsift/schemas.py     the fields for each document type (Pydantic)
docsift/reader.py      PDF text layer or OCR: find the page, even the light, straighten, denoise, read
samples/generate.py    makes the 60 test documents and their answers
evals/reading.py       the reading check; reports in evals/reports/
tests/                 test-set and reader tests
```
