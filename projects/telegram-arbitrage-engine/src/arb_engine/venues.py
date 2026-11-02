from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any


_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class Venue:
    id: str
    name: str
    dexscreener_id: str
    network: str
    chain_id: int
    factory: str
    router02: str
    swap_fee_bps: int
    deployment_source: str
    fee_source: str
    source_status: str
    notes: str

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@lru_cache(maxsize=1)
def load_venues() -> tuple[Venue, ...]:
    raw = json.loads(
        files(__package__).joinpath("venue_registry.json").read_text(encoding="utf-8")
    )
    if raw.get("schema_version") != 2 or not isinstance(raw.get("venues"), list):
        raise ValueError("Unsupported V2 venue registry schema")

    venues: list[Venue] = []
    seen: set[str] = set()
    for item in raw["venues"]:
        venue = Venue(**item)
        if not venue.id or venue.id in seen:
            raise ValueError("Venue IDs must be non-empty and unique")
        if not venue.dexscreener_id or venue.dexscreener_id != venue.dexscreener_id.lower():
            raise ValueError(f"Invalid DEX Screener ID for venue {venue.id}")
        if not isinstance(venue.chain_id, int) or venue.chain_id <= 0:
            raise ValueError(f"Invalid chain ID for venue {venue.id}")
        if not _ADDRESS.fullmatch(venue.factory) or not _ADDRESS.fullmatch(venue.router02):
            raise ValueError(f"Invalid factory/router address for venue {venue.id}")
        if not 0 <= venue.swap_fee_bps <= 1000:
            raise ValueError(f"Invalid swap fee for venue {venue.id}")
        if venue.source_status != "source_documented_not_live_verified":
            raise ValueError(f"Unexpected source status for venue {venue.id}")
        if not venue.deployment_source.startswith("https://") or not venue.fee_source.startswith("https://"):
            raise ValueError(f"Venue sources must use HTTPS for {venue.id}")
        seen.add(venue.id)
        venues.append(venue)
    return tuple(venues)


def list_venues(chain_id: int | None = None) -> tuple[Venue, ...]:
    venues = load_venues()
    if chain_id is None:
        return venues
    return tuple(venue for venue in venues if venue.chain_id == chain_id)


def get_venue_by_dexscreener_id(dex_id: str, chain_id: int) -> Venue:
    matches = [
        venue for venue in load_venues()
        if venue.chain_id == chain_id and venue.dexscreener_id == dex_id.lower()
    ]
    if len(matches) != 1:
        raise ValueError(f"No unique registered V2 venue for DEX Screener ID: {dex_id}")
    return matches[0]


def get_venue(venue_id: str, chain_id: int) -> Venue:
    for venue in load_venues():
        if venue.id == venue_id:
            if venue.chain_id != chain_id:
                raise ValueError(
                    f"Venue {venue_id} is for chain ID {venue.chain_id}, not {chain_id}"
                )
            return venue
    raise ValueError(f"Unknown V2 venue ID: {venue_id}")


def resolve_venue_config(
    venue_id: str | None,
    chain_id: int,
    fee_bps: int | None,
    factory: str | None,
    router: str | None,
) -> tuple[int, str | None, str | None, Venue | None]:
    if venue_id:
        venue = get_venue(venue_id, chain_id)
        if fee_bps is not None and fee_bps != venue.swap_fee_bps:
            raise ValueError(
                f"Fee override {fee_bps} bps conflicts with registry fee "
                f"{venue.swap_fee_bps} bps for {venue.id}"
            )
        if factory and factory.lower() != venue.factory.lower():
            raise ValueError(f"Factory override conflicts with registry venue {venue.id}")
        if router and router.lower() != venue.router02.lower():
            raise ValueError(f"Router override conflicts with registry venue {venue.id}")
        return venue.swap_fee_bps, venue.factory, venue.router02, venue

    if fee_bps is None:
        raise ValueError("Supply a known --*-venue or an explicit --*-fee-bps")
    return fee_bps, factory, router, None
