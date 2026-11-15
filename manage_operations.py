"""Inspect or resolve uncertain requests after manually checking Odoo."""
import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from store import Store

load_dotenv()
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("reference")
parser.add_argument("--resolve", choices=["success", "failed"])
parser.add_argument("--partner-id", type=int)
parser.add_argument("--detail", default="")
args = parser.parse_args()
store = Store(str(Path(os.getenv("DATA_DIR", "data")) / "operations.sqlite3"))
operation = store.get(args.reference)
if not operation:
    parser.error("Request not found")
if args.resolve:
    if operation["status"] not in ("pending", "unknown"):
        parser.error("Only pending or unknown requests may be resolved")
    if args.resolve == "success" and operation["kind"] == "customer" and not args.partner_id:
        parser.error("A confirmed customer ID is required for customer success")
    if args.partner_id is not None and args.partner_id <= 0:
        parser.error("Customer ID must be positive")
    if not args.detail:
        parser.error("Provide --detail describing the manual verification")
    store.finish(args.reference, args.resolve, args.detail, args.partner_id)
    operation = store.get(args.reference)
for key, value in operation.items():
    print(f"{key}: {value}")
