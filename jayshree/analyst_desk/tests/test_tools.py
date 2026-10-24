"""One or more tests per MCP tool (L7 minimum bar), called through a real
MCP client connected in memory — the same protocol path an agent uses.
"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client

from analystdesk import approval
from analystdesk.servers import AGENT_TOOLS, SERVERS, WRITE_TOOLS
from analystdesk.servers import edgar_server, filings_kb, portal, sandbox


def call(server, tool, **args):
    async def go():
        async with Client(server.mcp) as client:
            return (await client.call_tool(tool, args, raise_on_error=False)).structured_content
    return asyncio.run(go())


def list_tools(server):
    async def go():
        async with Client(server.mcp) as client:
            return await client.list_tools()
    return asyncio.run(go())


# ── filings-kb ────────────────────────────────────────────────────────────────

PASSAGES = [
    {"passage_id": "MSFT_2024_item7_020", "company": "MSFT", "fiscal_year": 2024, "item": "item7",
     "kind": "table", "accession": "0000950170-24-087843", "text": "Intelligent Cloud | $105,362"},
    {"passage_id": "MSFT_2024_item1a_007", "company": "MSFT", "fiscal_year": 2024, "item": "item1a",
     "kind": "text", "accession": "0000950170-24-087843", "text": "We are investing in AI"},
]


class FakeRetriever:
    """Stands in for HybridRetriever: these tests check the tool contract,
    retrieval quality is measured by scripts/eval_retrieval.py."""
    pid_to_passage = {p["passage_id"]: p for p in PASSAGES}

    def __init__(self):
        self.calls = []

    def search(self, query, top_k=5, company=None, year=None, item=None, **_):
        self.calls.append(dict(query=query, company=company, year=year, item=item))
        return [{**p, "score": 1.0} for p in PASSAGES
                if (not company or p["company"] == company)
                and (not year or p["fiscal_year"] == year)
                and (not item or p["item"] == item)][:top_k]


class FilingsKbTests(unittest.TestCase):
    def setUp(self):
        self.retriever = FakeRetriever()
        filings_kb.set_retriever(self.retriever)
        self.addCleanup(filings_kb.set_retriever, None)

    def test_search_filings_with_filters(self):
        r = call(filings_kb, "search_filings", query="cloud revenue", company="msft",
                 fiscal_year=2024, item="item7")
        self.assertTrue(r["ok"])
        self.assertEqual([p["passage_id"] for p in r["passages"]], ["MSFT_2024_item7_020"])
        self.assertEqual(self.retriever.calls[-1]["company"], "MSFT")

    def test_search_filings_decomposes_without_filters(self):
        call(filings_kb, "search_filings", query="Microsoft AI risks in 2024")
        self.assertEqual((self.retriever.calls[-1]["company"], self.retriever.calls[-1]["year"]),
                         ("MSFT", 2024))

    def test_search_filings_out_of_coverage_is_an_error_result(self):
        r = call(filings_kb, "search_filings", query="x", company="TSLA")
        self.assertEqual((r["ok"], r["error"]["code"]), (False, "NOT_IN_COVERAGE"))
        r = call(filings_kb, "search_filings", query="x", fiscal_year=2019)
        self.assertEqual(r["error"]["code"], "NOT_IN_COVERAGE")

    def test_get_passage(self):
        r = call(filings_kb, "get_passage", passage_id="MSFT_2024_item1a_007")
        self.assertEqual(r["passage"]["text"], "We are investing in AI")
        r = call(filings_kb, "get_passage", passage_id="NOPE_1")
        self.assertEqual(r["error"]["code"], "NOT_FOUND")


# ── edgar (reads the committed EDGAR cache) ──────────────────────────────────

class EdgarTests(unittest.TestCase):
    def test_list_filings(self):
        r = call(edgar_server, "list_filings", ticker="MSFT")
        years = {f["fiscal_year"] for f in r["filings"] if f["form"] == "10-K"}
        self.assertTrue({2022, 2023, 2024} <= years)

    def test_get_financials(self):
        r = call(edgar_server, "get_financials", ticker="MSFT", fiscal_year=2024)
        self.assertEqual(r["margin_basis"], "operating")
        self.assertEqual(r["revenue"]["value_usd"], 245_122_000_000)
        self.assertEqual(r["operating_income"]["fact_id"],
                         "OperatingIncomeLoss/2024/0000950170-24-087843")

    def test_get_financials_bank_and_restatement(self):
        r = call(edgar_server, "get_financials", ticker="BAC", fiscal_year=2024)
        self.assertEqual(r["margin_basis"], "pretax")
        self.assertEqual(r["revenue"]["value_usd"], 101_887_000_000)   # as reported
        self.assertEqual(r["revenue"]["restated_value_usd"], 105_856_000_000)
        self.assertTrue(r["warnings"])

    def test_get_financials_out_of_coverage(self):
        r = call(edgar_server, "get_financials", ticker="MSFT", fiscal_year=2019)
        self.assertEqual(r["error"]["code"], "NOT_IN_COVERAGE")

    def test_get_fact_verifies_exact_filing(self):
        r = call(edgar_server, "get_fact", fact_id="OperatingIncomeLoss/2024/0000950170-24-087843")
        self.assertEqual((r["ticker"], r["fact"]["value_usd"]), ("MSFT", 109_433_000_000))
        r = call(edgar_server, "get_fact", fact_id="OperatingIncomeLoss/2024/0000000000-00-000000")
        self.assertEqual(r["error"]["code"], "NOT_FOUND")
        r = call(edgar_server, "get_fact", fact_id="operating income 2024")
        self.assertEqual(r["error"]["code"], "BAD_INPUT")


# ── python-sandbox (local mode; Docker mode tested when available) ───────────

class SandboxTests(unittest.TestCase):
    mode = "local"

    def setUp(self):
        patcher = patch.dict(os.environ, {"SANDBOX_MODE": self.mode})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_run_python_calculates(self):
        r = call(sandbox, "run_python", code="result = round(data['op'] / data['rev'] * 100, 2)",
                 data={"op": 109433, "rev": 245122})
        self.assertEqual((r["ok"], r["result"], r["sandbox_mode"]), (True, 44.64, self.mode))

    def test_run_python_blocks_network_and_files(self):
        for code in ("import socket", "result = pd.read_csv('http://example.com/a.csv')",
                     "pd.DataFrame({'a': [1]}).to_csv('x.csv')"):
            with self.subTest(code=code):
                r = call(sandbox, "run_python", code=code)
                self.assertEqual(r["error"]["code"], "BLOCKED")

    def test_run_python_reports_errors_and_timeouts(self):
        self.assertEqual(call(sandbox, "run_python", code="result = 1/0")["error"]["code"], "EXEC_ERROR")
        r = call(sandbox, "run_python", code="while True: pass", timeout_s=2)
        self.assertEqual(r["error"]["code"], "TIMEOUT")

    def test_run_python_sees_no_secrets(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-secret"}):
            r = call(sandbox, "run_python", code="result = str(data)", data=None)
        self.assertNotIn("sk-secret", str(r))


@unittest.skipUnless(sandbox.docker_available(), "Docker or the analystdesk-sandbox image is not available")
class DockerSandboxTests(SandboxTests):
    mode = "docker"


# ── research-portal ──────────────────────────────────────────────────────────

class PortalTests(unittest.TestCase):
    NOTE = dict(title="MSFT vs GOOGL margins", body="MSFT 44.64% [fact ...]", tickers=["MSFT", "GOOGL"])

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {"PORTAL_DB": str(Path(tmp.name) / "portal.db"),
                                      "APPROVAL_SECRET": "test-secret"})
        env.start()
        self.addCleanup(env.stop)

    def _token(self, **note):
        n = {**self.NOTE, **note}
        return approval.sign(n["title"], n["body"], n["tickers"], approver="analyst")

    def test_publish_note_with_valid_approval(self):
        r = call(portal, "publish_note", **self.NOTE, approval_token=self._token())
        self.assertTrue(r["ok"], r)
        again = call(portal, "publish_note", **self.NOTE, approval_token=self._token())
        self.assertEqual(again["error"]["code"], "ALREADY_EXISTS")

    def test_edit_after_approval_invalidates_it(self):
        token = self._token()
        edited = {**self.NOTE, "body": self.NOTE["body"] + " Buy MSFT."}
        r = call(portal, "publish_note", **edited, approval_token=token)
        self.assertEqual(r["error"]["code"], "APPROVAL_INVALID")
        self.assertIn("approved again", r["error"]["message"])

    def test_forged_token_is_refused(self):
        token = self._token()
        payload, _ = token.split(".")
        r = call(portal, "publish_note", **self.NOTE, approval_token=payload + ".forged")
        self.assertEqual(r["error"]["code"], "APPROVAL_INVALID")
        with patch.dict(os.environ, {"APPROVAL_SECRET": "attacker-guess"}):
            forged = self._token()
        r = call(portal, "publish_note", **self.NOTE, approval_token=forged)
        self.assertEqual(r["error"]["code"], "APPROVAL_INVALID")

    def test_get_past_notes(self):
        call(portal, "publish_note", **self.NOTE, approval_token=self._token())
        r = call(portal, "get_past_notes", ticker="googl")
        self.assertEqual([n["title"] for n in r["notes"]], [self.NOTE["title"]])
        self.assertEqual(call(portal, "get_past_notes", ticker="AAPL")["notes"], [])


# ── schemas and permissions ──────────────────────────────────────────────────

class RegistryTests(unittest.TestCase):
    MODULES = {"filings-kb": filings_kb, "edgar": edgar_server,
               "python-sandbox": sandbox, "research-portal": portal}

    def test_every_tool_is_typed_and_marked_read_or_write(self):
        seen = set()
        for name, module in self.MODULES.items():
            for tool in list_tools(module):
                seen.add(tool.name)
                with self.subTest(tool=tool.name):
                    self.assertTrue(tool.outputSchema, "output must be typed")
                    self.assertTrue(tool.description)
                    read_only = tool.annotations.readOnlyHint
                    self.assertEqual(read_only, tool.name not in WRITE_TOOLS)
        self.assertEqual(len(seen), 8)
        self.assertEqual(set(self.MODULES), set(SERVERS))

    def test_only_publisher_can_write(self):
        for agent, tools in AGENT_TOOLS.items():
            if agent != "publisher":
                self.assertFalse(tools & WRITE_TOOLS, agent)
        self.assertEqual(AGENT_TOOLS["publisher"], WRITE_TOOLS)


if __name__ == "__main__":
    unittest.main()
