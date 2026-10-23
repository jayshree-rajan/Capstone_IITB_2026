"""10-K ingestion — download, parse, and chunk SEC filings.

Downloads 10-K HTML from EDGAR, splits by Item (1A Risk Factors, 7 MD&A),
keeps tables as structured text, and saves passages with metadata.

Each passage gets a unique passage_id used for citations in answers.
passage_id format: {ticker}_{year}_{item}_{seq}
  e.g. MSFT_2024_item7_003
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup

from .scope import COMPANIES

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
FILING_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}"
CACHE_DIR = Path(__file__).parent.parent / "data" / "edgar_cache"
PASSAGES_DIR = Path(__file__).parent.parent / "data" / "passages"
USER_AGENT = "AnalystDesk IITBombay contact@example.com"

# Items to extract — key for the Analyst Desk use case
TARGET_ITEMS = {
    "item1a": "Risk Factors",
    "item7":  "Management Discussion and Analysis",
}

ITEM_PATTERNS = [
    (r"item\s*1a[\.\s]", "item1a"),
    (r"item\s*7[\.\s]",  "item7"),
    (r"item\s*7a[\.\s]", "item7a"),   # boundary marker (stop item7 here)
    (r"item\s*8[\.\s]",  "item8"),    # boundary marker (stop item7a here)
    (r"item\s*2[\.\s]",  "item2"),    # boundary marker (stop item1a here)
]


def _pad_cik(cik: str) -> str:
    return cik.lstrip("0").zfill(10)


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _get_10k_url(cik: str, fiscal_year: int) -> str | None:
    """Find the primary 10-K document URL for a given company and fiscal year."""
    padded = _pad_cik(cik)
    cache_file = CACHE_DIR / f"submissions_{padded}.json"
    if cache_file.exists():
        data = json.loads(cache_file.read_text())
    else:
        data = json.loads(_fetch(SUBMISSIONS_URL.format(cik=padded)))
        cache_file.write_text(json.dumps(data))
        time.sleep(0.2)

    def _search_filings(filings: dict) -> str | None:
        forms = filings.get("form", [])
        dates = filings.get("filingDate", [])
        accessions = filings.get("accessionNumber", [])
        docs = filings.get("primaryDocument", [])
        for form, date, accn, doc in zip(forms, dates, accessions, docs):
            if form != "10-K":
                continue
            year = int(date[:4])
            if year == fiscal_year or (year == fiscal_year + 1 and date[5:7] <= "03"):
                accn_clean = accn.replace("-", "")
                return FILING_URL.format(
                    cik=cik.lstrip("0"), accession=accn_clean, doc=doc
                )
        return None

    url = _search_filings(data.get("filings", {}).get("recent", {}))
    if url:
        return url

    # Recent filings may not go far enough back — check older filings pages
    for older in data.get("filings", {}).get("files", []):
        name = older.get("name", "")
        older_cache = CACHE_DIR / name
        if older_cache.exists():
            older_data = json.loads(older_cache.read_text())
        else:
            older_url = f"https://data.sec.gov/submissions/{name}"
            try:
                older_data = json.loads(_fetch(older_url))
                older_cache.write_text(json.dumps(older_data))
                time.sleep(0.2)
            except Exception:
                continue
        url = _search_filings(older_data)
        if url:
            return url

    return None


def _parse_items(html: str) -> dict[str, str]:
    """Extract Item 1A and Item 7 text from 10-K HTML.

    Scans line-by-line so item headings embedded mid-paragraph (common in
    iXBRL filings like Alphabet) are found. Resets each section on its second
    occurrence so table-of-contents entries don't pollute actual content.
    Blank lines are preserved so _chunk_text can split on paragraph boundaries.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "footer", "header"]):
        tag.decompose()

    lines = soup.get_text(separator="\n").split("\n")

    sections: dict[str, list[str]] = {}
    current = None

    for line in lines:
        stripped = line.strip().lower()
        matched = None
        # Item headings are short-to-medium lines; 100-char cap avoids matching
        # body text while allowing MSFT's "ITEM 7. MANAGEMENT'S DISCUSSION..." headers
        if stripped and len(stripped) < 100:
            for pattern, label in ITEM_PATTERNS:
                if re.match(pattern, stripped):
                    matched = label
                    break

        if matched:
            # Reset on every new occurrence — clears TOC content when the real
            # section header is reached later in the document.
            sections[matched] = []
            current = matched
        elif current and current in TARGET_ITEMS:
            sections[current].append(line)

    # Normalize whitespace-only lines to "" so they become \n\n when joined,
    # giving _chunk_text the paragraph boundaries it needs.
    result = {}
    for k, v in sections.items():
        if k not in TARGET_ITEMS:
            continue
        normalized = ["" if not ln.strip() else ln for ln in v]
        result[k] = "\n".join(normalized).strip()
    return result


def _split_large_para(para: str, max_chars: int, overlap: int) -> list[str]:
    """Sentence-level fallback for paragraphs that exceed max_chars."""
    sentences = re.split(r"(?<=[.!?])\s+", para)
    parts, current = [], ""
    for sent in sentences:
        if len(current) + len(sent) > max_chars and current:
            parts.append(current.strip())
            current = current[-overlap:] + " " + sent
        else:
            current = (current + " " + sent).strip()
    if current:
        parts.append(current.strip())
    return parts or [para[:max_chars]]


def _chunk_text(text: str, max_chars: int = 1200, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks on paragraph boundaries.

    Falls back to sentence-level splitting for paragraphs larger than max_chars
    (handles filings like GOOGL where risk factors lack blank-line separators).
    """
    raw_paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    paragraphs: list[str] = []
    for para in raw_paras:
        if len(para) > max_chars:
            paragraphs.extend(_split_large_para(para, max_chars, overlap))
        else:
            paragraphs.append(para)

    chunks, current = [], ""
    for para in paragraphs:
        if len(current) + len(para) > max_chars and current:
            chunks.append(current.strip())
            current = current[-overlap:] + "\n\n" + para
        else:
            current = (current + "\n\n" + para).strip()
    if current:
        chunks.append(current.strip())
    return chunks


def ingest_company_year(ticker: str, cik: str, fiscal_year: int) -> list[dict]:
    """Download, parse and chunk one company-year 10-K. Returns list of passages."""
    out_file = PASSAGES_DIR / f"{ticker}_{fiscal_year}.jsonl"
    if out_file.exists():
        return [json.loads(l) for l in out_file.read_text().splitlines() if l.strip()]

    url = _get_10k_url(cik, fiscal_year)
    if not url:
        print(f"  ⚠  No 10-K URL found for {ticker} FY{fiscal_year}")
        return []

    print(f"  Downloading {ticker} FY{fiscal_year} 10-K...")
    try:
        html = _fetch(url).decode("utf-8", errors="replace")
        time.sleep(0.3)
    except Exception as e:
        print(f"  ⚠  Download failed: {e}")
        return []

    items = _parse_items(html)
    if not items:
        print(f"  ⚠  No items extracted for {ticker} FY{fiscal_year}")
        return []

    passages = []
    for item_key, text in items.items():
        chunks = _chunk_text(text)
        for seq, chunk in enumerate(chunks):
            pid = f"{ticker}_{fiscal_year}_{item_key}_{seq:03d}"
            passages.append({
                "passage_id": pid,
                "company": ticker,
                "fiscal_year": fiscal_year,
                "item": item_key,
                "item_label": TARGET_ITEMS.get(item_key, item_key),
                "text": chunk,
            })

    PASSAGES_DIR.mkdir(parents=True, exist_ok=True)
    out_file.write_text("\n".join(json.dumps(p) for p in passages))
    print(f"  ✓  {ticker} FY{fiscal_year}: {len(passages)} passages saved")
    return passages


def ingest_all(companies=None, years=None) -> list[dict]:
    """Ingest all company-year combinations. Returns all passages."""
    companies = companies or COMPANIES
    years = years or [2022, 2023, 2024]
    all_passages = []
    for company in companies:
        for year in years:
            passages = ingest_company_year(company.ticker, company.cik, year)
            all_passages.extend(passages)
    return all_passages


def load_passages() -> list[dict]:
    """Load all saved passages from disk."""
    passages = []
    for f in sorted(PASSAGES_DIR.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                passages.append(json.loads(line))
    return passages
