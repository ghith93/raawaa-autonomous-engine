from datetime import datetime, timezone
from decimal import Decimal

from arb_engine.dexscreener import DexScreenerClient
from arb_engine.scanner import find_spreads


def pair(dex: str, price: str, address: str):
    now = datetime.now(timezone.utc)
    return {
        "chainId": "ethereum", "dexId": dex, "pairAddress": address,
        "baseToken": {"address": "0xaaa", "symbol": "AAA"},
        "quoteToken": {"address": "0xbbb", "symbol": "USDC"},
        "priceUsd": price, "liquidity": {"usd": 50000},
        "volume": {"h24": 10000}, "pairCreatedAt": int(now.timestamp() * 1000),
        "url": f"https://dex.example/{address}",
    }


def test_parser_keeps_exact_decimal_and_rejects_bad_rows():
    now = datetime.now(timezone.utc)
    parsed = DexScreenerClient._parse_pair(pair("dex-a", "1.00000001", "p1"), now)
    assert parsed is not None
    assert parsed.price_usd == Decimal("1.00000001")
    assert DexScreenerClient._parse_pair({"priceUsd": "NaN"}, now) is None


def test_scanner_finds_cross_dex_spread_same_chain_and_pair():
    now = datetime.now(timezone.utc)
    rows = [
        DexScreenerClient._parse_pair(pair("dex-a", "1.00", "p1"), now),
        DexScreenerClient._parse_pair(pair("dex-b", "1.01", "p2"), now),
    ]
    results = find_spreads([row for row in rows if row is not None])
    assert len(results) == 1
    assert results[0].gross_spread_bps == Decimal("100.00")
    assert results[0].classification == "indicative_only_not_executable"


def test_scanner_does_not_compare_different_tokens():
    now = datetime.now(timezone.utc)
    first = DexScreenerClient._parse_pair(pair("dex-a", "1.00", "p1"), now)
    raw = pair("dex-b", "1.01", "p2")
    raw["baseToken"]["address"] = "0xccc"
    second = DexScreenerClient._parse_pair(raw, now)
    assert find_spreads([first, second]) == []


def test_scanner_filters_low_liquidity():
    now = datetime.now(timezone.utc)
    raw_a, raw_b = pair("dex-a", "1.00", "p1"), pair("dex-b", "1.01", "p2")
    raw_a["liquidity"]["usd"] = 100
    a = DexScreenerClient._parse_pair(raw_a, now)
    b = DexScreenerClient._parse_pair(raw_b, now)
    assert find_spreads([a, b]) == []


def test_scanner_rejects_old_collected_snapshots():
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    old = now - timedelta(seconds=45)
    a = DexScreenerClient._parse_pair(pair("dex-a", "1.00", "p1"), old)
    b = DexScreenerClient._parse_pair(pair("dex-b", "1.01", "p2"), old)
    assert find_spreads([a, b], max_data_age_seconds=Decimal("20")) == []


def test_scanner_rejects_future_collected_snapshots():
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    future = now + timedelta(seconds=5)
    a = DexScreenerClient._parse_pair(pair("dex-a", "1.00", "p1"), future)
    b = DexScreenerClient._parse_pair(pair("dex-b", "1.01", "p2"), future)
    assert find_spreads([a, b]) == []
