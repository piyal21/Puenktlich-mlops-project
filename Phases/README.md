# Phases — what was built, phase by phase

Each file explains one finished phase in plain language: the goal, what was built and why,
how it works, the decisions taken, how it was tested (with real results) and how to reproduce it.
The plan for all phases lives in [`docs/phases.md`](../docs/phases.md); this folder is the record of
what actually happened.

| Phase | Topic | Status | Doc |
|---|---|---|---|
| 0 | Foundations — project scaffold, local stack, base modules | ✅ merged (`e87e440`) | [phase-0-foundations.md](phase-0-foundations.md) |
| 1 | Data discovery & contracts — EDA, station list, silver schema, label policy | ✅ merged (`867636a`) | [phase-1-data-discovery.md](phase-1-data-discovery.md) |
| 2 | Historical ETL with Airflow — HF backfill to bronze/silver, quarantine, quality reports | ✅ merged (`b8fac56`) | [phase-2-hf-backfill.md](phase-2-hf-backfill.md) |
| 3 | Features & baseline — feature builder, gold snapshot, late-rate baseline + metrics | ✅ merged (`584f263`) | [phase-3-features-baseline.md](phase-3-features-baseline.md) |
| 4 | Training, tracking, registry, gate — LightGBM + calibration, MLflow registry, promotion gate, release/rollback, Airflow DAG | ✅ merged (`d9ea889`) | [phase-4-training.md](phase-4-training.md) |
| 5 | Local serving & UI — champion loader, FastAPI, sample board, Lambda/local image, React app | ✅ done | [phase-5-serving-ui.md](phase-5-serving-ui.md) |
