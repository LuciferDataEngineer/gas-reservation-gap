"""Row, range and cross-source checks against the warehouse."""

from __future__ import annotations

import duckdb

CHECKS = [
    (
        "facility rows",
        "SELECT COUNT(*) > 1000 FROM bronze.facility_day",
    ),
    (
        "three lng plants",
        """
        SELECT COUNT(DISTINCT facility_name) = 3
        FROM bronze.facility_day WHERE facility_type = 'LNGEXPORT'
        """,
    ),
    (
        "lng demand is not negative",
        """
        SELECT COALESCE(MIN(demand_tj), 0) >= 0
        FROM bronze.facility_day WHERE facility_type = 'LNGEXPORT'
        """,
    ),
    (
        "no duplicate facility days",
        """
        SELECT COUNT(*) = 0 FROM (
          SELECT gas_date, facility_name FROM bronze.facility_day
          GROUP BY 1, 2 HAVING COUNT(*) > 1
        )
        """,
    ),
    (
        "iona inventory in range",
        """
        SELECT MIN(held_tj) > 0 AND MAX(held_tj) < 40000
        FROM bronze.facility_day WHERE facility_name = 'Iona UGS' AND held_tj IS NOT NULL
        """,
    ),
    (
        "2024 feed gas matches the GSOO LNG line",
        """
        SELECT abs(feed - published) < 1 FROM (
          SELECT
            (SELECT SUM(demand_tj) / 1000 FROM bronze.facility_day
             WHERE facility_type = 'LNGEXPORT' AND year(gas_date) = 2024) AS feed,
            (SELECT pj FROM bronze.gsoo_consumption
             WHERE series = 'lng' AND year = 2024) AS published
        )
        """,
    ),
    (
        "adequacy has actuals and a forecast",
        """
        SELECT COUNT(*) FILTER (WHERE is_actual) > 300
           AND COUNT(*) FILTER (WHERE NOT is_actual) > 300
        FROM bronze.gsoo_adequacy_day
        """,
    ),
    (
        "heating degrees are in range",
        "SELECT MIN(hdd_18c) >= 0 AND MAX(hdd_18c) < 30 FROM bronze.hdd_daily",
    ),
    (
        "morning prices are positive",
        "SELECT MIN(price_6am) > 0 FROM bronze.dwgm_daily WHERE price_6am IS NOT NULL",
    ),
    (
        "swqp westbound capacity is published",
        "SELECT capacity_value = 512 FROM bronze.facility_reference WHERE code = 'SWQP'",
    ),
]


def run_checks(con: duckdb.DuckDBPyConnection) -> list[str]:
    failed = []
    for name, sql in CHECKS:
        ok = con.execute(sql).fetchone()[0]
        if not ok:
            failed.append(name)
    if failed:
        raise SystemExit("data checks failed: " + ", ".join(failed))
    return [name for name, _ in CHECKS]
