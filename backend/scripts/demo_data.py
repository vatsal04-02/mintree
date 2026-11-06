"""Demo environment CLI.

    cd backend
    export MINTREE_ENVIRONMENT=demo MINTREE_DATABASE_URL=postgresql+psycopg://USER:PASS@localhost:5433/mintree_demo
    python -m scripts.demo_data reset --confirm-reset-demo-database mintree_demo     # wipes ONLY a demo database, then migrates + seeds the base data
    python -m scripts.demo_data init                                                  # seeds the connected demo stories through the real workflows (idempotent)
    python -m scripts.demo_data verify                                                # integrity checks + demo-specific checks + dashboard reconciliation
    python -m scripts.demo_data status

Safety: see scripts/demo/guard.py. Everything below the master data goes through the HTTP API in-process (same permissions, approvals, costing and
ledger postings as production). The catalogue (12 products from the storefront) is in scripts/demo/catalogue.py. Supplier/customer names, costs,
discounts and quantities are illustrative. Marketplace accounts are DEMO accounts with sample orders: no live marketplace is contacted.
"""
from __future__ import annotations

import argparse
import sys

from scripts.demo.catalogue import *  # noqa: F401,F403  (BOMS, CATALOGUE, CUSTOMERS, GST, POS, RAW, SUPPLIERS, TRADE: re-exported, unchanged)
from scripts.demo.core import PASSWORD, PEOPLE  # noqa: F401


def main(argv: list[str] | None = None) -> int:
    from scripts.demo.cli import run
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
