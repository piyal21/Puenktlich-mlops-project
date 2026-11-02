from datetime import date

import pytest

from dbdelay.data.silver import silver_key
from dbdelay.errors import NotFoundError
from dbdelay.serving.samples import SAMPLE_DAYS, copy_silver_days, upload_main
from dbdelay.storage import ObjectStore
from tests.unit.conftest import TEST_BUCKET


def _put_days(store: ObjectStore, days: tuple[date, ...]) -> None:
    for day in days:
        store.put_bytes(silver_key(day), f"parquet of {day}".encode())


def test_sample_days_are_nine_consecutive_august_days() -> None:
    assert SAMPLE_DAYS[0] == date(2026, 8, 23)
    assert SAMPLE_DAYS[-1] == date(2026, 8, 31)
    assert len(SAMPLE_DAYS) == 9


def test_copy_silver_days_copies_bytes_identically(s3_store: ObjectStore) -> None:
    _put_days(s3_store, SAMPLE_DAYS)
    assert copy_silver_days(s3_store, s3_store, SAMPLE_DAYS, "samples/") == len(SAMPLE_DAYS)
    for day in SAMPLE_DAYS:
        assert s3_store.get_bytes(silver_key(day, "samples/")) == s3_store.get_bytes(
            silver_key(day)
        )


def test_copy_silver_days_missing_day_writes_nothing(s3_store: ObjectStore) -> None:
    _put_days(s3_store, SAMPLE_DAYS[:-1])
    with pytest.raises(NotFoundError):
        copy_silver_days(s3_store, s3_store, SAMPLE_DAYS, "samples/")
    assert list(s3_store.iter_keys("samples/")) == []


def test_upload_main_copies_days(
    s3_store: ObjectStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATA_BUCKET", TEST_BUCKET)
    _put_days(s3_store, SAMPLE_DAYS)
    assert upload_main(["--data-bucket", TEST_BUCKET]) == 0
    assert capsys.readouterr().out == "uploaded 9 sample days to samples/\n"
    assert len(list(s3_store.iter_keys("samples/"))) == 9


def test_upload_main_missing_day_is_refused(
    s3_store: ObjectStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATA_BUCKET", TEST_BUCKET)
    assert upload_main(["--data-bucket", TEST_BUCKET]) == 1
    assert capsys.readouterr().err.startswith("upload refused:")
