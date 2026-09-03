from datetime import date

import httpx

from energy_scraper.collectors.eex import PRODUCTS, maturity_code, parse_table_payload
from energy_scraper.collectors.nordpool import contract_resolution, parse_payload
from energy_scraper.core.calendars import local_intervals
from energy_scraper.core.error_classifier import classify_error


def test_dst_quarter_hour_counts():
    assert len(local_intervals(date(2026,3,29),15))==92
    assert len(local_intervals(date(2026,7,1),15))==96
    assert len(local_intervals(date(2026,10,25),15))==100


def test_maturity_mapping(): assert maturity_code(2027)=="202701"


def test_eex_dynamic_header_and_null():
    payload={"header":["tradeDate","settlPx","shortCode","maturityDate","totVolTrdd"],"data":[["2026-08-25",68.2,"F7BY","2027-01-01",None]],"uOM":"MWh","currency":"EUR"}
    rows,header=parse_table_payload(payload,PRODUCTS["F7BY"],date(2026,8,25),date(2026,8,31))
    assert rows[0]["settlement"]==68.2 and rows[0]["traded_volume_mwh"] is None


def test_eex_ignores_rolling_fallback_for_wrong_maturity():
    payload={"header":["shortCode","maturityDate","tradeDate","settlPx"],"data":[["F7BY",202701,"2026-08-25",68.2]]}
    rows,_=parse_table_payload(payload,PRODUCTS["F7BY"],date(2026,8,1),date(2026,8,31),2026)
    assert rows == []


def test_nordpool_qh_nulls_preserved():
    payload={"deliveryArea":"FR","deliveryDateCET":"2026-08-25","updateTime":"2026-08-25T12:00:00Z","priceUnit":"EUR/MWh","volumeUnit":"MW","contracts":[{"contractId":"1","contractName":"QH 12:00-12:15","openPrice":1,"highPrice":3,"lowPrice":1,"closePrice":2,"averagePrice":2,"averagePriceLast1H":None,"averagePriceLast3H":None,"volume":None}]}
    row=parse_payload(payload,"FR")[0]
    assert row["contract_type"]=="QH" and row["resolution_minutes"]==15 and row["vwap_1h"] is None and row["volume"] is None


def test_contract_resolutions():
    assert contract_resolution("PH 01")==('PH',60)
    assert contract_resolution("HH 01A")==('HH',30)


def test_error_classification():
    req=httpx.Request("GET","https://example.test")
    response=httpx.Response(503,request=req)
    assert classify_error(httpx.HTTPStatusError("x",request=req,response=response))==("RETRYABLE_REMOTE_ERROR",503)
