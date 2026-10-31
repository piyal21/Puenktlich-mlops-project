"""`make rollback-prod`: swap the prod (SSM) champion back to the previous version."""

from dbdelay.registry.publish import rollback_prod_main

if __name__ == "__main__":
    raise SystemExit(rollback_prod_main())
