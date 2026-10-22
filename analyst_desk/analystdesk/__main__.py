"""Run Part 1 with: python -m analystdesk --years 2022 2023 2024."""

import argparse
import json

from .scope import ScopeError, create_request


def main() -> None:
    parser = argparse.ArgumentParser(description="AnalystDesk Part 1: validate a research request.")
    parser.add_argument("--companies", nargs="+", default=["Microsoft", "Alphabet"])
    parser.add_argument("--years", nargs="+", required=True, type=int, help="Three explicit fiscal-year labels")
    args = parser.parse_args()
    try:
        request = create_request(args.companies, args.years)
    except ScopeError as error:
        parser.error(str(error))
    print(json.dumps({
        "status": "scope_validated",
        "request": request.to_dict(),
        "message": "Scope accepted. Filing availability and actual period dates have not been checked.",
    }, indent=2))


if __name__ == "__main__":
    main()
