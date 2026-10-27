# DocSift: AI document extraction

Reads invoices, receipts and W-9 forms (text PDFs, scans or phone photos), extracts their fields into checked,
structured data, and sends anything doubtful to a person for review instead of guessing.

> Work in progress. Day 1: test set and reader. Day 2: classify, extract, validate, review decision.
> Day 3: the web app (upload, review, approve), storage, Google Sheet and review emails via n8n.
> Next: the live demo.

```
document ─▶ read (PDF text or OCR) ─▶ classify (rules, AI if unsure) ─▶ extract (AI → schema)
         ─▶ validate (math, dates, every value looked up on the page) ─▶ approve, or send to a person
```

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

## Day 2: extraction and the review decision

- **Classify:** rules decide clear cases instantly ("Request for Taxpayer Identification Number" → W-9). All 60
  test documents are classified by rules; the AI is only asked when the rules aren't sure.
- **Extract:** Groq's `gpt-oss-120b` fills the type's schema (structured JSON output). Its answer is tidied
  ("$1,234.50" → 1234.5, "03/05/2026" → 2026-03-05, "1:44 PM" → 13:44), checked by Pydantic, and retried once
  with the errors if it doesn't fit. Tax IDs and card numbers are cut to the last 4 digits whatever comes back.
- **Validate:** the numbers must agree (items → subtotal → total, quantity × price), dates must be real and in
  order, and **every copied value is looked up on the page**. On a real PDF it must be there exactly; after OCR,
  letters may differ slightly (the AI may fix an OCR slip) but numbers never may.
- **Decide:** anything doubtful goes to review with a plain reason ("'5599' isn't on the page"). An unsigned W-9
  or an unticked tax box is flagged separately: the extraction is right, but someone has to follow up.
- **Privacy:** nothing returned holds a full tax ID or card number, including the text kept for the review page
  (a test checks it; it caught a leak where a clean PDF's digit boxes read "0 0 - 5 3 5 8 4  6 6").

### Calibrating the checks without the AI

Two offline runs measure the checks themselves (`evals/reports/day2-*.md`):

| Run | What it shows | Result |
| --- | --- | --- |
| `--oracle`: a fake AI that returns the correct answers | how often a *correct* document is sent to review | 6 of 60, all values OCR didn't read (card digits on blurry photos) |
| `--chaos`: the correct answers with one typical mistake planted per document (a wrong digit, a swapped day and month, a dropped line item, an invented value) | how many mistakes the checks catch | **97%** (58 of 60); the 2 missed are one-letter name changes on scans, indistinguishable from OCR noise |

Measuring changed the checks: the first version caught 70% of planted mistakes. Requiring exact matches on
real PDFs, exact numbers even after OCR, and treating "terms say Net 30 but no due date" as a doubt took it to
97%. Two false alarms turned out to be bugs (a "Shipping labels" item read as a shipping line; receipt times
printed as "5:21 PM" not found as "17:21").

### With the real AI (gpt-oss-120b on Groq)

First run, 60 documents:

| | Fields correct | Line items F1 | Auto-approved and fully correct | Mistakes caught |
| --- | --- | --- | --- | --- |
| Text PDFs (36) | 99.8% | 100% | 100% | 100% |
| Scans + photos (24) | 89.5% | 91.2% | 71.4% | 80.6% |
| All | 95.7% | 96.2% | 91.7% (target 98%) | 81.2% |

Four documents were approved with a wrong value, all scans or photos. What each one taught, and the fix:

- **Addresses after OCR could differ without review** (inv-09, w9-14). Now a key field or address that only
  *roughly* matches the page after OCR is sent for a quick check.
- **A very poor photo still counted as readable** (w9-18, OCR confidence 64, wrong business name and tax box).
  Pages under 70 now go to review, and the ticked tax box is **checked on the page**: both PDFs and OCR show the
  tick as an `X` before the option, which the code finds itself (20 of 20 test forms).
- **A misread tax-ID digit was copied faithfully** (w9-19). The digit boxes are now cross-checked: the last 4
  must match the boxes, an SSN/EIN must match which boxes hold the digits, and 11 digits in 9 boxes means a misread.

And two that were caught but handled badly:

- **One missing value threw away the whole answer** (inv-17: OCR lost the company name, the AI returned null, the
  schema rejected everything: 0 of 15 fields). Required fields now accept null and are flagged instead; the other
  14 values survive.
- **A clean W-9's tax ID reached the AI as `0 0 - 5 3 5 8 4  6 6`** and was misread. The reader now joins
  spaced digit boxes into `[digits in boxes] 00 5358466 (2+7 digits: EIN layout)`.

Second run, after those fixes: fields correct 95.7% → **97.7%**, line items 96.2% → **100%**, auto-approved
and fully correct 91.7% → **95.9%**, text PDFs **100%** on every measure. The two documents still approved with a
wrong value were both photos where OCR read "Blvd" as **"Bivd"** and the AI copied it: the value *was* on the
page, so the lookup passed.

Fix: the reader now keeps Tesseract's confidence for each word, and a value built on a word it scored under 80
goes to review ("OCR was unsure of 'Bivd' (confidence 77)"); the AI may also fix obvious misspellings of common
words, which then only roughly match the page and are checked anyway. **The trade-off, measured:** word
confidence is a weak signal. Correct words scored as low as 25 ("Ironside") and misreads as high as 77, so this
also sends some correct scans to a person (with the perfect fake AI: 14 of 60 documents reviewed, all scans or
photos; no text PDF). For a client, a 10-second check beats a wrong amount or address in their books.

Third run, final for Day 2 ([report](evals/reports/day2-extraction-gpt-oss-120b.md)):

| | Fields correct | Line items F1 | Sent to review | Auto-approved and fully correct | Mistakes caught |
| --- | --- | --- | --- | --- | --- |
| Text PDFs (36) | 100% | 100% | 2.8% (1: a W-9 with no box ticked) | 100% | – (none made) |
| Scans + photos (24) | 94.2% | 100% | 70.8% | 100% | 100% |
| **All (60)** | **97.7%** | **100%** | 30.0% | **100%** (target 98%) | **100%** (target 80%) |

Nothing wrong was approved. The price is the review rate on scans and photos: 4 of the 17 reviewed were correct
("Ironside" read right but scored 6/100 by Tesseract). The review page (Day 3) is where those take seconds.

## Day 3: the app

```
uvicorn docsift.app:app --reload     # then open http://127.0.0.1:8000
```

- **Upload or pick a sample.** PDF, JPG or PNG up to 5 MB and 3 pages; ten one-click samples from the test set.
- **Review page:** the document on the left, its fields on the right. Each field has a dot (green: found on the
  page; amber: roughly; red: doubtful) and the reason under it. Line items are an editable table. "Check again"
  re-runs every check on your edits; **Approve** asks for a second click ("Approve anyway") if something still
  looks doubtful, so nothing doubtful is approved by accident.
- **Duplicates:** the same vendor + invoice number (or merchant + date + total) seen before is flagged.
- **Storage:** Supabase Postgres (its own `docsift` schema) when `DATABASE_URL` is set, in memory otherwise.
  Original files are deleted after 24 hours; the fields stay. Stored text is masked (no full tax IDs).
- **n8n:** an approved document becomes a row in the Google Sheet tab for its type (Invoices / Receipts / W-9s;
  the app builds the row, n8n appends it); one that needs a person becomes an email with a link to its review
  page. Setup: [automation/README.md](automation/README.md).
- **Public-demo limits:** uploads per visitor per hour and per day, plain-English errors ("The AI service isn't
  available right now"), never a stack trace.

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

Copy `.env.example` to `.env` and add your Groq key (`GROQ_API_KEY`).

```powershell
python -m pytest -q                              # tests (no AI calls)
python -m docsift.pipeline samples\docs\inv-16.jpg   # one document, end to end
python -m evals.extraction                       # accuracy on all 60 (about 60 AI calls)
uvicorn docsift.app:app --reload                 # the app, at http://127.0.0.1:8000
```

## Layout

```
docsift/schemas.py     the fields for each document type (Pydantic)
docsift/reader.py      PDF text layer or OCR: find the page, even the light, straighten, denoise, read
docsift/classify.py    which type: rules first, the AI if unsure
docsift/extract.py     the AI fills the schema; tidying, one repair retry, last-4 masking
docsift/validate.py    math, dates, "is it on the page?", confidence per field, review reasons
docsift/pipeline.py    read -> classify -> extract -> validate; masks tax IDs in everything returned
docsift/llm.py         Groq client: structured JSON output, fallback model, plain-English errors
docsift/app.py         FastAPI: upload, samples, review, check, approve; limits
docsift/store.py       Postgres (Supabase) or in-memory storage; duplicates; originals deleted after 24 h
docsift/notify.py      events to n8n: the Sheet row per type, review emails
web/                   the page: upload, samples, review (no framework, no build step)
automation/            n8n workflow (JSON + generator), Sheet headers, setup guide
samples/generate.py    makes the 60 test documents and their answers
evals/reading.py       the reading check; reports in evals/reports/
evals/extraction.py    the accuracy check (--oracle / --chaos run without the AI)
tests/                 test-set and reader tests
```
