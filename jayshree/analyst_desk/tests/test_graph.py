import unittest

from analystdesk.agent.__main__ import _FakeToolLLM
from analystdesk.agent.graph import build_graph


class GraphShapeTests(unittest.TestCase):
    def test_nodes_gates_and_loops(self):
        g = build_graph(_FakeToolLLM(), [], None).compile().get_graph()
        nodes = set(g.nodes)
        for n in ("input_guard", "plan", "review_plan", "research", "tools", "replan",
                  "write", "check", "review_note", "publish"):
            self.assertIn(n, nodes)
        edges = {(e.source, e.target) for e in g.edges}
        self.assertIn(("tools", "replan"), edges)         # replanning
        self.assertIn(("check", "count_revision"), edges)  # self-correction loop
        self.assertIn(("review_note", "check"), edges)    # edited note is re-checked


if __name__ == "__main__":
    unittest.main()
