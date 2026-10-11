from datetime import datetime, timedelta, timezone
from math import isfinite


RAINFALL_WINDOWS = {"1h": 1, "3h": 3, "24h": 24}
MAX_RAINFALL_AGE = timedelta(hours=3)
MISSING_RISK_INPUTS = [
    ("soil_saturation", "Soil saturation and antecedent wetness are unavailable."),
    ("drainage_capacity", "Local drainage capacity and condition are unavailable."),
    ("historical_flood_records", "Verified local flood observations are unavailable."),
    ("validated_local_risk_model", "No locally validated risk model or thresholds are configured."),
]


def _parse_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _finite_nonnegative(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) and number >= 0 else None


def calculate_rainfall_features(rainfall, *, assessed_at=None):
    """Summarize observed hourly rain; never substitute missing values with zero."""
    rainfall = rainfall if isinstance(rainfall, dict) else {}
    current = rainfall.get("current") if isinstance(rainfall.get("current"), dict) else {}
    hourly = rainfall.get("hourly") if isinstance(rainfall.get("hourly"), list) else []
    cutoff = _parse_timestamp(current.get("time_utc"))
    now = _parse_timestamp(assessed_at) if isinstance(assessed_at, str) else assessed_at
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    unit = (rainfall.get("units") or {}).get("rain") if isinstance(rainfall.get("units"), dict) else None
    if not unit and "rain_mm" in (hourly[0] if hourly and isinstance(hourly[0], dict) else {}):
        unit = "mm"
    unit_supported = unit == "mm"

    grouped = {}
    invalid_timestamp_records = 0
    invalid_numeric_records = 0
    for record in hourly:
        if not isinstance(record, dict):
            invalid_timestamp_records += 1
            continue
        timestamp = _parse_timestamp(record.get("time_utc"))
        if timestamp is None:
            invalid_timestamp_records += 1
            continue
        value = _finite_nonnegative(record.get("rain_mm"))
        if value is None:
            invalid_numeric_records += 1
        grouped.setdefault(timestamp, []).append(value)

    duplicate_records = sum(max(0, len(values) - 1) for values in grouped.values())
    unique_records = {}
    ambiguous_duplicate_timestamps = 0
    for timestamp, values in grouped.items():
        if len(values) > 1 and (any(value is None for value in values) or len(set(values)) > 1):
            ambiguous_duplicate_timestamps += 1
            continue
        unique_records[timestamp] = values[0]

    observed = {}
    forecast_count = 0
    if cutoff is not None:
        for timestamp, value in unique_records.items():
            if timestamp <= cutoff:
                if value is not None and unit_supported:
                    observed[timestamp] = value
            else:
                forecast_count += 1

    latest = max(observed) if observed else None
    age = (now - latest) if latest else None
    stale = age is not None and age > MAX_RAINFALL_AGE
    windows = {}
    for label, hours in RAINFALL_WINDOWS.items():
        start = cutoff - timedelta(hours=hours) if cutoff else None
        values = [
            value for timestamp, value in observed.items()
            if start is not None and start < timestamp <= cutoff
        ]
        complete = unit_supported and len(values) == hours
        partial_sum = round(sum(values), 3) if values and unit_supported else None
        windows[label] = {
            "window_hours": hours,
            "accumulation": partial_sum if complete else None,
            "partial_accumulation": partial_sum if not complete else None,
            "unit": unit if unit_supported else None,
            "valid_observations": len(values),
            "expected_observations": hours,
            "coverage_fraction": round(len(values) / hours, 3),
            "complete": complete,
            "start_utc": start.isoformat() if start else None,
            "end_utc": cutoff.isoformat() if cutoff else None,
        }

    ordered = sorted(observed.items())
    trend = {"status": "insufficient_data", "direction": None, "change_mm_per_hour": None}
    if len(ordered) >= 6:
        last_six = ordered[-6:]
        consecutive = all(
            (right[0] - left[0]) == timedelta(hours=1)
            for left, right in zip(last_six, last_six[1:])
        )
        if consecutive:
            prior_mean = sum(value for _, value in last_six[:3]) / 3
            recent_mean = sum(value for _, value in last_six[3:]) / 3
            difference = recent_mean - prior_mean
            direction = "increasing" if difference > 0.01 else "decreasing" if difference < -0.01 else "stable"
            trend = {
                "status": "available",
                "direction": direction,
                "change_mm_per_hour": round(difference, 3),
                "comparison": "mean of latest 3 consecutive hours vs preceding 3 consecutive hours",
                "unit": "mm/hour",
                "prior_mean_mm": round(prior_mean, 3),
                "recent_mean_mm": round(recent_mean, 3),
                "start_utc": last_six[0][0].isoformat(),
                "end_utc": last_six[-1][0].isoformat(),
            }

    cutoff_age = (now - cutoff) if cutoff else None
    cutoff_stale = cutoff_age is not None and cutoff_age > MAX_RAINFALL_AGE
    missing = []
    if cutoff is None:
        missing.append("Open-Meteo current observation timestamp is missing or invalid.")
    if not unit_supported:
        missing.append(f"Rain unit is unavailable or unsupported ({unit!r}); mm accumulations were not calculated.")
    if not observed:
        missing.append("No valid observed hourly rain values are available before the current observation time.")
    if stale or cutoff_stale:
        missing.append("The latest usable rainfall observation is more than 3 hours old.")
    for label, feature in windows.items():
        if not feature["complete"]:
            missing.append(
                f"{label} rainfall window is incomplete ({feature['valid_observations']}/{feature['expected_observations']} hourly observations)."
            )

    return {
        "source": rainfall.get("source"),
        "unit": unit if unit_supported else None,
        "observation_cutoff_utc": cutoff.isoformat() if cutoff else None,
        "latest_usable_observation_utc": latest.isoformat() if latest else None,
        "latest_observation_age_seconds": max(0, int(age.total_seconds())) if age else None,
        "is_stale": bool(stale or cutoff_stale),
        "observed_record_count": len(observed),
        "forecast_record_count": forecast_count,
        "historical_coverage_note": "The connected Open-Meteo request asks for one past day and two forecast days; this is not long-term flood history.",
        "invalid_timestamp_record_count": invalid_timestamp_records,
        "invalid_numeric_record_count": invalid_numeric_records,
        "duplicate_record_count": duplicate_records,
        "ambiguous_duplicate_timestamp_count": ambiguous_duplicate_timestamps,
        "windows": windows,
        "trend": trend,
        "missing_data": missing,
        "assumptions": [
            "Only the provider's rain_mm field is aggregated; precipitation_mm is not substituted for rain.",
            "Naive timestamps are interpreted as UTC because the existing Open-Meteo tool requests timezone=UTC.",
            "The current.time_utc timestamp separates observed records (at or before it) from forecast records (after it).",
            "The current rainfall tool requests past_days=1 and forecast_days=2; the available history is limited to this short provider window.",
            "Window boundaries are (cutoff minus duration, cutoff], with one hourly record expected per hour.",
            "Accumulations are reported only for complete windows; partial sums remain explicitly labeled and are not treated as complete totals.",
            "The trend compares means of two adjacent sets of three consecutive hourly observations; it is descriptive, not a flood-risk threshold.",
        ],
    }


def assess_flood_risk(monitoring, *, assessed_at=None):
    """Return an explainable assessment without unsupported flood categories."""
    monitoring = monitoring if isinstance(monitoring, dict) else {}
    rainfall = monitoring.get("rainfall") if isinstance(monitoring.get("rainfall"), dict) else {}
    elevation = monitoring.get("elevation") if isinstance(monitoring.get("elevation"), dict) else {}
    alerts = monitoring.get("official_alerts") if isinstance(monitoring.get("official_alerts"), dict) else {}
    gauges = monitoring.get("river_gauges") if isinstance(monitoring.get("river_gauges"), dict) else {}
    requested = monitoring.get("requested_coordinates") or {}
    features = calculate_rainfall_features(rainfall, assessed_at=assessed_at)
    assessed = _parse_timestamp(assessed_at) if isinstance(assessed_at, str) else assessed_at
    assessed = assessed or datetime.now(timezone.utc)
    if assessed.tzinfo is None:
        assessed = assessed.replace(tzinfo=timezone.utc)

    missing_inputs = []
    evidence = []
    reasons = []
    rainfall_error = rainfall.get("error")
    if rainfall_error:
        missing_inputs.append({"input": "rainfall_observations", "reason": rainfall_error})
        reasons.append("Rainfall observations could not be retrieved from Open-Meteo.")
    else:
        current = rainfall.get("current") or {}
        current_value = _finite_nonnegative(current.get("rain_mm"))
        if current_value is not None:
            evidence.append({
                "kind": "observation",
                "name": "Current rain",
                "value": current_value,
                "unit": (rainfall.get("units") or {}).get("rain", "mm"),
                "observed_at_utc": current.get("time_utc"),
                "source": rainfall.get("source"),
                "provider_coordinates": rainfall.get("location"),
            })
        for label, feature in features["windows"].items():
            evidence.append({
                "kind": "derived_feature",
                "name": f"Rain accumulation over {label}",
                "value": feature["accumulation"] if feature["complete"] else feature["partial_accumulation"],
                "unit": feature["unit"],
                "coverage": f"{feature['valid_observations']}/{feature['expected_observations']} hourly observations",
                "complete": feature["complete"],
                "start_utc": feature["start_utc"],
                "end_utc": feature["end_utc"],
                "source": rainfall.get("source"),
                "provider_coordinates": rainfall.get("location"),
            })
        if features["trend"]["status"] == "available":
            evidence.append({
                "kind": "derived_feature",
                "name": "Short-term rain trend",
                "value": features["trend"]["direction"],
                "change_mm_per_hour": features["trend"]["change_mm_per_hour"],
                "start_utc": features["trend"]["start_utc"],
                "end_utc": features["trend"]["end_utc"],
                "source": rainfall.get("source"),
                "provider_coordinates": rainfall.get("location"),
            })
        missing_inputs.extend({"input": "rainfall_data_coverage", "reason": reason} for reason in features["missing_data"])
        if features["is_stale"]:
            reasons.append("Rainfall data is stale and cannot support a current assessment.")

    elevation_error = elevation.get("error")
    elevation_value = _finite_nonnegative(elevation.get("elevation_m"))
    if elevation_error:
        missing_inputs.append({"input": "terrain_elevation", "reason": elevation_error})
        reasons.append("Terrain elevation could not be retrieved from Open-Elevation.")
    elif elevation_value is None:
        missing_inputs.append({"input": "terrain_elevation", "reason": "A valid elevation measurement is unavailable."})
    else:
        evidence.append({
            "kind": "measurement",
            "name": "Terrain elevation",
            "value": elevation_value,
            "unit": (elevation.get("units") or {}).get("elevation", "metres"),
            "observed_at_utc": elevation.get("retrieved_at_utc"),
            "source": elevation.get("source"),
            "provider_coordinates": elevation.get("location"),
        })

    if alerts.get("status") == "unavailable" or alerts.get("error"):
        missing_inputs.append({
            "input": "official_flood_alerts",
            "reason": alerts.get("error") or "NDMA SACHET alert feed is unavailable; absence of data is not an all-clear.",
        })
    elif alerts.get("status") != "available":
        missing_inputs.append({
            "input": "official_flood_alerts",
            "reason": "Official alert feed status was not provided; alert state is unknown.",
        })
    else:
        for alert in alerts.get("alerts", []):
            if not isinstance(alert, dict):
                continue
            evidence.append({
                "kind": "official_alert" if alert.get("validity_status") == "active" else "official_alert_record",
                "name": alert.get("headline") or alert.get("event") or "Official alert",
                "severity": alert.get("severity"),
                "cap_status": alert.get("cap_status"),
                "wording": alert.get("description"),
                "instruction": alert.get("instruction"),
                "sender": alert.get("sender"),
                "effective_utc": alert.get("effective_utc"),
                "expires_utc": alert.get("expires_utc"),
                "validity_status": alert.get("validity_status"),
                "area_description": alert.get("area_description"),
                "matched_location": alert.get("matched_location"),
                "match_reason": alert.get("match_reason"),
                "source": alert.get("source"),
                "link": alert.get("link"),
            })
        if not alerts.get("active_matched_count"):
            missing_inputs.append({
                "input": "official_flood_alerts",
                "reason": alerts.get("message") or "No verified active matching alert was found; this is not confirmation that no active alerts exist.",
            })

    if gauges.get("status") == "unavailable" or gauges.get("error"):
        missing_inputs.append({
            "input": "river_gauge_levels",
            "reason": gauges.get("error") or "CWC gauge data is unavailable; no reading is inferred.",
        })
    elif gauges.get("status") != "available":
        missing_inputs.append({
            "input": "river_gauge_levels",
            "reason": "River-gauge provider status was not provided.",
        })
    else:
        for station in gauges.get("stations", []):
            if not isinstance(station, dict):
                continue
            observation = station.get("observation") or {}
            evidence.append({
                "kind": "context_only_gauge_candidate",
                "name": f"Nearest gauge candidate: {station.get('station', 'CWC station')}",
                "value": observation.get("value"),
                "unit": observation.get("unit"),
                "observed_at": observation.get("observed_at"),
                "is_stale": station.get("is_stale"),
                "station_location": station.get("station_location"),
                "distance_from_selected_point_km": station.get("distance_from_selected_point_km"),
                "relation_verified": False,
                "match_reason": station.get("match_reason"),
                "source": station.get("source"),
            })
        missing_inputs.append({
            "input": "river_gauge_location_relationship",
            "reason": "The nearest CWC station candidates are shown for context, but source-supported upstream, downstream, or basin connectivity to the selected point has not been established.",
        })

    missing_inputs.extend({"input": name, "reason": reason} for name, reason in MISSING_RISK_INPUTS)
    reasons.extend([
        "Rainfall and point elevation do not establish runoff, river response, inundation, or local flood probability.",
        "No validated local model or defensible category thresholds are available; a numerical or categorical risk rating is withheld.",
    ])
    return {
        "status": "not_assessed",
        "risk_category": None,
        "location": {
            "requested_latitude": requested.get("latitude"),
            "requested_longitude": requested.get("longitude"),
            "rainfall_provider_coordinates": rainfall.get("location"),
            "elevation_provider_coordinates": elevation.get("location"),
        },
        "rainfall_features": features,
        "evidence": evidence,
        "missing_inputs": missing_inputs,
        "limitations": [
            "This assessment is not a flood forecast or official warning.",
            "Open-Meteo returns weather-grid coordinates that may differ from the selected map point.",
            "Rainfall accumulation is not a validated flood indicator and does not account for infiltration or drainage.",
            "Elevation is a point estimate and does not describe nearby river stage, barriers, or inundation pathways.",
            "Observed and forecast hourly records are separated; forecasts are excluded from observed accumulation and trend features.",
            "The current Open-Meteo request contains only one past day, which cannot substitute for verified long-term flood history.",
        ],
        "reasons": reasons,
        "explanation": (
            "Flood risk is not assessed. Available rainfall and terrain measurements can be summarized, "
            "but there is not enough verified local evidence or a validated model to support a defensible flood-risk category."
        ),
        "assessed_at": assessed.astimezone(timezone.utc).isoformat(),
    }
