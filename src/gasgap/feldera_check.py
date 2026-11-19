"""Incremental feed-gas total in Feldera, checked against DuckDB."""

from __future__ import annotations

import time
from datetime import date, datetime

import duckdb
import pyarrow.parquet as pq
from feldera import FelderaClient, PipelineBuilder
from feldera.enums import CompilationProfile

SQL = """
CREATE TABLE facility_day (
    gas_date DATE,
    facility_name STRING,
    facility_type STRING,
    demand_tj DOUBLE
);

CREATE MATERIALIZED VIEW lng_total AS
SELECT COALESCE(SUM(demand_tj), 0.0) AS lng_tj
FROM facility_day
WHERE facility_type = 'LNGEXPORT';
"""


def _iso(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def compare(snapshot: str, url: str = "http://127.0.0.1:8080") -> dict:
    table = pq.read_table(
        snapshot,
        columns=["gas_date", "facility_name", "facility_type", "demand_tj"],
    )
    frame = table.to_pandas()
    frame = frame[frame["facility_type"] == "LNGEXPORT"].head(180)
    expected = float(duckdb.sql("SELECT SUM(demand_tj) FROM frame").fetchone()[0])
    rows = [
        {
            "gas_date": _iso(rec.gas_date),
            "facility_name": rec.facility_name,
            "facility_type": rec.facility_type,
            "demand_tj": float(rec.demand_tj),
        }
        for rec in frame.itertuples(index=False)
    ]
    client = FelderaClient(url)
    started = time.perf_counter()
    pipeline = PipelineBuilder(
        client,
        name="gas_feed",
        sql=SQL,
        compilation_profile=CompilationProfile.DEV,
    ).create_or_replace()
    try:
        pipeline.start()
        pipeline.input_json("facility_day", rows, wait=True)
        pipeline.wait_for_idle(idle_interval_s=1.0, timeout_s=120)
        got = list(pipeline.query_arrow_dicts("SELECT lng_tj FROM lng_total"))
    finally:
        pipeline.stop(force=True)
    if len(got) != 1:
        raise SystemExit(f"Feldera returned {len(got)} rows")
    actual = float(got[0]["lng_tj"])
    if abs(actual - expected) > 0.01:
        raise SystemExit(f"Feldera {actual} != DuckDB {expected}")
    return {
        "rows": len(rows),
        "lng_tj": actual,
        "seconds": round(time.perf_counter() - started, 1),
    }
