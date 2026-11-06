"""Courier connector architecture. One adapter per provider behind a small protocol; a provider is chosen per shipment.
Adapters are called OUTSIDE any database transaction and must be idempotent on the client reference (shipment id)."""
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Booking:
    awb: str
    tracking_url: str


class CourierAdapter(Protocol):
    name: str

    def book(self, *, client_ref: str, address: str | None, weight_kg: float | None, cartons: int | None) -> Booking: ...


class FakeCourier:
    """Deterministic stand-in used until Mintree confirms its logistics providers (PRD §12). Same client_ref => same AWB."""

    name = "fake"

    def book(self, *, client_ref, address, weight_kg, cartons) -> Booking:
        return Booking(awb=f"FAKE{int(client_ref):010d}", tracking_url=f"https://track.example.com/FAKE{int(client_ref):010d}")


_ADAPTERS: dict[str, CourierAdapter] = {"fake": FakeCourier()}


def get_courier(name: str) -> CourierAdapter:
    if name not in _ADAPTERS:
        raise KeyError(f"No courier adapter registered for '{name}'")
    return _ADAPTERS[name]


def register_courier(adapter: CourierAdapter) -> None:
    _ADAPTERS[adapter.name] = adapter
