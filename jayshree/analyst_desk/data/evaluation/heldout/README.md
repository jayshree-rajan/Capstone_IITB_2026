# Held-out cases — to be written by Role 5, not by whoever tunes the agent

Brief: "The person tuning the agent must not write the held-out cases." Put
40+ cases here as `*.jsonl`, same format as `../dev_cases.jsonl`
(types: numeric, comparison, risk_change, refusal). Write each expected
outcome BEFORE running the agent. Keep variants of one scenario together
(all in held-out, or all in dev). No agent tool can read this folder.

For numeric expectations, compute values from XBRL with code, e.g.
`python -c "from analystdesk.agent.figures import build_figures; print(build_figures(['MSFT'],[2023,2024])['MSFT_2024_margin']['text'])"`

Run: `python scripts/run_eval.py --configs c0 c1 c2 --split heldout`
