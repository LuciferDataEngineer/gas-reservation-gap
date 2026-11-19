"""Write-audit-publish: Iceberg on a Nessie branch, then merge to main."""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq
import requests
from pyiceberg.catalog import load_catalog
from pyiceberg.schema import Schema
from pyiceberg.types import DoubleType, NestedField, StringType

SCHEMA = Schema(
    NestedField(1, "gas_date", StringType(), required=False),
    NestedField(2, "lng_tj", DoubleType(), required=False),
)


def _catalog(uri: str):
    return load_catalog(
        "nessie",
        **{
            "type": "rest",
            "uri": uri,
            "warehouse": "warehouse",
            "s3.endpoint": "http://127.0.0.1:9000",
            "s3.access-key-id": "rustfsadmin",
            "s3.secret-access-key": "rustfsadmin",
            "s3.path-style-access": "true",
            "s3.region": "us-east-1",
        },
    )


def _ref(api: str, name: str) -> dict:
    response = requests.get(f"{api}/trees/{name}", timeout=30)
    response.raise_for_status()
    return response.json()


def publish(snapshot: str, api: str = "http://127.0.0.1:19120/api/v2") -> dict:
    """Load daily feed gas onto branch `audit`, check the sum, merge to `main`."""
    frame = pq.read_table(snapshot, columns=["gas_date", "facility_type", "demand_tj"]).to_pandas()
    feed = frame[frame["facility_type"] == "LNGEXPORT"]
    daily = feed.groupby(feed["gas_date"].astype(str).str.slice(0, 10), as_index=False)[
        "demand_tj"
    ].sum()
    daily = daily.rename(columns={"gas_date": "gas_date", "demand_tj": "lng_tj"})
    expected = float(daily["lng_tj"].sum())
    arrow = pa.Table.from_pandas(daily, preserve_index=False).select(["gas_date", "lng_tj"])

    main = _ref(api, "main")
    main_hash = main["reference"]["hash"] if "reference" in main else main["hash"]
    # Query name is the new branch. Body name is the source reference.
    payload = {"type": "BRANCH", "name": "main", "hash": main_hash}
    branch = requests.post(
        f"{api}/trees",
        params={"name": "audit", "type": "BRANCH"},
        json=payload,
        timeout=30,
    )
    if branch.status_code == 409:
        current = _hash(_ref(api, "audit"))
        gone = requests.delete(
            f"{api}/trees/audit@{current}",
            params={"type": "BRANCH"},
            timeout=30,
        )
        gone.raise_for_status()
        branch = requests.post(
            f"{api}/trees",
            params={"name": "audit", "type": "BRANCH"},
            json=payload,
            timeout=30,
        )
    branch.raise_for_status()

    base = api.removesuffix("/api/v2")
    catalog = _catalog(f"{base}/iceberg/audit")
    namespaces = [tuple(item) for item in catalog.list_namespaces()]
    if ("gas",) not in namespaces:
        catalog.create_namespace("gas")
    if catalog.table_exists("gas.lng_daily"):
        catalog.drop_table("gas.lng_daily")
    table = catalog.create_table("gas.lng_daily", schema=SCHEMA)
    table.append(arrow)
    written = table.scan().to_arrow()
    got = float(written.column("lng_tj").to_pandas().sum())
    if abs(got - expected) > 0.01 or written.num_rows != len(daily):
        raise SystemExit(f"audit branch failed: {got} vs {expected}, rows {written.num_rows}")

    audit_hash = _hash(_ref(api, "audit"))
    # Nessie's merge call rejects these catalog commits: their history does not
    # share the repository's empty root as a merge ancestor. Publishing moves
    # main to the audited hash, and only after the sum above matched.
    main_now = _hash(_ref(api, "main"))
    published = requests.put(
        f"{api}/trees/main@{main_now}",
        params={"type": "BRANCH"},
        json={"type": "BRANCH", "name": "audit", "hash": audit_hash},
        timeout=30,
    )
    published.raise_for_status()
    published = _catalog(f"{base}/iceberg/main")
    check = published.load_table("gas.lng_daily").scan().to_arrow()
    final = float(check.column("lng_tj").to_pandas().sum())
    if abs(final - expected) > 0.01:
        raise SystemExit(f"main branch sum {final} != {expected}")
    return {"rows": int(check.num_rows), "lng_tj": final}


def _hash(payload: dict) -> str:
    if "reference" in payload:
        return payload["reference"]["hash"]
    return payload["hash"]
