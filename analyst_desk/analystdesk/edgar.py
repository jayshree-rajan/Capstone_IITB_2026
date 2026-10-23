"""EDGAR XBRL data fetcher with local cache.

SEC EDGAR provides structured financial data (XBRL) for every US public company
as free JSON — no API key needed. We use this as ground truth: every number the
agent produces must be traceable to an XBRL fact from here.

Why cache? EDGAR asks automated users to stay under 10 requests per second and
to identify themselves via a User-Agent header. We cache every companyfacts file
locally so we only hit the network once per company, ever.

fact_id format: {concept}/{fiscal_year}/{accession_number}
  e.g. "OperatingIncomeLoss/2024/0000789019-24-000014"
This ID is what the compliance critic later uses to verify each number.
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

EDGAR_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
CACHE_DIR = Path(__file__).parent.parent / "data" / "edgar_cache"

# SEC requires a descriptive User-Agent with contact info
USER_AGENT = "AnalystDesk IITBombay contact@example.com"

# Revenue concept names differ by company. Most post-ASC-606 companies use
# RevenueFromContractWithCustomerExcludingAssessedTax; older or bank filings
# use Revenues. We try each in order and take the first hit.
# Banks (JPM, GS, BAC, MS, WFC) report net revenues differently — we use
# "Revenues" which maps to their total net revenue line in XBRL.
REVENUE_CONCEPTS = {
    # ── Technology ──────────────────────────────────────────────────────────
    "0000789019": [                                         # Microsoft
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
    ],
    "0001652044": [                                         # Alphabet
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
    ],
    "0000320193": [                                         # Apple
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
    ],
    "0001018724": [                                         # Amazon
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
    ],
    "0001326801": [                                         # Meta
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
    ],
    "0001045810": [                                         # NVIDIA
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
    ],
    "0000002488": [                                         # AMD
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "NetRevenue",
        "Revenues",
    ],
    "0001341439": [                                         # Oracle
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
    ],
    # ── Finance ─────────────────────────────────────────────────────────────
    "0000019617": [                                         # JPMorgan Chase
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
    ],
    "0000886982": [                                         # Goldman Sachs
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
    ],
    "0000070858": [                                         # Bank of America
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
    ],
    "0001403161": [                                         # Visa
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
    ],
    "0000895421": [                                         # Morgan Stanley
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
    ],
    "0001141391": [                                         # Mastercard
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
    ],
    "0000072971": [                                         # Wells Fargo
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
    ],
}
OPERATING_INCOME_CONCEPT = "OperatingIncomeLoss"


def _pad_cik(cik: str) -> str:
    """SEC API expects a 10-digit zero-padded CIK, e.g. '0000789019'."""
    return cik.lstrip("0").zfill(10)


def fetch_company_facts(cik: str) -> dict:
    """Return the full companyfacts JSON for a company, using local cache.

    First call per company hits the network and writes a cache file.
    Every subsequent call reads from disk — no network, no rate-limit risk.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    padded = _pad_cik(cik)
    cache_file = CACHE_DIR / f"{padded}.json"

    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    url = EDGAR_FACTS_URL.format(cik=padded)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()

    data = json.loads(raw)
    cache_file.write_text(json.dumps(data), encoding="utf-8")
    time.sleep(0.2)                 # polite pause between network requests
    return data


def get_annual_fact(cik: str, concept: str, fiscal_year: int) -> dict | None:
    """Return the annual 10-K value for one XBRL concept and fiscal year.

    Filters to full-year (fp=="FY") 10-K filings only. If multiple filings
    exist for the same year (e.g. an amended 10-K/A), returns the latest one.

    Returns None if the concept or year is not present in EDGAR.

    Return dict keys:
        val       raw USD value (not in millions — we convert later)
        end       period end date, e.g. "2024-06-30"
        accn      accession number, e.g. "0000789019-24-000014"
        filed     filing date, e.g. "2024-07-30"
        fact_id   "{concept}/{fiscal_year}/{accession_number}"
    """
    facts = fetch_company_facts(cik)
    entries = (
        facts.get("facts", {})
             .get("us-gaap", {})
             .get(concept, {})
             .get("units", {})
             .get("USD", [])
    )

    # Match by end-date year, not EDGAR's fy field.
    # EDGAR's fy can be misaligned for non-December fiscal years (e.g. Microsoft
    # ends June 30). Filtering on end[:4] works for both Microsoft (June-end)
    # and Alphabet (December-end). This is Signature Challenge A: fiscal year
    # alignment must be done by the period end date, not by the fy label.
    annual = [
        e for e in entries
        if e.get("fp") == "FY"
        and e.get("form") in ("10-K", "10-K/A")
        and e.get("end", "")[:4] == str(fiscal_year)
    ]

    if not annual:
        return None

    entry = max(annual, key=lambda e: e.get("filed", ""))
    return {
        "val": entry["val"],
        "end": entry["end"],
        "accn": entry["accn"],
        "filed": entry["filed"],
        "fact_id": f"{concept}/{fiscal_year}/{entry['accn']}",
    }


def get_financials(cik: str, fiscal_year: int) -> dict:
    """Return revenue and operating_income for one company-year.

    Tries each revenue concept in priority order (company-specific list).
    Operating income uses the same concept for all companies.

    Returns:
        {
          "revenue":          { val, end, accn, filed, fact_id } | None,
          "operating_income": { val, end, accn, filed, fact_id } | None,
        }
    """
    revenue = None
    for concept in REVENUE_CONCEPTS.get(cik, ["Revenues"]):
        revenue = get_annual_fact(cik, concept, fiscal_year)
        if revenue is not None:
            break

    op_income = get_annual_fact(cik, OPERATING_INCOME_CONCEPT, fiscal_year)

    return {"revenue": revenue, "operating_income": op_income}
