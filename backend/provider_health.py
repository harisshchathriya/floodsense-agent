"""Process-local, request-driven health for FloodSense data providers."""
from datetime import datetime, timedelta, timezone
from threading import RLock


RAINFALL_MAX_AGE = timedelta(hours=3)  # Existing risk-assessment application policy.
GAUGE_MAX_AGE = timedelta(hours=3)  # Existing CWC application policy; not a CWC rule.

PROVIDER_CATALOG = {
    "open_meteo_rainfall": {"name": "Open-Meteo rainfall", "source": "Open-Meteo", "url": "https://api.open-meteo.com/v1/forecast"},
    "open_elevation": {"name": "Open-Elevation", "source": "Open-Elevation", "url": "https://api.open-elevation.com/api/v1/lookup"},
    "ndma_sachet": {"name": "NDMA SACHET alerts", "source": "NDMA SACHET India CAP RSS", "url": "https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml"},
    "cwc_cauvery_gauges": {"name": "CWC Cauvery gauges", "source": "Central Water Commission via NWIC", "url": "https://nwdp.nwic.gov.in/en/dataset/river-water-level-telemetry-hourly-central-water-commission-cwc"},
    "cwc_musiri_gauge": {"name": "CWC Musiri gauge", "source": "Central Water Commission via NWIC", "url": "https://nwdp.nwic.gov.in/en/dataset/river-water-level-telemetry-hourly-central-water-commission-cwc"},
}
_LOCK = RLock()
_STATION_CATALOG = {}
_STATE = {
    key: {"_last_started": None, "_failure_since_success": False,
          "last_successful_retrieval_at": None, "last_attempted_at": None,
          "last_result_status": "unknown", "last_error_category": None,
          "observation_freshness": "unknown", "latest_observation_at": None,
          "recovered_since_failure": None, "status": "unknown",
          "status_reason": "No request has queried this provider in this process."}
    for key in PROVIDER_CATALOG
}


def register_station_provider(station_id, name, url):
    """Register a station-specific CWC request health dimension in memory."""
    key = f"cwc_station:{station_id}"
    with _LOCK:
        _STATION_CATALOG[key] = {"name": f"CWC · {name}", "source": "Central Water Commission via NWIC", "url": url}
        _STATE.setdefault(key, {"_last_started": None, "_failure_since_success": False,
            "last_successful_retrieval_at": None, "last_attempted_at": None,
            "last_result_status": "unknown", "last_error_category": None,
            "observation_freshness": "unknown", "latest_observation_at": None,
            "recovered_since_failure": None, "status": "unknown",
            "status_reason": "No request has queried this station in this process."})
    return key


def _parse_time(value, *, assume_utc=False):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%d-%m-%Y %H:%M", "%d-%m-%Y %H:%M:%S"):
            try:
                parsed = datetime.strptime(value.strip(), fmt)
                break
            except ValueError:
                parsed = None
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        if not assume_utc:
            return None
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _freshness(provider, payload, now):
    if provider == "open_elevation":
        return "not_applicable", None
    if provider == "ndma_sachet":
        # Alert expiry/effectivity is evaluated per CAP record, not via an invented age limit.
        return "not_applicable", None
    if provider == "open_meteo_rainfall":
        current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
        stamp = current.get("time_utc")
        parsed = _parse_time(stamp, assume_utc=True)
        if parsed is None:
            return "unknown", stamp
        age = (now - parsed).total_seconds()
        if age < -900:
            return "unknown", stamp
        return ("stale" if age > RAINFALL_MAX_AGE.total_seconds() else "fresh"), stamp
    if provider == "cwc_musiri_gauge":
        freshness = (payload.get("freshness") or {}).get("status", "unknown")
        stamp = (payload.get("observation") or {}).get("observed_at")
        return ({"unknown_timezone": "unknown"}.get(freshness, freshness), stamp)
    if provider.startswith("cwc_station:"):
        freshness = (payload.get("freshness") or {}).get("status", "unknown")
        observation = payload.get("latest_observation") or {}
        stamp = observation.get("observed_at_utc") or observation.get("observed_at")
        return ({"unknown_timezone": "unknown"}.get(freshness, freshness), stamp)
    stations = payload.get("stations") if isinstance(payload.get("stations"), list) else []
    states = [station.get("freshness_status", "unknown") for station in stations if isinstance(station, dict)]
    if not states:
        return "unknown", None
    if all(item == "stale" for item in states):
        return "stale", None
    if any(item == "stale" for item in states):
        return "degraded", None
    if all(item == "fresh" for item in states):
        return "fresh", None
    return "unknown", None


def _error_category(error, payload):
    if error is not None:
        name = type(error).__name__.casefold()
        if "timeout" in name:
            return "timeout"
        if "http" in name:
            return "http_error"
        if isinstance(error, (ValueError, KeyError, TypeError)):
            return "malformed_response"
        if isinstance(error, RuntimeError):
            return "empty_or_invalid_response"
        return "provider_error"
    if payload.get("status") == "unavailable" or payload.get("error"):
        error = str(payload.get("error", "" )).split(":", 1)[0].casefold()
        if "timeout" in error:
            return "timeout"
        if "http" in error:
            return "http_error"
        if "valueerror" in error or "parseerror" in error or "csv.error" in error:
            return "malformed_response"
        return "provider_error"
    return None


def record_provider_result(provider, started_at, *, result=None, error=None, now=None):
    """Record one request outcome; older concurrent attempts cannot replace newer ones."""
    if provider not in _STATE:
        raise KeyError(f"Unknown provider key: {provider}")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    started = _parse_time(started_at, assume_utc=True) if isinstance(started_at, str) else started_at
    if not isinstance(started, datetime):
        raise ValueError("started_at must be a valid timestamp")
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    started = started.astimezone(timezone.utc)
    payload = result if isinstance(result, dict) else {}
    category = _error_category(error, payload)
    malformed_result = not isinstance(result, dict)
    failed = error is not None or malformed_result or payload.get("status") == "unavailable" or bool(payload.get("error"))
    if malformed_result and error is None:
        category = "malformed_response"
    retrieval = _parse_time(payload.get("retrieved_at_utc"), assume_utc=True) or now
    freshness, observed_at = _freshness(provider, payload, now) if not failed else ("unknown", None)
    source_quality = payload.get("quality_status")
    if source_quality in {"partial", "invalid", "degraded"} or payload.get("status") in {"partial", "degraded"}:
        quality_degraded = True
    elif provider == "cwc_cauvery_gauges" and payload.get("invalid_row_count", 0):
        quality_degraded = True
    elif provider == "cwc_cauvery_gauges" and any(
        (station.get("observation") or {}).get("quality_status") != "valid_numeric_value"
        for station in payload.get("stations", []) if isinstance(station, dict)
    ):
        quality_degraded = True
    elif provider == "ndma_sachet" and any(
        alert.get("validity_status") in {"validity_unknown", "updated_status_unresolved"}
        for alert in payload.get("alerts", []) if isinstance(alert, dict)
    ):
        quality_degraded = True
    elif provider == "cwc_musiri_gauge" and payload.get("observation", {}).get("quality_status") != "valid_numeric_value":
        quality_degraded = True
    elif provider.startswith("cwc_station:") and (payload.get("latest_observation") or {}).get("quality_status") != "valid_numeric_value":
        quality_degraded = True
    elif provider == "open_meteo_rainfall" and payload.get("quality_status") in {"partial", "invalid"}:
        quality_degraded = True
    else:
        quality_degraded = False
    if failed:
        status = "unavailable"
        reason = "The latest request failed; details are limited to a safe error category."
    elif freshness == "stale":
        status = "stale"
        reason = "The provider responded, but its latest observation exceeds the application freshness policy."
    elif freshness == "degraded" or freshness == "unknown" or quality_degraded:
        status = "degraded"
        reason = "The provider responded, but data freshness or quality is incomplete or unknown."
    else:
        status = "healthy"
        reason = "The latest request succeeded; source-specific observation freshness is shown separately."
    with _LOCK:
        state = _STATE[provider]
        if state["_last_started"] is not None and started < state["_last_started"]:
            return False
        prior_failed = state["last_result_status"] == "unavailable" or state["_failure_since_success"]
        state["_last_started"] = started
        state["last_attempted_at"] = started.isoformat()
        state["last_result_status"] = "unavailable" if failed else str(payload.get("status", "available"))
        state["last_error_category"] = category
        state["observation_freshness"] = freshness
        state["latest_observation_at"] = observed_at
        state["status"] = status
        state["status_reason"] = reason
        if failed:
            state["_failure_since_success"] = True
            state["recovered_since_failure"] = False
        else:
            if retrieval >= (_parse_time(state["last_successful_retrieval_at"], assume_utc=True) or datetime.min.replace(tzinfo=timezone.utc)):
                state["last_successful_retrieval_at"] = retrieval.isoformat()
            if prior_failed:
                state["recovered_since_failure"] = True
                state["_failure_since_success"] = False
    return True


def get_provider_health():
    """Return a safe snapshot; this is request-driven and resets when the process restarts."""
    with _LOCK:
        return {
            "collection_mode": "request_driven",
            "message": "Health reflects requests handled by this process; no scheduled background checks are running.",
            "providers": [
                {"key": key, **PROVIDER_CATALOG[key], **{field: value for field, value in state.items() if not field.startswith("_")}}
                for key, state in _STATE.items()
                if key in PROVIDER_CATALOG
            ],
            "station_providers": [
                {"key": key, **catalog, **{field: value for field, value in _STATE[key].items() if not field.startswith("_")}}
                for key, catalog in _STATION_CATALOG.items()
            ],
        }


def reset_provider_health_for_tests():
    with _LOCK:
        for key in PROVIDER_CATALOG:
            _STATE[key] = {"_last_started": None, "_failure_since_success": False,
                "last_successful_retrieval_at": None, "last_attempted_at": None,
                "last_result_status": "unknown", "last_error_category": None,
                "observation_freshness": "unknown", "latest_observation_at": None,
                "recovered_since_failure": None, "status": "unknown",
                "status_reason": "No request has queried this provider in this process."}
        for key in list(_STATION_CATALOG):
            _STATE.pop(key, None)
        _STATION_CATALOG.clear()
