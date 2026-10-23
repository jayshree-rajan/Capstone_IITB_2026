# Analyst Desk — Architecture

> Adapted from the IIT Bombay Agentic AI Capstone reference architecture (Figure 6).
> Every box maps to the lecture that teaches it. Build each layer only after understanding the lecture.

---

## System Overview

**What it does:** A team of research agents reads SEC 10-K filings, calculates financial ratios in code,
compares companies and years, and drafts a cited research note for an analyst to sign before publication.

**Who uses it:** An equity research analyst at an asset manager.

**Core rule:** Every number in the note must have a `fact_id` that links it to an XBRL filing.
The compliance critic verifies each number with code — not with an LLM.

---

## Architecture Diagram

```
                         ┌─────────────────────────────────────────────────┐
                         │            Research Analyst                      │
                         │   asks for a note · edits plan · approves note  │
                         └───────────────────┬─────────────────────────────┘
                                             │
                         ┌───────────────────▼─────────────────────────────┐
                         │         Research UI + FastAPI          [L16]     │
                         │      plan view · streaming draft · auth          │
                         └──────┬──────────────────────────────────┬────────┘
                                │                                  │
               ┌────────────────▼──────┐              ┌───────────▼───────────────┐
               │    Input Guardrails   │[L15]         │   Output Guardrails       │[L15]
               │  scope check ·        │              │  every number has fact ID  │
               │  web text as data ·   │              │  no advice language ·      │
               │  Llama Guard 4 ·      │              │  grounding check ·         │
               │  Prompt Guard 2       │              │  Llama Guard 4             │
               └────────────────┬──────┘              └───────────────────────────┘
                                │
     ┌──────────────────────────▼──────────────────────────────────────────────┐
     │              Research Lead + Planner (LangGraph)             [L8][L10]  │
     │   sub-questions + outline · parallel fan-out · critic loop (max 2)      │
     │   token budget · SQLite checkpointer · stop and resume                  │
     └───┬──────────────┬──────────────┬──────────────┬──────────────┬─────────┘
         │              │              │              │              │
    ┌────▼────┐   ┌─────▼────┐  ┌─────▼─────┐  ┌────▼──────┐  ┌───▼──────┐
    │Researcher│  │Researcher│  │   Quant    │  │ Risk-Diff │  │Compliance│  [L11]
    │ MSFT     │  │ GOOGL    │  │   Agent    │  │   Agent   │  │  Critic  │
    │(parallel)│  │(parallel)│  │XBRL→ratios │  │Item 1A:   │  │numbers + │
    │filings-kb│  │filings-kb│  │in sandbox  │  │this vs    │  │claims    │
    │hybrid    │  │hybrid    │  │            │  │last year  │  │verified  │
    │search    │  │search    │  │            │  │           │  │          │
    └────┬─────┘  └──────────┘  └─────┬─────┘  └─────┬─────┘  └────┬─────┘
         │                            │               │              │
         └────────────────────────────┴───────────────┴──────────────┘
                                             │
                                      ┌──────▼──────┐
                                      │   Writer    │[L11]
                                      │ drafts note │
                                      │ publishes   │
                                      │ only after  │
                                      │ approval    │
                                      └──────┬──────┘
                                             │
                                      ┌──────▼──────────────────┐
                                      │    Approval Gate        │[L14]
                                      │ analyst signs exact note│
                                      │ approve · edit · reject │
                                      │ edit invalidates prior  │
                                      │ approval                │
                                      └─────────────────────────┘

     ══════════════════════════════════════════════════════════════════
     Hooks (wrap every tool call)                                [L14]
       pre-tool:  permission check · token budget enforcement
       post-tool: trace log · EDGAR rate-limit guard
     ══════════════════════════════════════════════════════════════════

     ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
     │  filings-kb  │  │    edgar     │  │python-sandbox│  │research-porta│  [L7]
     │  MCP server  │  │  MCP server  │  │  MCP server  │  │  MCP server  │
     │ hybrid search│  │ list filings │  │ no network · │  │ past notes · │
     │   rerank     │  │ get section  │  │ time limit · │  │ publish_note │
     │              │  │ XBRL facts   │  │ pandas only  │  │ (write-only) │
     │              │  │ (all cached) │  │              │  │              │
     └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
            │                 │                  │                  │
     ┌──────▼───────┐  ┌──────▼───────┐  ┌──────▼───────┐  ┌──────▼───────┐
     │ Hybrid Index │  │ EDGAR Cache  │  │   Sandbox    │  │  Portal DB   │
     │ BM25+vectors │  │ filings JSON │  │  container   │  │  published   │
     │ 10-K items + │  │ companyfacts │  │              │  │  notes       │
     │ tables [L5-6]│  │ JSON         │  │              │  │              │
     └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘

     ┌──────────────────────────────────────────────────────────────────────┐
     │                    Memory (LangGraph store)              [L9]        │
     │  analyst preferences · coverage list · past notes ("what changed?") │
     │  Reflexion lessons (dev cases only, human-reviewed)      [L12]       │
     └──────────────────────────────────────────────────────────────────────┘

     ══════════════ Operate and Measure ═══════════════════════════════════
     ┌──────────┐  ┌───────────────────┐  ┌───────────────┐  ┌──────────┐
     │  Traces  │  │  Metrics+Grafana  │  │ Eval Harness  │  │   CI/CD  │
     │ per note │  │ verified numbers %│  │ XBRL truth ·  │  │ eval gate│
     │ Langfuse │  │ cost per note ·   │  │ FinanceBench ·│  │ Docker   │
     │ LangSmith│  │ critic rejections │  │ 60+ cases ·   │  │ Compose  │
     │  [L13]   │  │ EDGAR cache hits ·│  │ judge+kappa   │  │ GitHub   │
     │          │  │ latency · alerts  │  │  [L13][L15]   │  │ Actions  │
     │          │  │ alert <98% verif  │  │               │  │  [L16]   │
     └──────────┘  │   [L13][L16]      │  └───────────────┘  └──────────┘
                   └───────────────────┘

     ┌──────────────────────────────────────────────────────────────────────┐
     │               Ingestion (offline, run once per filing)   [L4][L5]   │
     │  10-K HTML → split by Item (1A, 7) · keep tables as tables ·        │
     │  attach metadata (company, year, item, passage_id) · embed + index  │
     └──────────────────────────────────────────────────────────────────────┘
```

---

## Three rules that hold throughout (from the guide)

1. **The model proposes; code and people decide.**
   Arithmetic, eligibility rules, money and permissions live in plain tested code.
   The model selects the applicable rule and explains the result.

2. **Treat outside text as data, never as instructions.**
   Retrieved passages, tool outputs, web articles and filing text may contain attacks.

3. **A person approves each exact change to the outside world.**
   Approval is tied to the exact payload. Any subsequent change makes that approval invalid.

---

## Data flow for one request

> "Compare Microsoft's and Alphabet's operating margins over the last three fiscal years,
>  and tell me what changed in their risk factors."

1. Analyst enters request in the **Research UI**. Input guardrails check scope and tag any web text as data.
2. **Research lead** writes sub-questions and an outline (the plan object). Analyst can edit via an edit interrupt. Note receives a token budget.
3. **Filings researchers** work in parallel, one per company. Hybrid search + reranking over 10-K sections (Item 7 MD&A, Item 1A). Return findings with passage IDs.
4. **Quant agent** retrieves XBRL facts (revenue, operating income) via the edgar server and calculates margins **in the python-sandbox**. Each number carries a `fact_id` = `concept/fiscal_year/accession_number`.
5. **Risk-diff agent** compares this year's Item 1A with last year's. Cites risks added, removed or materially changed.
6. **Writer** drafts the note. **Compliance critic** checks each number against its fact_id (code) and each claim against its passage (judge model). Can request at most 2 revision rounds.
7. **Approval gate**: analyst signs the exact note. Only then does `publish_note` write it to the research portal.
8. **Memory** stores the note in coverage history for "what changed since our last note?" queries.

---

## Experiment configurations (E1–E4)

| ID | Question | Compare |
|----|----------|---------|
| E1 | Does the agent team justify its cost? | C0 fixed pipeline vs C1 single agent vs C2 team |
| E2 | Do defences work? | Guardrails off vs on (attack success + false block rate) |
| E3 | Did the Reflexion lessons help? | Held-out before vs after playbook |
| E4 | Signature ablation | Critic loop on vs off · table-aware parsing vs plain text |

---

## Key technology per layer

| Layer | Technology |
|-------|-----------|
| Models | OpenRouter — `openai/gpt-4.1-mini` (specialists), stronger model (supervisor) |
| Judge | Different family — `google/gemini-3.1-flash-lite` or `meta-llama/llama-3.3-70b-instruct` |
| Orchestration | LangGraph + SQLite checkpointer + `interrupt()` + `Command(resume=...)` |
| Long-term memory | LangGraph store (SQLite-backed) |
| Tools | FastMCP servers + `langchain-mcp-adapters` |
| Vector store | Chroma or Qdrant |
| BM25 | `rank-bm25` |
| Reranker | Cross-encoder (sentence-transformers) |
| Embeddings | Local MiniLM or bge on CPU |
| Guardrails | Llama Guard 4 (`meta-llama/llama-guard-4-12b`) + Llama Prompt Guard 2 on CPU |
| Traces | LangSmith or self-hosted Langfuse |
| Metrics | `prometheus_client` → Prometheus → Grafana |
| Eval | pytest + JSONL runner + RAGAS + Cohen's kappa |
| UI | Gradio or Chainlit |
| Serving | FastAPI with streaming |
| Deployment | Docker Compose + GitHub Actions |

---

## Phase 1 — C0 Baseline Data Flow

How the EDGAR extraction works end to end (no LLM, no agents).

### Where the data comes from

SEC EDGAR provides structured financial data (XBRL) as free public JSON — one file per company,
no API key needed. The CIK (Central Index Key) is the company identifier.

```
https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json   ← Microsoft (5.2 MB)
https://data.sec.gov/api/xbrl/companyfacts/CIK0001652044.json   ← Alphabet  (3.3 MB)
```

### Shape of the EDGAR response

```
companyfacts.json
├── cik           → 789019
├── entityName    → "Microsoft Corporation"
└── facts
    ├── dei       → company metadata (fiscal year end date, etc.)
    └── us-gaap   → 562 financial concepts (revenue, income, ratios...)
        ├── OperatingIncomeLoss
        │   └── units
        │       └── USD  → [ array of 278 entries — annual + quarterly, back to 2008 ]
        ├── RevenueFromContractWithCustomerExcludingAssessedTax
        │   └── units
        │       └── USD  → [ array of entries ]
        └── ... 560 more concepts
```

Each entry in the array:
```json
{
  "start":  "2023-07-01",
  "end":    "2024-06-30",
  "val":    109433000000,
  "accn":   "0001193125-26-323660",
  "fy":     2024,
  "fp":     "FY",
  "form":   "10-K",
  "filed":  "2024-07-30"
}
```

### How we filter from 278 entries down to 1

```
278 entries for OperatingIncomeLoss (Microsoft)
     │
     ├── filter fp == "FY"         → drop Q1/Q2/Q3 quarterly entries
     ├── filter form == "10-K"     → keep only annual reports
     └── filter end[:4] == "2024"  → period must end in the requested year
                                       (NOT EDGAR's fy field — see note below)
     │
     ▼
1 entry: val=109433000000, end="2024-06-30", accn="0001193125-26-323660"
```

> **Why end[:4] and not EDGAR's fy field?**
> EDGAR's `fy` label can be misaligned for companies whose fiscal year doesn't end in December.
> Microsoft's FY2022 ends June 30, 2022 — but EDGAR's `fy` field was pointing to 2020 data.
> Filtering by the `end` date year is reliable for both Microsoft (June-end) and
> Alphabet (December-end). This is Signature Challenge A: fiscal year alignment.

### C0 pipeline flow

```
SEC EDGAR API (one GET per company, first run only)
     │
     ▼
data/edgar_cache/{cik}.json          ← flat file cache, read on every subsequent run
     │
     │  fetch_company_facts(cik)
     ▼
get_annual_fact(cik, concept, year)
     │  filter: fp=FY, form=10-K, end[:4]==year
     │  pick latest if multiple (amended 10-K/A)
     ▼
{ val, end, accn, filed, fact_id }
     │
     │  _to_millions(val)            ← divide by 1,000,000 — in Python, never in LLM
     │  _operating_margin(rev, op)   ← op / rev × 100 — in Python, never in LLM
     ▼
MetricRow(company, year, revenue_usd_m, op_income_usd_m, margin, fact_id, ...)
     │
     │  one row per company per year (6 rows: 2 companies × 3 years)
     ▼
ResearchNote(title, status="draft", rows, verification_pct, warnings)
     │
     ▼
JSON output — every number has a fact_id traceable to an exact SEC filing
```

### Where data is stored in Phase 1

```
analyst_desk/
└── data/
    └── edgar_cache/
        ├── 0000789019.json    ← Microsoft (5.2 MB, flat JSON file)
        └── 0001652044.json    ← Alphabet  (3.3 MB, flat JSON file)
```

**This is not a database.** It is flat file caching — the full EDGAR response saved to disk.
No schema, no migrations, no ORM. A database (SQLite) is introduced in Phase 4 for the
LangGraph checkpointer and in Phase 5 for the long-term memory store.

### fact_id — the traceability link

Every number in the note carries a `fact_id`:
```
format:   {concept}/{fiscal_year}/{accession_number}
example:  OperatingIncomeLoss/2024/0001193125-26-323660
```

The accession number (`0001193125-26-323660`) is the SEC's unique identifier for the exact
filing. Given this ID you can look up the original 10-K on EDGAR and verify the number
by hand. The compliance critic (Phase 6) uses this link to verify each number with code.

### C0 actual output (real EDGAR data, Oct 2026)

| Company | FY | Revenue ($M) | Op. Income ($M) | Margin | Period End |
|---------|----|-------------|----------------|--------|-----------|
| Microsoft | 2022 | 198,270 | 83,383 | 42.1% | 2022-06-30 |
| Microsoft | 2023 | 211,915 | 88,523 | 41.8% | 2023-06-30 |
| Microsoft | 2024 | 245,122 | 109,433 | 44.6% | 2024-06-30 |
| Alphabet | 2022 | 282,836 | 74,842 | 26.5% | 2022-12-31 |
| Alphabet | 2023 | 307,394 | 84,293 | 27.4% | 2023-12-31 |
| Alphabet | 2024 | 350,018 | 112,390 | 32.1% | 2024-12-31 |

`verification_pct: 100.0` — every number has a fact_id, zero warnings.
