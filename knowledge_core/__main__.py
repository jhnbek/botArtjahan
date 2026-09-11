"""Local, JSON-producing command line interface; no network calls."""
import argparse
import json
import sqlite3
import sys

from .db import CoreError


def main(argv=None):
    parser = argparse.ArgumentParser(description="Independent, local lecture knowledge core")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("import", help="Build a validated, separate dataset from retained inputs")
    build.add_argument("--source", required=True)
    build.add_argument("--destination", required=True)
    build.add_argument("--previous", help="Keep revisions from this earlier dataset in a fresh destination")
    validate = commands.add_parser("validate", help="Verify schema, links, inventory and every retained file")
    validate.add_argument("dataset")
    search = commands.add_parser("search", help="Search current text, OCR, claims, rules and concepts")
    search.add_argument("dataset")
    search.add_argument("text")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--exact", action="store_true", help="Match complete words instead of prefixes")
    show = commands.add_parser("show", help="Retrieve full evidence and declared lineage")
    show.add_argument("dataset")
    show.add_argument("identifier")
    show.add_argument("--namespace")
    show.add_argument("--run-id", help="Inspect a retained historical import")
    for name in ("export", "restore"):
        command = commands.add_parser(name, help="Verified, lossless local " + name)
        command.add_argument("source")
        command.add_argument("destination")
    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            from .importer import build_core
            result = build_core(args.source, args.destination, previous=args.previous)
        elif args.command == "validate":
            from .validation import validate_core
            result = validate_core(args.dataset)
        elif args.command == "search":
            from .query import search
            result = search(args.dataset, args.text, limit=args.limit, prefix=not args.exact)
        elif args.command == "show":
            from .query import trace
            result = trace(args.dataset, args.identifier, namespace=args.namespace, run_id=args.run_id)
        elif args.command == "export":
            from .transfer import export_core
            result = export_core(args.source, args.destination)
        else:
            from .transfer import restore_core
            result = restore_core(args.source, args.destination)
        if isinstance(result, dict) and "manifest" in result:
            result["file_count"] = len(result.pop("manifest")["files"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if isinstance(result, dict) and result.get("valid") is False else 0
    except (CoreError, OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
