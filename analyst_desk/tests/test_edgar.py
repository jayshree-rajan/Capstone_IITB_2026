"""Tests for edgar.py — cache behaviour, fact_id format, missing data handling."""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from analystdesk.edgar import (
    CACHE_DIR,
    _pad_cik,
    get_annual_fact,
    get_financials,
    fetch_company_facts,
)

MSFT_CIK = "0000789019"
GOOGL_CIK = "0001652044"

# Minimal companyfacts JSON that mimics EDGAR's real response shape
FAKE_FACTS = {
    "cik": 789019,
    "entityName": "FAKE CORP",
    "facts": {
        "us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "units": {
                    "USD": [
                        {
                            "end": "2023-06-30", "val": 211915000000,
                            "accn": "0000789019-23-000001", "fy": 2023,
                            "fp": "FY", "form": "10-K", "filed": "2023-07-27"
                        },
                        # quarterly entry — must be filtered out
                        {
                            "end": "2023-03-31", "val": 52857000000,
                            "accn": "0000789019-23-000099", "fy": 2023,
                            "fp": "Q3", "form": "10-Q", "filed": "2023-04-25"
                        },
                    ]
                }
            },
            "OperatingIncomeLoss": {
                "units": {
                    "USD": [
                        {
                            "end": "2023-06-30", "val": 88523000000,
                            "accn": "0000789019-23-000001", "fy": 2023,
                            "fp": "FY", "form": "10-K", "filed": "2023-07-27"
                        },
                    ]
                }
            },
        }
    },
}


class TestPadCik(unittest.TestCase):
    def test_pads_short_cik(self):
        self.assertEqual(_pad_cik("789019"), "0000789019")

    def test_leaves_full_cik_unchanged(self):
        self.assertEqual(_pad_cik("0000789019"), "0000789019")


class TestFetchCompanyFacts(unittest.TestCase):
    def test_uses_cache_on_second_call(self):
        """Second call must read from disk, not hit the network."""
        cache_file = CACHE_DIR / f"{_pad_cik(MSFT_CIK)}.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(FAKE_FACTS), encoding="utf-8")

        with patch("urllib.request.urlopen") as mock_open:
            result = fetch_company_facts(MSFT_CIK)
            mock_open.assert_not_called()   # network must not be touched

        self.assertEqual(result["entityName"], "FAKE CORP")

    def tearDown(self):
        # Clean up so other tests start fresh
        cache_file = CACHE_DIR / f"{_pad_cik(MSFT_CIK)}.json"
        if cache_file.exists():
            cache_file.unlink()


class TestGetAnnualFact(unittest.TestCase):
    def setUp(self):
        # Pre-populate cache so no network calls happen
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = CACHE_DIR / f"{_pad_cik(MSFT_CIK)}.json"
        cache_file.write_text(json.dumps(FAKE_FACTS), encoding="utf-8")

    def tearDown(self):
        cache_file = CACHE_DIR / f"{_pad_cik(MSFT_CIK)}.json"
        if cache_file.exists():
            cache_file.unlink()

    def test_returns_annual_fact(self):
        fact = get_annual_fact(
            MSFT_CIK,
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            2023,
        )
        self.assertIsNotNone(fact)
        self.assertEqual(fact["val"], 211915000000)

    def test_filters_out_quarterly_entries(self):
        # Q3 entry for the same year must not be returned
        fact = get_annual_fact(
            MSFT_CIK,
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            2023,
        )
        self.assertEqual(fact["end"], "2023-06-30")   # annual, not quarterly

    def test_fact_id_format(self):
        fact = get_annual_fact(
            MSFT_CIK,
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            2023,
        )
        expected = (
            "RevenueFromContractWithCustomerExcludingAssessedTax"
            "/2023/0000789019-23-000001"
        )
        self.assertEqual(fact["fact_id"], expected)

    def test_returns_none_for_missing_year(self):
        fact = get_annual_fact(
            MSFT_CIK,
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            1990,       # year not in our fake data
        )
        self.assertIsNone(fact)

    def test_returns_none_for_unknown_concept(self):
        fact = get_annual_fact(MSFT_CIK, "NonExistentConcept", 2023)
        self.assertIsNone(fact)


class TestGetFinancials(unittest.TestCase):
    def setUp(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = CACHE_DIR / f"{_pad_cik(MSFT_CIK)}.json"
        cache_file.write_text(json.dumps(FAKE_FACTS), encoding="utf-8")

    def tearDown(self):
        cache_file = CACHE_DIR / f"{_pad_cik(MSFT_CIK)}.json"
        if cache_file.exists():
            cache_file.unlink()

    def test_returns_both_metrics(self):
        result = get_financials(MSFT_CIK, 2023)
        self.assertIsNotNone(result["revenue"])
        self.assertIsNotNone(result["operating_income"])

    def test_missing_year_returns_none_values(self):
        result = get_financials(MSFT_CIK, 1990)
        self.assertIsNone(result["revenue"])
        self.assertIsNone(result["operating_income"])


if __name__ == "__main__":
    unittest.main()
