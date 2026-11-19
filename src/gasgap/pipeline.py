"""Bronze to gold, then the briefing pages. Reads only the committed snapshot."""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from pathlib import Path

import duckdb
import ibis
import pandas as pd

from gasgap.causal import estimate
from gasgap.charts import bars, lines
from gasgap.insights import write_insights
from gasgap.quality import run_checks
from gasgap.scenario import cover, demand_calibrated, reserve
from gasgap.site import write_site

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "data" / "snapshot"
OUTPUTS = ROOT / "outputs"
TABLES = (
    "facility_day",
    "southern_pipe_day",
    "lng_shipments",
    "lng_transactions",
    "facility_reference",
    "dwgm_daily",
    "sttm_daily",
    "gsoo_adequacy_day",
    "gsoo_annual_gap",
    "gsoo_consumption",
    "gsoo_southern_capacity",
    "hdd_daily",
)
TEAL, AMBER, CORAL, BLUE, VIOLET = "#3ddec0", "#ffc14d", "#ff6b81", "#7eb6ff", "#c9b6ff"


def nice_date(value) -> str:
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    if isinstance(value, datetime):
        value = value.date()
    return f"{value.day} {value.strftime('%B %Y')}"


def whole(value: float) -> str:
    return f"{abs(round(value)):,.0f}"


def one(value: float) -> str:
    return f"{value:,.1f}"


def signed(value: float) -> str:
    mark = "−" if value < 0 else "+"
    return f"{mark}{abs(round(value)):,.0f}"


_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _quarter_start(label: str) -> datetime:
    day, mon, year = str(label).split()[0].split("-")
    return datetime(int(year), _MONTHS[mon[:3].lower()], int(day))


def _next_quarter(when: datetime) -> datetime:
    month = when.month + 3
    year = when.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return datetime(year, month, 1)


def _about_money(value: float) -> str:
    stepped = int(abs(value) * 2 + 0.5) / 2
    if abs(stepped - int(stepped)) < 1e-9:
        return f"${int(stepped)}"
    return f"${stepped:.2f}"


def _high_quarter_note(published: list[dict]) -> str:
    """Note the highest quarter when it sits above the usual range. Otherwise say nothing."""
    if len(published) < 4:
        return ""
    prices = sorted(float(item["price"]) for item in published)
    n = len(prices)

    def quantile(p: float) -> float:
        pos = p * (n - 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        return prices[lo] * (1 - (pos - lo)) + prices[hi] * (pos - lo)

    fence = quantile(0.75) + 1.5 * (quantile(0.75) - quantile(0.25))
    top = max(published, key=lambda item: item["price"])
    if top["price"] <= fence:
        return ""
    index = next(i for i, item in enumerate(published) if item is top)
    if index == 0:
        return ""
    prev = published[index - 1]
    jump = top["price"] - prev["price"]
    if jump < 0.5:
        return ""
    months = (top["dt"].year - prev["dt"].year) * 12 + top["dt"].month - prev["dt"].month
    before = "the quarter before" if months == 3 else "the previous published quarter"
    return (
        f" {top['full']} is the highest at ${top['price']:.2f}, "
        f"about ${int(jump + 0.5)} above {before}, on {one(top['volume'])} PJ."
    )


def _load(db_path: Path) -> None:
    import dlt

    if db_path.exists():
        db_path.unlink()
    shutil.rmtree(OUTPUTS / "dlt", ignore_errors=True)

    def resource(name: str):
        @dlt.resource(name=name, write_disposition="replace")
        def _rows():
            yield pd.read_parquet(SNAPSHOT / f"{name}.parquet")

        return _rows()

    pipe = dlt.pipeline(
        pipeline_name="gasgap",
        destination=dlt.destinations.duckdb(str(db_path)),
        dataset_name="bronze",
        pipelines_dir=str(OUTPUTS / "dlt"),
    )
    pipe.run([resource(name) for name in TABLES])


def _ibis_agrees(db_path: Path, con: duckdb.DuckDBPyConnection) -> float:
    ib = ibis.duckdb.connect(str(db_path))
    fac = ib.table("facility_day", database="bronze")
    lng = fac.filter(fac.facility_type == "LNGEXPORT")
    ibis_total = float(lng.demand_tj.sum().execute())
    sql_total = float(
        con.execute(
            "SELECT SUM(demand_tj) FROM bronze.facility_day WHERE facility_type = 'LNGEXPORT'"
        ).fetchone()[0]
    )
    if abs(ibis_total - sql_total) > 0.01:
        raise SystemExit(f"Ibis {ibis_total} and DuckDB {sql_total} disagree on feed gas")
    daily = lng.group_by("gas_date").aggregate(lng_tj=lng.demand_tj.sum())
    daily_df = daily.execute()
    con.register("ibis_lng", daily_df)
    con.execute("CREATE OR REPLACE TABLE silver.lng_daily AS SELECT * FROM ibis_lng")
    return sql_total


def _gap_color(value: float) -> str:
    return CORAL if value > 0 else TEAL


def build_brief(con: duckdb.DuckDBPyConnection) -> dict:
    manifest = json.loads((SNAPSHOT / "manifest.json").read_text())
    peaks = con.execute(
        """
        SELECT year(gas_date) AS yr,
               arg_max(gas_date, demand_tj) AS peak_date,
               max(demand_tj) AS peak_demand,
               arg_max(max_production_tj, demand_tj) AS production,
               arg_max(production_swqp_tj, demand_tj) AS with_pipe,
               arg_max(plus_deep_storage_tj, demand_tj) AS with_deep,
               arg_max(plus_anticipated_tj, demand_tj) AS full_stack,
               avg(demand_tj) FILTER (WHERE month(gas_date) IN (6, 7, 8)) AS winter_avg
        FROM bronze.gsoo_adequacy_day
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchdf()
    peaks["local_gap"] = peaks["peak_demand"] - peaks["production"]
    peaks["pipe_gap"] = peaks["peak_demand"] - peaks["with_pipe"]
    peaks["full_gap"] = peaks["peak_demand"] - peaks["full_stack"]

    def row_for(year: int) -> pd.Series:
        return peaks.loc[peaks["yr"] == year].iloc[0]

    y2025, y2029, y2030 = row_for(2025), row_for(2029), row_for(2030)
    feed = con.execute(
        """
        SELECT year(gas_date) AS yr, SUM(demand_tj) / 1000 AS pj,
               SUM(demand_tj) / COUNT(DISTINCT gas_date) AS tj_day,
               COUNT(DISTINCT gas_date) AS days
        FROM bronze.facility_day WHERE facility_type = 'LNGEXPORT'
        GROUP BY 1 ORDER BY 1
        """
    ).fetchdf()
    feed_year = {int(r.yr): r for r in feed.itertuples()}
    cargo = con.execute(
        """
        SELECT year(shipment_date) AS yr, SUM(volume_pj) AS pj, MAX(shipment_date) AS last_day
        FROM bronze.lng_shipments GROUP BY 1 ORDER BY 1
        """
    ).fetchdf()
    cargo_year = {int(r.yr): r for r in cargo.itertuples()}
    plants = con.execute(
        """
        SELECT facility_name, SUM(demand_tj) / 1000 AS pj
        FROM bronze.facility_day
        WHERE facility_type = 'LNGEXPORT' AND year(gas_date) = 2025
        GROUP BY 1 ORDER BY 2 DESC
        """
    ).fetchdf()
    outlook_lng = con.execute(
        """
        SELECT year, pj FROM bronze.gsoo_consumption WHERE series = 'lng'
        """
    ).fetchdf()
    lng_outlook = {int(r.year): float(r.pj) for r in outlook_lng.itertuples()}
    domestic = con.execute(
        """
        SELECT year, pj FROM bronze.gsoo_consumption
        WHERE series = 'domestic_ex_gpg_step_change'
        """
    ).fetchdf()
    domestic_year = {int(r.year): float(r.pj) for r in domestic.itertuples()}
    gaps = con.execute(
        """
        SELECT year, annual_gap_pj FROM bronze.gsoo_annual_gap
        WHERE scenario = 'Step Change' AND year BETWEEN 2026 AND 2035
        ORDER BY year
        """
    ).fetchdf()
    iona = con.execute(
        """
        SELECT gas_date, held_tj / 1000 AS pj
        FROM bronze.facility_day
        WHERE facility_name = 'Iona UGS' AND held_tj IS NOT NULL
        ORDER BY gas_date
        """
    ).fetchdf()
    iona["gas_date"] = pd.to_datetime(iona["gas_date"], utc=True)
    iona["day"] = iona["gas_date"].dt.date

    def held(day: str) -> float:
        hit = iona.loc[iona["day"] == date.fromisoformat(day), "pj"]
        if hit.empty:
            raise SystemExit(f"no Iona reading on {day}")
        return float(hit.iloc[0])

    longford = con.execute(
        """
        SELECT year(gas_date) AS yr, AVG(supply_tj) AS avg_tj, MAX(supply_tj) AS max_tj,
               COUNT(*) AS days
        FROM bronze.facility_day WHERE facility_name = 'Longford'
        GROUP BY 1 ORDER BY 1
        """
    ).fetchdf()
    longford_year = {int(r.yr): r for r in longford.itertuples()}
    prices = con.execute(
        """
        SELECT year(d.gas_date) AS yr,
               avg(d.price_6am) FILTER (WHERE month(d.gas_date) IN (6, 7, 8)) AS mel,
               avg(s.syd_price) FILTER (WHERE month(d.gas_date) IN (6, 7, 8)) AS syd,
               avg(s.adl_price) FILTER (WHERE month(d.gas_date) IN (6, 7, 8)) AS adl
        FROM bronze.dwgm_daily d
        LEFT JOIN bronze.sttm_daily s ON d.gas_date = s.gas_date
        WHERE d.price_6am IS NOT NULL
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchdf()
    price_year = {int(r.yr): r for r in prices.itertuples()}
    cold = con.execute(
        """
        SELECT
          CASE
            WHEN h.hdd_18c < 0.5 THEN 'None'
            WHEN h.hdd_18c < 3 THEN 'Mild'
            WHEN h.hdd_18c < 6 THEN 'Cold'
            ELSE 'Very cold'
          END AS band,
          avg(d.price_6am) AS price,
          COUNT(*) AS days
        FROM bronze.dwgm_daily d
        JOIN bronze.hdd_daily h ON d.gas_date = h.gas_date AND h.city = 'Melbourne'
        WHERE d.price_6am IS NOT NULL AND d.gas_date >= DATE '2023-01-01'
        GROUP BY 1
        """
    ).fetchdf()
    band_order = ["None", "Mild", "Cold", "Very cold"]
    cold["ord"] = cold["band"].map({name: i for i, name in enumerate(band_order)})
    cold = cold.sort_values("ord")
    deals = con.execute(
        """
        SELECT quarter_label, price_per_gj, volume_pj
        FROM bronze.lng_transactions
        WHERE price_per_gj IS NOT NULL
        ORDER BY quarter_label
        """
    ).fetchdf()
    pipe_peak = float(
        con.execute(
            """
            SELECT SUM(pipe_demand_tj) FROM bronze.southern_pipe_day
            WHERE gas_date = ?
            """,
            [pd.Timestamp(y2025.peak_date).date()],
        ).fetchone()[0]
    )
    ref = {
        r.code: r
        for r in con.execute("SELECT * FROM bronze.facility_reference").fetchdf().itertuples()
    }
    daily = con.execute(
        """
        SELECT CAST(f.gas_date AS DATE) AS gas_date,
               f.lng_tj,
               lf.supply_tj AS longford_tj,
               d.price_6am,
               d.total_demand_tj,
               h.hdd_18c AS hdd_mel
        FROM silver.lng_daily f
        LEFT JOIN bronze.facility_day lf
          ON f.gas_date = lf.gas_date AND lf.facility_name = 'Longford'
        LEFT JOIN bronze.dwgm_daily d ON f.gas_date = d.gas_date
        LEFT JOIN bronze.hdd_daily h ON f.gas_date = h.gas_date AND h.city = 'Melbourne'
        """
    ).fetchdf()
    daily["gas_date"] = pd.to_datetime(daily["gas_date"])
    causal = estimate(daily)

    feed_2025 = float(feed_year[2025].pj)
    cargo_2025 = float(cargo_year[2025].pj)
    day_reserve = reserve(float(feed_year[2025].tj_day))
    cover_2029 = cover(day_reserve, float(y2029.full_gap))
    cover_2030 = cover(day_reserve, float(y2030.full_gap))
    annual_rows = []
    for rec in gaps.itertuples():
        reserved_pj = reserve(lng_outlook[int(rec.year)])
        piece = cover(reserved_pj, float(rec.annual_gap_pj))
        annual_rows.append({"year": int(rec.year), "reserved": reserved_pj, **piece})
    cal_2028 = demand_calibrated(domestic_year[2028], lng_outlook[2028])
    draw_2025 = held("2025-05-31") - held("2025-08-31")
    draw_2026 = held("2026-05-31") - held("2026-08-31")
    iona_now = float(iona.iloc[-1].pj)
    iona_now_date = iona.iloc[-1].gas_date
    lf_2022 = float(longford_year[2022].avg_tj)
    lf_2026 = float(longford_year[2026].avg_tj)
    drop = (lf_2022 - lf_2026) / lf_2022 * 100

    def gap_bars(column: str) -> str:
        rows = []
        for rec in peaks.itertuples():
            value = float(getattr(rec, column))
            rows.append(
                {
                    "name": str(int(rec.yr)),
                    "value": value,
                    "label": signed(value),
                    "color": _gap_color(value),
                }
            )
        return bars(
            rows, title="Peak-day gap", y_label="Terajoules. Above the line is a shortfall."
        )

    def exact_gaps(column: str) -> str:
        bits = []
        for rec in peaks.itertuples():
            kind = "actual" if int(rec.yr) <= 2025 else "outlook"
            bits.append(
                f"{int(rec.yr)} {kind} {nice_date(rec.peak_date)}: demand {one(rec.peak_demand)} TJ, "
                f"gap {signed(getattr(rec, column))} TJ"
            )
        return "Exact values. " + ". ".join(bits) + "."

    annual_chart = bars(
        [
            {
                "name": str(rec["year"]),
                "value": rec["reserved"],
                "label": f"{rec['reserved']:.0f}",
                "color": TEAL,
            }
            for rec in annual_rows
            if rec["year"] <= 2032
        ],
        title="Reserved gas",
        y_label="Petajoules a year, one fifth of the outlook LNG line.",
    )
    # The annual gap is small beside the reserve. A second scale would hide it,
    # so the exact line carries the gap and the bars carry the reserve.
    annual_exact = (
        "Exact values. "
        + ". ".join(
            f"{rec['year']}: reserve {one(rec['reserved'])} PJ, annual gap {one(rec['gap'])} PJ, "
            f"left over {one(rec['left_over'])} PJ"
            for rec in annual_rows
            if rec["year"] <= 2032
        )
        + "."
    )

    plant_colors = {
        "Australia Pacific LNG": TEAL,
        "QCLNG LNG Plant": BLUE,
        "GLNG (Curtis Island)": AMBER,
    }
    plant_names = {
        "Australia Pacific LNG": "APLNG",
        "QCLNG LNG Plant": "QCLNG",
        "GLNG (Curtis Island)": "GLNG",
    }
    plant_bars = bars(
        [
            {
                "name": plant_names[r.facility_name],
                "value": float(r.pj),
                "label": f"{float(r.pj):.0f}",
                "color": plant_colors[r.facility_name],
            }
            for r in plants.itertuples()
        ],
        title="2025 feed gas",
        y_label="Petajoules taken by each plant in 2025.",
    )
    compare_bars = bars(
        [
            {
                "name": "Cargo 24",
                "value": float(cargo_year[2024].pj),
                "label": f"{cargo_year[2024].pj:.0f}",
                "color": BLUE,
            },
            {
                "name": "Taken 24",
                "value": float(feed_year[2024].pj),
                "label": f"{feed_year[2024].pj:.0f}",
                "color": TEAL,
            },
            {"name": "Cargo 25", "value": cargo_2025, "label": f"{cargo_2025:.0f}", "color": BLUE},
            {"name": "Taken 25", "value": feed_2025, "label": f"{feed_2025:.0f}", "color": TEAL},
        ],
        title="Cargoes and gas taken",
        y_label="Petajoules.",
    )
    month = iona[iona["day"] >= date(2024, 1, 1)].copy()
    month["label"] = month["gas_date"].dt.strftime("%b %y")
    month_end = month.groupby(month["gas_date"].dt.to_period("M"), as_index=False).tail(1)
    iona_line = lines(
        [
            {
                "name": "Iona",
                "color": TEAL,
                "points": [(r.label, float(r.pj)) for r in month_end.itertuples()],
            }
        ],
        title="Iona inventory",
        y_label="Petajoules in storage, month end.",
    )
    draw_bars = bars(
        [
            {
                "name": "2024",
                "value": held("2024-05-31") - held("2024-08-31"),
                "label": "",
                "color": BLUE,
            },
            {"name": "2025", "value": draw_2025, "label": f"{draw_2025:.1f}", "color": AMBER},
            {"name": "2026", "value": draw_2026, "label": f"{draw_2026:.1f}", "color": TEAL},
        ],
        title="Winter draw",
        y_label="Petajoules, 31 May minus 31 August.",
    )
    # Fill the 2024 label now that the value exists.
    draw_2024 = held("2024-05-31") - held("2024-08-31")
    draw_bars = bars(
        [
            {"name": "2024", "value": draw_2024, "label": f"{draw_2024:.1f}", "color": BLUE},
            {"name": "2025", "value": draw_2025, "label": f"{draw_2025:.1f}", "color": AMBER},
            {"name": "2026", "value": draw_2026, "label": f"{draw_2026:.1f}", "color": TEAL},
        ],
        title="Winter draw",
        y_label="Petajoules, 31 May minus 31 August.",
    )
    lf_bars = bars(
        [
            {
                "name": str(yr) if yr < 2026 else "2026*",
                "value": float(longford_year[yr].avg_tj),
                "label": f"{longford_year[yr].avg_tj:.0f}",
                "color": CORAL if yr >= 2025 else BLUE,
            }
            for yr in range(2022, 2027)
        ],
        title="Longford",
        y_label="Average terajoules a day. 2026 is the year so far.",
    )

    def season_bars(year: int) -> str:
        rec = price_year[year]
        return bars(
            [
                {
                    "name": "Melbourne",
                    "value": float(rec.mel),
                    "label": f"{rec.mel:.2f}",
                    "color": TEAL,
                },
                {
                    "name": "Sydney",
                    "value": float(rec.syd),
                    "label": f"{rec.syd:.2f}",
                    "color": BLUE,
                },
                {
                    "name": "Adelaide",
                    "value": float(rec.adl),
                    "label": f"{rec.adl:.2f}",
                    "color": AMBER,
                },
            ],
            title=f"Winter {year}",
            y_label="Dollars per gigajoule, June to August.",
        )

    cold_bars = bars(
        [
            {"name": r.band, "value": float(r.price), "label": f"{r.price:.2f}", "color": BLUE}
            for r in cold.itertuples()
        ],
        title="Price and cold",
        y_label="Average Melbourne 6am price, dollars per gigajoule, from 2023.",
    )
    mild_price = float(cold.loc[cold["band"] == "Mild", "price"].iloc[0])
    very_price = float(cold.loc[cold["band"] == "Very cold", "price"].iloc[0])
    cold_premium = very_price - mild_price
    if cold_premium >= 0.25:
        cold_title = (
            f"Very cold days cost about {_about_money(cold_premium)} more than mild ones; "
            "export volume made no clear difference"
        )
    else:
        cold_title = (
            "Very cold and mild days cost about the same; export volume made no clear difference"
        )

    by_start = {_quarter_start(r.quarter_label): r for r in deals.itertuples()}
    cursor = min(by_start)
    last_quarter = max(by_start)
    quarters: list[tuple[datetime, object | None]] = []
    while cursor <= last_quarter:
        quarters.append((cursor, by_start.get(cursor)))
        cursor = _next_quarter(cursor)
    deal_rows: list[dict] = []
    exact_bits: list[str] = []
    published: list[dict] = []
    for when, rec in quarters:
        quarter = (when.month - 1) // 3 + 1
        short = f"Q{quarter} {when.year % 100:02d}"
        full = f"Q{quarter} {when.year}"
        if rec is None:
            deal_rows.append({"name": short, "gap": True})
            exact_bits.append(f"{full} not published")
            continue
        price = float(rec.price_per_gj)
        deal_rows.append({"name": short, "value": price, "label": f"{price:.0f}", "color": VIOLET})
        exact_bits.append(f"{full} ${price:.2f}")
        published.append({"full": full, "price": price, "volume": float(rec.volume_pj), "dt": when})
    deal_bars = bars(
        deal_rows,
        title="Published transaction prices",
        y_label="Dollars per gigajoule. Missing quarters are marked.",
    )
    deal_legend = [{"name": "Volume-weighted price where the cell was filled", "color": VIOLET}]
    if any(rec is None for _, rec in quarters):
        deal_legend.append({"name": "No published price", "color": AMBER})
    deal_exact = (
        "Exact values, dollars per gigajoule. "
        + " · ".join(exact_bits)
        + "."
        + _high_quarter_note(published)
        + " This is not a landed export price."
    )
    swqp = float(ref["SWQP"].capacity_value)
    iona_rate = float(ref["IONA"].capacity_value)
    as_of = (
        manifest["accessed_aedt"]
        .replace("2026-11-20", "20 November 2026")
        .replace(" AEDT", " am AEDT")
    )
    # accessed string is "2026-11-20 09:08 AEDT"
    stamp = datetime.strptime(manifest["accessed_aedt"].replace(" AEDT", ""), "%Y-%m-%d %H:%M")
    as_of = stamp.strftime("%-d %B %Y, %-I:%M %p").replace("AM", "am").replace("PM", "pm") + " AEDT"
    gas_through = nice_date(iona_now_date)
    price_through = nice_date(
        con.execute("SELECT MAX(gas_date) FROM bronze.dwgm_daily").fetchone()[0]
    )
    ship_through = nice_date(
        con.execute("SELECT MAX(shipment_date) FROM bronze.lng_shipments").fetchone()[0]
    )

    cover_local_2029 = cover(day_reserve, float(y2029.pipe_gap))
    pages = {
        "gap": {
            "title": "The gap",
            "kicker": "Southern winter",
            "headline": (
                f"A fifth of one export day covers the {whole(y2029.full_gap)} TJ hole "
                f"in 2029. The pipe still has to carry it."
            ),
            "sub": (
                "2024 and 2025 are days that happened. Later years are AEMO's outlook. "
                "A positive number is gas still missing after that source."
            ),
            "kpis": [
                {
                    "kicker": "2029 peak day, full outlook",
                    "value": whole(y2029.full_gap),
                    "unit": "TJ short",
                    "take": f"{nice_date(y2029.peak_date)}, after local gas, the pipe and storage",
                    "tone": "tight",
                },
                {
                    "kicker": "A fifth of a 2025 export day",
                    "value": whole(day_reserve),
                    "unit": "TJ",
                    "take": "Average gas the three plants took, times one fifth",
                    "tone": "ok",
                },
                {
                    "kicker": "Left over that day, on paper",
                    "value": whole(cover_2029["left_over"]),
                    "unit": "TJ",
                    "take": "Only if that gas can reach the southern cities",
                    "tone": "watch",
                },
                {
                    "kicker": "Southbound pipe, published limit",
                    "value": whole(swqp),
                    "unit": "TJ/day",
                    "take": "Wallumbilla to Moomba. Already counted in the outlook",
                    "tone": "watch",
                },
            ],
            "hero": {
                "title": "On the busiest day, how much is still missing?",
                "legend": [
                    {"name": "Short", "color": CORAL},
                    {"name": "Spare", "color": TEAL},
                ],
                "panes": [
                    {
                        "id": "full",
                        "pill": "With storage",
                        "svg": gap_bars("full_gap"),
                        "exact": exact_gaps("full_gap"),
                    },
                    {
                        "id": "pipe",
                        "pill": "Pipe, no storage",
                        "svg": gap_bars("pipe_gap"),
                        "exact": exact_gaps("pipe_gap"),
                    },
                    {
                        "id": "local",
                        "pill": "Local gas only",
                        "svg": gap_bars("local_gap"),
                        "exact": exact_gaps("local_gap"),
                    },
                ],
            },
            "support": [
                {
                    "title": "Across a whole year, a fifth of exports dwarfs the annual gap",
                    "svg": annual_chart,
                    "legend": [{"name": "One fifth of that year's outlook LNG", "color": TEAL}],
                    "exact": annual_exact,
                }
            ],
            "means": (
                f"In 2029 the outlook's busiest day is {whole(y2029.full_gap)} TJ short even after "
                f"local gas, the north-south pipe and storage. A fifth of an average 2025 day at the "
                f"export plants is {whole(day_reserve)} TJ, so the volume is larger than that hole, "
                f"with {whole(cover_2029['left_over'])} TJ left over on paper. "
                f"Without storage the same day is {whole(y2029.pipe_gap)} TJ short, and "
                f"{whole(day_reserve)} TJ does not cover it "
                f"({whole(cover_local_2029['still_short'])} TJ still missing). "
                f"The published southbound pipe is {whole(swqp)} TJ a day and the outlook already uses it. "
                f"Gas held back at Gladstone is not in Melbourne the same day. "
                f"By 2030 the full-outlook hole is {whole(y2030.full_gap)} TJ, still under a fifth of one 2025 day, "
                f"with {whole(cover_2030['left_over'])} TJ left over on paper."
            ),
        },
        "exports": {
            "title": "Exports",
            "kicker": "Gladstone and Curtis Island",
            "headline": (
                f"The three plants shipped {one(cargo_2025)} PJ in 2025. "
                f"A fifth of that is {one(reserve(cargo_2025))} PJ."
            ),
            "sub": "Cargo energy is what left on ships. Feed gas is what the plants took from the east-coast system. Feed gas runs higher.",
            "kpis": [
                {
                    "kicker": "Shipped in 2025",
                    "value": one(cargo_2025),
                    "unit": "PJ",
                    "take": "Cargo energy at Curtis Island",
                    "tone": "ok",
                },
                {
                    "kicker": "A fifth of those cargoes",
                    "value": one(reserve(cargo_2025)),
                    "unit": "PJ",
                    "take": "The cap, applied to 2025 exports",
                    "tone": "ok",
                },
                {
                    "kicker": "Gas the plants took, 2025",
                    "value": one(feed_2025),
                    "unit": "PJ",
                    "take": "Bulletin board. Matches AEMO's LNG line",
                    "tone": "watch",
                },
                {
                    "kicker": "2028 cap against home use",
                    "value": one(float(cal_2028["obligation"])),
                    "unit": "PJ",
                    "take": "Home and business use plus 10% is larger, so the cap binds",
                    "tone": "watch",
                },
            ],
            "hero": {
                "title": "Where the 2025 gas went, plant by plant",
                "legend": [
                    {"name": "APLNG", "color": TEAL},
                    {"name": "QCLNG", "color": BLUE},
                    {"name": "GLNG", "color": AMBER},
                ],
                "panes": [
                    {
                        "id": "plants",
                        "pill": "By plant",
                        "svg": plant_bars,
                        "exact": "Exact values, feed gas in 2025. "
                        + ". ".join(
                            f"{plant_names[r.facility_name]} {one(r.pj)} PJ"
                            for r in plants.itertuples()
                        )
                        + f". Published plant sizes: APLNG {one(ref['APLNG'].capacity_value)} Mt a year, "
                        f"QCLNG {one(ref['QCLNG'].capacity_value)}, GLNG {one(ref['GLNG'].capacity_value)}.",
                    },
                    {
                        "id": "cargo",
                        "pill": "Ships and gas taken",
                        "svg": compare_bars,
                        "exact": (
                            f"Exact values. 2024 cargoes {one(cargo_year[2024].pj)} PJ, "
                            f"feed gas {one(feed_year[2024].pj)} PJ. "
                            f"2025 cargoes {one(cargo_2025)} PJ, feed gas {one(feed_2025)} PJ. "
                            f"2026 cargoes so far {one(cargo_year[2026].pj)} PJ through {ship_through}. "
                            f"2026 feed gas so far {one(feed_year[2026].pj)} PJ over {int(feed_year[2026].days)} days."
                        ),
                    },
                ],
            },
            "support": [],
            "means": (
                f"A fifth of 2025 cargoes is {one(reserve(cargo_2025))} PJ. "
                f"The southern annual gap in the middle outlook is "
                f"{one(next(r['gap'] for r in annual_rows if r['year'] == 2029))} PJ in 2029 and "
                f"{one(next(r['gap'] for r in annual_rows if r['year'] == 2030))} PJ in 2030. "
                f"Using that year's own LNG outlook, the fifth left over after the annual gap is "
                f"{one(next(r['left_over'] for r in annual_rows if r['year'] == 2029))} PJ in 2029 and "
                f"{one(next(r['left_over'] for r in annual_rows if r['year'] == 2030))} PJ in 2030. "
                f"In 2028, home and business use plus 10% is {one(float(cal_2028['asked']))} PJ and "
                f"a fifth of outlook LNG is {one(float(cal_2028['cap']))} PJ, so the obligation is the cap. "
                f"The annual gap that year is zero. The reserved gas is much larger than the annual gap "
                f"and smaller than a full year of home and business use."
            ),
        },
        "storage": {
            "title": "Storage",
            "kicker": "Iona and Longford",
            "headline": (
                f"Iona gave up {one(draw_2026)} PJ in winter 2026 and was full before it started."
            ),
            "sub": (
                f"Longford's average day has fallen from {whole(lf_2022)} TJ in 2022 "
                f"to {whole(lf_2026)} TJ so far in 2026."
            ),
            "kpis": [
                {
                    "kicker": "Iona, winter 2026",
                    "value": one(draw_2026),
                    "unit": "PJ drawn",
                    "take": f"From {one(held('2026-05-31'))} PJ on 31 May to {one(held('2026-08-31'))} on 31 August",
                    "tone": "watch",
                },
                {
                    "kicker": "Iona now",
                    "value": one(iona_now),
                    "unit": "PJ",
                    "take": f"{gas_through}. Lower than it was at the end of winter",
                    "tone": "tight",
                },
                {
                    "kicker": "Longford, since 2022",
                    "value": f"{drop:.0f}%",
                    "unit": "lower",
                    "take": f"Average day {whole(lf_2022)} TJ then, {whole(lf_2026)} TJ so far this year",
                    "tone": "tight",
                },
                {
                    "kicker": "Iona can send out",
                    "value": whole(iona_rate),
                    "unit": "TJ/day",
                    "take": "Published rate. The 2025 and 2026 outlooks add this on top of the pipe",
                    "tone": "ok",
                },
            ],
            "hero": {
                "title": "Iona was full at the start of winter, then ran down",
                "legend": [{"name": "Reported inventory, month end", "color": TEAL}],
                "panes": [
                    {
                        "id": "level",
                        "pill": "Level",
                        "svg": iona_line,
                        "exact": (
                            f"Exact values, petajoules. 31 May 2025 {one(held('2025-05-31'))}. "
                            f"31 August 2025 {one(held('2025-08-31'))}. "
                            f"31 May 2026 {one(held('2026-05-31'))}. "
                            f"31 August 2026 {one(held('2026-08-31'))}. "
                            f"{gas_through} {one(iona_now)}. "
                            f"Published storage: {ref['IONA'].capacity_note} "
                            f"Heytesbury addition listed at {ref['HUGS'].capacity_note} "
                            f"and {whole(ref['HUGS'].capacity_value)} TJ a day."
                        ),
                    }
                ],
            },
            "support": [
                {
                    "title": "Each winter Iona supplied more than 10 PJ",
                    "svg": draw_bars,
                    "legend": [{"name": "31 May minus 31 August", "color": AMBER}],
                    "exact": (
                        f"Exact values. 2024 draw {one(draw_2024)} PJ. "
                        f"2025 draw {one(draw_2025)} PJ. 2026 draw {one(draw_2026)} PJ."
                    ),
                },
                {
                    "title": "Longford's average day is about two fifths lower than 2022",
                    "svg": lf_bars,
                    "legend": [{"name": "Average supply, terajoules a day", "color": BLUE}],
                    "exact": "Exact values. "
                    + ". ".join(
                        f"{yr}: average {one(longford_year[yr].avg_tj)} TJ, "
                        f"highest day {one(longford_year[yr].max_tj)} TJ"
                        + (" (year so far)" if yr == 2026 else "")
                        for yr in range(2022, 2027)
                    )
                    + f". The facility table lists {whole(ref['LONGFORD'].capacity_value)} TJ a day "
                    f"in the capacity column and about {whole(float(ref['LONGFORD'].capacity_note))} TJ a day "
                    "in the description.",
                },
            ],
            "means": (
                f"Iona started winter at {one(held('2026-05-31'))} PJ, at or slightly above "
                f"its published 24.4 PJ maximum, and ended August at {one(held('2026-08-31'))} PJ. "
                f"There was no empty tank in late May to park extra gas ahead of winter. "
                f"By {gas_through} the level was {one(iona_now)} PJ, still falling after winter. "
                f"Longford, the main Gippsland plant, averaged {whole(lf_2026)} TJ a day so far in 2026 "
                f"against {whole(lf_2022)} in 2022. "
                f"Southern maximum daily production in the outlook falls from "
                f"{whole(y2025.production)} TJ in 2025 to {whole(y2029.production)} TJ in 2029. "
                "That decline, not a sudden jump in use, is why the peak day stops fitting."
            ),
        },
        "prices": {
            "title": "Prices",
            "kicker": "Wholesale prices",
            "headline": (
                f"Winter mornings in 2026 averaged ${price_year[2026].mel:.2f} in Melbourne, "
                f"down from ${price_year[2022].mel:.2f} in 2022."
            ),
            "sub": causal["plain"],
            "kpis": [
                {
                    "kicker": "Melbourne, winter 2026",
                    "value": f"{price_year[2026].mel:.2f}",
                    "unit": "$/GJ",
                    "take": "6am price, June to August",
                    "tone": "ok",
                },
                {
                    "kicker": "Melbourne, winter 2022",
                    "value": f"{price_year[2022].mel:.2f}",
                    "unit": "$/GJ",
                    "take": "Same clock, four winters earlier",
                    "tone": "watch",
                },
                {
                    "kicker": "Sydney, winter 2026",
                    "value": f"{price_year[2026].syd:.2f}",
                    "unit": "$/GJ",
                    "take": "Daily market, June to August",
                    "tone": "ok",
                },
                {
                    "kicker": "Adelaide, winter 2026",
                    "value": f"{price_year[2026].adl:.2f}",
                    "unit": "$/GJ",
                    "take": "Daily market, June to August",
                    "tone": "ok",
                },
            ],
            "hero": {
                "title": "Winter prices are far below the 2022 spike",
                "legend": [
                    {"name": "Melbourne 6am", "color": TEAL},
                    {"name": "Sydney", "color": BLUE},
                    {"name": "Adelaide", "color": AMBER},
                ],
                "panes": [
                    {
                        "id": "y2026",
                        "pill": "Winter 2026",
                        "svg": season_bars(2026),
                        "exact": (
                            f"Exact values, winter 2026. Melbourne ${price_year[2026].mel:.2f}, "
                            f"Sydney ${price_year[2026].syd:.2f}, Adelaide ${price_year[2026].adl:.2f}."
                        ),
                    },
                    {
                        "id": "y2025",
                        "pill": "Winter 2025",
                        "svg": season_bars(2025),
                        "exact": (
                            f"Exact values, winter 2025. Melbourne ${price_year[2025].mel:.2f}, "
                            f"Sydney ${price_year[2025].syd:.2f}, Adelaide ${price_year[2025].adl:.2f}."
                        ),
                    },
                    {
                        "id": "y2022",
                        "pill": "Winter 2022",
                        "svg": season_bars(2022),
                        "exact": (
                            f"Exact values, winter 2022. Melbourne ${price_year[2022].mel:.2f}, "
                            f"Sydney ${price_year[2022].syd:.2f}, Adelaide ${price_year[2022].adl:.2f}."
                        ),
                    },
                ],
            },
            "support": [
                {
                    "title": cold_title,
                    "svg": cold_bars,
                    "legend": [{"name": "Average 6am price since 2023", "color": BLUE}],
                    "exact": "Exact values. "
                    + ". ".join(
                        f"{r.band}: ${r.price:.2f} over {int(r.days)} days"
                        for r in cold.itertuples()
                    )
                    + ". Cold is a Melbourne heating degree at base 18°C: none under 0.5, mild to 3, cold to 6, then very cold.",
                },
                {
                    "title": "AEMO published a transaction price for some quarters only",
                    "svg": deal_bars,
                    "legend": deal_legend,
                    "exact": deal_exact,
                },
            ],
            "means": causal["plain"]
            + " Prices are what the domestic markets cleared. They are not a quote for holding gas back from export, and these free files do not include an export netback.",
        },
    }
    return {
        "as_of": as_of,
        "gas_through": gas_through,
        "price_through": price_through,
        "shipment_through": ship_through,
        "feed_2024_pj": one(feed_year[2024].pj),
        "gsoo_lng_2024_pj": one(lng_outlook[2024]),
        "cargo_2025_pj": one(cargo_2025),
        "feed_2025_pj": one(feed_2025),
        "peak_2025_date": nice_date(y2025.peak_date),
        "peak_2025_demand_tj": one(y2025.peak_demand),
        "pipe_peak_2025_tj": one(pipe_peak),
        "causal": causal,
        "pages": pages,
        "checks_note": {
            "day_reserve_tj": day_reserve,
            "gap_2029": float(y2029.full_gap),
            "gap_2030": float(y2030.full_gap),
            "cargo_reserve_2025": reserve(cargo_2025),
            "cap_binds_2028": bool(cal_2028["cap_binds"]),
        },
    }


def run() -> dict:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    db_path = OUTPUTS / "gas.duckdb"
    _load(db_path)
    con = duckdb.connect(str(db_path))
    con.execute("CREATE SCHEMA IF NOT EXISTS silver")
    _ibis_agrees(db_path, con)
    run_checks(con)
    brief = build_brief(con)
    # JSON-safe causal copy for the contract tests.
    payload = {
        "as_of": brief["as_of"],
        "checks": brief["checks_note"],
        "causal_plain": brief["causal"]["plain"],
        "causal_per_100": brief["causal"]["linear_per_100tj"],
        "ci_crosses_zero": brief["causal"]["ci_crosses_zero"],
    }
    (OUTPUTS / "brief.json").write_text(json.dumps(payload, indent=2) + "\n")
    write_site(brief, ROOT)
    write_insights(brief, ROOT)
    con.close()
    return brief


if __name__ == "__main__":
    run()
    print("pipeline wrote site/ and INSIGHTS.md")
