"""Fetch raw DB Timetables API responses for a few stations (Phase 1 discovery / fixtures).

Dev-only helper: saves `plan` (current Berlin hour) and `fchg` XML per station into
`data/raw/api/` (git-ignored). Credentials come from `.env` via `dbdelay.config`;
only HTTP status codes and sizes are printed. Non-200 bodies go to `*.err.xml` so a good
capture is never overwritten.

Uses stdlib `urllib` + `print` on purpose: httpx/tenacity/Powertools (rules.md §2) arrive with
the real ingestion client in Phase 7; this script is not part of the `dbdelay` package.

Usage:
    uv run python scripts/fetch_api_samples.py 8000105 8098160
    uv run python scripts/fetch_api_samples.py --station "Erfurt Hbf"
"""

import argparse
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dbdelay.config import get_settings

BASE_URL = "https://apis.deutschebahn.com/db-api-marketplace/apis/timetables/v1"
OUT_DIR = Path("data/raw/api")
TIMEOUT_S = 20
HTTP_OK = 200
EVA_PATTERN = re.compile(r"[1-9][0-9]{6}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: urllib would forward the API key headers to the new host.

    Returning None makes urllib raise HTTPError for the 3xx, which `_get` saves as `.err.xml`.
    """

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _get(path: str) -> tuple[int, bytes]:
    settings = get_settings()
    if settings.db_api_client_id is None or settings.db_api_key is None:
        sys.exit("DB_API_CLIENT_ID / DB_API_KEY not set in .env")
    request = urllib.request.Request(  # noqa: S310 - fixed https base URL
        f"{BASE_URL}/{path}",
        headers={
            "DB-Client-Id": settings.db_api_client_id.get_secret_value(),
            "DB-Api-Key": settings.db_api_key.get_secret_value(),
            "Accept": "application/xml",
        },
    )
    try:
        with _OPENER.open(request, timeout=TIMEOUT_S) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _save(name: str, path: str) -> None:
    status, body = _get(path)
    target = OUT_DIR / (name if status == HTTP_OK else name.replace(".xml", ".err.xml"))
    target.write_bytes(body)
    print(f"{status} {len(body):>8} B  {target.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evas", nargs="*", help="EVA numbers to fetch plan + fchg for")
    parser.add_argument("--station", action="append", default=[], help="station search pattern")
    args = parser.parse_args()
    bad = [eva for eva in args.evas if not EVA_PATTERN.fullmatch(eva)]
    if bad:
        parser.error(f"not a 7-digit EVA number: {', '.join(bad)}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    date_hour = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%y%m%d/%H")
    for pattern in args.station:
        slug = re.sub(r"[^a-z0-9]+", "_", pattern.lower())
        _save(f"station_{slug}.xml", f"station/{urllib.parse.quote(pattern)}")
    for eva in args.evas:
        _save(f"plan_{eva}_{date_hour.replace('/', '')}.xml", f"plan/{eva}/{date_hour}")
        _save(f"fchg_{eva}.xml", f"fchg/{eva}")


if __name__ == "__main__":
    main()
