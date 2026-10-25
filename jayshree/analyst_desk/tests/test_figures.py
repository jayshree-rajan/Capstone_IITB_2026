import unittest

from analystdesk.agent.checks import check_note
from analystdesk.agent.figures import build_figures, render


class FiguresTests(unittest.TestCase):
    def test_rendered_figures_always_pass_the_checker(self):
        figures = build_figures(["MSFT", "GOOGL", "JPM"], [2022, 2023, 2024])
        body = " ".join(f"{{{k}}}" for k in figures if not k.endswith("period_end"))
        rendered, issues = render(body, figures)
        self.assertEqual(issues, [])
        r = check_note(rendered)
        self.assertTrue(r.ok, r.issues[:3])
        self.assertEqual(r.verified_share, 100.0)

    def test_bank_uses_pretax_figures(self):
        f = build_figures(["JPM"], [2024])
        self.assertIn("JPM_2024_pretax_income", f)
        self.assertNotIn("JPM_2024_operating_income", f)

    def test_unknown_placeholder_reported(self):
        _, issues = render("{MSFT_2019_revenue}", build_figures(["MSFT"], [2024]))
        self.assertEqual(issues, ["Unknown placeholder {MSFT_2019_revenue}."])


if __name__ == "__main__":
    unittest.main()
