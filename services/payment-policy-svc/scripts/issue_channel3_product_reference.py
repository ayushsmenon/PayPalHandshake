"""Issue one trusted Channel3 product reference after an independent re-fetch."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from payment_policy.product_reference import (  # noqa: E402
    ProductReferenceIssuanceError,
    issue_product_reference,
)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Issue a trusted Channel3 product reference.")
    parser.add_argument("product_id")
    parser.add_argument("--merchant", required=True, help="Expected Channel3 offer domain.")
    parser.add_argument("--price", required=True, help="Expected decimal price, for example 329.00.")
    parser.add_argument("--currency", default="USD")
    parser.add_argument(
        "--output-file",
        type=Path,
        help="Also write the signed product-reference JSON to this local file.",
    )
    args = parser.parse_args(arguments)
    load_dotenv(ROOT / ".env", override=False)
    try:
        signed = issue_product_reference(
            args.product_id,
            args.merchant,
            args.price,
            args.currency,
            environment=os.environ,
        )
    except ProductReferenceIssuanceError as error:
        print(f"Product-reference issuance stopped: {error}")
        return 1
    output = json.dumps(signed.model_dump(mode="json"), indent=2)
    if args.output_file is not None:
        try:
            args.output_file.write_text(output + "\n", encoding="utf-8")
        except OSError as error:
            print(f"Product-reference output stopped: could not write {args.output_file}: {error}")
            return 1
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
