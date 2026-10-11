from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent import ask_floodsense
from risk_assessment import assess_flood_risk
from hydrological_validation import validation_readiness
from tools.elevation import get_elevation
from tools.official_sources import get_official_alerts, get_river_gauges, get_musiri_gauge, get_station_observations
from musiri_monitoring import evaluate_musiri_threshold, MUSIRI_WARNING_THRESHOLD_REFERENCE
from tools.rainfall import get_rainfall
from provider_health import get_provider_health, record_provider_result, register_station_provider
from station_registry import get_station, list_stations
from assessment_readiness import AssessmentReadinessRecord, build_assessment_readiness
from river_network import (
    river_network_geometry,
    river_network_relationships,
    river_network_stations,
    river_network_status,
    station_reach_matches,
)


# --------------------------------------------------
# 1. Create FastAPI application
# --------------------------------------------------

app = FastAPI(
    title="FloodSense API",
    description=(
        "Flood awareness API providing rainfall and "
        "terrain information for Tiruchirappalli, Tamil Nadu."
    ),
    version="1.0.0",
)


@app.get("/validation/historical-events")
def historical_event_validation_endpoint():
    """Return the reproducible historical event/linkage audit artifact."""
    report_path = Path(__file__).resolve().parent / "data" / "processed" / "cwc_cauvery_event_linkage_audit.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="Historical event validation report is unavailable; regenerate the local audit.") from exc
    if report.get("report_type") != "historical_event_station_linkage_audit":
        raise HTTPException(status_code=503, detail="Historical event validation report is malformed.")
    return report



@app.get("/river-network/status")
def river_network_status_endpoint():
    """Summarize loaded official geometry and network-topology limitations."""
    return river_network_status()


@app.get("/river-network/stations")
def river_network_stations_endpoint():
    """Return evidence-qualified station-to-reach matches."""
    return river_network_stations()


@app.get("/river-network/relationships")
def river_network_relationships_endpoint(station_id: str | None = Query(default=None)):
    """Return station pairs, optionally relative to one local station key."""
    result = river_network_relationships(station_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Station identifier is not present in the CWC telemetry registry.")
    return result


@app.get("/river-network/geometry")
def river_network_geometry_endpoint():
    """Return the reprojected Cauvery basin polygon and official river lines."""
    return river_network_geometry()


@app.get("/stations")
def stations_endpoint():
    """Return stations represented in the preserved CWC telemetry datasets."""
    health = {item["key"]: item for item in get_provider_health().get("station_providers", [])}
    network_matches = {item["station_id"]: item for item in station_reach_matches()}
    stations = list_stations()
    for station in stations:
        key = f"cwc_station:{station['station_id']}"
        station["provider_health"] = health.get(key, {"key": key, "status": "unknown", "last_result_status": "unknown"})
        station["river_network_match"] = network_matches.get(station["station_id"], {
            "match_status": "insufficient_network_data", "candidate_reach": None,
            "uncertainty": "River network data is unavailable.",
        })
    return {
        "status": "available" if stations else "unavailable",
        "count": len(stations),
        "stations": stations,
        "source": "CWC via NWIC preserved 2021-2025 and 2026-2030 telemetry files",
        "message": "Station names form local route keys; they are not official CWC identifiers. The map uses source coordinates and does not encode flow order.",
        "river_network": river_network_status(),
    }


@app.get("/stations/{station_id}")
def station_detail_endpoint(station_id: str):
    station = get_station(station_id)
    if station is None:
        raise HTTPException(status_code=404, detail="Station identifier is not present in the CWC telemetry registry.")
    key = register_station_provider(station_id, station["official_name"]["value"], station["source_url"])
    health = {item["key"]: item for item in get_provider_health().get("station_providers", [])}
    station["provider_health"] = health.get(key, {"key": key, "status": "unknown", "last_result_status": "unknown"})
    return station


@app.get("/stations/{station_id}/observations")
def station_observations_endpoint(station_id: str, limit: int = Query(default=24, ge=1, le=200)):
    station = get_station(station_id)
    if station is None:
        raise HTTPException(status_code=404, detail="Station identifier is not present in the CWC telemetry registry.")
    provider_key = register_station_provider(station_id, station["official_name"]["value"], station["source_url"])
    observations = _tracked_provider_call(
        provider_key,
        lambda name, count: get_station_observations(name, limit=count),
        station["source_station_identifier"], limit,
    )
    for record in observations.get("observations", []):
        record["station_id"] = station_id
    if isinstance(observations.get("latest_observation"), dict):
        observations["latest_observation"]["station_id"] = station_id
    observations["station_id"] = station_id
    observations["station_name"] = station["official_name"]["value"]
    observations["metadata_verification_status"] = station["metadata_verification_status"]
    observations["hydrological_relationship"] = station["hydrological_relationship"]
    observations["threshold"] = station["threshold"]
    observations["provider_health"] = next(
        (item for item in get_provider_health().get("station_providers", []) if item["key"] == provider_key),
        {"key": provider_key, "status": "unknown", "last_result_status": "unknown"},
    )
    return observations


@app.get("/assessment/readiness", response_model=AssessmentReadinessRecord)
def assessment_readiness_endpoint(station_id: str = Query(min_length=1, max_length=120)):
    """Return deterministic evidence gates for one registered station; never calculate risk."""
    station = get_station(station_id)
    if station is None:
        raise HTTPException(status_code=404, detail="Station identifier is not present in the CWC telemetry registry.")

    provider_key = register_station_provider(station_id, station["official_name"]["value"], station["source_url"])
    try:
        observation_response = _tracked_provider_call(
            provider_key, lambda name: get_station_observations(name, limit=1),
            station["source_station_identifier"],
        )
    except Exception as exc:
        observation_response = {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}",
            "source": "Central Water Commission via NWIC", "source_url": station.get("source_url"),
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "freshness": {"status": "unknown"},
            "latest_observation": None, "observations": []}

    coordinates_verified = ((station.get("latitude") or {}).get("status") == "verified"
        and (station.get("longitude") or {}).get("status") == "verified")
    if coordinates_verified:
        latitude = station["latitude"]["value"]
        longitude = station["longitude"]["value"]
        monitoring = collect_monitoring_data(latitude, longitude)
    else:
        # Do not query location-sensitive alerts/weather against conflicted coordinates.
        monitoring = {"status": "partial", "requested_coordinates": {"latitude": None, "longitude": None},
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "rainfall": {"status": "unavailable", "message": "Station coordinates are not verified."},
            "elevation": {"status": "unavailable", "message": "Station coordinates are not verified."},
            "official_alerts": {"status": "unavailable", "message": "Station coordinates are not verified; no location match was attempted."},
            "river_gauges": {"status": "unavailable", "message": "Station coordinates are not verified."}}

    relationships = river_network_relationships(station_id)
    matches = {item["station_id"]: item for item in station_reach_matches()}
    return build_assessment_readiness(station, observation_response, monitoring,
        matches.get(station_id), relationships)


@app.get("/stations/musiri/status")
def musiri_station_status():
    """Return the exact Musiri telemetry row and a guarded threshold status."""
    try:
        gauge = _tracked_provider_call("cwc_musiri_gauge", get_musiri_gauge)
    except Exception as exc:
        gauge = {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}",
                 "station": None, "observation": None,
                 "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                 "freshness": {"status": "unknown", "is_stale": None}}
    station = gauge.get("station")
    observation = gauge.get("observation")
    decision_observation = dict(observation) if isinstance(observation, dict) else observation
    if isinstance(decision_observation, dict):
        decision_observation["freshness_status"] = (gauge.get("freshness") or {}).get("status", "unknown")
    decision = evaluate_musiri_threshold(station, decision_observation, MUSIRI_WARNING_THRESHOLD_REFERENCE)
    return {**gauge, "registry_station_id": "musiri", "station_id_verification_status": "unknown",
            "threshold_monitoring": decision,
            "risk_assessment": "not_assessed",
            "notice": "Informational gauge record only; this endpoint is not a flood forecast or official public warning."}


# --------------------------------------------------
# 2. Configure CORS for the frontend
# --------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------
# 3. Define request and response models
# --------------------------------------------------

class AskRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Question to ask the FloodSense agent.",
    )
    latitude: float = Field(..., ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(..., ge=-180, le=180, allow_inf_nan=False)


class AskResponse(BaseModel):
    answer: str


class CoordinatesRequest(BaseModel):
    latitude: float = Field(..., ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(..., ge=-180, le=180, allow_inf_nan=False)


# --------------------------------------------------
# 4. Root endpoint
# --------------------------------------------------

@app.get("/")
def root():
    return {
        "message": "Welcome to the FloodSense API",
        "status": "running",
        "docs": "/docs",
    }


# --------------------------------------------------
# 5. Health-check endpoint
# --------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "FloodSense API",
    }


@app.get("/health/providers")
def provider_health_endpoint():
    """Request-driven health snapshot for configured data providers."""
    return get_provider_health()


def _tracked_provider_call(provider_key, function, *args):
    started_at = datetime.now(timezone.utc)
    try:
        result = function(*args)
        if not isinstance(result, dict):
            raise ValueError("Provider returned a malformed response object")
    except Exception as exc:
        record_provider_result(provider_key, started_at, error=exc)
        raise
    record_provider_result(provider_key, started_at, result=result)
    return result


# --------------------------------------------------
# 6. Ask FloodSense endpoint
# --------------------------------------------------

@app.post("/ask", response_model=AskResponse)
def ask_floodsense_endpoint(request: AskRequest):
    question = request.question.strip()

    if not question:
        raise HTTPException(
            status_code=422,
            detail="Question cannot be empty.",
        )

    location_question = (
        f"{question}\n\n"
        f"Selected monitoring coordinates:\n"
        f"Latitude: {request.latitude}\n"
        f"Longitude: {request.longitude}\n"
        "Use these exact coordinates when calling both "
        "location-dependent tools. Call both tools with "
        "the same latitude and longitude when both "
        "rainfall and elevation are requested."
    )

    try:
        answer = ask_floodsense(location_question)

        return AskResponse(answer=answer)

    except Exception as exc:
        # Log the full exception in the backend terminal.
        print(
            f"FloodSense agent error: "
            f"{type(exc).__name__}: {exc}"
        )

        # Return a generic error to the API client.
        raise HTTPException(
            status_code=502,
            detail=(
                "FloodSense could not generate a response. "
                "Please try again later."
            ),
        ) from exc


def collect_monitoring_data(latitude: float, longitude: float):
    with ThreadPoolExecutor(max_workers=4) as executor:
        rainfall_future = executor.submit(_tracked_provider_call, "open_meteo_rainfall", get_rainfall, latitude, longitude)
        elevation_future = executor.submit(_tracked_provider_call, "open_elevation", get_elevation, latitude, longitude)
        alerts_future = executor.submit(_tracked_provider_call, "ndma_sachet", get_official_alerts, latitude, longitude)
        gauges_future = executor.submit(_tracked_provider_call, "cwc_cauvery_gauges", get_river_gauges, latitude, longitude)

    def provider_result(result):
        if isinstance(result, BaseException):
            return {"error": f"{type(result).__name__}: {result}"}
        return result

    def resolve(future):
        try:
            return future.result()
        except Exception as exc:
            return exc

    rainfall, elevation, official_alerts, river_gauges = map(
        provider_result,
        (resolve(rainfall_future), resolve(elevation_future),
         resolve(alerts_future), resolve(gauges_future)),
    )
    provider_values = (rainfall, elevation, official_alerts, river_gauges)
    available = sum(
        value.get("status") != "unavailable" and "error" not in value
        for value in provider_values
    )
    status = "success" if available == len(provider_values) else "partial" if available else "failed"
    return {
        "requested_coordinates": {
            "latitude": latitude,
            "longitude": longitude,
        },
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "rainfall": rainfall,
        "elevation": elevation,
        "official_alerts": official_alerts,
        "river_gauges": river_gauges,
    }


@app.post("/monitor")
def monitor_location(request: CoordinatesRequest):
    """Return measured provider data without asking the LLM to parse it."""
    return collect_monitoring_data(request.latitude, request.longitude)


@app.post("/validation-readiness")
def validation_readiness_endpoint(request: CoordinatesRequest):
    """Inspect current provider availability and historical-validation blockers."""
    monitoring = collect_monitoring_data(request.latitude, request.longitude)
    report = validation_readiness(monitoring)
    report["requested_coordinates"] = monitoring["requested_coordinates"]
    report["generated_at_utc"] = monitoring["retrieved_at_utc"]
    report["provider_status"] = monitoring["status"]
    return report


@app.post("/assess-risk")
def assess_risk_endpoint(request: CoordinatesRequest):
    """Retrieve current evidence and return a deterministic risk assessment."""
    monitoring = collect_monitoring_data(request.latitude, request.longitude)
    assessment = assess_flood_risk(monitoring)
    readiness = validation_readiness(monitoring)
    return {
        "data_status": monitoring["status"],
        "requested_coordinates": monitoring["requested_coordinates"],
        "retrieved_at_utc": monitoring["retrieved_at_utc"],
        "rainfall": monitoring["rainfall"],
        "elevation": monitoring["elevation"],
        "official_alerts": monitoring["official_alerts"],
        "river_gauges": monitoring["river_gauges"],
        "assessment": assessment,
        "validation_readiness": readiness,
    }
