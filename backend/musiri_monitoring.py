"""Deterministic Musiri gauge threshold evaluation and historical replay."""
from datetime import datetime, timezone
import math


MUSIRI_WARNING_THRESHOLD_REFERENCE = {
    "value": 82.12,
    "unit": "m",
    "severity": "warning",
    "source": "Central Water Commission Appraisal Report 2024, Annex XIII",
    "source_url": "https://cwc.gov.in/sites/default/files/appraisal-report-2024-publicationcompressed.pdf",
    "reference_site_code": "CW1CAL000207",
    "documented_for_years": [2023, 2024],
    "comparison": "strictly greater than",
    "threshold_verification_status": "not_verified",
    "decision_eligible": False,
    "datum_verified": False,
    "station_identity_verified": False,
    "validity_period": None,
    "limitations": [
        "The 2024 appraisal documents a historical warning level, not its operational validity in 2026.",
        "The current telemetry CSV does not publish the CWC site code and its gauge datum fields do not establish compatibility with this threshold.",
        "The current telemetry coordinates differ from the CWC 2025 inventory coordinates.",
    ],
}


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        try:
            parsed = datetime.strptime(str(value), "%d-%m-%Y %H:%M")
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _parse_replay_time(row):
    value = row.get("observed_at_utc") or row.get("observed_at")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        try:
            parsed = datetime.strptime(str(value), "%d-%m-%Y %H:%M")
        except (TypeError, ValueError):
            return None
    # Naive source times are used only to order samples and estimate gaps.
    # This does not assert that the source time is UTC.
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def evaluate_musiri_threshold(station, observation, threshold=None, *, now=None, max_age_seconds=10800):
    """Evaluate only when identity, datum and the threshold's current applicability are verified."""
    threshold = threshold or MUSIRI_WARNING_THRESHOLD_REFERENCE
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    result = {"monitoring_status": "unknown", "threshold_verification_status": threshold.get("threshold_verification_status", "not_verified"),
              "threshold": threshold, "explanation": "No threshold decision is available."}
    if not isinstance(observation, dict) or not isinstance(station, dict):
        result.update(monitoring_status="unknown", explanation="Station observation is unavailable.")
        return result
    if station.get("name", "").strip().casefold() != "musiri":
        result.update(monitoring_status="invalid_data", explanation="Observation is not from the exact Musiri station.")
        return result
    if observation.get("unit") != threshold.get("unit"):
        result.update(monitoring_status="invalid_data", explanation="Observation unit does not match the documented threshold unit.")
        return result
    timestamp = _parse_time(observation.get("observed_at_utc") or observation.get("observed_at"))
    if timestamp is None:
        freshness = observation.get("freshness_status")
        status = "stale_data" if freshness == "stale" else "unknown" if freshness in {"unknown", "unknown_timezone"} else "invalid_data" if observation.get("value") is not None else "unknown"
        explanation = ("The source timestamp has no verified timezone; it is conservatively old enough to classify this observation as stale." if status == "stale_data" else
                      "Observation time has no verified timezone, so freshness cannot be established." if status == "unknown" else
                      "A timezone-qualified observation timestamp is required for freshness evaluation.")
        result.update(monitoring_status=status, explanation=explanation)
        return result
    age = (now.astimezone(timezone.utc) - timestamp).total_seconds()
    if age < -900:
        result.update(monitoring_status="invalid_data", explanation="Observation timestamp is in the future.")
        return result
    if age > max_age_seconds:
        result.update(monitoring_status="stale_data", age_seconds=int(age), explanation="The latest observation is stale; no threshold conclusion is made.")
        return result
    value = observation.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        result.update(monitoring_status="unknown" if value is None else "invalid_data", explanation="The latest observation is missing or not a finite numeric level.")
        return result
    result.update(value=value, observed_at=observation.get("observed_at"), age_seconds=max(0, int(age)))
    if threshold.get("decision_eligible") is not True or threshold.get("threshold_verification_status") != "verified":
        result.update(monitoring_status="threshold_not_verified", explanation="The documented threshold is reference information only; its current applicability to this exact telemetry station and datum is unverified.")
        return result
    if station.get("identity_verified") is not True or threshold.get("station_identity_verified") is not True:
        result.update(monitoring_status="threshold_not_verified", explanation="The telemetry station identity is not verified against the threshold's station.")
        return result
    if threshold.get("datum_verified") is not True:
        result.update(monitoring_status="threshold_not_verified", explanation="The observation datum is not verified against the threshold datum.")
        return result
    if value > float(threshold["value"]):
        result.update(monitoring_status="threshold_exceeded", explanation="The level is strictly above the verified warning threshold.")
    else:
        result.update(monitoring_status="below_documented_threshold", explanation="The level is at or below the verified warning threshold. This is not an all-clear or a flood-risk assessment.")
    return result


def replay_musiri_observations(observations, threshold=None, *, expected_interval_seconds=3600):
    """Replay ordered measurements; count transitions, and retain gaps/missing readings."""
    threshold = threshold or MUSIRI_WARNING_THRESHOLD_REFERENCE
    rows = sorted(observations, key=lambda row: _parse_replay_time(row) or datetime.max.replace(tzinfo=timezone.utc))
    times = [_parse_replay_time(row) for row in rows]
    gaps = []
    for previous, current in zip(times, times[1:]):
        if previous and current and (current - previous).total_seconds() > expected_interval_seconds * 1.5:
            gaps.append({"from": previous.isoformat(), "to": current.isoformat(), "missing_expected_slots": max(1, round((current - previous).total_seconds() / expected_interval_seconds) - 1)})
    eligible = threshold.get("decision_eligible") is True and threshold.get("threshold_verification_status") == "verified"
    above = False
    crossings, returns, evaluated = [], [], 0
    missing = invalid = 0
    if eligible:
        for row, timestamp in zip(rows, times):
            val = row.get("value")
            if timestamp is None or val is None:
                missing += 1
                above = False
                continue
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
                invalid += 1
                above = False
                continue
            evaluated += 1
            is_above = val > float(threshold["value"])
            if is_above and not above:
                crossings.append(timestamp.isoformat())
            elif above and not is_above:
                returns.append(timestamp.isoformat())
            above = is_above
    else:
        missing = sum(row.get("value") is None for row in rows)
        invalid = sum(row.get("value") is not None and (isinstance(row.get("value"), bool) or not isinstance(row.get("value"), (int, float)) or not math.isfinite(row.get("value"))) for row in rows)
    return {"status": "replayed" if eligible else "threshold_not_verified", "observation_count": len(rows), "evaluated_count": evaluated,
            "missing_count": missing, "invalid_count": invalid, "coverage_start": times[0].isoformat() if times and times[0] else None,
            "coverage_end": times[-1].isoformat() if times and times[-1] else None, "gap_count": len(gaps), "gaps": gaps,
            "threshold": threshold, "crossings": crossings if eligible else None, "returns_below": returns if eligible else None,
            "message": "Historical crossings were not computed because threshold applicability is unverified." if not eligible else "Crossings count transitions, not each above-threshold sample."}
