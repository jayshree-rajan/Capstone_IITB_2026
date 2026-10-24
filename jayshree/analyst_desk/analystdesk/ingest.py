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

# Item-number headings ("Item 1A.", "ITEM 7 MANAGEMENT'S ...", "Item 9A").
# Every item is captured so that any next item (1B, 1C, 2, 7A, 8...) ends the
# current section. The "." or space after the number is required: a bare
# "Item 8" line is a cross-reference link (Visa has dozens), not a heading.
ITEM_HEADING = re.compile(r"item\s*(\d{1,2}[a-c]?)[.\s]")

# Title-only headings, for filers that do not number their sections
# (Morgan Stanley) or put them in an annual-report exhibit (Wells Fargo,
# whose MD&A is titled "Financial Review"). Only used when item-number
# headings do not find the target sections. Short generic titles must match
# exactly so risk-factor subheadings like "Cybersecurity risks" don't end
# Item 1A early; long titles match as prefixes.
EXACT_TITLES = {
    "risk factors": "item1a",
    "financial review": "item7",
    "unresolved staff comments": "item1b",
    "cybersecurity": "item1c",
    "legal proceedings": "item3",
    "controls and procedures": "item9a",
}
PREFIX_TITLES = {
    "management's discussion and analysis": "item7",
    "quantitative and qualitative disclosures about": "item7a",
    "financial statements and supplementary data": "item8",
}

# A target section shorter than this is treated as not found (e.g. a stub
# that points to an exhibit).
MIN_SECTION_CHARS = 5000

# Headings that always follow MD&A. Filers that don't number their body
# sections (JPM) otherwise run MD&A on into the financial statements. Only
# applied past MDA_END_MIN_CHARS so a page index at the start can't cut it.
MDA_END_TITLES = (
    "report of independent registered public accounting firm",
    "management's report on internal control over financial reporting",
)
MDA_END_MIN_CHARS = 10000

FILINGS_CACHE = CACHE_DIR / "filings"


def _pad_cik(cik: str) -> str:
    return cik.lstrip("0").zfill(10)


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _fetch_cached(url: str, cache_file: Path) -> bytes:
    """Fetch url once; later calls read the local copy (EDGAR asks for caching)."""
    if cache_file.exists():
        return cache_file.read_bytes()
    data = _fetch(url)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_bytes(data)
    time.sleep(0.2)
    return data


def _find_10k(cik: str, fiscal_year: int) -> tuple[str, str] | None:
    """Return (accession, primary_document) of the 10-K for a fiscal year.

    The fiscal year is the year the reporting period ends (reportDate), the
    same rule edgar.py uses for XBRL facts, so passages and numbers line up
    for June-end (MSFT) and January-end (NVDA) filers alike.
    """
    padded = _pad_cik(cik)
    cache_file = CACHE_DIR / f"submissions_{padded}.json"
    if cache_file.exists():
        data = json.loads(cache_file.read_text())
    else:
        data = json.loads(_fetch(SUBMISSIONS_URL.format(cik=padded)))
        cache_file.write_text(json.dumps(data))
        time.sleep(0.2)

    def _search_filings(filings: dict) -> tuple[str, str] | None:
        for form, report_date, accn, doc in zip(
            filings.get("form", []),
            filings.get("reportDate", []),
            filings.get("accessionNumber", []),
            filings.get("primaryDocument", []),
        ):
            if form == "10-K" and report_date[:4] == str(fiscal_year):
                return accn, doc
        return None

    found = _search_filings(data.get("filings", {}).get("recent", {}))
    if found:
        return found

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
        found = _search_filings(older_data)
        if found:
            return found

    return None


def _heading_label(line: str, use_titles: bool) -> str | None:
    """Return the item label if this line is a section heading, else None."""
    s = re.sub(r"\s+", " ", line.strip().lower().replace("’", "'"))
    # Headings are short-to-medium lines; the 100-char cap avoids matching body
    # text while allowing MSFT's "ITEM 7. MANAGEMENT'S DISCUSSION..." headers
    if not s or len(s) >= 100:
        return None
    m = ITEM_HEADING.match(s)
    if m:
        return f"item{m.group(1)}"
    s = s.rstrip(" .:")
    if use_titles:
        if s in EXACT_TITLES:
            return EXACT_TITLES[s]
        for prefix, label in PREFIX_TITLES.items():
            if s.startswith(prefix):
                return label
    return None


def _item_order(label: str) -> tuple[int, str]:
    m = re.fullmatch(r"item(\d+)([a-c]?)", label)
    return int(m.group(1)), m.group(2)


def _split_sections(lines: list[str], use_titles: bool) -> dict[str, str]:
    """Split lines into sections at headings; keep the longest run per target.

    A heading for the section we are already in is a running page header
    (Mastercard repeats "ITEM 1A. RISK FACTORS" on every page), so it continues
    the section instead of restarting it. Table-of-contents entries and
    cross-references produce short runs, which lose to the real section.

    Items run in increasing order, so once inside a real section, a numbered
    heading that jumps back (BAC's MD&A links "Item 1A. Risk Factors" on its
    own line) is a cross-reference and stays in the text. Title headings are
    exempt: WFC's annual report puts "Risk Factors" after its MD&A.
    """
    best: dict[str, list[str]] = {}
    current, run, run_chars = None, [], 0

    def _close():
        if current in TARGET_ITEMS and len("\n".join(run)) > len("\n".join(best.get(current, []))):
            best[current] = run

    for line in lines:
        label = _heading_label(line, use_titles)
        if (label and current and not use_titles and run_chars > 2000
                and _item_order(label) < _item_order(current)):
            label = None
        if label and label != current:
            _close()
            current, run, run_chars = label, [], 0
        elif label is None and current:
            run.append(line)
            run_chars += len(line)
    _close()

    if "item7" in best:
        chars = 0
        for i, line in enumerate(best["item7"]):
            s = re.sub(r"\s+", " ", line.strip().lower().replace("’", "'"))
            if chars > MDA_END_MIN_CHARS and s.startswith(MDA_END_TITLES):
                best["item7"] = best["item7"][:i]
                break
            chars += len(line)

    # Normalize whitespace-only lines to "" so they become \n\n when joined,
    # giving _chunk_text the paragraph boundaries it needs.
    return {
        k: "\n".join("" if not ln.strip() else ln for ln in v).strip()
        for k, v in best.items()
    }


def _parse_items(html: str) -> dict[str, str]:
    """Extract Item 1A and Item 7 text from 10-K HTML.

    Scans line-by-line so item headings embedded mid-paragraph (common in
    iXBRL filings like Alphabet) are found. Tries numbered "Item" headings
    first and falls back to section titles for filers that don't number them.
    Blank lines are preserved so _chunk_text can split on paragraph boundaries.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "footer", "header"]):
        tag.decompose()

    lines = soup.get_text(separator="\n").split("\n")

    result = _split_sections(lines, use_titles=False)
    if all(len(result.get(k, "")) >= MIN_SECTION_CHARS for k in TARGET_ITEMS):
        return result
    by_title = _split_sections(lines, use_titles=True)
    for k in TARGET_ITEMS:
        if len(by_title.get(k, "")) > len(result.get(k, "")):
            result[k] = by_title[k]
    return {k: v for k, v in result.items() if v}


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

    found = _find_10k(cik, fiscal_year)
    if not found:
        print(f"  ⚠  No 10-K found for {ticker} FY{fiscal_year}")
        return []
    accn, primary_doc = found
    accn_clean = accn.replace("-", "")
    base_url = FILING_URL.format(cik=cik.lstrip("0"), accession=accn_clean, doc="")
    filing_cache = FILINGS_CACHE / ticker / accn_clean

    print(f"  Loading {ticker} FY{fiscal_year} 10-K ({accn})...")
    try:
        html = _fetch_cached(base_url + primary_doc, filing_cache / primary_doc)
        items = _parse_items(html.decode("utf-8", errors="replace"))

        # Some banks (WFC) answer Items 1A and 7 by reference to an annual
        # report filed as an exhibit; the largest non-XBRL HTML document in
        # the filing is that report.
        if any(len(items.get(k, "")) < MIN_SECTION_CHARS for k in TARGET_ITEMS):
            index = json.loads(_fetch_cached(base_url + "index.json", filing_cache / "index.json"))
            exhibits = sorted(
                (f for f in index["directory"]["item"]
                 if f["name"].endswith((".htm", ".html"))
                 and f["name"] != primary_doc
                 and not re.fullmatch(r"R\d+\.htm", f["name"])
                 and "index" not in f["name"]),
                key=lambda f: int(f.get("size") or 0),
                reverse=True,
            )
            if exhibits:
                name = exhibits[0]["name"]
                ex_items = _parse_items(
                    _fetch_cached(base_url + name, filing_cache / name).decode("utf-8", errors="replace")
                )
                for k, v in ex_items.items():
                    if len(v) > len(items.get(k, "")):
                        items[k] = v
    except Exception as e:
        print(f"  ⚠  Download failed: {e}")
        return []

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
                "accession": accn,
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
