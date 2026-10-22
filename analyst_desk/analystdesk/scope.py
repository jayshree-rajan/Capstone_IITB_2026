"""Part 1: define and validate the supported research request.

This module validates scope only. Fiscal labels are not actual period dates;
later SEC ingestion must resolve dates and confirm filing availability.
"""

from dataclasses import asdict, dataclass


class ScopeError(ValueError):
    """The requested comparison falls outside this baseline's scope."""


@dataclass(frozen=True)
class Company:
    name: str
    ticker: str
    cik: str


COMPANIES = (
    Company("Microsoft", "MSFT", "0000789019"),
    Company("Alphabet", "GOOGL", "0001652044"),
)
SUPPORTED_METRICS = ("revenue", "operating_income", "operating_margin")


def resolve_company(value: str) -> Company:
    """Accept an exact supported company name or ticker, ignoring case."""
    if not isinstance(value, str):
        raise ScopeError("Company must be a name or ticker.")
    key = value.strip().casefold()
    for company in COMPANIES:
        if key in (company.name.casefold(), company.ticker.casefold()):
            return company
    raise ScopeError(f"Unsupported company: {value!r}. Use Microsoft or Alphabet.")


@dataclass(frozen=True)
class ResearchRequest:
    """An immutable, validated contract for the rest of the pipeline."""

    companies: tuple[Company, Company]
    fiscal_years: tuple[int, int, int]
    metrics: tuple[str, ...] = SUPPORTED_METRICS

    def __post_init__(self) -> None:
        if not isinstance(self.companies, tuple) or len(self.companies) != 2:
            raise ScopeError("Select Microsoft and Alphabet for this comparison.")
        if any(not isinstance(c, Company) or c not in COMPANIES for c in self.companies):
            raise ScopeError("Only Microsoft and Alphabet are supported.")
        if len(set(self.companies)) != 2:
            raise ScopeError("Select two different companies: Microsoft and Alphabet.")
        years = self.fiscal_years
        if not isinstance(years, tuple) or len(years) != 3:
            raise ScopeError("Provide exactly three fiscal years.")
        if any(type(year) is not int or not 1000 <= year <= 9999 for year in years):
            raise ScopeError("Fiscal years must be four-digit integers.")
        if len(set(years)) != 3:
            raise ScopeError("Fiscal years must be distinct.")
        if not isinstance(self.metrics, tuple) or self.metrics != SUPPORTED_METRICS:
            raise ScopeError("This baseline supports revenue, operating income and operating margin together.")
        # Canonical order gives later stages a predictable input contract.
        object.__setattr__(self, "companies", COMPANIES)
        object.__setattr__(self, "fiscal_years", tuple(sorted(years)))

    def to_dict(self) -> dict:
        """Return JSON-compatible values without fetching or inventing facts."""
        result = asdict(self)
        result["companies"] = list(result["companies"])
        result["fiscal_years"] = list(self.fiscal_years)
        result["metrics"] = list(self.metrics)
        return result


def create_request(companies: list[str], fiscal_years: list[int]) -> ResearchRequest:
    """Validate user selections and return the contract used by later parts."""
    return ResearchRequest(
        companies=tuple(resolve_company(value) for value in companies),
        fiscal_years=tuple(fiscal_years),
    )
