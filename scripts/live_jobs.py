"""`make ingest-local` / `make etl-live-local DAY=…`: live jobs against local MinIO (Phase 7)."""

import sys

from dbdelay.live.cli import etl_main, ingest_main

if __name__ == "__main__":
    command, args = sys.argv[1], sys.argv[2:]
    raise SystemExit(ingest_main(args) if command == "ingest" else etl_main(args))
