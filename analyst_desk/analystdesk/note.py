"""Research note schema — the typed output contract for the Analyst Desk pipeline.

Every downstream stage (compliance critic, writer, approval gate, portal) consumes
a ResearchNote. Defining the schema here means all stages agree on the shape.

Key invariant: every MetricRow with a non-None value MUST have a corresponding
fact_id. A number without a fact_id cannot be verified and must not appear in a
published note. The compliance critic enforces this with code, not with an LLM.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class MetricRow:
    """One company + fiscal year measurement, with sources.

    All dollar values are in USD millions (rounded to 1 decimal place).
    operating_margin_pct is derived — it has no EDGAR fact_id of its own
    because it is computed in code from revenue and operating_income.
    """
    company: str
    fiscal_year: int
    revenue_usd_m: float | None
    operating_income_usd_m: float | None
    operating_margin_pct: float | None      # computed, not from EDGAR
    revenue_fact_id: str | None
    operating_income_fact_id: str | None
    period_end: str | None                  # e.g. "2024-06-30" from EDGAR


@dataclass
class ResearchNote:
    """The output of the Analyst Desk pipeline.

    status:
        "draft"     — produced by the pipeline, not yet reviewed
        "approved"  — analyst signed the exact payload
        "published" — written to the research portal
        "rejected"  — analyst rejected, not published

    rows:     one MetricRow per company per year (6 rows: 2 companies × 3 years)
    summary:  analyst-readable text (empty in C0; written by the writer agent later)
    warnings: any missing data, mismatched periods, or unverified numbers
    """
    title: str
    status: str
    rows: list[MetricRow]
    summary: str = ""
    warnings: list[str] = field(default_factory=list)

    def verified_count(self) -> int:
        """How many rows have both revenue and operating_income fact_ids."""
        return sum(
            1 for r in self.rows
            if r.revenue_fact_id and r.operating_income_fact_id
        )

    def verification_pct(self) -> float:
        """Percentage of rows where all numbers are EDGAR-verified."""
        if not self.rows:
            return 0.0
        return round(self.verified_count() / len(self.rows) * 100, 1)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "status": self.status,
            "summary": self.summary,
            "verification_pct": self.verification_pct(),
            "warnings": self.warnings,
            "rows": [
                {
                    "company": r.company,
                    "fiscal_year": r.fiscal_year,
                    "period_end": r.period_end,
                    "revenue_usd_m": r.revenue_usd_m,
                    "operating_income_usd_m": r.operating_income_usd_m,
                    "operating_margin_pct": r.operating_margin_pct,
                    "revenue_fact_id": r.revenue_fact_id,
                    "operating_income_fact_id": r.operating_income_fact_id,
                }
                for r in self.rows
            ],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
