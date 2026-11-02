"""`make publish-model`: copy the local champion to AWS and point the SSM pointer at it."""

from dbdelay.registry.publish import publish_main

if __name__ == "__main__":
    raise SystemExit(publish_main())
