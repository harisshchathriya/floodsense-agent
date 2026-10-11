"""Evidence-based, deterministic readiness gates; this module never scores flood risk."""
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any

from pydantic import BaseModel, ConfigDict


OBSERVATION_MAX_AGE = timedelta(hours=3)


class AssessmentReadinessRecord(BaseModel):
    """Typed response record preserving unknowns and per-source provenance."""
    model_config = ConfigDict(extra="forbid")

    assessment_id: str
    generated_at_utc: str
    status: str
    selected_station: dict[str, Any]
    latest_observation: dict[str, Any] | None
    rainfall_evidence: dict[str, Any]
    elevation_evidence: dict[str, Any]
    official_alert_evidence: dict[str, Any]
    river_network_evidence: dict[str, Any]
    threshold_evidence: dict[str, Any]
    missing_inputs: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    eligibility: dict[str, Any]
    provenance: list[dict[str, Any]]
    explanation: str


def _is_number(value):
    if isinstance(value, bool) or value is None:
        return False
    try:
        return isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _parse_utc(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _source(payload, fallback_name, fallback_url=None):
    payload = payload if isinstance(payload, dict) else {}
    return {"name": payload.get("source") or fallback_name,
            "url": payload.get("source_url") or fallback_url,
            "retrieved_at_utc": payload.get("retrieved_at_utc")}


def _latest_rainfall(payload):
    payload = payload if isinstance(payload, dict) else {}
    current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
    return {"status": payload.get("status") or ("available" if current and "error" not in payload else "unavailable"), "current": current,
            "units": payload.get("units"), "quality_status": payload.get("quality_status"),
            "source": _source(payload, "Open-Meteo rainfall", "https://api.open-meteo.com/v1/forecast")}


def _elevation(payload):
    payload = payload if isinstance(payload, dict) else {}
    return {"status": payload.get("status", "available" if _is_number(payload.get("elevation_m")) else "unavailable"),
            "elevation_m": payload.get("elevation_m"), "unit": "m" if _is_number(payload.get("elevation_m")) else None,
            "location": payload.get("location"), "source": _source(payload, "Open-Elevation")}


def _alerts(payload):
    payload = payload if isinstance(payload, dict) else {}
    items = payload.get("alerts") if isinstance(payload.get("alerts"), list) else []
    active = [item for item in items if isinstance(item, dict) and item.get("validity_status") == "active"]
    # A successful empty/mismatching feed is not evidence of safety.
    if payload.get("status") == "unavailable" or payload.get("error"):
        state = "unknown"
    elif payload.get("active_matched_count", len(active)) > 0 or active:
        state = "confirmed_official_alert"
    else:
        state = "no_matching_alert_found_not_all_clear"
    return {"status": state, "provider_status": payload.get("status", "unknown"),
            "active_matched_count": payload.get("active_matched_count", len(active)),
            "alerts": items, "message": payload.get("message") or "No matching alert is not proof that no flood danger exists.",
            "source": _source(payload, "NDMA SACHET CAP alerts", "https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml")}


def build_assessment_readiness(station, observation_response, monitoring, network_match=None,
                               relationships=None, *, generated_at=None):
    """Build a stable evidence record and independent eligibility decisions.

    Summary eligibility is intentionally narrower than showing raw records in
    the UI. Location-specific flood risk is never calculated here.
    """
    station = station if isinstance(station, dict) else {}
    obs_response = observation_response if isinstance(observation_response, dict) else {}
    monitoring = monitoring if isinstance(monitoring, dict) else {}
    latest = obs_response.get("latest_observation")
    latest = latest if isinstance(latest, dict) else None
    now = generated_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    generated = now.isoformat()
    station_id = station.get("station_id") or "unknown"
    source_name = station.get("source_station_identifier")
    latest_name = latest.get("source_station_identifier") if latest else None
    exact_source_name = bool(source_name and latest_name and source_name.casefold() == latest_name.casefold())

    observed_at = latest.get("observed_at") if latest else None
    observed_utc = latest.get("observed_at_utc") if latest else None
    stamp = _parse_utc(observed_utc)
    provider_freshness = (obs_response.get("freshness") or {}).get("status", "unknown")
    freshness = provider_freshness if provider_freshness in {"fresh", "stale"} else "unknown"
    if stamp:
        age = (now - stamp).total_seconds()
        if age < -900:
            freshness = "invalid_timestamp"
        else:
            freshness = "stale" if age > OBSERVATION_MAX_AGE.total_seconds() else "fresh"
    elif provider_freshness == "stale":
        freshness = "stale"
    else:
        # A source-local timestamp without verified timezone cannot support a current claim.
        freshness = "unknown"
    historical_only = obs_response.get("availability") == "historical_only" or (latest and latest.get("record_type") == "historical_snapshot")
    if historical_only:
        freshness = "stale"
    value = latest.get("value") if latest else None
    numeric_value = _is_number(value)
    quality = latest.get("quality_status") if latest else "missing"
    unit = latest.get("unit") if latest else None
    unit_status = latest.get("unit_status") if latest else "unknown"
    unit_known = unit_status in {"verified", "verified_from_telemetry_column_header"} and bool(unit)
    record_valid = bool(latest and numeric_value and quality == "valid_numeric_value" and unit_known)
    summary_ok = bool(obs_response.get("status") == "available" and exact_source_name and record_valid and freshness == "fresh")

    identity = {"source_name_match": "verified" if exact_source_name else "unknown",
                "official_station_code": station.get("station_id_verification_status", "unknown"),
                "local_station_key": station_id,
                "note": "Source-name matching does not verify an official CWC site code or physical instrument crosswalk."}
    coords = station.get("latitude") or {}
    longitude = station.get("longitude") or {}
    coord_verified = coords.get("status") == "verified" and longitude.get("status") == "verified"
    datum = station.get("reference_datum") or {"value": None, "status": "unknown"}
    threshold = station.get("threshold") or {"value": None, "unit": None, "applicability_status": "not_available"}
    threshold_verified = (threshold.get("applicability_status") == "verified"
                          and threshold.get("value_verification_status") == "verified"
                          and threshold.get("station_identity_verified") is True
                          and station.get("station_id_verification_status") == "verified"
                          and threshold.get("datum_verified") is True
                          and threshold.get("decision_eligible") is True
                          and _is_number(threshold.get("value"))
                          and threshold.get("unit") == unit
                          and datum.get("status") == "verified")
    threshold_ok = bool(summary_ok and threshold_verified)

    alerts = _alerts(monitoring.get("official_alerts"))
    rain = _latest_rainfall(monitoring.get("rainfall"))
    elev = _elevation(monitoring.get("elevation"))
    matches = relationships.get("relationships", []) if isinstance(relationships, dict) else []
    verified_connections = [item for item in matches if isinstance(item, dict) and item.get("connection_status") in {"verified_connected_reaches", "verified_upstream_downstream", "verified_river_network_connection"}]
    network = {"station_reach_match": network_match or {"match_status": "insufficient_network_data"},
               "verified_station_connections": verified_connections,
               "connection_count": len(verified_connections),
               "topology_available": bool((relationships or {}).get("topology_available")) if isinstance(relationships, dict) else False,
               "flow_direction_available": bool((relationships or {}).get("flow_direction_available")) if isinstance(relationships, dict) else False}

    summary_reasons = []
    if not exact_source_name:
        summary_reasons.append("The latest observation cannot be matched to this registry station by exact source name.")
    if not record_valid:
        summary_reasons.append("A finite, source-valid observation with a known unit is unavailable.")
    if freshness != "fresh":
        summary_reasons.append(f"Observation freshness is {freshness}; a current summary is withheld.")
    if obs_response.get("status") != "available":
        summary_reasons.append("The exact-station observation provider is unavailable.")

    threshold_reasons = []
    if not summary_ok:
        threshold_reasons.append("A fresh, valid exact-station observation is required.")
    if identity.get("official_station_code") != "verified":
        threshold_reasons.append("Official station identity is not verified against a CWC site code.")
    if unit_status not in {"verified", "verified_from_telemetry_column_header"}:
        threshold_reasons.append("Observation units are not verified.")
    if datum.get("status") != "verified":
        threshold_reasons.append("Observation datum is unknown or not compatible with the threshold datum.")
    if threshold.get("applicability_status") != "verified" or not threshold_verified:
        threshold_reasons.append("No officially documented threshold is verified as applicable to this exact station, unit, and datum.")
    if station.get("known_conflicts"):
        threshold_reasons.append("Station metadata contains unresolved conflicts.")

    location_reasons = []
    if not coord_verified:
        location_reasons.append("Selected station coordinates are not verified and conflict-free.")
    if network.get("station_reach_match", {}).get("match_status") != "verified_match":
        location_reasons.append("A verified station-to-reach match is unavailable.")
    if not threshold_ok:
        location_reasons.append("Threshold comparison is not eligible for this station.")
    location_reasons.append("No locally validated flood-risk model is available; flood risk remains not assessed.")

    missing = []
    def add_missing(key, reason):
        missing.append({"input": key, "status": "missing_or_unknown", "reason": reason})
    if not latest:
        add_missing("latest_station_observation", "No exact-station observation is available.")
    elif not record_valid:
        add_missing("valid_station_observation", f"Latest observation is missing, malformed, non-finite, invalid, or has an unknown unit (quality: {quality}; unit status: {unit_status}).")
    if freshness != "fresh":
        add_missing("fresh_observation_timestamp", f"Observation freshness is {freshness}; source timezone and timestamp must support a current reading.")
    if datum.get("status") != "verified":
        add_missing("measurement_datum", "The station datum is not verified against an applicable threshold datum.")
    if not threshold_verified:
        add_missing("applicable_threshold", "A documented, currently applicable threshold for the exact station is unavailable.")
    if not network.get("topology_available"):
        add_missing("verified_hydrological_topology", "The river geometry does not provide a verified station connection or flow direction.")
    if alerts["status"] == "unknown":
        add_missing("official_alert_feed", "Alert provider was unavailable; no-alert status cannot be concluded.")
    elif alerts["status"] == "no_matching_alert_found_not_all_clear":
        add_missing("location_matched_official_alert", "No matching alert was found; this does not establish that no flood danger exists.")
    if rain["status"] != "available":
        add_missing("rainfall", "Rainfall evidence is unavailable or malformed.")
    if elev["status"] != "available":
        add_missing("elevation", "Elevation evidence is unavailable or malformed.")
    conflicts = list(station.get("known_conflicts") or [])
    if source_name and latest_name and not exact_source_name:
        conflicts.append({"field": "station_identity", "status": "conflicting", "registry_source_name": source_name, "observation_source_name": latest_name})
    if latest and quality == "conflicting_latest_rows":
        conflicts.append({"field": "latest_observation", "status": "conflicting", "note": "Multiple source rows share the latest timestamp with different values."})

    provenance = [
        {"evidence": "station_registry", **_source(station, "CWC/NWIC station registry", station.get("source_url"))},
        {"evidence": "station_observation", **_source(obs_response, "CWC/NWIC exact-station telemetry", obs_response.get("source_url"))},
        {"evidence": "rainfall", **rain["source"]},
        {"evidence": "elevation", **elev["source"]},
        {"evidence": "official_alerts", **alerts["source"]},
        {"evidence": "river_network", "name": "CWC River Network via NWDP", "url": "https://nwdp.nwic.gov.in/dataset/river-line", "retrieved_at_utc": None},
    ]
    selected = {"station_id": station_id, "name": (station.get("official_name") or {}).get("value"),
                "latitude": coords.get("value"), "longitude": longitude.get("value"),
                "coordinate_verification": "verified" if coord_verified else "conflicting" if "conflicting" in {coords.get("status"), longitude.get("status")} else "unknown",
                "station_identity": identity, "source": _source(station, "CWC/NWIC station registry", station.get("source_url"))}
    observation_record = None if latest is None else {key: latest.get(key) for key in (
        "source_station_identifier", "observed_at", "observed_at_utc", "value", "raw_value", "unit", "unit_status", "quality_status", "record_type", "raw_zero_of_gauge", "raw_mean_sea_level", "source", "source_url")}
    if observation_record is not None:
        observation_record.update({"freshness_status": freshness, "retrieved_at_utc": obs_response.get("retrieved_at_utc"), "datum": datum})

    record = AssessmentReadinessRecord(
        assessment_id=f"station-readiness:{station_id}:{generated}", generated_at_utc=generated,
        status="not_assessed", selected_station=selected, latest_observation=observation_record,
        rainfall_evidence=rain, elevation_evidence=elev, official_alert_evidence=alerts,
        river_network_evidence=network, threshold_evidence={"value": threshold.get("value"), "unit": threshold.get("unit"),
            "applicability_status": threshold.get("applicability_status", "unknown"), "verification_status": threshold.get("value_verification_status", "unknown"),
            "source": threshold.get("source") or threshold.get("provenance"), "source_url": threshold.get("source_url"),
            "eligible_for_comparison": threshold_ok}, missing_inputs=missing, conflicts=conflicts,
        eligibility={"eligible_for_observation_summary": {"eligible": summary_ok, "reasons": summary_reasons},
            "eligible_for_threshold_comparison": {"eligible": threshold_ok, "reasons": threshold_reasons},
            "eligible_for_location_specific_assessment": {"eligible": False, "status": "not_assessed", "reasons": location_reasons}},
        provenance=provenance,
        explanation="Readiness gates describe whether specific evidence operations are supportable. They do not calculate flood probability or declare an all-clear. Official alerts are reported separately; flood risk remains not assessed.")
    return record
