"""`make parity-check DAY=…`: HF raw XML through our live code vs HF silver in MinIO (Phase 7)."""

from dbdelay.live.parity import parity_main

if __name__ == "__main__":
    raise SystemExit(parity_main())
