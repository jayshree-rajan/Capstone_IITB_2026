"""Analyst Desk — C0 Baseline Demo UI.

Run with:
    /Users/25.jayshree/Documents/Jayshree.agentic/.venv/bin/python app.py

Opens at http://127.0.0.1:7861
"""

import sys
from pathlib import Path

# Make the analystdesk package importable when running from this folder
sys.path.insert(0, str(Path(__file__).parent))

import gradio as gr

from analystdesk.baseline import run_baseline
from analystdesk.scope import ScopeError, create_request


def run(year1: int, year2: int, year3: int):
    years = sorted({int(year1), int(year2), int(year3)})
    if len(years) != 3:
        return [], [], "❌ Please select three different fiscal years.", ""

    try:
        request = create_request(["Microsoft", "Alphabet"], years)
    except ScopeError as e:
        return [], [], f"❌ Scope error: {e}", ""

    try:
        note = run_baseline(request)
    except Exception as e:
        return [], [], f"❌ EDGAR fetch failed: {e}", ""

    # Main results table
    rows = []
    for r in note.rows:
        rows.append([
            r.company,
            r.fiscal_year,
            r.period_end,
            f"${r.revenue_usd_m:,.1f}M" if r.revenue_usd_m else "—",
            f"${r.operating_income_usd_m:,.1f}M" if r.operating_income_usd_m else "—",
            f"{r.operating_margin_pct:.1f}%" if r.operating_margin_pct else "—",
        ])

    # fact_id traceability table
    fact_rows = []
    for r in note.rows:
        if r.revenue_fact_id or r.operating_income_fact_id:
            fact_rows.append([
                r.company,
                r.fiscal_year,
                r.revenue_fact_id or "—",
                r.operating_income_fact_id or "—",
            ])

    # Status summary
    warnings_text = "\n".join(f"⚠️  {w}" for w in note.warnings) if note.warnings else "✅  No warnings"
    status = (
        f"**{note.title}**\n\n"
        f"Verification: `{note.verification_pct()}%` of rows fully sourced from EDGAR\n\n"
        f"{warnings_text}\n\n"
        f"_{note.summary}_"
    )

    return rows, fact_rows, status, note.to_json()


with gr.Blocks(title="Analyst Desk — C0 Baseline") as demo:
    gr.Markdown(
        "## Analyst Desk · C0 Baseline\n"
        "**Microsoft vs Alphabet** — operating margins sourced directly from SEC EDGAR XBRL.\n"
        "No LLM calls. No agents. Every number is linked to a `fact_id` from the exact SEC filing."
    )

    with gr.Row():
        y1 = gr.Dropdown([2020, 2021, 2022, 2023, 2024], value=2022, label="Year 1")
        y2 = gr.Dropdown([2020, 2021, 2022, 2023, 2024], value=2023, label="Year 2")
        y3 = gr.Dropdown([2020, 2021, 2022, 2023, 2024], value=2024, label="Year 3")
        run_btn = gr.Button("Run Baseline", variant="primary")

    with gr.Tab("Results"):
        results_table = gr.Dataframe(
            headers=["Company", "Fiscal Year", "Period End", "Revenue", "Op. Income", "Op. Margin"],
            column_count=(6, "fixed"),
            label="Operating Margins (C0 — EDGAR XBRL, no LLM)",
            wrap=True,
        )
        status_box = gr.Markdown()

    with gr.Tab("Fact IDs (Traceability)"):
        gr.Markdown(
            "Every number links back to its exact SEC filing via a `fact_id`.\n"
            "Format: `{concept}/{fiscal_year}/{accession_number}`\n"
            "The compliance critic (Phase 6) uses these IDs to verify each number with code."
        )
        fact_table = gr.Dataframe(
            headers=["Company", "Fiscal Year", "Revenue fact_id", "Op. Income fact_id"],
            column_count=(4, "fixed"),
            label="Source citations",
            wrap=True,
        )

    with gr.Tab("Raw JSON"):
        raw_json = gr.Code(language="json", label="Full ResearchNote JSON")

    run_btn.click(
        fn=run,
        inputs=[y1, y2, y3],
        outputs=[results_table, fact_table, status_box, raw_json],
    )

    # Auto-run on load with defaults
    demo.load(
        fn=run,
        inputs=[y1, y2, y3],
        outputs=[results_table, fact_table, status_box, raw_json],
    )


if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7861, theme=gr.themes.Soft())
