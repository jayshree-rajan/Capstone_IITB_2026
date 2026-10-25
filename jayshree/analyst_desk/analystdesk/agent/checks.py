"""Code checks on a draft note — the self-correction loop's judge (L8, L13).

Every number in a note must be traceable, in one of three ways:

  1. An XBRL fact, rendered by code from a placeholder (see figures.py):
         $245,122.0M [fact: RevenueFromContract.../2024/0000950170-24-087843]
  2. A ratio of two facts:
         44.64% [calc: OperatingIncomeLoss/2024/... / RevenueFromContract.../2024/...]
  3. A figure quoted from a filing: the same line cites the passage, e.g.
     [MSFT_2024_item7_017], and the number appears in that passage's text.

check_note() verifies all three in code, not with an LLM (rule 1):
  * each [fact: ...] value equals the XBRL fact, in $ millions to 1 decimal;
  * each [calc: A / B] percentage equals A / B x 100, to 2 decimals;
  * each quoted number really occurs in a passage cited on its line;
  * cited passages exist;
  * no investment-advice wording ("buy", "price target", ...).
The issues it returns are fed back to the writer for a revision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..servers.edgar_server import get_fact

# A fact_id has its own slashes (Concept/2024/0000950170-24-087843), so the
# division in [calc: A / B] is the " / " between two complete fact_ids
FACT_ID = r"[A-Za-z]+/\d{4}/\d{10}-\d{2}-\d{6}"
FACT = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s?([MB])\s*\[fact:\s*([^\]]+?)\s*\]")
CALC = re.compile(rf"(-?[\d.]+)\s?%\s*\[calc:\s*({FACT_ID})\s*/\s*({FACT_ID})\s*\]")
PASSAGE_ID = re.compile(r"\b([A-Z]{1,5}_\d{4}_item(?:1a|7)_\d{3})\b")
# Any money amount or percentage, to catch numbers with no source
MONEY = re.compile(r"\$\s?[\d,]+(?:\.\d+)?\s?(?:[MBK]\b|million|billion)?")
PERCENT = re.compile(r"-?\d+(?:\.\d+)?\s?%")
ADVICE = re.compile(
    r"\b(buy|sell|hold rating|overweight|underweight|outperform|underperform|"
    r"price target|we recommend|strong buy|investors should)\b", re.I)


@dataclass
class CheckResult:
    ok: bool
    issues: list[str] = field(default_factory=list)
    verified_numbers: int = 0
    total_numbers: int = 0

    @property
    def verified_share(self) -> float:
        return round(100 * self.verified_numbers / self.total_numbers, 1) if self.total_numbers else 100.0


def _fact_value(fact_id: str, cache: dict) -> float | None:
    if fact_id not in cache:
        r = get_fact(fact_id)
        cache[fact_id] = r.fact.value_usd if r.ok else None
    return cache[fact_id]


def _digits(number: str) -> str:
    """The digits of a quoted number: '$7.74 billion' -> '7.74', '16%' -> '16'."""
    return re.sub(r"[^\d.]", "", number).strip(".")


def _quoted_in(number: str, text: str) -> bool:
    d = _digits(number)
    if not d:
        return False
    # match as a whole number in the passage, ignoring thousands separators
    plain = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    return re.search(rf"(?<![\d.]){re.escape(d.replace(',', ''))}(?![\d])", plain) is not None


def check_note(body: str, passage_exists=None, passage_text=None) -> CheckResult:
    """Verify every number and citation in a draft note body.

    passage_text(pid) -> str | None enables checking quoted numbers against
    the cited passage; passage_exists(pid) -> bool only checks citations.
    """
    if passage_exists is None and passage_text is not None:
        passage_exists = lambda pid: passage_text(pid) is not None   # noqa: E731
    issues: list[str] = []
    cache: dict = {}
    verified = total = 0
    covered: list[tuple[int, int]] = []   # spans of numbers that carry a source

    for m in FACT.finditer(body):
        total += 1
        covered.append(m.span())
        shown = float(m.group(1).replace(",", "")) * (1000 if m.group(2) == "B" else 1)
        actual = _fact_value(m.group(3), cache)
        if actual is None:
            issues.append(f"Unknown fact_id {m.group(3)!r} for ${m.group(1)}{m.group(2)}.")
        elif abs(shown - round(actual / 1e6, 1)) > (50 if m.group(2) == "B" else 0.05):
            issues.append(f"${m.group(1)}{m.group(2)} does not match {m.group(3)} "
                          f"(reported ${actual / 1e6:,.1f}M).")
        else:
            verified += 1

    for m in CALC.finditer(body):
        total += 1
        covered.append(m.span())
        num, den = _fact_value(m.group(2), cache), _fact_value(m.group(3), cache)
        if num is None or den is None or den == 0:
            issues.append(f"{m.group(1)}% cites an unknown fact in [calc: {m.group(2)} / {m.group(3)}].")
        elif abs(float(m.group(1)) - round(num / den * 100, 2)) > 0.01:
            issues.append(f"{m.group(1)}% is wrong: {m.group(2)} / {m.group(3)} = "
                          f"{num / den * 100:.2f}%.")
        else:
            verified += 1

    def is_covered(span):
        return any(s <= span[0] and span[1] <= e for s, e in covered)

    # Numbers without a fact/calc tag must be quotes from a passage cited on the same line
    line_starts = [0] + [i + 1 for i, ch in enumerate(body) if ch == "\n"]
    def line_of(pos):
        start = max(s for s in line_starts if s <= pos)
        end = body.find("\n", pos)
        return body[start:end if end != -1 else len(body)]

    for pattern in (MONEY, PERCENT):
        for m in pattern.finditer(body):
            if is_covered(m.span()):
                continue
            total += 1
            number = m.group().strip()
            cited = PASSAGE_ID.findall(line_of(m.start()))
            if passage_text is not None and cited and any(
                    _quoted_in(number, passage_text(pid) or "") for pid in cited):
                verified += 1
            elif cited:
                issues.append(f"Number {number!r} does not appear in the passage(s) cited on its line "
                              f"({', '.join(cited)}).")
            else:
                issues.append(f"Number {number!r} has no source: use a figure placeholder, or quote "
                              f"it from a filing and cite the passage on the same line.")

    if passage_exists is not None:
        for pid in sorted(set(PASSAGE_ID.findall(body))):
            if not passage_exists(pid):
                issues.append(f"Cited passage {pid} does not exist.")

    for m in ADVICE.finditer(body):
        issues.append(f"Investment-advice wording {m.group()!r} is not allowed in a research note.")

    return CheckResult(ok=not issues, issues=issues, verified_numbers=verified, total_numbers=total)
