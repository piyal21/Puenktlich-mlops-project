# Phases — what was built, phase by phase

Each file explains one finished phase in plain language: the goal, what was built and why,
how it works, the decisions taken, how it was tested (with real results) and how to reproduce it.
The plan for all phases lives in [`docs/phases.md`](../docs/phases.md); this folder is the record of
what actually happened.

| Phase | Topic | Status | Doc |
|---|---|---|---|
| 0 | Foundations — project scaffold, local stack, base modules | ✅ merged (`e87e440`) | [phase-0-foundations.md](phase-0-foundations.md) |
| 1 | Data discovery & contracts — EDA, station list, silver schema, label policy | ✅ merged (`867636a`) | [phase-1-data-discovery.md](phase-1-data-discovery.md) |
| 2 | Historical ETL with Airflow — HF backfill to bronze/silver, quarantine, quality reports | ✅ done, awaiting merge | [phase-2-hf-backfill.md](phase-2-hf-backfill.md) |
