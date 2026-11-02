import json
import sys

import pytest

from arb_engine import cli
from arb_engine.venues import (
    default_scan_venue_ids, get_venue, get_venue_by_dexscreener_id,
    list_venues, resolve_venue_config,
)


def test_registry_contains_only_source_documented_ethereum_v2_venues():
    venues = list_venues(chain_id=1)
    assert {venue.id for venue in venues} == {
        "uniswap_v2_ethereum",
        "sushiswap_v2_ethereum",
    }
    assert all(venue.swap_fee_bps == 30 for venue in venues)
    assert all(venue.source_status == "source_documented_not_live_verified" for venue in venues)
    assert all(venue.deployment_source.startswith("https://") for venue in venues)
    assert {venue.dexscreener_id for venue in venues} == {"uniswap", "sushiswap"}


def test_registry_rejects_unknown_venues_and_wrong_chain():
    with pytest.raises(ValueError, match="Unknown V2 venue"):
        get_venue("unknown_v2", 1)
    with pytest.raises(ValueError, match="chain ID 1"):
        get_venue("uniswap_v2_ethereum", 8453)


def test_registry_autofills_factory_router_and_fee():
    fee, factory, router, venue = resolve_venue_config(
        "uniswap_v2_ethereum", 1, None, None, None,
    )
    assert fee == 30
    assert factory == "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"
    assert router == "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
    assert venue is not None
    assert venue.id == "uniswap_v2_ethereum"


def test_registry_fails_closed_on_fee_or_address_override():
    with pytest.raises(ValueError, match="conflicts with registry fee"):
        resolve_venue_config("uniswap_v2_ethereum", 1, 25, None, None)
    with pytest.raises(ValueError, match="Factory override conflicts"):
        resolve_venue_config(
            "uniswap_v2_ethereum", 1, None, "0x" + "1" * 40, None,
        )
    with pytest.raises(ValueError, match="Router override conflicts"):
        resolve_venue_config(
            "uniswap_v2_ethereum", 1, None, None, "0x" + "2" * 40,
        )


def test_manual_venue_requires_explicit_fee():
    assert resolve_venue_config(None, 1, 25, None, None) == (
        25, None, None, None,
    )
    with pytest.raises(ValueError, match=r"explicit --\*-fee-bps"):
        resolve_venue_config(None, 1, None, None, None)


def test_venues_cli_is_read_only_and_labels_registry_unverified(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["arb-engine", "venues", "--chain-id", "1"])
    cli.main()
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "read_only_source_documented_venue_registry"
    assert output["live_verified"] is False
    assert len(output["venues"]) == 2
    assert "not live-verified" in output["warning"]



def test_venue_check_cli_uses_read_only_verifier(monkeypatch, capsys):
    from arb_engine import cli

    monkeypatch.setattr(cli, "JsonRpcClient", lambda endpoint, timeout_seconds: object())
    monkeypatch.setattr(
        cli,
        "verify_registry_venues",
        lambda rpc, venues, expected_chain_id: {
            "mode": "read_only_live_venue_deployment_verification",
            "chain_id": expected_chain_id,
            "verification_status": "live_read_only_onchain_checks_passed",
            "venues": [{"venue_id": venue.id} for venue in venues],
        },
    )
    monkeypatch.setattr(sys, "argv", ["arb-engine", "venue-check", "--chain-id", "1"])
    cli.main()
    output = json.loads(capsys.readouterr().out)
    assert output["verification_status"] == "live_read_only_onchain_checks_passed"
    assert len(output["venues"]) == 2



def test_venue_check_cli_error_message_ends_with_real_newline(monkeypatch, capsys):
    from arb_engine.venue_smoke import VenueVerificationError

    monkeypatch.setattr(cli, "JsonRpcClient", lambda endpoint, timeout_seconds: object())
    monkeypatch.setattr(
        cli, "verify_registry_venues",
        lambda rpc, venues, expected_chain_id: (_ for _ in ()).throw(
            VenueVerificationError("simulated failure")
        ),
    )
    monkeypatch.setattr(sys, "argv", ["arb-engine", "venue-check", "--chain-id", "1"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert capsys.readouterr().err == "venue-check failed: simulated failure\n"


def test_live_rpc_client_rejects_credentials_and_query_parameters():
    from arb_engine.venue_smoke import JsonRpcClient

    with pytest.raises(ValueError, match="without credentials"):
        JsonRpcClient("https://user:secret@example.com/rpc")
    with pytest.raises(ValueError, match="without credentials"):
        JsonRpcClient("https://example.com/rpc?api-key=secret")
    with pytest.raises(ValueError, match="HTTPS RPC"):
        JsonRpcClient("http://127.0.0.1:8545")



def test_dexscreener_ids_resolve_only_to_registered_ethereum_v2_venues():
    assert get_venue_by_dexscreener_id("uniswap", 1).id == "uniswap_v2_ethereum"
    assert get_venue_by_dexscreener_id("sushiswap", 1).id == "sushiswap_v2_ethereum"
    with pytest.raises(ValueError, match="No unique registered V2 venue"):
        get_venue_by_dexscreener_id("unknown-dex", 1)


def test_bsc_pancakeswap_and_apeswap_v2_registry_entries_are_source_documented():
    from arb_engine.venues import list_venues

    venues = {venue.id: venue for venue in list_venues(56)}
    assert set(venues) == {"pancakeswap_v2_bsc", "apeswap_v2_bsc"}
    pancake = venues["pancakeswap_v2_bsc"]
    ape = venues["apeswap_v2_bsc"]
    assert pancake.network == "bsc"
    assert pancake.dexscreener_id == "pancakeswap"
    assert pancake.swap_fee_bps == 25
    assert ape.network == "bsc"
    assert ape.dexscreener_id == "apeswap"
    assert ape.swap_fee_bps == 20
    assert all(v.source_status == "source_documented_not_live_verified" for v in venues.values())



def test_default_scan_venue_ids_are_selected_per_chain_without_cross_chain_fallback():
    assert default_scan_venue_ids(1) == (
        "uniswap_v2_ethereum", "sushiswap_v2_ethereum",
    )
    assert default_scan_venue_ids(56) == (
        "pancakeswap_v2_bsc", "apeswap_v2_bsc",
    )
    assert default_scan_venue_ids(42161) == (
        "uniswap_v2_arbitrum", "sushiswap_v2_arbitrum",
    )
    assert default_scan_venue_ids(10) == (
        "uniswap_v2_optimism", "sushiswap_v2_optimism",
    )
    assert default_scan_venue_ids(8453) == (
        "uniswap_v2_base", "sushiswap_v2_base",
    )
    assert default_scan_venue_ids(43114) == (
        "uniswap_v2_avalanche", "sushiswap_v2_avalanche",
    )
    assert default_scan_venue_ids(137) == (
        "uniswap_v2_polygon", "quickswap_v2_polygon",
    )
    with pytest.raises(ValueError, match="has 0 registered V2 venues"):
        default_scan_venue_ids(84532)
    with pytest.raises(ValueError, match="positive integer"):
        default_scan_venue_ids(True)



def test_additional_evm_v2_registry_entries_have_distinct_factory_router_and_source_metadata():
    from arb_engine.venues import list_venues

    expected = {
        42161: "arbitrum",
        10: "optimism",
        8453: "base",
        43114: "avalanche",
    }
    for chain_id, network in expected.items():
        venues = list_venues(chain_id)
        assert {item.id for item in venues} == {
            f"uniswap_v2_{network}", f"sushiswap_v2_{network}",
        }
        assert {item.dexscreener_id for item in venues} == {"uniswap", "sushiswap"}
        assert len({item.factory.lower() for item in venues}) == 2
        assert len({item.router02.lower() for item in venues}) == 2
        assert all(item.swap_fee_bps == 30 for item in venues)
        assert all(item.source_status == "source_documented_not_live_verified" for item in venues)
        assert all(item.deployment_source.startswith("https://") for item in venues)



def test_polygon_v2_registry_uses_quickswap_as_independent_second_venue():
    from arb_engine.venues import list_venues

    venues = list_venues(137)
    assert {item.id for item in venues} == {
        "uniswap_v2_polygon", "quickswap_v2_polygon",
    }
    assert {item.dexscreener_id for item in venues} == {"uniswap", "quickswap"}
    assert len({item.factory.lower() for item in venues}) == 2
    assert len({item.router02.lower() for item in venues}) == 2
    assert all(item.swap_fee_bps == 30 for item in venues)
    assert all(item.source_status == "source_documented_not_live_verified" for item in venues)
