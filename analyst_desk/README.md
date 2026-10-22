# AnalystDesk — Part 1: baseline scope

This is the first incremental part of the approved baseline plan. It accepts
Microsoft and Alphabet, exactly three distinct four-digit fiscal-year labels,
and the fixed metrics revenue, operating income and operating margin.

## Run on Windows in VS Code

Extract this project, open the `analyst_desk` folder in VS Code, and run:

```powershell
python -m analystdesk --years 2022 2023 2024
```

Python 3.10 or newer is sufficient. There are no external packages, API keys,
network requests or installation steps. The years above are an example, not
an automatic selection of the latest available fiscal years.

Company names or tickers are accepted:

```powershell
python -m analystdesk --companies MSFT GOOGL --years 2022 2023 2024
```

An unsupported company or invalid year selection prints an error and exits
with status 2. Success prints JSON with status `scope_validated`.

## Files and responsibilities

- `analystdesk/scope.py`: company configuration, immutable request, validation.
- `analystdesk/__main__.py`: command-line input and JSON output.
- `analystdesk/__init__.py`: package marker.
- `tests/test_scope.py`: scope boundary checks using Python's unittest.

## Test

```powershell
python -m unittest discover -s tests -v
```

## Current boundary and next integration

Scope validation does not prove that SEC filings exist for the requested years.
No financial facts or actual fiscal-period dates are generated here. Later
ingestion must resolve and verify these from filings. There is no data fetching,
arithmetic, LLM call, research note, UI, memory or publication in this part.

The next approved part will define the expected note output. Subsequent parts
will consume `ResearchRequest` rather than rebuilding input validation. Preserve
this folder as the project base and add future modules to `analystdesk`.

The eventual baseline is C0: a fixed workflow. It is an intermediate project
milestone, not the complete capstone minimum bar.
