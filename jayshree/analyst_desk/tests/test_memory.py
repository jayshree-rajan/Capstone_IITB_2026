import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from analystdesk import memory
from analystdesk.agent.figures import build_figures
from analystdesk.agent.graph import trim_context

REV = "RevenueFromContractWithCustomerExcludingAssessedTax/2024/0000950170-24-087843"
OPI = "OperatingIncomeLoss/2024/0000950170-24-087843"
NOTE = (f"Microsoft revenue $245,122.0M [fact: {REV}], operating income $109,433.0M [fact: {OPI}], "
        f"margin 44.64% [calc: {OPI} / {REV}].")


class TempMemory(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch.object(memory, "MEMORY_DB", Path(tmp.name) / "memory.sqlite")
        p.start()
        self.addCleanup(p.stop)


class MemoryTests(TempMemory):
    def test_figures_parsed_from_verified_tags(self):
        f = memory.figures_from_note(NOTE)["MSFT"]
        self.assertEqual(f["revenue_2024"]["value"], 245122.0)
        self.assertEqual(f["operating_income_2024"]["value"], 109433.0)
        self.assertEqual(f["margin_2024"]["value"], 44.64)

    def test_remember_note_then_recall_and_coverage(self):
        asyncio.run(memory.remember_published_note(7, "MSFT note", NOTE, ["MSFT"], "Jayshree", "desk"))
        last = asyncio.run(memory.last_note("msft"))
        self.assertEqual((last["note_id"], last["approver"]), (7, "Jayshree"))
        cov = asyncio.run(memory.coverage("desk"))
        self.assertEqual([(c["ticker"], c["notes"]) for c in cov], [("MSFT", [7])])

    def test_preferences_add_and_forget(self):
        key = asyncio.run(memory.add_preference("desk", "Always include a peer table"))
        self.assertEqual([p["text"] for p in asyncio.run(memory.preferences("desk"))],
                         ["Always include a peer table"])
        asyncio.run(memory.forget(("preferences", "desk"), key))
        self.assertEqual(asyncio.run(memory.preferences("desk")), [])

    def test_what_changed_is_computed_by_code(self):
        previous = {"note_id": 1, "title": "old", "published_at": "2026-10-25T00:00:00",
                    "figures": {"margin_2023": {"value": 41.77, "unit": "%", "text": "41.77% [calc: ...]"}}}
        current = memory.figures_from_note(NOTE)["MSFT"]
        lines = memory.what_changed("MSFT", previous, current)
        self.assertTrue(any("+2.87 pp" in l for l in lines), lines)

    def test_unchanged_when_same_year_and_value(self):
        current = memory.figures_from_note(NOTE)["MSFT"]
        previous = {"note_id": 1, "title": "t", "published_at": "2026-10-25", "figures": current}
        self.assertTrue(any("unchanged" in l for l in memory.what_changed("MSFT", previous, current)))

    def test_current_figures_round_trip_through_parser(self):
        f = build_figures(["MSFT"], [2024])
        parsed = memory.figures_from_note(" ".join(v["text"] for v in f.values()))["MSFT"]
        self.assertEqual(parsed["margin_2024"]["value"], 44.64)


class TrimTests(unittest.TestCase):
    def test_trim_keeps_head_and_starts_on_ai_message(self):
        msgs = [SystemMessage("sys"), HumanMessage("request")]
        for i in range(30):
            msgs += [AIMessage("", tool_calls=[{"name": "t", "args": {}, "id": f"c{i}"}]),
                     ToolMessage("x" * 1000, tool_call_id=f"c{i}")]
        trimmed = trim_context(msgs, max_chars=5000)
        self.assertEqual(trimmed[:2], msgs[:2])
        self.assertIn("trimmed", trimmed[2].content)
        self.assertIsInstance(trimmed[3], AIMessage)
        self.assertLess(len(trimmed), len(msgs))

    def test_small_context_untouched(self):
        msgs = [SystemMessage("s"), HumanMessage("r"), AIMessage("a")]
        self.assertEqual(trim_context(msgs), msgs)


if __name__ == "__main__":
    unittest.main()
