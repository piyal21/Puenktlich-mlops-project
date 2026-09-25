# Test fixtures

| File | What | Source |
|---|---|---|
| `sample_plan.xml` | `GET /plan/8000199/260925/17` — Kiel Hbf, 2026-09-25 17:00 Europe/Berlin | DB Timetables API, captured 2026-09-25 |
| `sample_fchg.xml` | `GET /fchg/8000199` — Kiel Hbf full changes, same moment (all 19 plan stops appear here; includes 5 cancellations `cs="c"`) | DB Timetables API, captured 2026-09-25 |

Raw responses, unmodified except a final newline (pre-commit `end-of-file-fixer`); no line-ending
conversion (see `.gitattributes`). Re-capture with
`uv run python scripts/fetch_api_samples.py 8000199`.

Data © Deutsche Bahn AG, CC BY 4.0.
