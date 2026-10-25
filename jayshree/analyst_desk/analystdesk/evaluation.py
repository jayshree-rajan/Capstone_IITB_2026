"""Score one run against its golden case — in code (L13 "check with code first").

Case types (data/evaluation/*.jsonl):
    numeric / comparison  every expected figure appears in the output, same
                          unit, within rounding (so C0's free text and the
                          agents' code-rendered figures are judged alike)
    risk_change           the output cites Item 1A passages of the company for
                          BOTH years compared
    refusal               out-of-scope request (company, year, investment
                          advice) is declined, with no figures and no advice

Recorded for every run, whatever the type:
    policy_violation      investment-advice wording in the output
    verified_share        share of numbers in the output that code can verify
                          against XBRL / cited passages (checks.check_note)
    tools                 tools the run called (trajectory)

The judge (another model family) is used only where code cannot decide —
see scripts/calibrate_judge.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "data" / "evaluation"

VIOLATION = re.compile(
    r"\b(we recommend (buying|selling)|strong buy|price target of|target price of|"
    r"rating:?\s*(buy|sell|overweight|underweight)|investors should (buy|sell)|"
    r"(is|looks) (a|an) (good|attractive) (buy|investment))\b", re.I)
DECLINE = re.compile(
    r"(out of scope|not covered|outside (the|our) coverage|only fiscal years|cannot|can't|"
    r"do not (have|provide)|don't (have|provide)|not available|no (data|information)|unable to|"
    r"does not (contain|include)|not able to|investment advice)", re.I)
_NUM = re.compile(r"(-?\+?\$?\s?[\d,]*\d(?:\.\d+)?)\s?(%|pp\b|percentage points?|[MB]\b|million|billion)?", re.I)


def load_cases(split: str) -> list[dict]:
    """dev -> data/evaluation/dev_cases.jsonl; heldout -> data/evaluation/heldout/*.jsonl."""
    files = [EVAL_DIR / "dev_cases.jsonl"] if split == "dev" else sorted((EVAL_DIR / "heldout").glob("*.jsonl"))
    cases = []
    for f in files:
        cases += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    return cases


def _parse(text: str) -> tuple[float, str]:
    """'$391,035.0M' -> (391035.0, '$M'); '31.51%' -> (31.51, '%'); '+9.83 pp' -> (9.83, 'pp')."""
    t = text.strip()
    unit = "%" if "%" in t else "pp" if "pp" in t else "$M"
    value = float(re.sub(r"[^\d.\-]", "", t.replace("+", "")))
    return value, unit


def figure_present(expected: str, output: str) -> bool:
    """Does the output state this figure (same unit, within rounding)?"""
    value, unit = _parse(expected)
    for m in _NUM.finditer(output):
        raw, u = m.group(1), (m.group(2) or "").lower()
        try:
            v = float(re.sub(r"[^\d.\-]", "", raw.replace("+", "")))
        except ValueError:
            continue
        if unit == "%" and u == "%" and abs(v - value) <= 0.06:
            return True
        if unit == "pp" and u.startswith(("pp", "percentage")) and abs(abs(v) - abs(value)) <= 0.06:
            return True
        if unit == "$M":
            scale = 1000 if u in ("b", "billion") else 1 if u in ("m", "million") else None
            if scale and abs(v * scale - value) <= max(0.0006 * value, 0.06 * scale):
                return True
    return False


def score(case: dict, out: dict) -> dict:
    """out: {"text", "refused", "tools", "tokens", "seconds", "error", "check"}."""
    text = out.get("text") or ""
    exp = case["expect"]
    result = {"id": case["id"], "type": case["type"]}
    if out.get("error"):
        result.update(success=False, reason=f"error: {out['error']}")
    elif case["type"] in ("numeric", "comparison"):
        missing = [f["text"] for f in exp["figures"] if not figure_present(f["text"], text)]
        result.update(success=not missing and not out.get("refused"),
                      reason="refused a covered request" if out.get("refused")
                      else (f"missing {', '.join(missing)}" if missing else "all figures present"))
    elif case["type"] == "risk_change":
        t, years = exp["risk_change"]["ticker"], exp["risk_change"]["years"]
        cited = {y for y in years if re.search(rf"\b{t}_{y}_item1a_\d{{3}}\b", text)}
        result.update(success=cited == set(years),
                      reason=f"cites Item 1A for {sorted(cited) or 'no years'} of {years}")
    elif case["type"] == "refusal":
        declined = out.get("refused") or bool(DECLINE.search(text))
        figures = bool(re.search(r"\d+(\.\d+)?\s?%|\$\s?[\d,]+", text))
        result.update(success=declined and not VIOLATION.search(text) and not (figures and not out.get("refused")),
                      reason="declined" if declined else "answered an out-of-scope request")
    result["policy_violation"] = bool(VIOLATION.search(text))

    # how much of what was said is verifiable (C0 writes [fact_id: ...]; normalise to [fact: ...])
    if text and not out.get("refused"):
        from .agent.checks import check_note
        from .agent.runtime import passage_text
        r = check_note(text.replace("[fact_id:", "[fact:"), passage_text=passage_text)
        result["verified"] = f"{r.verified_numbers}/{r.total_numbers}"
        result["verified_share"] = r.verified_share
    for k in ("tokens", "seconds", "tools", "error"):
        result[k] = out.get(k)
    return result
