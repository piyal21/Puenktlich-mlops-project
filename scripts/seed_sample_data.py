"""`make seed`: write a sample live board (a real silver day replayed onto today) into MinIO."""

from dbdelay.serving.seed import seed_main

if __name__ == "__main__":
    raise SystemExit(seed_main())
