"""`make reconcile DAYS="..."`: live silver (AWS) vs HF raw data through the same code (Phase 7)."""

from dbdelay.live.parity import reconcile_main

if __name__ == "__main__":
    raise SystemExit(reconcile_main())
