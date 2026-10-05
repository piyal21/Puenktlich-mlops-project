"""Station search for the UI: ignores case, accents and umlaut spelling (München = muenchen)."""

import re
import unicodedata
from collections.abc import Sequence

from dbdelay.data.stations import Station

_UMLAUT_SPELLING = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"})
_NOT_ALNUM = re.compile(r"[^0-9a-z]+")


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    letters = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NOT_ALNUM.sub(" ", letters).strip()


def _keys(name: str) -> tuple[str, str]:
    folded = name.casefold()
    return _plain(folded), _plain(folded.translate(_UMLAUT_SPELLING))


def search_stations(stations: Sequence[Station], query: str) -> list[Station]:
    """Stations whose name contains ``query``; prefix matches first, then by name.
    An empty query returns all stations by name."""
    needle = _plain(query)
    if not needle:
        return sorted(stations, key=lambda s: s.name)
    hits = [s for s in stations if any(needle in key for key in _keys(s.name))]
    return sorted(
        hits, key=lambda s: (not any(k.startswith(needle) for k in _keys(s.name)), s.name)
    )
