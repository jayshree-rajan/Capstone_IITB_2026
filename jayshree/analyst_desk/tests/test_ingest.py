import unittest

from analystdesk.ingest import _split_sections

BODY = "Body text that is long enough to count as real section content. " * 40


def lines(*parts):
    return "\n".join(parts).split("\n")


class SplitSectionsTests(unittest.TestCase):
    def test_toc_entries_lose_to_real_sections(self):
        doc = lines("Item 1A. Risk Factors", "Item 7. MD&A", "Item 8. Financial Statements",
                    "ITEM 1A. RISK FACTORS", BODY, "ITEM 2. PROPERTIES", "x",
                    "ITEM 7. MANAGEMENT'S DISCUSSION", BODY, "ITEM 7A. MARKET RISK", "x")
        result = _split_sections(doc, use_titles=False)
        self.assertIn("Body text", result["item1a"])
        self.assertIn("Body text", result["item7"])

    def test_running_page_header_continues_section(self):
        # Mastercard repeats the item heading at the top of every page
        doc = lines("ITEM 1A. RISK FACTORS", "page one " + BODY,
                    "ITEM 1A. RISK FACTORS", "page two " + BODY, "ITEM 1B. UNRESOLVED", "x")
        result = _split_sections(doc, use_titles=False)
        self.assertIn("page one", result["item1a"])
        self.assertIn("page two", result["item1a"])

    def test_bare_item_link_is_not_a_heading(self):
        # Visa: cross-reference links render as a bare "Item 8" line
        doc = lines("ITEM 7. MD&A", "first " + BODY, "Item 8", "second " + BODY, "ITEM 7A. RISK", "x")
        self.assertIn("second", _split_sections(doc, use_titles=False)["item7"])

    def test_backward_item_reference_stays_in_section(self):
        # BAC: "Item 1A. Risk Factors" linked on its own line inside MD&A
        doc = lines("ITEM 7. MD&A", "first " + BODY, "Item 1A. Risk Factors", "second " + BODY,
                    "ITEM 7A. RISK", "x")
        self.assertIn("second", _split_sections(doc, use_titles=False)["item7"])

    def test_mdna_ends_at_auditor_report(self):
        # JPM: no numbered heading separates MD&A from the financial statements
        doc = lines("Item 7. MD&A", BODY * 5, "Report of Independent Registered Public Accounting Firm",
                    "FINANCIAL STATEMENTS")
        self.assertNotIn("FINANCIAL STATEMENTS", _split_sections(doc, use_titles=False)["item7"])

    def test_title_headings_for_unnumbered_filers(self):
        # Morgan Stanley / Wells Fargo annual report
        doc = lines("Financial Review", "mdna " + BODY, "Risk Factors", "risks " + BODY,
                    "Controls and Procedures", "x")
        result = _split_sections(doc, use_titles=True)
        self.assertIn("mdna", result["item7"])
        self.assertIn("risks", result["item1a"])


if __name__ == "__main__":
    unittest.main()
