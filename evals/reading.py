"""How much of each test document does the reader get back? (Day 1's check.)

    python -m evals.reading                 # all 60 documents
    python -m evals.reading --only rcp      # ids starting with rcp
    python -m evals.reading --show w9-14    # print what was read from one document

For every document, the key values exactly as printed ("$1,234.50", "March 5, 2026", "FRESHWAY MARKET #214")
are looked up in the text the reader returned, ignoring case and spaces. "Found" is the share of them that
came back. Extraction (day 2) can't be better than reading: a value that wasn't read can't be extracted.
Writes a Markdown report to evals/results/.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from docsift.reader import read

ROOT = Path(__file__).resolve().parent.parent
ANSWERS, DOCS, RESULTS = ROOT / "samples" / "answers", ROOT / "samples" / "docs", ROOT / "evals" / "results"


def squash(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def check(answer: dict) -> dict:
    t = time.perf_counter()
    r = read(DOCS / answer["file"])
    seconds = time.perf_counter() - t
    text = squash(r.text)
    missing = [s for s in answer["printed"] if squash(s) not in text]
    found = 1 - len(missing) / len(answer["printed"])
    return {"id": answer["id"], "type": answer["type"], "condition": answer["meta"]["condition"],
            "method": r.method, "confidence": r.confidence, "readable": r.readable, "found": found,
            "missing": missing, "seconds": seconds, "notes": [n for p in r.pages for n in p.notes],
            "warnings": r.warnings, "text": r.text}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="only ids starting with this")
    ap.add_argument("--show", help="print the text read from this document id")
    a = ap.parse_args(argv)
    answers = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(ANSWERS.glob("*.json"))]
    if a.show:
        res = check(next(x for x in answers if x["id"] == a.show))
        print(res["text"])
        print(f"\n{res['method']}, confidence {res['confidence']}, found {res['found']:.0%}, missing {res['missing']}")
        return 0
    if a.only:
        answers = [x for x in answers if x["id"].startswith(a.only)]
    results = []
    for ans in answers:
        res = check(ans)
        results.append(res)
        flag = "" if res["readable"] else "  UNREADABLE"
        print(f"{res['id']:7} {res['condition']:5} {res['method']:5} conf {res['confidence']:5.1f}  "
              f"found {res['found']:6.1%}  {res['seconds']:5.1f}s{flag}"
              + (f"  missing: {', '.join(res['missing'][:4])}" if res["missing"] else ""))

    groups = defaultdict(list)
    for r in results:
        groups[(r["type"], r["condition"])].append(r)
        groups[(r["type"], "all")].append(r)
        groups[("all", r["condition"])].append(r)
        groups[("all", "all")].append(r)
    table = ["| Type | Condition | Documents | Values found | Lowest | Avg OCR confidence | Flagged unreadable | Avg seconds |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for (t, c), rs in sorted(groups.items(), key=lambda kv: (kv[0][0] == "all", kv[0][0], kv[0][1] == "all", kv[0][1])):
        ocr = [r["confidence"] for r in rs if r["method"] != "text"]
        table.append(f"| {t} | {c} | {len(rs)} | {sum(r['found'] for r in rs) / len(rs):.1%} | "
                     f"{min(r['found'] for r in rs):.0%} | {sum(ocr) / len(ocr):.0f} | " if ocr else
                     f"| {t} | {c} | {len(rs)} | {sum(r['found'] for r in rs) / len(rs):.1%} | "
                     f"{min(r['found'] for r in rs):.0%} | (text layer) | ")
        table[-1] += f"{sum(not r['readable'] for r in rs)} | {sum(r['seconds'] for r in rs) / len(rs):.1f} |"
    misses = [f"- **{r['id']}** ({r['condition']}): {', '.join(r['missing'])}" for r in results if r["missing"]]
    report = (f"# Reading check, {datetime.now():%Y-%m-%d %H:%M}\n\n" + "\n".join(table) +
              "\n\n## Values not found\n\n" + ("\n".join(misses) or "None.") + "\n")
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"reading-{datetime.now():%Y%m%d-%H%M}.md"
    out.write_text(report, encoding="utf-8")
    print("\n" + "\n".join(table) + f"\n\nReport: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
