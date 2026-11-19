from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data" / "snapshot" / "facility_day.parquet"


@pytest.mark.feldera
def test_feldera_matches_duckdb():
    from gasgap.feldera_check import compare

    result = compare(str(SNAPSHOT))
    assert result["rows"] == 180
    assert result["lng_tj"] > 0


@pytest.mark.iceberg
def test_nessie_write_audit_publish():
    import s3fs

    from gasgap.iceberg_wap import publish

    fs = s3fs.S3FileSystem(
        key="rustfsadmin",
        secret="rustfsadmin",
        client_kwargs={"endpoint_url": "http://127.0.0.1:9000"},
    )
    try:
        fs.mkdir("gas-warehouse")
    except FileExistsError:
        pass
    result = publish(str(SNAPSHOT))
    assert result["rows"] > 100
    assert result["lng_tj"] > 1_000_000
