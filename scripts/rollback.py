"""`make rollback`: swap the champion pointer back to the previous model version."""

from dbdelay.registry.release import rollback_main

if __name__ == "__main__":
    raise SystemExit(rollback_main())
