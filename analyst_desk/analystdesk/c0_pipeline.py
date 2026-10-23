"""C0 fixed RAG pipeline — retrieval + EDGAR facts + LLM with fixed prompt.

This is the complete C0 experiment configuration. It is a fixed workflow:
  1. Retrieve top-k passages from the hybrid index (no agent decides what to retrieve)
  2. Fetch XBRL numbers from EDGAR for the companies mentioned
  3. Fill a fixed prompt template with the retrieved content + numbers
  4. Call the LLM once with that prompt (no tool loop, no planning)
  5. Return the answer with passage citations and fact_ids

C0 uses OpenRouter (free models) via the OpenAI-compatible API.
The model is fixed in config — not chosen by an agent.
"""

from __future__ import annotations

import os
from pathlib import Path

from .edgar import get_financials
from .scope import COMPANIES

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
C0_MODEL = os.getenv("C0_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct:free")

SYSTEM_PROMPT = """You are a financial research assistant.
Answer the analyst's question using ONLY the provided context (retrieved passages and EDGAR numbers).
Do not use prior knowledge. If the context does not answer the question, say so.
Always cite passage IDs in square brackets like [MSFT_2024_item7_003].
Do not recommend buying or selling any security."""

ANSWER_TEMPLATE = """
RETRIEVED PASSAGES:
{passages}

EDGAR VERIFIED NUMBERS (source: SEC XBRL filings):
{numbers}

ANALYST QUESTION:
{question}

Answer concisely with citations. Every number must cite its fact_id.
"""


def _format_passages(hits: list[dict]) -> str:
    parts = []
    for h in hits:
        pid = h["passage_id"]
        company = h["company"]
        year = h["fiscal_year"]
        item = h.get("item_label", h["item"])
        score = h.get("score", 0)
        parts.append(
            f"[{pid}] {company} FY{year} — {item} (relevance: {score:.2f})\n"
            f"{h['text'][:800]}"
        )
    return "\n\n---\n\n".join(parts) if parts else "(no passages retrieved)"


def _format_numbers(companies_in_query: list[str], years: list[int]) -> str:
    lines = []
    company_map = {c.ticker: c for c in COMPANIES}
    for ticker in companies_in_query:
        company = company_map.get(ticker)
        if not company:
            continue
        for year in years:
            data = get_financials(company.cik, year)
            rev = data["revenue"]
            op = data["operating_income"]
            if rev:
                rev_m = round(rev["val"] / 1_000_000, 1)
                lines.append(
                    f"{ticker} FY{year} Revenue: ${rev_m:,.1f}M "
                    f"[fact_id: {rev['fact_id']}]"
                )
            if op:
                op_m = round(op["val"] / 1_000_000, 1)
                margin = round(op_m / (rev["val"] / 1_000_000) * 100, 2) if rev else None
                lines.append(
                    f"{ticker} FY{year} Operating Income: ${op_m:,.1f}M"
                    + (f" → Margin: {margin}%" if margin else "")
                    + f" [fact_id: {op['fact_id']}]"
                )
    return "\n".join(lines) if lines else "(no EDGAR numbers fetched)"


def answer(
    question: str,
    retriever,
    years: list[int] | None = None,
    top_k: int = 5,
) -> dict:
    """Run the C0 fixed pipeline. Returns answer dict with citations."""
    years = years or [2022, 2023, 2024]

    # Step 1 — fixed retrieval (no agent decides what to retrieve)
    hits = retriever.search(question, top_k=top_k)

    # Step 2 — fixed EDGAR fetch for all companies and years in scope
    numbers_text = _format_numbers(["MSFT", "GOOGL"], years)

    # Step 3 — fill fixed prompt template
    user_content = ANSWER_TEMPLATE.format(
        passages=_format_passages(hits),
        numbers=numbers_text,
        question=question,
    )

    # Step 4 — single LLM call, no tool loop
    answer_text = "(LLM not configured — set OPENROUTER_API_KEY)"
    if OPENROUTER_API_KEY:
        try:
            from openai import OpenAI
            client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=OPENROUTER_API_KEY,
            )
            response = client.chat.completions.create(
                model=C0_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=800,
                temperature=0.1,
            )
            answer_text = response.choices[0].message.content or "(empty response)"
        except Exception as e:
            answer_text = f"(LLM error: {e})"

    return {
        "answer": answer_text,
        "passages": hits,
        "years": years,
        "model": C0_MODEL,
        "pipeline": "C0-fixed-RAG",
    }
