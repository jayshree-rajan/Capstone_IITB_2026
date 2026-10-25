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


if __name__ == "__main__":
    unittest.main()
