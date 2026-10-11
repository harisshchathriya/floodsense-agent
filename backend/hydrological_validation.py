"""Hydrological relevance and historical validation preparation utilities.

No geography or event labels are bundled here. Callers must provide evidence
from documented authoritative datasets before a connection can be verified.
"""
from datetime import datetime, timezone
from math import isfinite, sqrt


def assess_gauge_connection(station, *, selected_basin=None, network_evidence=None):
    station = station if isinstance(station, dict) else {}
    location = station.get("station_location") or {}
    lat, lon = location.get("latitude"), location.get("longitude")
    has_coordinates = (
        all(isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)
            for value in (lat, lon))
        and -90 <= lat <= 90 and -180 <= lon <= 180
    )
    if not has_coordinates:
        status = "insufficient_geographic_data"
        reason = "Station coordinates are missing or invalid."
    else:
        station_basin = station.get("basin")
        same_basin = bool(selected_basin and station_basin and
                          str(selected_basin).strip().casefold() == str(station_basin).strip().casefold())
        network = network_evidence if isinstance(network_evidence, dict) else {}
        if same_basin and network.get("connected") is True and network.get("verified_by") and network.get("source") and network.get("flow_direction_known") is True and network.get("station_placement_known") is True:
            status = "verified_connection"
            reason = "Authoritative network evidence confirms a connected path, flow direction, and station placement."
        elif same_basin:
            status = "same_basin_unverified_connection"
            reason = "Both basin labels match, but a connected river path and relevant flow direction are not verified."
        else:
            status = "proximity_only"
            reason = "Station is a geographic candidate; no matching verified basin or river-network connection is available."
    return {
        "status": status,
        "distance_km": station.get("distance_from_selected_point_km"),
        "selected_basin": selected_basin,
        "station_basin": station.get("basin"),
        "network_connection_verified": status == "verified_connection",
        "flow_direction_known": bool((network_evidence or {}).get("flow_direction_known")) if isinstance(network_evidence, dict) else False,
        "station_placement_known": bool((network_evidence or {}).get("station_placement_known")) if isinstance(network_evidence, dict) else False,
        "reason": reason,
    }


def _parse_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        for pattern in ("%d-%m-%Y %H:%M", "%d-%m-%Y %H:%M:%S"):
            try:
                return datetime.strptime(value.strip(), pattern)
            except ValueError:
                continue
        return None


def prepare_historical_records(records, *, dataset_kind, expected_unit=None, expected_step_hours=1):
    """Validate and normalize event/gauge records while retaining raw values.

    Required source schema: source_id, observed_at, value, unit; event datasets
    additionally require an explicit boolean flood_event label. Unit conversion
    is intentionally not attempted.
    """
    issues, normalized, seen = [], [], {}
    if dataset_kind not in {"gauge", "event"}:
        raise ValueError("dataset_kind must be 'gauge' or 'event'")
    for index, record in enumerate(records if isinstance(records, list) else []):
        if not isinstance(record, dict):
            issues.append({"row": index, "issue": "record_not_object"})
            continue
        source_id = record.get("source_id")
        raw_time = record.get("observed_at")
        parsed = _parse_time(raw_time)
        if "latitude" in record or "longitude" in record:
            try:
                lat = float(record.get("latitude"))
                lon = float(record.get("longitude"))
                coordinates_valid = isfinite(lat) and isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180
            except (TypeError, ValueError):
                coordinates_valid = False
            if not coordinates_valid:
                issues.append({"row": index, "issue": "invalid_station_metadata"})
                continue
        value = record.get("value")
        unit = record.get("unit")
        if not source_id:
            issues.append({"row": index, "issue": "missing_source_id"})
            continue
        if parsed is None:
            issues.append({"row": index, "issue": "missing_or_invalid_timestamp"})
            continue
        if dataset_kind == "event" and not isinstance(record.get("flood_event"), bool):
            issues.append({"row": index, "issue": "invalid_or_missing_event_label"})
            continue
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value)):
            issues.append({"row": index, "issue": "invalid_numeric_value"})
            continue
        if value is not None and (not isinstance(unit, str) or not unit.strip()):
            issues.append({"row": index, "issue": "missing_unit_for_value"})
            continue
        if expected_unit and value is not None and unit != expected_unit:
            issues.append({"row": index, "issue": "unit_mismatch", "expected": expected_unit, "received": unit})
            continue
        timezone_original = parsed.tzname() if parsed.tzinfo else None
        utc = parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else None
        dedup_time = parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else raw_time
        key = (str(source_id), dedup_time, record.get("variable", "water_level"))
        record_type = record.get("record_type", "observation")
        if record_type not in {"observation", "forecast"}:
            issues.append({"row": index, "issue": "invalid_record_type"})
            continue
        signature = (value, unit, record.get("flood_event"))
        if key in seen:
            issue = "duplicate_record" if seen[key] == signature else "conflicting_duplicate_record"
            issues.append({"row": index, "issue": issue})
            continue
        seen[key] = signature
        normalized.append({**record, "record_type": record_type, "observed_at_original": raw_time,
                           "original_timezone": timezone_original,
                           "time_basis": timezone_original or "unknown",
                           "timezone_verified": parsed.tzinfo is not None,
                           "observed_at_utc": utc, "time_interpretation": "normalized" if utc else "timezone_unknown",
                           "value": value, "unit": unit if value is not None else unit})
    times_by_source = {}
    for record in normalized:
        timestamp = _parse_time(record["observed_at_original"])
        if timestamp is not None:
            times_by_source.setdefault((record["source_id"], timestamp.tzinfo is not None), []).append(timestamp)
    gaps = []
    for (source_id, _timezone_aware), times in times_by_source.items():
        ordered_times = sorted(times)
        for left, right in zip(ordered_times, ordered_times[1:]):
            hours = (right - left).total_seconds() / 3600
            if hours > expected_step_hours:
                gaps.append({"source_id": source_id,
                             "from_utc": left.astimezone(timezone.utc).isoformat() if left.tzinfo else None,
                             "to_utc": right.astimezone(timezone.utc).isoformat() if right.tzinfo else None,
                             "from_original": left.isoformat(), "to_original": right.isoformat(),
                             "gap_hours": round(hours, 3)})
    coverage_times = [
        _parse_time(record["observed_at_original"])
        for record in normalized
        if _parse_time(record["observed_at_original"]) is not None
    ]
    aware_times = [instant.astimezone(timezone.utc) for instant in coverage_times if instant.tzinfo]
    naive_times = [instant for instant in coverage_times if instant.tzinfo is None]
    return {"dataset_kind": dataset_kind, "records": normalized,
            "record_count": len(normalized), "invalid_or_excluded_count": len(issues),
            "issues": issues, "temporal_gaps": gaps,
            "coverage": {"start_utc": min(aware_times).isoformat() if aware_times else None,
                         "end_utc": max(aware_times).isoformat() if aware_times else None,
                         "start_original_timezone_unknown": min(naive_times).isoformat() if naive_times else None,
                         "end_original_timezone_unknown": max(naive_times).isoformat() if naive_times else None,
                         "timezone_unknown_count": sum(r["observed_at_utc"] is None for r in normalized)},
            "provenance_required": True, "source_attribution": "must be supplied by dataset manifest",
            "observations_forecasts_separated": True,
            "unit_conversion_performed": False}


def confusion_metrics(y_true, y_pred):
    if len(y_true) != len(y_pred) or not y_true:
        raise ValueError("Labels and predictions must have equal, non-zero length")
    if any(type(value) is not bool for value in list(y_true) + list(y_pred)):
        raise ValueError("Labels and predictions must be booleans")
    tp = sum(t and p for t, p in zip(y_true, y_pred))
    fp = sum((not t) and p for t, p in zip(y_true, y_pred))
    tn = sum((not t) and (not p) for t, p in zip(y_true, y_pred))
    fn = sum(t and (not p) for t, p in zip(y_true, y_pred))
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision is not None and recall is not None and precision + recall else 0.0 if precision is not None and recall is not None else None
    return {"evaluation_unit": "caller-defined independent labeled unit (prefer flood event, not hourly row)",
            "true_positive": tp, "false_positive": fp, "true_negative": tn, "false_negative": fn,
            "precision": precision, "recall": recall, "f1": f1,
            "false_alarm_rate": fp / (fp + tn) if fp + tn else None,
            "false_alarm_rate_denominator": "all observed negative evaluation units (FP + TN)"}


def event_detection_lead_times(events, detections):
    """Return onset-to-first-detection lead times, in hours, per event ID."""
    first_detection = {}
    for item in detections:
        event_id = item.get("event_id")
        instant = _parse_time(item.get("detected_at"))
        if event_id and instant and instant.tzinfo:
            first_detection[event_id] = min(first_detection.get(event_id, instant), instant)
    output = []
    for event in events:
        event_id = event.get("event_id")
        onset = _parse_time(event.get("onset_at"))
        detection = first_detection.get(event_id)
        if not event_id or not onset or not onset.tzinfo or not detection:
            continue
        output.append({"event_id": event_id,
                       "lead_time_hours": round((onset - detection).total_seconds() / 3600, 3),
                       "detected_at": detection.isoformat(), "onset_at": onset.isoformat(),
                       "detected_before_onset": detection <= onset})
    return output


def match_official_event_periods(observations, event_periods):
    """Link observations to documented positive station periods only.

    This does not label records outside a published period as negative. A caller
    must provide a verified common time basis and exact station identity.
    """
    linked, excluded = [], []
    for period in event_periods:
        required = ("event_id", "station", "start", "end", "source_url", "definition", "time_basis")
        if any(not period.get(key) for key in required):
            excluded.append({"event_id": period.get("event_id"), "reason": "incomplete_label_provenance_or_definition"})
            continue
        start, end = _parse_time(period["start"]), _parse_time(period["end"])
        if not start or not end or end < start:
            excluded.append({"event_id": period["event_id"], "reason": "invalid_event_interval"})
            continue
        candidates = [record for record in observations
                      if str(record.get("source_id", "")).strip().casefold() == str(period["station"]).strip().casefold()]
        if not candidates:
            excluded.append({"event_id": period["event_id"], "station": period["station"],
                             "reason": "no_exact_station_match"})
            continue
        compatible = [record for record in candidates
                      if record.get("timezone_verified") is True
                      and period.get("timezone_verified") is True
                      and record.get("time_basis") == period["time_basis"]]
        if not compatible:
            excluded.append({"event_id": period["event_id"], "station": period["station"],
                             "reason": "time_basis_not_verified"})
            continue
        matched = 0
        for record in compatible:
            instant = _parse_time(record.get("observed_at_utc") or record.get("observed_at_original") or record.get("observed_at"))
            if instant is not None and start <= instant <= end:
                linked.append({"event_id": period["event_id"], "station": period["station"],
                               "observed_at": record.get("observed_at_original") or record.get("observed_at"),
                               "label": True, "label_definition": period["definition"],
                               "source_url": period["source_url"]})
                matched += 1
        if not matched:
            excluded.append({"event_id": period["event_id"], "station": period["station"],
                             "reason": "no_observations_inside_official_period"})
    return {"linked_positive_observations": linked, "excluded_periods": excluded,
            "negative_labels_created": 0,
            "note": "Observations outside official event periods remain unlabeled, not negative."}


def coverage_summary(expected_count, observed_values):
    """Summarize completeness without treating missing observations as zero."""
    valid = sum(value is not None for value in observed_values)
    expected = max(0, int(expected_count))
    return {"expected_count": expected, "received_count": len(observed_values),
            "valid_count": valid, "missing_count": max(0, expected - valid),
            "coverage_fraction": round(valid / expected, 4) if expected else None,
            "missingness_fraction": round(max(0, expected - valid) / expected, 4) if expected else None,
            "missing_values_imputed": False}


def temporal_split(records, *, train_end, validation_end, event_key="event_id"):
    """Chronological split; an event key may occur in only one split."""
    train_cut, validation_cut = _parse_time(train_end), _parse_time(validation_end)
    if (not train_cut or not validation_cut or train_cut.tzinfo is None
            or validation_cut.tzinfo is None or train_cut >= validation_cut):
        raise ValueError("Require valid train_end < validation_end timestamps")
    result = {"train": [], "validation": [], "test": []}
    event_splits = {}
    for record in records:
        instant = _parse_time(record.get("observed_at_utc") or record.get("observed_at"))
        if instant is None:
            raise ValueError("Every split record must have a valid timestamp")
        if instant.tzinfo is None:
            raise ValueError("Split timestamps must include a timezone")
        window_start = _parse_time(record.get("window_start_utc"))
        window_end = _parse_time(record.get("window_end_utc"))
        if window_start and window_end:
            for boundary in (train_cut, validation_cut):
                if window_start <= boundary < window_end:
                    raise ValueError("Evaluation window overlaps a temporal split boundary")
        split = "train" if instant <= train_cut else "validation" if instant <= validation_cut else "test"
        event_id = record.get(event_key)
        if event_id is not None and event_id in event_splits and event_splits[event_id] != split:
            raise ValueError(f"Event {event_id!r} crosses temporal split boundary")
        if event_id is not None:
            event_splits[event_id] = split
        result[split].append(record)
    return result


def validation_readiness(monitoring):
    monitoring = monitoring if isinstance(monitoring, dict) else {}
    gauges = monitoring.get("river_gauges") or {}
    stations = gauges.get("stations") or []
    gauge_reports = []
    for station in stations:
        relation = assess_gauge_connection(station)
        gauge_reports.append({"station": station.get("station"), "distance_km": relation["distance_km"],
                              "freshness_status": station.get("freshness_status", "unknown"),
                              "connection": relation})
    missing = [
        "No ingested, source-attributed historical flood-event labels are available.",
        "No historical gauge time series is connected; the live telemetry snapshot is not historical validation data.",
        "No river network with flow direction or station placement is integrated for connection verification.",
        "No selected-point basin assignment is computed from authoritative boundary geometry.",
        "No locally documented decision thresholds or validated flood-risk rules are configured.",
    ]
    return {"report_version": 1, "evaluation_possible": False,
            "validation_status": "not_ready", "real_historical_validation_completed": False,
            "available_sources": [
                {"name": "Open-Meteo", "status": (monitoring.get("rainfall") or {}).get("status", "available" if monitoring.get("rainfall") and "error" not in monitoring["rainfall"] else "unavailable"), "coverage": "point-based recent hourly history and forecast; short window only", "historical_flood_labels": False},
                {"name": "CWC/NWIC Cauvery telemetry CSV", "status": gauges.get("status", "unknown"), "coverage": "Cauvery dataset snapshot; latest station observations only", "historical_gauge_series_integration": False},
                {"name": "NDMA SACHET", "status": (monitoring.get("official_alerts") or {}).get("status", "unknown"), "coverage": "public alert feed; alerts are not verified flood-event labels", "historical_event_archive_integrated": False},
                {"name": "Open-Elevation", "status": (monitoring.get("elevation") or {}).get("status", "available" if monitoring.get("elevation") and "error" not in monitoring["elevation"] else "unavailable"), "coverage": "point elevation; not a historical hydrology record", "historical_records": False}],
            "geography": {"basin_boundary_source": "India-WRIS/NWIC Hydrological Boundaries; documented, download dataset, not integrated", "river_network_source": None, "flow_direction_source": None,
                          "gauge_connection_status": gauge_reports},
        "historical_time_coverage": {"verified_event_records": 0, "historical_gauge_records": 0, "status": "available_for_ingestion_not_integrated", "note": "Official historical CWC gauge datasets are listed below but have not been ingested or quality-reviewed in FloodSense."},
        "historical_sources": [
            {"name": "CWC/NWDP Cauvery hourly gauge history", "periods": ["1961-1990", "1991-2020", "2021-2025"], "status": "cataloged_downloadable_not_ingested"},
            {"name": "CWC India flood affected area assessment", "period": "1986-2022", "spatial_resolution": "aggregated sub-district extent, not event timestamps", "status": "context_source_not_event_labels"}],
            "data_quality_problems": [
                {"station": station.get("station"), "issue": station.get("freshness_status")}
                for station in stations
                if station.get("freshness_status") in {"stale", "unknown_timezone"}
            ],
            "missing_evidence": missing,
            "evaluation_design": {"metrics": ["TP", "FP", "TN", "FN", "precision", "recall", "F1", "false_alarm_rate", "event_lead_time", "coverage", "missingness"],
                                 "split_policy": "chronological train/validation/test; keep each event and its overlapping windows within one split",
                                 "false_alarm_rate_denominator": "FP / (FP + TN), where units are independent labeled negative events or time windows declared by the evaluation protocol"},
            "classification_blockers": missing + ["Flood risk remains not_assessed; this report does not create a risk category."]}
