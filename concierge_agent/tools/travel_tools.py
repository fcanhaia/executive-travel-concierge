"""Typed executive travel tools with explicit Pydantic JSON schemas and guided errors.

Implements FastMCP-style contract validation, descriptive verb-noun naming,
comprehensive Google-style docstrings, OpenTelemetry span instrumentation,
Intent/Outcome structured logging, and actionable LLM error recovery hints.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from concierge_agent.observability import (
    record_tool_intent,
    record_tool_outcome,
    scrub_pii,
    traced_operation,
)

_FLIGHT_CATALOG: dict[tuple[str, str], list[dict[str, Any]]] = {
    ("GRU", "LHR"): [
        {
            "flight_number": "BA246",
            "carrier": "British Airways",
            "stops": 0,
            "duration_hours": 11.4,
            "economy_usd": 980.0,
            "business_usd": 3150.0,
            "seat_options": ["aisle", "window"],
        },
        {
            "flight_number": "LA8084",
            "carrier": "LATAM Airlines",
            "stops": 0,
            "duration_hours": 11.6,
            "economy_usd": 920.0,
            "business_usd": 2890.0,
            "seat_options": ["aisle", "window"],
        },
    ],
    ("JFK", "LHR"): [
        {
            "flight_number": "BA178",
            "carrier": "British Airways",
            "stops": 0,
            "duration_hours": 7.1,
            "economy_usd": 640.0,
            "business_usd": 2250.0,
            "seat_options": ["aisle", "window"],
        },
        {
            "flight_number": "VS004",
            "carrier": "Virgin Atlantic",
            "stops": 0,
            "duration_hours": 7.2,
            "economy_usd": 610.0,
            "business_usd": 2180.0,
            "seat_options": ["aisle", "window"],
        },
    ],
    ("LHR", "ZRH"): [
        {
            "flight_number": "LX317",
            "carrier": "SWISS",
            "stops": 0,
            "duration_hours": 1.8,
            "economy_usd": 240.0,
            "business_usd": 590.0,
            "seat_options": ["aisle", "window"],
        },
        {
            "flight_number": "BA712",
            "carrier": "British Airways",
            "stops": 0,
            "duration_hours": 1.9,
            "economy_usd": 215.0,
            "business_usd": 540.0,
            "seat_options": ["aisle", "window"],
        },
    ],
    ("ZRH", "GRU"): [
        {
            "flight_number": "LX092",
            "carrier": "SWISS",
            "stops": 0,
            "duration_hours": 11.9,
            "economy_usd": 1010.0,
            "business_usd": 3120.0,
            "seat_options": ["aisle", "window"],
        }
    ],
    ("ZRH", "JFK"): [
        {
            "flight_number": "LX014",
            "carrier": "SWISS",
            "stops": 0,
            "duration_hours": 8.8,
            "economy_usd": 670.0,
            "business_usd": 2340.0,
            "seat_options": ["aisle", "window"],
        }
    ],
}

_HOTEL_CATALOG: dict[str, list[dict[str, Any]]] = {
    "LONDON": [
        {
            "hotel_id": "LON-01",
            "property_name": "Canary Wharf Executive Suites",
            "neighborhood": "Canary Wharf",
            "transit_walk_minutes": 4,
            "nightly_rate_usd": 310.0,
            "style": "boutique",
        },
        {
            "hotel_id": "LON-02",
            "property_name": "Paddington Transit Plaza",
            "neighborhood": "Paddington",
            "transit_walk_minutes": 3,
            "nightly_rate_usd": 265.0,
            "style": "business",
        },
    ],
    "ZURICH": [
        {
            "hotel_id": "ZRH-01",
            "property_name": "Bahnhofstrasse Boutique Haus",
            "neighborhood": "Central Station",
            "transit_walk_minutes": 3,
            "nightly_rate_usd": 340.0,
            "style": "boutique",
        },
        {
            "hotel_id": "ZRH-02",
            "property_name": "Oerlikon Corporate Residence",
            "neighborhood": "Oerlikon",
            "transit_walk_minutes": 5,
            "nightly_rate_usd": 235.0,
            "style": "business",
        },
    ],
}

_WEATHER_FX_CATALOG: dict[str, dict[str, Any]] = {
    "LONDON": {
        "currency_code": "GBP",
        "usd_to_local_rate": 0.79,
        "expected_temp_celsius": 13.0,
        "conditions": "Light rain and mild breeze",
        "disruption_risk": "LOW",
        "packing_advisory": "Pack a waterproof trench coat and compact umbrella.",
    },
    "ZURICH": {
        "currency_code": "CHF",
        "usd_to_local_rate": 0.88,
        "expected_temp_celsius": 10.5,
        "conditions": "Crisp alpine air with clear mornings",
        "disruption_risk": "LOW",
        "packing_advisory": "Bring layered business attire and a warm jacket.",
    },
}


class FlightSearchInput(BaseModel):
    """Strict input JSON schema for flight option searches."""

    model_config = ConfigDict(extra="forbid")

    origin_iata: str = Field(..., min_length=3, max_length=3, description="3-letter origin airport IATA code.")
    destination_iata: str = Field(..., min_length=3, max_length=3, description="3-letter destination airport IATA code.")
    departure_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="ISO departure date YYYY-MM-DD.")
    cabin_class: str = Field(default="economy", description="Cabin class: economy or business.")
    max_price_usd: float = Field(default=3500.0, gt=0, description="Maximum one-way ticket fare in USD.")

    @field_validator("origin_iata", "destination_iata")
    @classmethod
    def _uppercase_iata(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned.isalpha():
            raise ValueError("IATA airport codes must contain exactly 3 letters.")
        return cleaned

    @field_validator("cabin_class")
    @classmethod
    def _validate_cabin(cls, value: str) -> str:
        lowered = value.strip().lower()
        if lowered not in {"economy", "business"}:
            raise ValueError("cabin_class must be either 'economy' or 'business'.")
        return lowered


class FlightSearchOutput(BaseModel):
    """Strict output JSON schema for flight option responses."""

    model_config = ConfigDict(extra="forbid")

    status: str
    route: str
    departure_date: str
    cabin_class: str
    matching_flights: list[dict[str, Any]]
    cheapest_fare_usd: float


class HotelSearchInput(BaseModel):
    """Strict input JSON schema for hotel stay queries."""

    model_config = ConfigDict(extra="forbid")

    city_name: str = Field(..., min_length=2, description="Destination city name (e.g., London, Zurich).")
    check_in_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="ISO check-in date YYYY-MM-DD.")
    nights: int = Field(..., ge=1, le=30, description="Number of hotel nights between 1 and 30.")
    max_nightly_rate_usd: float = Field(default=450.0, gt=0, description="Maximum nightly corporate rate in USD.")


class HotelSearchOutput(BaseModel):
    """Strict output JSON schema for curated hotel responses."""

    model_config = ConfigDict(extra="forbid")

    status: str
    city: str
    nights: int
    options: list[dict[str, Any]]
    lowest_total_stay_usd: float


class WeatherFxInput(BaseModel):
    """Strict input JSON schema for destination weather and FX checks."""

    model_config = ConfigDict(extra="forbid")

    city_name: str = Field(..., min_length=2, description="Target city name.")
    planned_spend_usd: float = Field(default=1000.0, ge=0, description="Planned local spend in USD to convert.")


class WeatherFxOutput(BaseModel):
    """Strict output JSON schema for weather and FX advisories."""

    model_config = ConfigDict(extra="forbid")

    status: str
    city: str
    currency_code: str
    usd_to_local_rate: float
    estimated_local_budget: float
    expected_temp_celsius: float
    conditions: str
    disruption_risk: str
    packing_advisory: str


class BookingConfirmationInput(BaseModel):
    """Strict input JSON schema for final executive itinerary reservation."""

    model_config = ConfigDict(extra="forbid")

    traveler_identifier: str = Field(..., min_length=2, description="Traveler profile ID or sanitized alias.")
    flight_numbers: list[str] = Field(..., min_length=1, description="Selected flight numbers to reserve.")
    hotel_ids: list[str] = Field(..., min_length=1, description="Selected corporate hotel IDs.")
    total_estimated_usd: float = Field(..., gt=0, le=25000.0, description="Total itinerary price in USD.")
    human_approval_token: str = Field(..., min_length=6, description="Explicit human approval token.")


class BookingConfirmationOutput(BaseModel):
    """Strict output JSON schema for confirmed reservations."""

    model_config = ConfigDict(extra="forbid")

    status: str
    booking_reference: str
    reserved_flights: list[str]
    reserved_hotels: list[str]
    total_charged_usd: float


TOOL_JSON_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_verified_flight_options": {
        "input_schema": FlightSearchInput.model_json_schema(),
        "output_schema": FlightSearchOutput.model_json_schema(),
    },
    "search_curated_hotel_stays": {
        "input_schema": HotelSearchInput.model_json_schema(),
        "output_schema": HotelSearchOutput.model_json_schema(),
    },
    "evaluate_destination_weather_and_fx": {
        "input_schema": WeatherFxInput.model_json_schema(),
        "output_schema": WeatherFxOutput.model_json_schema(),
    },
    "confirm_executive_itinerary_booking": {
        "input_schema": BookingConfirmationInput.model_json_schema(),
        "output_schema": BookingConfirmationOutput.model_json_schema(),
    },
}


def _guided_error(
    tool_name: str,
    error_code: str,
    message: str,
    recovery_instructions: str,
    started_at: float,
) -> dict[str, Any]:
    """Builds an LLM-actionable error response and logs the AFTER_EXECUTION outcome."""
    latency_ms = (time.perf_counter() - started_at) * 1000.0
    payload = {
        "status": "error",
        "tool": tool_name,
        "error_code": error_code,
        "message": scrub_pii(message),
        "recovery_instructions": recovery_instructions,
    }
    record_tool_outcome(
        tool_name=tool_name,
        status="error",
        outcome_summary=payload,
        latency_ms=latency_ms,
    )
    return payload


def search_verified_flight_options(
    origin_iata: str,
    destination_iata: str,
    departure_date: str,
    cabin_class: str = "economy",
    max_price_usd: float = 3500.0,
) -> dict[str, Any]:
    """Searches verified corporate flight schedules between two IATA airport codes.

    Validates input arguments against FlightSearchInput and filters carrier fares
    within the traveler's maximum USD ceiling.

    Args:
        origin_iata: Three-letter IATA airport code for departure (e.g., 'GRU', 'JFK', 'LHR').
        destination_iata: Three-letter IATA airport code for arrival (e.g., 'LHR', 'ZRH', 'JFK').
        departure_date: Departure calendar date formatted strictly as 'YYYY-MM-DD'.
        cabin_class: Requested cabin tier, either 'economy' or 'business'.
        max_price_usd: Maximum acceptable one-way ticket fare in USD (must be > 0).

    Returns:
        Dictionary matching FlightSearchOutput on success, or a structured error
        payload containing 'recovery_instructions' so the agent can self-correct.
    """
    started = time.perf_counter()
    raw_args = {
        "origin_iata": origin_iata,
        "destination_iata": destination_iata,
        "departure_date": departure_date,
        "cabin_class": cabin_class,
        "max_price_usd": max_price_usd,
    }
    with traced_operation("tool.search_verified_flight_options", raw_args):
        record_tool_intent("search_verified_flight_options", raw_args)
        try:
            req = FlightSearchInput(**raw_args)
        except ValidationError as exc:
            return _guided_error(
                tool_name="search_verified_flight_options",
                error_code="INVALID_FLIGHT_PARAMETERS",
                message=str(exc.errors()[0]["msg"]),
                recovery_instructions=(
                    "Pass 3-letter alphabetic IATA codes (such as 'GRU', 'JFK', 'LHR', 'ZRH'), "
                    "format departure_date as 'YYYY-MM-DD', and set cabin_class to 'economy' or 'business'."
                ),
                started_at=started,
            )

        candidates = _FLIGHT_CATALOG.get((req.origin_iata, req.destination_iata), [])
        if not candidates:
            supported = ", ".join(f"{o}->{d}" for o, d in sorted(_FLIGHT_CATALOG))
            return _guided_error(
                tool_name="search_verified_flight_options",
                error_code="UNSUPPORTED_ROUTE",
                message=f"No direct corporate inventory for {req.origin_iata}->{req.destination_iata}.",
                recovery_instructions=(
                    f"Retry using one of the supported hub routes ({supported}) or connect via LHR/ZRH."
                ),
                started_at=started,
            )

        price_key = "business_usd" if req.cabin_class == "business" else "economy_usd"
        within_budget = [
            {
                "flight_number": item["flight_number"],
                "carrier": item["carrier"],
                "stops": item["stops"],
                "duration_hours": item["duration_hours"],
                "fare_usd": item[price_key],
                "seat_options": item["seat_options"],
            }
            for item in candidates
            if item[price_key] <= req.max_price_usd
        ]
        if not within_budget:
            lowest_available = min(item[price_key] for item in candidates)
            return _guided_error(
                tool_name="search_verified_flight_options",
                error_code="FARE_CAP_EXCEEDED",
                message=f"No {req.cabin_class} fares under ${req.max_price_usd:.2f} USD.",
                recovery_instructions=(
                    f"Switch cabin_class to 'economy' or increase max_price_usd to at least "
                    f"${lowest_available:.2f} USD and retry."
                ),
                started_at=started,
            )

        output = FlightSearchOutput(
            status="success",
            route=f"{req.origin_iata}-{req.destination_iata}",
            departure_date=req.departure_date,
            cabin_class=req.cabin_class,
            matching_flights=within_budget,
            cheapest_fare_usd=min(f["fare_usd"] for f in within_budget),
        ).model_dump()
        record_tool_outcome(
            tool_name="search_verified_flight_options",
            status="success",
            outcome_summary={"route": output["route"], "count": len(within_budget)},
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
        return output


def search_curated_hotel_stays(
    city_name: str,
    check_in_date: str,
    nights: int,
    max_nightly_rate_usd: float = 450.0,
) -> dict[str, Any]:
    """Queries corporate-approved hotels near transit hubs for a destination city.

    Args:
        city_name: Target destination city name (e.g., 'London' or 'Zurich').
        check_in_date: Check-in calendar date formatted strictly as 'YYYY-MM-DD'.
        nights: Length of stay in nights (integer between 1 and 30).
        max_nightly_rate_usd: Maximum allowed nightly hotel rate in USD.

    Returns:
        Dictionary matching HotelSearchOutput with total stay calculations, or a
        structured error dictionary containing actionable 'recovery_instructions'.
    """
    started = time.perf_counter()
    raw_args = {
        "city_name": city_name,
        "check_in_date": check_in_date,
        "nights": nights,
        "max_nightly_rate_usd": max_nightly_rate_usd,
    }
    with traced_operation("tool.search_curated_hotel_stays", raw_args):
        record_tool_intent("search_curated_hotel_stays", raw_args)
        try:
            req = HotelSearchInput(**raw_args)
        except ValidationError as exc:
            return _guided_error(
                tool_name="search_curated_hotel_stays",
                error_code="INVALID_HOTEL_PARAMETERS",
                message=str(exc.errors()[0]["msg"]),
                recovery_instructions=(
                    "Provide check_in_date as 'YYYY-MM-DD', set nights between 1 and 30, "
                    "and ensure max_nightly_rate_usd is positive."
                ),
                started_at=started,
            )

        city_key = req.city_name.strip().upper()
        properties = _HOTEL_CATALOG.get(city_key, [])
        if not properties:
            supported = ", ".join(sorted(k.title() for k in _HOTEL_CATALOG))
            return _guided_error(
                tool_name="search_curated_hotel_stays",
                error_code="CITY_NOT_IN_CATALOG",
                message=f"No curated corporate hotels registered for '{req.city_name}'.",
                recovery_instructions=f"Query one of the supported executive hubs: {supported}.",
                started_at=started,
            )

        eligible = [
            {
                **prop,
                "total_stay_usd": round(prop["nightly_rate_usd"] * req.nights, 2),
            }
            for prop in properties
            if prop["nightly_rate_usd"] <= req.max_nightly_rate_usd
        ]
        if not eligible:
            min_rate = min(p["nightly_rate_usd"] for p in properties)
            return _guided_error(
                tool_name="search_curated_hotel_stays",
                error_code="NIGHTLY_CAP_TOO_LOW",
                message=f"No properties in {city_key.title()} under ${req.max_nightly_rate_usd:.2f}/night.",
                recovery_instructions=(
                    f"Increase max_nightly_rate_usd to at least ${min_rate:.2f} USD and retry."
                ),
                started_at=started,
            )

        output = HotelSearchOutput(
            status="success",
            city=city_key.title(),
            nights=req.nights,
            options=eligible,
            lowest_total_stay_usd=min(item["total_stay_usd"] for item in eligible),
        ).model_dump()
        record_tool_outcome(
            tool_name="search_curated_hotel_stays",
            status="success",
            outcome_summary={"city": output["city"], "count": len(eligible)},
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
        return output


def evaluate_destination_weather_and_fx(
    city_name: str,
    planned_spend_usd: float = 1000.0,
) -> dict[str, Any]:
    """Evaluates destination weather conditions, travel disruption risk, and FX rates.

    Args:
        city_name: Destination city name to inspect (e.g., 'London' or 'Zurich').
        planned_spend_usd: Estimated local expense budget in USD to convert into local currency.

    Returns:
        Dictionary matching WeatherFxOutput with converted local currency budget and
        packing advice, or a structured error with 'recovery_instructions'.
    """
    started = time.perf_counter()
    raw_args = {"city_name": city_name, "planned_spend_usd": planned_spend_usd}
    with traced_operation("tool.evaluate_destination_weather_and_fx", raw_args):
        record_tool_intent("evaluate_destination_weather_and_fx", raw_args)
        try:
            req = WeatherFxInput(**raw_args)
        except ValidationError as exc:
            return _guided_error(
                tool_name="evaluate_destination_weather_and_fx",
                error_code="INVALID_WEATHER_FX_INPUT",
                message=str(exc.errors()[0]["msg"]),
                recovery_instructions="Provide a non-empty city_name and non-negative planned_spend_usd.",
                started_at=started,
            )

        city_key = req.city_name.strip().upper()
        info = _WEATHER_FX_CATALOG.get(city_key)
        if not info:
            supported = ", ".join(sorted(k.title() for k in _WEATHER_FX_CATALOG))
            return _guided_error(
                tool_name="evaluate_destination_weather_and_fx",
                error_code="UNSUPPORTED_DESTINATION",
                message=f"Weather and FX feed unavailable for '{req.city_name}'.",
                recovery_instructions=f"Use a supported destination city ({supported}).",
                started_at=started,
            )

        output = WeatherFxOutput(
            status="success",
            city=city_key.title(),
            currency_code=info["currency_code"],
            usd_to_local_rate=info["usd_to_local_rate"],
            estimated_local_budget=round(req.planned_spend_usd * info["usd_to_local_rate"], 2),
            expected_temp_celsius=info["expected_temp_celsius"],
            conditions=info["conditions"],
            disruption_risk=info["disruption_risk"],
            packing_advisory=info["packing_advisory"],
        ).model_dump()
        record_tool_outcome(
            tool_name="evaluate_destination_weather_and_fx",
            status="success",
            outcome_summary={"city": output["city"], "currency": output["currency_code"]},
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
        return output


def confirm_executive_itinerary_booking(
    traveler_identifier: str,
    flight_numbers: list[str],
    hotel_ids: list[str],
    total_estimated_usd: float,
    human_approval_token: str,
) -> dict[str, Any]:
    """Commits final reservations for flights and hotels after human approval.

    High-stakes mutating tool protected by both schema validation and the ADK
    before_tool_callback Human-in-the-Loop governance gate.

    Args:
        traveler_identifier: Sanitized traveler profile handle (never raw passport/card).
        flight_numbers: List of selected flight designators (e.g., ['BA246', 'LX317']).
        hotel_ids: List of selected corporate hotel identifiers (e.g., ['LON-01', 'ZRH-01']).
        total_estimated_usd: Total approved itinerary spend in USD.
        human_approval_token: Explicit confirmation token starting with 'APPROVED-' granted by the user.

    Returns:
        Dictionary matching BookingConfirmationOutput with deterministic PNR reference,
        or a guided error instructing the agent to obtain explicit human confirmation first.
    """
    started = time.perf_counter()
    raw_args = {
        "traveler_identifier": scrub_pii(traveler_identifier),
        "flight_numbers": flight_numbers,
        "hotel_ids": hotel_ids,
        "total_estimated_usd": total_estimated_usd,
        "human_approval_token": human_approval_token,
    }
    with traced_operation("tool.confirm_executive_itinerary_booking", raw_args):
        record_tool_intent("confirm_executive_itinerary_booking", raw_args)
        try:
            req = BookingConfirmationInput(**raw_args)
        except ValidationError as exc:
            return _guided_error(
                tool_name="confirm_executive_itinerary_booking",
                error_code="INVALID_BOOKING_PAYLOAD",
                message=str(exc.errors()[0]["msg"]),
                recovery_instructions=(
                    "Ensure flight_numbers and hotel_ids are non-empty lists, total_estimated_usd > 0, "
                    "and request an explicit human_approval_token before calling this booking tool."
                ),
                started_at=started,
            )

        if not req.human_approval_token.startswith("APPROVED-"):
            return _guided_error(
                tool_name="confirm_executive_itinerary_booking",
                error_code="HUMAN_APPROVAL_REQUIRED",
                message="Booking blocked: human_approval_token lacks 'APPROVED-' prefix.",
                recovery_instructions=(
                    "Present the itemized itinerary and total USD cost to the user, wait for explicit "
                    "written confirmation, and pass the resulting 'APPROVED-<session>' token."
                ),
                started_at=started,
            )

        digest = hashlib.sha256(
            f"{req.traveler_identifier}:{','.join(req.flight_numbers)}:{','.join(req.hotel_ids)}".encode()
        ).hexdigest()[:8].upper()
        output = BookingConfirmationOutput(
            status="confirmed",
            booking_reference=f"EXEC-{digest}",
            reserved_flights=req.flight_numbers,
            reserved_hotels=req.hotel_ids,
            total_charged_usd=round(req.total_estimated_usd, 2),
        ).model_dump()
        record_tool_outcome(
            tool_name="confirm_executive_itinerary_booking",
            status="confirmed",
            outcome_summary={"booking_reference": output["booking_reference"]},
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
        return output