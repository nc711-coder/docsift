"""Day 2's check: run every test document through the pipeline and score each field against the answers.

    python -m evals.extraction                  # all 60 (needs GROQ_API_KEY in .env)
    python -m evals.extraction --only inv       # ids starting with "inv"
    python -m evals.extraction --regrade        # re-score the last run with the current rules, no AI calls
    python -m evals.extraction --oracle         # a "perfect AI" that returns the answers: shows how often the
                                                # checks send a CORRECT document to review (false alarms)
    python -m evals.extraction --chaos          # the perfect AI plus one typical mistake planted per document
                                                # (a wrong digit, a swapped date, a dropped item...): shows how
                                                # many mistakes the checks catch, with no AI calls

What is measured (the plan's targets in brackets):
  type detected            [98%]   detected type == true type
  fields correct           [95% text PDFs, 90% scans/photos]
                                   money to the cent, dates exact, text equal ignoring case/spaces/punctuation;
                                   a value where the document has none counts as wrong, and so does a miss
  line items F1            [90%]   items matched on amount + description; F1 balances missed and invented ones
  auto-approved correct    [98%]   of documents NOT sent to review, the share that are fully correct
  mistakes caught          [80%]   of wrong fields, the share in documents that were sent to review

Reading results are cached in evals/results/cache/, so only the first run waits for OCR.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from docsift.reader import Page, ReadResult, read
from docsift.schemas import MODELS

ROOT = Path(__file__).resolve().parent.parent
ANSWERS, DOCS = ROOT / "samples" / "answers", ROOT / "samples" / "docs"
RESULTS = ROOT / "evals" / "results"
CACHE = RESULTS / "cache"
LAST = RESULTS / "extraction-last.json"


# ------------------------------------------------------------------------------------------- reading, cached

def read_cached(path: Path) -> ReadResult:
    data = path.read_bytes()
    key = hashlib.sha1(data + (ROOT / "docsift" / "reader.py").read_bytes()).hexdigest()[:16]
    f = CACHE / f"{path.stem}-{key}.json"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        return ReadResult([Page(**p) for p in d["pages"]], d["warnings"])
    r = read(path)
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"pages": [p.__dict__ for p in r.pages], "warnings": r.warnings}), encoding="utf-8")
    return r


# ------------------------------------------------------------------------------------------- scoring

def norm_text(v) -> str:
    return re.sub(r"[\s.,]+", "", str(v)).lower()


def same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 0.005
    return norm_text(a) == norm_text(b)


def item_f1(expected: list[dict], got: list[dict]) -> tuple[float, int, int, int]:
    """F1 of line items: an item matches when the amount is equal and the description is close."""
    unused = list(got)
    tp = 0
    for e in expected:
        for g in unused:
            if same(e.get("amount"), g.get("amount")) and \
                    SequenceMatcher(None, norm_text(e["description"]), norm_text(g.get("description", ""))).ratio() >= 0.8:
                tp += 1
                unused.remove(g)
                break
    if not expected and not got:
        return 1.0, 0, 0, 0
    return 2 * tp / (len(expected) + len(got)), tp, len(expected), len(got)


def grade(answer: dict, result: dict) -> dict:
    kind, truth = answer["type"], answer["fields"]
    out = {"type_ok": result["type"] == kind, "fields": {}, "wrong": [], "item_f1": None}
    if not out["type_ok"] or result["fields"] is None:
        names = [k for k, v in truth.items() if not isinstance(v, list)]
        out["fields"] = {k: False for k in names}
        out["wrong"] = [{"field": k, "expected": truth[k], "got": None} for k in names]
        out["item_f1"] = 0.0
        return out
    got = result["fields"]
    for k, v in truth.items():
        if isinstance(v, list):
            f1, tp, ne, ng = item_f1(v, got.get(k) or [])
            out["item_f1"] = f1
            out["items"] = {"matched": tp, "expected": ne, "got": ng}
            continue
        ok = same(v, got.get(k))
        out["fields"][k] = ok
        if not ok:
            out["wrong"].append({"field": k, "expected": v, "got": got.get(k)})
    out["fully_correct"] = not out["wrong"] and out["item_f1"] in (None, 1.0)
    return out


# ------------------------------------------------------------------------------------------- oracle (no AI)

class Oracle:
    """Returns the known answer as if it were the AI: everything is correct, so every review is a false alarm.
    With chaos=True, one typical mistake is planted in each answer instead."""

    def __init__(self, answers, chaos=False, seed=7):
        import random
        self.current, self.chaos, self.rng = None, chaos, random.Random(seed)
        self.planted = {}

    def json(self, system, user, schema=None, name="result", max_tokens=0):
        if name == "classification":
            return {"type": self.current["type"], "reason": "oracle"}, {"model": "oracle", "seconds": 0}
        fields = json.loads(json.dumps(self.current["fields"]))
        if self.chaos:
            self.planted[self.current["id"]] = plant_mistake(fields, self.rng)
        return fields, {"model": "oracle", "seconds": 0}


def plant_mistake(f: dict, rng) -> str:
    """The kinds of mistake extraction models make: misread digits, day/month swaps, invented or dropped values,
    a missing line item, the wrong last 4."""
    from datetime import date
    scalars = [k for k, v in f.items() if v is not None and not isinstance(v, (list, bool))
               and k not in ("currency", "category", "tin_type", "tax_classification", "llc_tax_code", "payment_method")]
    rows = "line_items" if "line_items" in f else "items" if "items" in f else None
    options = ["value"] * 4 + ["dropped"] + (["row"] if rows and len(f[rows]) > 1 else []) + \
        (["invented"] if any(v is None for k, v in f.items() if k in ("due_date", "po_number", "tip", "business_name")) else [])
    kind = rng.choice(options)
    if kind == "row":
        f[rows].pop(rng.randrange(len(f[rows])))
        return f"dropped a line item"
    if kind == "invented":
        k = rng.choice([k for k in ("due_date", "po_number", "tip", "business_name") if k in f and f[k] is None])
        f[k] = {"due_date": "2026-11-30", "po_number": "PO-10000", "tip": 5.0, "business_name": "Acme LLC"}[k]
        return f"invented {k}"
    k = rng.choice(scalars)
    if kind == "dropped":
        f[k] = None
        return f"dropped {k}"
    v = f[k]
    if isinstance(v, float):
        f[k] = round(v + rng.choice([-9, -1, 1, 9, 10, 0.9]) * (1 if v < 1000 else 10), 2)
    elif k.endswith("date") or k == "date":
        d = date.fromisoformat(v)
        f[k] = (date(d.year, d.day, d.month) if d.day <= 12 and d.day != d.month else d.replace(day=min(d.day + 1, 28))).isoformat()
    elif k in ("tin_last4", "card_last4"):
        f[k] = v[:-1] + str((int(v[-1]) + 3) % 10)
    else:
        s = str(v)
        i = rng.randrange(len(s))
        f[k] = s[:i] + ("8" if s[i].isdigit() and s[i] != "8" else "e" if s[i] != "e" else "a") + s[i + 1:]
    return f"changed {k}: {v} -> {f[k]}"


# ------------------------------------------------------------------------------------------- report

def summarise(rows: list[dict]) -> dict:
    n = len(rows)
    fields = [ok for r in rows for ok in r["grade"]["fields"].values()]
    f1s = [r["grade"]["item_f1"] for r in rows if r["grade"]["item_f1"] is not None and r["type"] != "w9"]
    approved = [r for r in rows if not r["result"]["needs_review"]]
    wrong_fields = [(r, w) for r in rows for w in r["grade"]["wrong"]]
    caught = [1 for r, w in wrong_fields if r["result"]["needs_review"]]
    reviewed = [r for r in rows if r["result"]["needs_review"]]
    return {
        "documents": n,
        "type_ok": sum(r["grade"]["type_ok"] for r in rows) / n,
        "fields_ok": sum(fields) / len(fields) if fields else 0,
        "item_f1": sum(f1s) / len(f1s) if f1s else None,
        "fully_correct": sum(bool(r["grade"].get("fully_correct")) for r in rows) / n,
        "sent_to_review": len(reviewed) / n,
        "approved_correct": (sum(bool(r["grade"].get("fully_correct")) for r in approved) / len(approved)) if approved else None,
        "caught": (len(caught) / len(wrong_fields)) if wrong_fields else None,
        # sent to review over a doubt although every value was right (an unsigned W-9 is a real problem, not one)
        "false_alarms": sum(bool(r["grade"].get("fully_correct")) and bool(r["result"].get("doubts", r["result"]["reasons"]))
                            for r in reviewed),
        "seconds": sum(r["result"]["seconds"].get("extract", 0) + r["result"]["seconds"].get("classify", 0)
                       for r in rows) / n,
    }


def pct(x):
    return "–" if x is None else f"{x:.1%}"


def report(rows: list[dict], label: str) -> str:
    groups = defaultdict(list)
    for r in rows:
        groups[("all", "all")].append(r)
        groups[(r["type"], "all")].append(r)
        groups[("all", "text PDFs" if r["condition"] == "clean" else "scans + photos")].append(r)
    order = [("all", "all"), ("invoice", "all"), ("receipt", "all"), ("w9", "all"), ("all", "text PDFs"),
             ("all", "scans + photos")]
    lines = [f"# Extraction check: {label}, {datetime.now():%Y-%m-%d %H:%M}", "",
             "| Documents | n | Type | Fields correct | Line items F1 | Fully correct | Sent to review | "
             "Auto-approved correct | Mistakes caught | False alarms | AI seconds/doc |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for key in order:
        if key not in groups:
            continue
        s = summarise(groups[key])
        name = key[0] if key[1] == "all" else key[1]
        lines.append(f"| {name} | {s['documents']} | {pct(s['type_ok'])} | {pct(s['fields_ok'])} | {pct(s['item_f1'])} | "
                     f"{pct(s['fully_correct'])} | {pct(s['sent_to_review'])} | {pct(s['approved_correct'])} | "
                     f"{pct(s['caught'])} | {s['false_alarms']} | {s['seconds']:.1f} |")
    per_field = defaultdict(lambda: [0, 0])
    for r in rows:
        for k, ok in r["grade"]["fields"].items():
            per_field[(r["type"], k)][0] += ok
            per_field[(r["type"], k)][1] += 1
    worst = sorted(((ok / n, t, k, n - ok) for (t, k), (ok, n) in per_field.items() if ok < n))
    lines += ["", "## Fields most often wrong", ""]
    lines += [f"- {t}.{k}: {miss} wrong ({acc:.0%} correct)" for acc, t, k, miss in worst[:12]] or ["None."]
    lines += ["", "## Every wrong field", ""]
    for r in rows:
        for w in r["grade"]["wrong"]:
            flag = "caught (sent to review)" if r["result"]["needs_review"] else "**NOT caught**"
            lines.append(f"- {r['id']} ({r['condition']}) `{w['field']}`: expected `{w['expected']}`, got "
                         f"`{w['got']}` — {flag}")
        if r["grade"].get("items") and r["grade"]["item_f1"] < 1:
            i = r["grade"]["items"]
            lines.append(f"- {r['id']} line items: {i['matched']} of {i['expected']} matched, {i['got']} returned")
    if any("planted" in r for r in rows):
        lines += ["", "## Planted mistakes that were NOT caught", ""]
        lines += [f"- {r['id']} ({r['condition']}): {r['planted']}" for r in rows
                  if r.get("planted") and not r["grade"].get("fully_correct") and not r["result"]["needs_review"]] \
            or ["None."]
    lines += ["", "## Sent to review although fully correct (false alarms)", ""]
    lines += [f"- {r['id']} ({r['condition']}): {'; '.join(r['result']['doubts'])}" for r in rows
              if r["result"].get("doubts") and r["grade"].get("fully_correct")] or ["None."]
    lines += ["", "## Sent to review because the document is incomplete (correct to flag)", ""]
    lines += [f"- {r['id']}: {'; '.join(x for x in r['result']['reasons'] if x not in r['result'].get('doubts', []))}"
              for r in rows if r["result"]["needs_review"] and set(r["result"]["reasons"]) - set(r["result"].get("doubts", []))] \
        or ["None."]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Score extraction on the test documents.")
    ap.add_argument("--only", help="only ids starting with this")
    ap.add_argument("--oracle", action="store_true", help="a perfect fake AI: measures false alarms only")
    ap.add_argument("--chaos", action="store_true", help="perfect fake AI + one planted mistake per document")
    ap.add_argument("--regrade", action="store_true", help="re-score the last run, no AI calls")
    ap.add_argument("--pause", type=float, default=0, help="seconds between documents (rate limits)")
    a = ap.parse_args(argv)

    from docsift.pipeline import analyse
    from docsift.llm import LLMError, load_dotenv
    answers = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(ANSWERS.glob("*.json"))]
    if a.only:
        answers = [x for x in answers if x["id"].startswith(a.only)]
    by_id = {x["id"]: x for x in answers}

    if a.regrade:
        saved = json.loads(LAST.read_text(encoding="utf-8"))
        rows = [dict(r, grade=grade(by_id[r["id"]], r["result"])) for r in saved["rows"] if r["id"] in by_id]
        label = saved["label"] + " (re-graded)"
    else:
        load_dotenv()
        if a.oracle or a.chaos:
            llm = Oracle(answers, chaos=a.chaos)
            label = "chaos (one planted mistake per document)" if a.chaos else "oracle (perfect AI)"
        else:
            from docsift.llm import GroqJSON
            llm = GroqJSON()
            label = llm.model
        rows = []
        for ans in answers:
            r = read_cached(DOCS / ans["file"])
            if a.oracle or a.chaos:
                llm.current = ans
            try:
                res = analyse(r, llm).to_dict()
            except LLMError as e:
                print(f"{ans['id']}: {e}")
                if "usage limit" in str(e) or "API key" in str(e) or "not set" in str(e):
                    print("Stopping: fix that first, then run again.")
                    break
                continue
            res.pop("text")
            row = {"id": ans["id"], "type": ans["type"], "condition": ans["meta"]["condition"], "result": res}
            row["grade"] = grade(ans, res)
            if a.chaos:
                row["planted"] = llm.planted.get(ans["id"])
            rows.append(row)
            g = row["grade"]
            status = "review " if res["needs_review"] else "approve"
            print(f"{ans['id']:7} {row['condition']:5} {status}  fields {sum(g['fields'].values())}/{len(g['fields'])}"
                  + (f"  items F1 {g['item_f1']:.2f}" if g["item_f1"] is not None and ans["type"] != "w9" else "")
                  + (f"  wrong: {', '.join(w['field'] for w in g['wrong'][:5])}" if g["wrong"] else "")
                  + (f"  | {res['reasons'][0][:70]}" if res["reasons"] else ""))
            if a.pause:
                time.sleep(a.pause)
        if not rows:
            return 1
        RESULTS.mkdir(parents=True, exist_ok=True)
        if not (a.oracle or a.chaos):
            LAST.write_text(json.dumps({"label": label, "rows": rows}, default=str), encoding="utf-8")
    text = report(rows, label)
    out = RESULTS / f"extraction-{'chaos-' if a.chaos else 'oracle-' if a.oracle else ''}{datetime.now():%Y%m%d-%H%M}.md"
    out.write_text(text, encoding="utf-8")
    print("\n" + text.split("## Every wrong field")[0] + f"Full report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
