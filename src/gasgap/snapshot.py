"""Download public files and write the committed snapshot.

The pipeline does not call the network. This module does. Run it only when
refreshing ``data/snapshot``.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import openpyxl
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "data" / "snapshot"
MELBOURNE = ZoneInfo("Australia/Melbourne")
UA = (
    "gas-reservation-gap/0.1 "
    "(public research; https://github.com/LuciferDataEngineer/gas-reservation-gap)"
)

SOURCES: list[dict] = [
    {
        "id": "gbb_actual_flow_storage",
        "publisher": "AEMO Gas Bulletin Board",
        "url": "https://nemweb.com.au/Reports/CURRENT/GBB/GasBBActualFlowStorage.zip",
        "licence": "© AEMO. Public download, no API key. Not a Creative Commons licence.",
        "coverage": "Facility flows and storage, gas days from 29 Sep 2018",
    },
    {
        "id": "gbb_lng_shipments",
        "publisher": "AEMO Gas Bulletin Board",
        "url": "https://nemweb.com.au/Reports/CURRENT/GBB/GasBBLNGShipments.CSV",
        "licence": "© AEMO. Public download, no API key. Not a Creative Commons licence.",
        "coverage": "LNG cargo volumes in PJ",
    },
    {
        "id": "gbb_lng_transactions",
        "publisher": "AEMO Gas Bulletin Board",
        "url": "https://nemweb.com.au/Reports/CURRENT/GBB/GasBBLNGTransactions.csv",
        "licence": "© AEMO. Public download, no API key. Not a Creative Commons licence.",
        "coverage": "Quarterly LNG transaction volumes and volume-weighted prices",
    },
    {
        "id": "gsoo_figures",
        "publisher": "AEMO",
        "url": "https://www.aemo.com.au/-/media/files/gas/national_planning_and_forecasting/gsoo/2026/2026-gsoo-report-figures-and-data.xlsx",
        "licence": "© AEMO 2026. Use is subject to AEMO's published copyright permissions.",
        "coverage": "2026 Gas Statement of Opportunities chart data, published 26 Mar 2026",
    },
    {
        "id": "gsoo_facilities",
        "publisher": "AEMO",
        "url": "https://www.aemo.com.au/-/media/files/gas/national_planning_and_forecasting/gsoo/2026/2026-gsoo-supply-data.xlsx",
        "licence": "© AEMO 2026. Use is subject to AEMO's published copyright permissions.",
        "coverage": "2026 GSOO supply workbook: processing, storage, pipelines, LNG projects",
    },
    {
        "id": "dwgm_prices",
        "publisher": "AEMO DWGM",
        "url": "https://www.aemo.com.au/-/media/files/gas/dwgm/dwgm-prices-and-demand.xlsx",
        "licence": "© AEMO. Public download, no API key. Not a Creative Commons licence.",
        "coverage": "Victorian declared wholesale prices and daily demand",
    },
    {
        "id": "sttm_prices",
        "publisher": "AEMO STTM",
        "url": "https://www.aemo.com.au/-/media/files/gas/sttm/data/sttm-price-and-withdrawals.xlsx",
        "licence": "© AEMO. Public download, no API key. Not a Creative Commons licence.",
        "coverage": "Sydney, Adelaide and Brisbane short-term trading prices and allocations",
    },
]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "*/*"})
    return s


def _get(session: requests.Session, url: str) -> bytes:
    response = session.get(url, timeout=180)
    response.raise_for_status()
    return response.content


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_parquet(frame: pd.DataFrame, path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    raw = path.read_bytes()
    return {
        "path": str(path.relative_to(ROOT)),
        "rows": int(len(frame)),
        "bytes": len(raw),
        "sha256": _sha(raw),
    }


def _facility_extract(zip_bytes: bytes) -> tuple[pd.DataFrame, pd.DataFrame]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        name = archive.namelist()[0]
        csv_path = SNAPSHOT / "_flow.csv"
        csv_path.write_bytes(archive.read(name))
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE flows AS
        SELECT
          CAST(GasDate AS DATE) AS gas_date,
          FacilityName AS facility_name,
          FacilityType AS facility_type,
          State AS state,
          LocationName AS location_name,
          TRY_CAST(Demand AS DOUBLE) AS demand_tj,
          TRY_CAST(Supply AS DOUBLE) AS supply_tj,
          TRY_CAST(NULLIF(HeldInStorage, '') AS DOUBLE) AS held_tj
        FROM read_csv(?, header=true, all_varchar=true)
        """,
        [str(csv_path)],
    )
    facility = con.execute(
        """
        SELECT gas_date, facility_name, facility_type, state, location_name,
               demand_tj, supply_tj, held_tj
        FROM flows
        WHERE facility_type = 'LNGEXPORT'
           OR facility_name IN ('Longford', 'Iona UGS', 'Otway', 'Orbost', 'Lang Lang', 'ATHENA', 'Minerva', 'Moomba')
        ORDER BY gas_date, facility_name
        """
    ).fetchdf()
    pipes = con.execute(
        """
        SELECT gas_date, state, ROUND(SUM(demand_tj), 3) AS pipe_demand_tj
        FROM flows
        WHERE facility_type = 'PIPE'
          AND state IN ('VIC', 'NSW', 'SA', 'TAS')
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    ).fetchdf()
    csv_path.unlink(missing_ok=True)
    return facility, pipes


def _shipments(raw: bytes) -> pd.DataFrame:
    frame = pd.read_csv(io.BytesIO(raw))
    frame["shipment_date"] = pd.to_datetime(frame["ShipmentDate"], format="%d %b %Y %H:%M:%S")
    frame["volume_pj"] = pd.to_numeric(frame["VolumePJ"], errors="coerce")
    return frame.rename(
        columns={
            "TransactionId": "transaction_id",
            "FacilityId": "facility_id",
            "FacilityName": "facility_name",
        }
    )[["transaction_id", "facility_id", "facility_name", "shipment_date", "volume_pj"]]


def _transactions(raw: bytes) -> pd.DataFrame:
    frame = pd.read_csv(io.BytesIO(raw))
    frame["volume_pj"] = pd.to_numeric(frame["VolumePJ"], errors="coerce")
    frame["price_per_gj"] = pd.to_numeric(frame["VolWeightPrice"], errors="coerce")
    return frame.rename(columns={"TransactionMonth": "quarter_label"})[
        ["quarter_label", "volume_pj", "price_per_gj", "SupplyStartDate", "SupplyEndDate"]
    ].rename(columns={"SupplyStartDate": "supply_start", "SupplyEndDate": "supply_end"})


def _rows(ws):
    for row in ws.iter_rows(values_only=True):
        yield list(row)


def _gsoo_figures(raw: bytes) -> dict[str, pd.DataFrame]:
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    adequacy = []
    ws = wb["Figure 4 & 35"]
    for row in _rows(ws):
        when = row[1] if len(row) > 1 else None
        if not isinstance(when, datetime):
            continue

        def num(i):
            value = row[i] if i < len(row) else None
            return float(value) if isinstance(value, (int, float)) else 0.0

        rci_a, gpg_a, rci_f, gpg_f = num(2), num(3), num(4), num(5)
        actual = rci_a + gpg_a
        forecast = rci_f + gpg_f
        adequacy.append(
            {
                "gas_date": when.date(),
                "is_actual": actual > 0,
                "demand_tj": actual if actual > 0 else forecast,
                "rci_tj": rci_a if actual > 0 else rci_f,
                "gpg_tj": gpg_a if actual > 0 else gpg_f,
                "max_production_tj": num(6),
                "production_swqp_tj": num(7),
                "plus_deep_storage_tj": num(8),
                "plus_shallow_storage_tj": num(9),
                "plus_anticipated_tj": num(10),
            }
        )
    gaps = []
    ws = wb["Figure 5 & 38"]
    year = None
    for row in _rows(ws):
        if isinstance(row[1], int):
            year = int(row[1])
        scenario = row[2]
        value = row[6] if len(row) > 6 else None
        if (
            year
            and scenario in {"Step Change", "Slower Growth", "Accelerated Transition"}
            and isinstance(value, (int, float))
        ):
            gaps.append({"year": year, "scenario": scenario, "annual_gap_pj": float(value)})
    consumption = []
    ws = wb["Figure 1"]
    for row in _rows(ws):
        if isinstance(row[1], int) and isinstance(row[2], (int, float)):
            consumption.append(
                {"year": int(row[1]), "series": "domestic_ex_gpg_actual", "pj": float(row[2])}
            )
        if isinstance(row[1], int) and isinstance(row[4], (int, float)):
            consumption.append(
                {"year": int(row[1]), "series": "domestic_ex_gpg_step_change", "pj": float(row[4])}
            )
    ws = wb["Figure 9"]
    for row in _rows(ws):
        if not isinstance(row[1], int):
            continue
        labels = [(3, "industrial"), (4, "residential_commercial"), (5, "gpg"), (6, "lng")]
        for idx, name in labels:
            if idx < len(row) and isinstance(row[idx], (int, float)):
                consumption.append({"year": int(row[1]), "series": name, "pj": float(row[idx])})
    capacity = []
    ws = wb["Figure 3 & 30"]
    pending_year = None
    for row in _rows(ws):
        if isinstance(row[1], int):
            pending_year = int(row[1])
            if isinstance(row[5], (int, float)):
                capacity.append(
                    {
                        "year": pending_year,
                        "series": "gsoo2026_existing_committed",
                        "tj_per_day": float(row[5]),
                    }
                )
            if isinstance(row[6], (int, float)):
                capacity.append(
                    {
                        "year": pending_year,
                        "series": "gsoo2026_anticipated",
                        "tj_per_day": float(row[6]),
                    }
                )
            continue
        if pending_year and isinstance(row[2], (int, float)):
            capacity.append({"year": pending_year, "series": "actual", "tj_per_day": float(row[2])})
        if pending_year and isinstance(row[5], (int, float)) and not isinstance(row[1], int):
            capacity.append(
                {
                    "year": pending_year,
                    "series": "gsoo2026_existing_committed",
                    "tj_per_day": float(row[5]),
                }
            )
        if pending_year and len(row) > 6 and isinstance(row[6], (int, float)) and row[1] is None:
            capacity.append(
                {
                    "year": pending_year,
                    "series": "gsoo2026_anticipated",
                    "tj_per_day": float(row[6]),
                }
            )
    wb.close()
    # The year row and the following row can both emit the 2026 series. Keep the last.
    cap = pd.DataFrame(capacity).drop_duplicates(["year", "series"], keep="last")
    return {
        "gsoo_adequacy_day": pd.DataFrame(adequacy),
        "gsoo_annual_gap": pd.DataFrame(gaps).drop_duplicates(["year", "scenario"], keep="last"),
        "gsoo_consumption": pd.DataFrame(consumption),
        "gsoo_southern_capacity": cap,
    }


def _named_cell(sheet, name: str) -> list | None:
    header = None
    for row in sheet.iter_rows(values_only=True):
        cells = list(row)
        if cells and cells[1] == "Name":
            header = cells
            continue
        if header and cells and cells[1] == name:
            return cells
    return None


def _facilities(raw: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        workbook_name = [n for n in archive.namelist() if n.endswith("Facilities.xlsx")][0]
        payload = archive.read(workbook_name)
    wb = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    records = []
    lng = {
        "QGC (Curtis Island)": "QCLNG",
        "Australia Pacific LNG": "APLNG",
        "Gladstone LNG": "GLNG",
    }
    for name, code in lng.items():
        row = _named_cell(wb["LNG Export"], name)
        records.append(
            {
                "kind": "lng",
                "name": name,
                "code": code,
                "status": row[3],
                "capacity_value": float(row[10]),
                "capacity_unit": "Mtpa",
                "capacity_note": None,
                "detail": str(row[9]) if row else None,
            }
        )
    iona = _named_cell(wb["Storage"], "Iona Underground Gas Storage")
    records.append(
        {
            "kind": "storage",
            "name": "Iona Underground Gas Storage",
            "code": "IONA",
            "status": iona[3],
            "capacity_value": float(iona[10]),
            "capacity_unit": "TJ/day withdrawal",
            "capacity_note": str(iona[11]),
            "detail": f"injection {iona[9]} TJ/day",
        }
    )
    hugs = _named_cell(wb["Storage"], "Heytesbury Underground Gas Storage Project (HUGS)")
    records.append(
        {
            "kind": "storage",
            "name": "Heytesbury Underground Gas Storage",
            "code": "HUGS",
            "status": hugs[3],
            "capacity_value": float(hugs[10]),
            "capacity_unit": "TJ/day withdrawal",
            "capacity_note": str(hugs[11]),
            "detail": f"injection {hugs[9]} TJ/day",
        }
    )
    longford = _named_cell(wb["Processing"], "Longford Gas Plant")
    note = str(longford[2])
    match = re.search(r"approximately\s+([\d,]+)\s+TJ per day", note)
    records.append(
        {
            "kind": "plant",
            "name": "Longford Gas Plant",
            "code": "LONGFORD",
            "status": longford[3],
            "capacity_value": float(longford[10]),
            "capacity_unit": "TJ/day",
            "capacity_note": match.group(1).replace(",", "") if match else None,
            "detail": "Capacity column and the approximate figure in the same description",
        }
    )
    swqp = _named_cell(wb["Pipelines"], "South West Queensland Pipeline (“SWQP”)")
    text = str(swqp[11])
    west = re.search(r"(\d+)\s*West", text)
    east = re.search(r"(\d+)\s*East", text)
    records.append(
        {
            "kind": "pipeline",
            "name": "South West Queensland Pipeline",
            "code": "SWQP",
            "status": swqp[3],
            "capacity_value": float(west.group(1)) if west else None,
            "capacity_unit": "TJ/day westbound",
            "capacity_note": str(east.group(1)) if east else None,
            "detail": text.replace("\n", " "),
        }
    )
    wb.close()
    return pd.DataFrame(records)


def _dwgm(raw: bytes) -> pd.DataFrame:
    prices = pd.read_excel(io.BytesIO(raw), sheet_name="Prices")
    demand = pd.read_excel(io.BytesIO(raw), sheet_name="Demand")
    morning = prices.loc[prices["Hour"] == 6, ["Gas_Date", "Price"]].rename(
        columns={"Gas_Date": "gas_date", "Price": "price_6am"}
    )
    morning["gas_date"] = pd.to_datetime(morning["gas_date"]).dt.date
    demand = demand.rename(
        columns={
            "Gas_Date": "gas_date",
            "System Demand": "system_demand_tj",
            "GPG": "gpg_tj",
            "Total Demand": "total_demand_tj",
        }
    )
    demand["gas_date"] = pd.to_datetime(demand["gas_date"]).dt.date
    out = demand.merge(morning, on="gas_date", how="left")
    out = out[out["gas_date"] >= datetime(2019, 1, 1).date()]
    return out.sort_values("gas_date")


def _sttm(raw: bytes) -> pd.DataFrame:
    frames = []
    specs = [
        ("SYD price and withdrawals", "syd", "SYD exante_price", "SYD Network_allocation"),
        ("ADL price and withdrawals", "adl", "ADL exante_price", "ADL Network_allocation"),
        ("BRI price and withdrawals", "bri", "BRI exante_price", "BRI Network_allocation"),
    ]
    for sheet, prefix, price, alloc in specs:
        frame = pd.read_excel(io.BytesIO(raw), sheet_name=sheet)
        part = pd.DataFrame(
            {
                "gas_date": pd.to_datetime(frame["DateTime"]).dt.date,
                f"{prefix}_price": pd.to_numeric(frame[price], errors="coerce"),
                f"{prefix}_demand_tj": pd.to_numeric(frame[alloc], errors="coerce") / 1000.0,
            }
        )
        frames.append(part.drop_duplicates("gas_date"))
    out = frames[0]
    for part in frames[1:]:
        out = out.merge(part, on="gas_date", how="outer")
    out = out[out["gas_date"] >= datetime(2019, 1, 1).date()]
    return out.sort_values("gas_date")


def _weather(session: requests.Session, when: str) -> tuple[pd.DataFrame, list[dict]]:
    cities = [
        ("Melbourne", -37.8136, 144.9631, "Australia/Melbourne"),
        ("Sydney", -33.8688, 151.2093, "Australia/Sydney"),
        ("Adelaide", -34.9285, 138.6007, "Australia/Adelaide"),
        ("Hobart", -42.8821, 147.3272, "Australia/Hobart"),
    ]
    frames = []
    notes = []
    for name, lat, lon, tz in cities:
        url = (
            "https://archive-api.open-meteo.com/v1/archive"
            f"?latitude={lat}&longitude={lon}&start_date=2019-01-01&end_date=2026-11-18"
            f"&daily=temperature_2m_mean&timezone={tz.replace('/', '%2F')}"
        )
        payload = _get(session, url)
        body = json.loads(payload)
        daily = body["daily"]
        frame = pd.DataFrame(
            {
                "city": name,
                "gas_date": pd.to_datetime(daily["time"]).date,
                "temp_c": daily["temperature_2m_mean"],
            }
        )
        frame["hdd_18c"] = (18.0 - frame["temp_c"]).clip(lower=0)
        frames.append(frame)
        notes.append(
            {
                "id": f"open_meteo_{name.lower()}",
                "publisher": "Open-Meteo",
                "url": url,
                "licence": "CC BY 4.0",
                "coverage": f"Daily mean temperature at {lat}, {lon}, timezone {tz}",
                "bytes": len(payload),
                "sha256": _sha(payload),
                "accessed_aedt": when,
            }
        )
    return pd.concat(frames, ignore_index=True), notes


def build() -> None:
    SNAPSHOT.mkdir(parents=True, exist_ok=True)
    when = datetime.now(MELBOURNE).strftime("%Y-%m-%d %H:%M %Z")
    session = _session()
    downloaded = []
    blobs = {}
    for source in SOURCES:
        raw = _get(session, source["url"])
        blobs[source["id"]] = raw
        downloaded.append({**source, "bytes": len(raw), "sha256": _sha(raw), "accessed_aedt": when})
    facility, pipes = _facility_extract(blobs["gbb_actual_flow_storage"])
    figures = _gsoo_figures(blobs["gsoo_figures"])
    files = {
        "facility_day": facility,
        "southern_pipe_day": pipes,
        "lng_shipments": _shipments(blobs["gbb_lng_shipments"]),
        "lng_transactions": _transactions(blobs["gbb_lng_transactions"]),
        "facility_reference": _facilities(blobs["gsoo_facilities"]),
        "dwgm_daily": _dwgm(blobs["dwgm_prices"]),
        "sttm_daily": _sttm(blobs["sttm_prices"]),
        **figures,
    }
    weather, weather_notes = _weather(session, when)
    files["hdd_daily"] = weather
    derived = [_write_parquet(frame, SNAPSHOT / f"{name}.parquet") for name, frame in files.items()]
    manifest = {
        "project": "gas-reservation-gap",
        "accessed_aedt": when,
        "note": (
            "Derived parquet files are the committed snapshot. "
            "SHA-256 values on sources are of the bytes downloaded from the URL. "
            "AEMO material is published for public download. The permissions page "
            "did not load from the build environment (HTTP 403), so this snapshot "
            "does not claim a Creative Commons licence for AEMO files. "
            "Open-Meteo is CC BY 4.0. Nothing here was invented."
        ),
        "sources": downloaded + weather_notes,
        "derived": derived,
    }
    (SNAPSHOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    lng = facility[facility["facility_type"] == "LNGEXPORT"]
    by_year = lng.groupby(pd.to_datetime(lng["gas_date"]).dt.year)["demand_tj"].sum() / 1000
    print("accessed", when)
    print("lng PJ", by_year.round(2).to_dict())
    print("files", {item["path"]: item["rows"] for item in derived})


if __name__ == "__main__":
    build()
