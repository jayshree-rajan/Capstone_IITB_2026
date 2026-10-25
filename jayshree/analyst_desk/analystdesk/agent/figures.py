"""Key figures the writer may use — produced and rendered by code (rule 1).

The writer never types a reported figure. It writes a placeholder such as
{MSFT_2024_revenue} and code replaces it with the exact value and its
source tag. So the numbers in a note cannot be mistyped or invented: the
model chooses *which* figure to mention; code decides *what it says*.

For every company and fiscal year in the plan:
    {T_Y_revenue}            $245,122.0M [fact: ...]
    {T_Y_operating_income}   (operating-margin companies)
    {T_Y_pretax_income}      (banks)
    {T_Y_margin}             44.64% [calc: income fact / revenue fact]
    {T_Y_period_end}         2024-06-30 (fiscal years differ by company)
"""

from __future__ import annotations

import re

from ..edgar import get_financials
from ..scope import COMPANIES

_BY_TICKER = {c.ticker: c for c in COMPANIES}
PLACEHOLDER = re.compile(r"\{([A-Z]{1,5}_\d{4}_[a-z_]+)\}")


def build_figures(companies: list[str], years: list[int]) -> dict[str, dict]:
    """Key -> {"text": rendered value with source tag, "label": description}."""
    figures: dict[str, dict] = {}
    for t in companies:
        c = _BY_TICKER.get(t)
        if c is None:
            continue
        for y in years:
            data = get_financials(c.cik, y)
            rev = data["revenue"]
            income_key = "pretax_income" if c.margin_basis == "pretax" else "operating_income"
            inc = data[income_key]
            if rev:
                figures[f"{t}_{y}_revenue"] = {
                    "text": f"${rev['val'] / 1e6:,.1f}M [fact: {rev['fact_id']}]",
                    "label": f"{c.name} FY{y} {'net ' if c.margin_basis == 'pretax' else ''}revenue"}
                figures[f"{t}_{y}_period_end"] = {"text": rev["end"],
                                                  "label": f"{c.name} FY{y} period end"}
            if inc:
                figures[f"{t}_{y}_{income_key}"] = {
                    "text": f"${inc['val'] / 1e6:,.1f}M [fact: {inc['fact_id']}]",
                    "label": f"{c.name} FY{y} {income_key.replace('_', ' ')}"}
            if rev and inc and rev["val"]:
                figures[f"{t}_{y}_margin"] = {
                    "text": f"{inc['val'] / rev['val'] * 100:.2f}% [calc: {inc['fact_id']} / {rev['fact_id']}]",
                    "label": f"{c.name} FY{y} {c.margin_basis} margin"}
    return figures


def figures_table(figures: dict[str, dict]) -> str:
    """The list shown to the writer: placeholder, meaning, value."""
    return "\n".join(f"{{{k}}}  {v['label']}: {v['text'].split(' [')[0]}" for k, v in figures.items())


def render(body: str, figures: dict[str, dict]) -> tuple[str, list[str]]:
    """Replace placeholders with values + source tags; report unknown ones."""
    unknown = sorted({k for k in PLACEHOLDER.findall(body) if k not in figures})
    rendered = PLACEHOLDER.sub(lambda m: figures[m.group(1)]["text"] if m.group(1) in figures
                               else m.group(0), body)
    return rendered, [f"Unknown placeholder {{{k}}}." for k in unknown]
