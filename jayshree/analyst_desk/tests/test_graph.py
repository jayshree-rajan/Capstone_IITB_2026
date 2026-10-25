import unittest

from analystdesk.agent.runtime import _NoModel
from analystdesk.agent.graph import build_graph


class GraphShapeTests(unittest.TestCase):
    def test_nodes_gates_and_loops(self):
        g = build_graph(_NoModel(), [], None).compile().get_graph()
        nodes = set(g.nodes)
        for n in ("input_guard", "plan", "review_plan", "research", "tools", "replan",
                  "write", "check", "review_note", "publish"):
            self.assertIn(n, nodes)
        edges = {(e.source, e.target) for e in g.edges}
        self.assertIn(("tools", "replan"), edges)         # replanning
        self.assertIn(("check", "count_revision"), edges)  # self-correction loop
        self.assertIn(("review_note", "check"), edges)    # edited note is re-checked



class ToolJsonTests(unittest.TestCase):
    def test_parses_adapter_content_blocks(self):
        from analystdesk.agent.graph import tool_json
        inner = '{"ok":true,"note_id":1}'
        self.assertEqual(tool_json([{"type": "text", "text": inner}])["note_id"], 1)
        self.assertEqual(tool_json('[{"type": "text", "text": "{\\"ok\\":true,\\"note_id\\":2}"}]')["note_id"], 2)
        self.assertEqual(tool_json('{"ok": false}'), {"ok": False})
        self.assertEqual(tool_json("not json"), {})



class TeamGraphTests(unittest.TestCase):
    def test_team_shape(self):
        from analystdesk.agent.team import build_team_graph
        g = build_team_graph(_NoModel(), [], None).compile().get_graph()
        for n in ("researcher", "quant", "risk_diff", "gather", "replan", "write", "check", "publish"):
            self.assertIn(n, set(g.nodes))
        edges = {(e.source, e.target) for e in g.edges}
        for a in ("researcher", "quant", "risk_diff"):
            self.assertIn(("review_plan", a), edges)   # parallel fan-out
            self.assertIn((a, "gather"), edges)
        self.assertIn(("gather", "replan"), edges)


class HooksTests(unittest.TestCase):
    def test_permission_budget_and_logging(self):
        import asyncio
        from analystdesk.agent.hooks import Hooks
        h = Hooks(max_calls_per_agent=2)
        self.assertEqual(asyncio.run(h.pre("researcher", "run_python"))["error"]["code"], "PERMISSION_DENIED")
        self.assertIsNone(asyncio.run(h.pre("researcher", "search_filings", "researcher/MSFT")))
        self.assertIsNone(asyncio.run(h.pre("researcher", "search_filings", "researcher/MSFT")))
        self.assertEqual(asyncio.run(h.pre("researcher", "search_filings", "researcher/MSFT"))["error"]["code"],
                         "BUDGET_EXCEEDED")
        self.assertIsNone(asyncio.run(h.pre("researcher", "search_filings", "researcher/GOOGL")))
        ev = h.post("quant", "get_fact", {}, '{"ok": true}', 3.2)
        self.assertTrue(ev["ok"])
        self.assertFalse(asyncio.run(h.pre("publisher", "publish_note")))   # only the publisher may write


if __name__ == "__main__":
    unittest.main()
