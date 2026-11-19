import json
from pathlib import Path

import pytest

from gasgap.pipeline import run

ROOT = Path(__file__).resolve().parents[1]
BANNED = (
    "duckdb",
    "iceberg",
    "feldera",
    "nessie",
    "dlt",
    "ibis",
    "schema",
    "regression",
    "placebo",
    "parquet",
    "dowhy",
    "econml",
    "p-value",
)


@pytest.fixture(scope="module")
def built():
    brief = run()
    return brief


def test_headline_quantities(built):
    checks = built["checks_note"]
    assert 700 < checks["day_reserve_tj"] < 850
    assert 100 < checks["gap_2029"] < 200
    assert 200 < checks["gap_2030"] < 400
    assert checks["cap_binds_2028"] is True
    assert "not come with a clearly" in built["causal"]["plain"]


def test_pages_are_plain_language(built):
    for name in ("index.html", "exports.html", "storage.html", "prices.html"):
        text = (ROOT / "site" / name).read_text().lower()
        assert text.count('class="kpi ') == 4
        assert "what this means" in text
        assert "aedt" in text
        assert "exact values" in text
        for word in BANNED:
            assert word not in text, word
    notes = (ROOT / "INSIGHTS.md").read_text()
    assert "DoWhy" in notes
    assert "LinearDML" in notes
    payload = json.loads((ROOT / "outputs" / "brief.json").read_text())
    assert payload["checks"]["gap_2029"] == pytest.approx(built["checks_note"]["gap_2029"])
