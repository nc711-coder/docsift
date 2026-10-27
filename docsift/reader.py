"""Step 1 of the pipeline: get the text out of a document, and say how much to trust it.

    from docsift.reader import read
    r = read("samples/docs/inv-16.jpg")
    r.text          # all pages, with "--- page 2 ---" between them
    r.method        # "text" (a real PDF), "ocr" (scan or photo) or "mixed"
    r.confidence    # 0-100: 100 for a PDF's own text, Tesseract's word confidence for OCR
    r.readable      # False when the text is too thin or too uncertain to extract from: send it to review

How each kind of file is read:
  PDF with a text layer   the text the PDF already contains (exact, instant), laid out in columns
  scanned PDF             each page rendered at 300 dpi, then OCR
  photo (JPG/PNG)         find the page in the photo and flatten it, even out the lighting, straighten, then OCR

Tesseract must be installed: Windows https://github.com/UB-Mannheim/tesseract/wiki (the default folder is found
automatically, or set TESSERACT_CMD), Mac `brew install tesseract`, Linux `apt install tesseract-ocr`.
"""
from __future__ import annotations

import io
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

MIN_TEXT_CHARS = 25          # a PDF page with less text than this is a scan
OCR_DPI = 300
# Below either, the page isn't trustworthy enough to approve without a person. 70: on the test set the worst
# photo (OCR confidence 64) produced a wrong business name that still looked plausible.
MIN_WORDS, MIN_CONFIDENCE = 8, 70
# A word Tesseract scored below this is remembered as "unsure": a value built on it goes to review even when it
# matches the page (on the test set, "Blvd" read as "Bivd" was copied faithfully by the AI).
UNSURE_WORD = 80
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}


@dataclass
class Page:
    number: int
    text: str
    method: str                 # "text" | "ocr"
    confidence: float           # 0-100
    words: int
    notes: list[str] = field(default_factory=list)    # what was done to the image ("page found in photo", ...)
    unsure: dict[str, float] = field(default_factory=dict)   # OCR words with low confidence -> their score

    @property
    def readable(self) -> bool:
        return self.words >= MIN_WORDS and self.confidence >= MIN_CONFIDENCE


@dataclass
class ReadResult:
    pages: list[Page]
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        if len(self.pages) == 1:
            return self.pages[0].text
        return "\n\n".join(f"--- page {p.number} ---\n{p.text}" for p in self.pages)

    @property
    def method(self) -> str:
        kinds = {p.method for p in self.pages}
        return kinds.pop() if len(kinds) == 1 else "mixed"

    @property
    def confidence(self) -> float:
        return min((p.confidence for p in self.pages), default=0.0)

    @property
    def readable(self) -> bool:
        return bool(self.pages) and all(p.readable for p in self.pages)

    @property
    def unsure(self) -> dict[str, float]:
        out = {}
        for p in self.pages:
            for w, c in p.unsure.items():
                out[w] = min(c, out.get(w, 100.0))
        return out


def read(source: str | Path | bytes, filename: str | None = None) -> ReadResult:
    """Read a PDF or image, given a path or the file's bytes (+ its name, for the extension)."""
    if isinstance(source, (str, Path)):
        filename = filename or str(source)
        data = Path(source).read_bytes()
    else:
        data = source
    ext = Path(filename or "").suffix.lower()
    if ext == ".pdf" or data[:5] == b"%PDF-":
        return _read_pdf(data)
    if ext in IMAGE_TYPES or not ext:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))      # phones store rotation separately
        page = _ocr(img, 1, photo=True)
        return ReadResult([page], [] if page.readable else [_why_unreadable(page)])
    raise ValueError(f"Unsupported file type '{ext}'. Use PDF, JPG or PNG.")


# ------------------------------------------------------------------------------------------- PDFs

def _read_pdf(data: bytes) -> ReadResult:
    import pdfplumber
    import pypdfium2 as pdfium

    pages, warnings = [], []
    rendered = None
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for i, p in enumerate(pdf.pages):
            text = _tidy(p.extract_text(layout=True) or "")
            if len(text.replace(" ", "").replace("\n", "")) >= MIN_TEXT_CHARS:
                boxed = _spaced_digits(text)
                if boxed:
                    text += "\n" + "\n".join(f"[digits in boxes] {b}" for b in boxed)
                pages.append(Page(i + 1, text, "text", 100.0, len(text.split())))
                continue
            rendered = rendered or pdfium.PdfDocument(data)
            img = rendered[i].render(scale=OCR_DPI / 72).to_pil()
            pages.append(_ocr(img, i + 1, photo=False))
    warnings += [_why_unreadable(p) for p in pages if not p.readable]
    return ReadResult(pages, warnings)


def _tidy(text: str) -> str:
    """pdfplumber's layout text pads every line to the page width: drop the padding and blank runs."""
    lines = [line.rstrip() for line in text.splitlines()]
    out, blank = [], 0
    for line in lines:
        blank = blank + 1 if not line else 0
        if blank <= 1:
            out.append(line)
    while out and not out[0]:
        out.pop(0)
    # the same left margin on every line carries no information
    indent = min((len(l) - len(l.lstrip()) for l in out if l.strip()), default=0)
    return "\n".join(l[indent:] for l in out).strip()


# ------------------------------------------------------------------------------------------- OCR

def _tesseract():
    import pytesseract
    cmd = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
    if not cmd:
        default_win = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        cmd = default_win if Path(default_win).exists() else None
    if not cmd:
        raise RuntimeError("Tesseract isn't installed (needed for scans and photos). Install it, or set "
                           "TESSERACT_CMD to tesseract.exe's full path.")
    pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract


def _ocr(img: Image.Image, number: int, photo: bool) -> Page:
    import cv2
    notes = []
    rgb = np.asarray(img.convert("RGB"))
    if photo:
        flat = _find_page(rgb)
        if flat is not None:
            rgb = flat
            notes.append("page found in photo and flattened")
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    # Remove scanner grain before enlarging (enlarging would turn specks into stray "letters"). Measured on the
    # test set: scanned receipts 70% -> 91% of values read, OCR confidence 66 -> 92.
    gray = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
    gray, scale = _resize_for_ocr(gray)
    if scale != 1:
        notes.append(f"resized x{scale:.2f}")
    gray = _even_lighting(gray)
    angle = _skew_angle(gray)
    if abs(angle) >= 0.2:
        gray = _rotate(gray, angle)
        notes.append(f"straightened {angle:+.1f} deg")
    text, conf, words, unsure = _tesseract_text(gray)
    boxed = _boxed_digits(gray)
    if boxed:
        text += "\n" + "\n".join(f"[digits in boxes] {b}" for b in boxed)
        notes.append(f"read {len(boxed)} row(s) of boxed digits")
    return Page(number, text, "ocr", conf, words, notes, unsure)


def _find_page(rgb: np.ndarray) -> np.ndarray | None:
    """The page in a photo is the largest bright four-cornered shape. Returns it flattened, or None."""
    import cv2
    h, w = rgb.shape[:2]
    k = 1000 / max(h, w)
    small = cv2.resize(rgb, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    # paper is brighter than the desk: threshold, then keep the biggest blob's outline
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(c)
    if area < 0.15 * small.shape[0] * small.shape[1] or area > 0.97 * small.shape[0] * small.shape[1]:
        return None                    # no clear page edge (a screenshot, or the page fills the photo)
    quad = cv2.approxPolyDP(cv2.convexHull(c), 0.02 * cv2.arcLength(c, True), True)
    if len(quad) != 4:
        rect = cv2.minAreaRect(c)
        quad = cv2.boxPoints(rect)
    pts = _order_corners(np.float32(quad).reshape(4, 2) / k)
    tl, tr, br, bl = pts
    width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    dst = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    M = cv2.getPerspectiveTransform(pts, dst)
    return cv2.warpPerspective(rgb, M, (width, height))


def _order_corners(p: np.ndarray) -> np.ndarray:
    s, d = p.sum(1), np.diff(p, axis=1).ravel()
    return np.float32([p[np.argmin(s)], p[np.argmin(d)], p[np.argmax(s)], p[np.argmax(d)]])


def _resize_for_ocr(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """Tesseract reads best when capital letters are ~30 px tall. Pages and photos arrive at any size, so aim
    for a width of ~2400 px (a letter page at ~290 dpi; a receipt's 40 columns at ~60 px each)."""
    import cv2
    h, w = gray.shape
    scale = min(2400 / w, 3.0)
    if 0.85 < scale < 1.15:
        return gray, 1.0
    interp = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
    return cv2.resize(gray, None, fx=scale, fy=scale, interpolation=interp), scale


def _even_lighting(gray: np.ndarray) -> np.ndarray:
    """Divide by a blurred copy (the 'background'), so shadows and grey paper become white."""
    import cv2
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    bg = cv2.GaussianBlur(bg, (0, 0), 15)
    flat = cv2.divide(gray, bg, scale=255)
    return cv2.normalize(flat, None, 0, 255, cv2.NORM_MINMAX)


def _skew_angle(gray: np.ndarray) -> float:
    """Try angles from -5 to +5 degrees; text lines are straightest where the row sums are most uneven."""
    import cv2
    k = 800 / max(gray.shape)
    small = cv2.resize(gray, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    ink = (small < 160).astype(np.float32)
    if ink.mean() < 0.002:
        return 0.0
    best, best_score = 0.0, -1.0
    for a in np.arange(-5, 5.01, 0.25):
        rows = _rotate(ink, a, border=0).sum(1)
        score = float(np.var(rows))
        if score > best_score:
            best, best_score = float(a), score
    # refine around the best
    for a in np.arange(best - 0.25, best + 0.26, 0.05):
        rows = _rotate(ink, a, border=0).sum(1)
        score = float(np.var(rows))
        if score > best_score:
            best, best_score = float(a), score
    return round(best, 2)


def _rotate(img: np.ndarray, angle: float, border=255) -> np.ndarray:
    import cv2
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=border)


def _tesseract_text(gray: np.ndarray) -> tuple[str, float, int, dict]:
    """OCR, then rebuild each line keeping wide gaps as 3 spaces, so table columns stay apart. Also returns the
    words Tesseract was unsure of (lower-cased letters and digits only -> confidence)."""
    tess = _tesseract()
    d = tess.image_to_data(gray, config="--oem 1 --psm 6", output_type=tess.Output.DICT)
    lines: dict[tuple, list] = {}
    total, weight = 0.0, 0
    unsure: dict[str, float] = {}
    for i, word in enumerate(d["text"]):
        word = word.strip()
        conf = float(d["conf"][i])
        if not word or conf < 0:
            continue
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        lines.setdefault(key, []).append((d["left"][i], d["width"][i], d["height"][i], word))
        total += conf * len(word)
        weight += len(word)
        key_word = re.sub(r"[^0-9a-z]", "", word.lower())
        if conf < UNSURE_WORD and len(key_word) >= 3:
            unsure[key_word] = min(conf, unsure.get(key_word, 100.0))
    out = []
    for key in sorted(lines):                       # Tesseract's reading order
        words = sorted(lines[key])
        line, prev_end = "", None
        for left, width, height, word in words:
            if prev_end is not None:
                gap = left - prev_end
                line += "   " if gap > 1.5 * max(height, 10) else " "
            line += word
            prev_end = left + width
        out.append(line)
    words_n = sum(len(v) for v in lines.values())
    return "\n".join(out), round(total / weight, 1) if weight else 0.0, words_n, unsure


def box_layout(groups: list[str]) -> str:
    """Name the layout of a tax ID's boxes: 3+2+4 digits is an SSN, 2+7 an EIN."""
    sizes = "+".join(str(len(g)) for g in groups)
    kind = {"3+2+4": "SSN layout", "2+7": "EIN layout"}.get(sizes)
    return f"{' '.join(groups)} ({sizes} digits{': ' + kind if kind else ''})"


def _spaced_digits(text: str) -> list[str]:
    """A PDF's own text gives digits typed in boxes as "0 0 - 5 3 5 8 4  6 6": join each run back up."""
    out = []
    for m in re.finditer(r"(?<![\d])(?:\d[ \t]+|\d[ \t]*-[ \t]*){6,}\d(?![\d])", text):
        groups = [re.sub(r"\D", "", g) for g in m.group(0).split("-")]
        if all(groups) and sum(map(len, groups)) >= 7:
            out.append(box_layout(groups))
    return out


def _boxed_digits(gray: np.ndarray) -> list[str]:
    """Forms put one digit per box (a W-9's tax ID: [9][8][7]-[6][5]-[4][3][2][1]). Page-level OCR reads the box
    walls as letters, so: find rows of small, equal, square boxes; cut out what's inside each; read the row as
    digits only. Returns one string per row with any digits in it, groups split by a gap: "987 65 4321"."""
    import cv2
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(bw, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    W = gray.shape[1]
    cells = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if 0.012 * W < w < 0.045 * W and 0.8 < h / w < 1.6 and cv2.contourArea(c) > 0.6 * w * h:
            cells.append((x, y, w, h))
    # the same box is often found twice (outer and inner edge of its border): keep the inner one
    cells.sort(key=lambda b: b[2] * b[3])
    unique = []
    for b in cells:
        if not any(abs(b[0] + b[2] / 2 - u[0] - u[2] / 2) < u[2] / 2 and abs(b[1] + b[3] / 2 - u[1] - u[3] / 2) < u[3] / 2
                   for u in unique):
            unique.append(b)
    rows: list[list] = []
    for b in sorted(unique, key=lambda b: (b[1], b[0])):
        for row in rows:
            if abs(row[0][1] - b[1]) < 0.4 * b[3] and abs(row[0][3] - b[3]) < 0.25 * b[3]:
                row.append(b)
                break
        else:
            rows.append([b])
    tess = _tesseract()
    out = []
    for row in rows:
        if len(row) < 4:
            continue
        row.sort()
        h = max(b[3] for b in row)
        strip, groups = [], []
        for i, (x, y, w, hh) in enumerate(row):
            m = max(3, int(0.18 * min(w, hh)))                 # stay clear of the box's border
            cell = gray[y + m:y + hh - m, x + m:x + w - m]
            cell = cv2.copyMakeBorder(cell, m, h - hh + m, m, m, cv2.BORDER_CONSTANT, value=255)
            if i and x - (row[i - 1][0] + row[i - 1][2]) > 0.4 * w:
                strip.append(np.full((cell.shape[0], int(1.2 * w)), 255, np.uint8))   # a gap between groups
            strip.append(cell)
            strip.append(np.full((cell.shape[0], int(0.3 * w)), 255, np.uint8))
        img = cv2.copyMakeBorder(np.hstack(strip), 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
        digits = tess.image_to_string(img, config="--oem 1 --psm 7 -c tessedit_char_whitelist=0123456789").strip()
        if sum(ch.isdigit() for ch in digits) >= len(row) / 2:      # mostly empty: checkboxes, not digits
            out.append(box_layout(digits.split()))
    return out


def _why_unreadable(p: Page) -> str:
    if p.words < MIN_WORDS:
        return f"page {p.number}: almost no text found ({p.words} words). Is it blank, or a very poor photo?"
    return f"page {p.number}: OCR is unsure (confidence {p.confidence:.0f}/100). Check the values by hand."
