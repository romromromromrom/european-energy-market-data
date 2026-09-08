import json
from datetime import date

import pytest

from energy_scraper.collectors.eex import PRODUCTS, default_maturities, normalize_maturity
from energy_scraper.collectors.epex_spot import parse_epex_ajax
from energy_scraper.collectors.rte_balancing import parse_rte_payload


def test_eex_month_and_quarter_maturities_cross_year():
    assert {"F7BM", "F7BQ", "F7PM", "F7PQ"} <= set(PRODUCTS)
    assert default_maturities(date(2026, 12, 20), "Month") == ["202701", "202702", "202703"]
    assert default_maturities(date(2026, 12, 20), "Quarter") == ["202701", "202704", "202707", "202710"]
    assert normalize_maturity("202702", "Month") == "MON-2027-02"
    assert normalize_maturity("202610", "Quarter") == "Q4-2026"


def test_epex_observed_fixture_regression():
    with open("tests/test_spot.txt", encoding="utf-8") as handle:
        payload = json.load(handle)
    rows, index = parse_epex_ajax(payload, date(2026, 9, 8), date(2026, 9, 7))
    assert len(rows) == 96
    assert rows[0]["period_start"].endswith("+02:00")
    assert rows[0]["buy_volume_mwh"] == 2792.9
    assert rows[-1]["period_end"].startswith("2026-09-09T00:00:00")
    assert index["baseload_eur_mwh"] == 149.69
    assert index["reconstructed_baseload_eur_mwh"] == pytest.approx(149.692, abs=.001)
    assert index["tb2_eur_mwh"] == pytest.approx(185.32, abs=.01)
    assert index["tb4_eur_mwh"] == pytest.approx(169.56, abs=.01)


def test_rte_observed_schema_preserves_nulls():
    payload = {"values": [], "updatedDate": "2026-09-08T08:27:01+02:00"}
    from energy_scraper.core.calendars import local_intervals
    for _, local in local_intervals(date(2026, 9, 7), 15):
        payload["values"].append({"date": local.isoformat(), "fcr": {"rise": 2.23, "drop": 7.79},
                                  "afrr": {"rise": None, "drop": 25.28}, "deltap": -40.91})
    rows, updated = parse_rte_payload(payload, date(2026, 9, 7))
    assert updated == payload["updatedDate"]
    assert len(rows) == 96 * 5
    assert any(row["source_field"] == "afrr" and row["direction"] == "rise" and row["value"] is None for row in rows)
    assert {row["unit"] for row in rows} == {"MWh"}
