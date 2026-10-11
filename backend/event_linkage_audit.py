"""Build a reproducible, conservative audit of official event/station pairs."""
import argparse
import csv
import json
import math
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hydrological_validation import _parse_time


RIVER_NETWORK_URL = "https://nwdp.nwic.gov.in/dataset/river-line"


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _coordinates(record):
    lat = _number(record.get("latitude"))
    lon = _number(record.get("longitude"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def _distance_km(left, right):
    lat1, lon1 = map(math.radians, left)
    lat2, lon2 = map(math.radians, right)
    arc = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return round(6371.0088 * 2 * math.asin(math.sqrt(arc)), 2)


def _same_name(left, right):
    return " ".join(str(left or "").casefold().split()) == " ".join(str(right or "").casefold().split())


def _label_status(event):
    """Accept only a source-cited, station-specific and dated threshold period."""
    threshold = _number(event.get("threshold_value"))
    valid = bool(
        event.get("source_url")
        and event.get("location_source_url")
        and event.get("station_code")
        and event.get("source_claims_period_above_threshold") is True
        and event.get("threshold_type") in {"warning", "danger"}
        and threshold is not None
        and event.get("threshold_unit") == "m"
        and _same_name(event.get("threshold_station"), event.get("station"))
        and event.get("threshold_validity_year") == (_parse_time(event.get("start")).year if _parse_time(event.get("start")) else None)
        and _parse_time(event.get("start")) is not None
        and _parse_time(event.get("end")) is not None
    )
    return {
        "status": (f"documented_station_{event.get('threshold_type')}_threshold_exceeded" if valid else "event_label_unresolved"),
        "definition": f"CWC-reported interval above this station's {event.get('threshold_type')} threshold" if valid else None,
        "source_url": event.get("source_url"),
        "threshold_value": threshold,
        "threshold_unit": event.get("threshold_unit"),
        "threshold_type": event.get("threshold_type"),
        "station_applicability": "verified_for_named_CWC_station" if valid else "unverified",
        "validity_period": event.get("threshold_validity_year"),
        "reason": None if valid else "threshold_source_unit_station_or_validity_not_verified",
    }


def summarize_event_window_observations(csv_path, events, *, id_column="Station", timestamp_column="Data Acquisition Time"):
    """Count calendar-clock observations in each reported interval without timezone conversion."""
    periods = {}
    for event in events:
        start, end = _parse_time(event.get("start")), _parse_time(event.get("end"))
        if start is not None and end is not None and end >= start:
            periods[event.get("event_id")] = (start, end)
    summary = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        for row in reader:
            station = (row.get(id_column) or "").strip()
            observed_at = _parse_time((row.get(timestamp_column) or "").strip())
            if not station or observed_at is None:
                continue
            for event_id, (start, end) in periods.items():
                aware = observed_at.tzinfo is not None
                if aware != (start.tzinfo is not None) or aware != (end.tzinfo is not None):
                    # Mixed aware/naive clocks cannot be compared without an explicit conversion basis.
                    continue
                observed_cmp, start_cmp, end_cmp = ((observed_at.astimezone(timezone.utc), start.astimezone(timezone.utc), end.astimezone(timezone.utc))
                    if aware else (observed_at, start, end))
                if start_cmp <= observed_cmp <= end_cmp:
                    item = summary.setdefault((event_id, station), {"count": 0, "first": None, "last": None, "timezone_verified": aware})
                    item["count"] += 1
                    stamp = observed_at.isoformat()
                    item.setdefault("timestamps", []).append(stamp)
                    item["first"] = min(item["first"], stamp) if item["first"] else stamp
                    item["last"] = max(item["last"], stamp) if item["last"] else stamp
    return summary


def _measurement_value(value):
    if value is None or not str(value).strip():
        return "missing", None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "invalid", None
    if not math.isfinite(number):
        return "invalid", None
    return "valid", number


def audit_historical_telemetry(csv_path, events, *, now=None, expected_interval_hours=1):
    """Measure raw source quality and per-event calendar-clock coverage without relabeling time."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    grouped = {}
    field_missingness = Counter()
    row_count = 0
    source_fields = []
    period_map = {}
    for event in events:
        start, end = _parse_time(event.get("start")), _parse_time(event.get("end"))
        if start is not None and end is not None and end >= start:
            period_map[event.get("event_id")] = (start, end)

    with open(csv_path, newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        source_fields = reader.fieldnames or []
        headers_normalized = {name.casefold().replace("_", " ").strip(): name for name in source_fields}
        station_column = next((headers_normalized[key] for key in ("station", "station name") if key in headers_normalized), None)
        time_column = next((headers_normalized[key] for key in ("data acquisition time", "timestamp", "observed at") if key in headers_normalized), None)
        level_column = next((name for name in source_fields if "water level" in name.casefold() and "meter" in name.casefold()), None)
        if not station_column or not time_column or not level_column:
            raise ValueError("Historical telemetry is missing station, timestamp, or meter-level fields")
        unit_column = next((name for name in source_fields if name.casefold().strip() in {"unit", "units", "measurement unit"}), None)
        for row in reader:
            row_count += 1
            for field in source_fields:
                if not (row.get(field) or "").strip():
                    field_missingness[field] += 1
            name = (row.get(station_column) or "").strip()
            if not name:
                name = "[missing station name]"
            record = grouped.setdefault(name, {"rows": 0, "missing_measurements": 0, "invalid_measurements": 0,
                "valid_measurements": 0, "missing_timestamps": 0, "invalid_timestamps": 0,
                "records": [], "units": set(), "zero_gauge": set(), "mean_sea_level": set(),
                "coordinates": set(), "rivers": set(), "local_rivers": set(), "basins": set(), "event_windows": {}})
            record["rows"] += 1
            stamp_raw = (row.get(time_column) or "").strip()
            stamp = _parse_time(stamp_raw) if stamp_raw else None
            if not stamp_raw:
                record["missing_timestamps"] += 1
            elif stamp is None:
                record["invalid_timestamps"] += 1
            value_status, value = _measurement_value(row.get(level_column))
            record[{"missing": "missing_measurements", "invalid": "invalid_measurements", "valid": "valid_measurements"}[value_status]] += 1
            if unit_column and (row.get(unit_column) or "").strip():
                record["units"].add((row.get(unit_column) or "").strip())
            for source_key, target in (("RL_of_zeroGauge", "zero_gauge"), ("MeanSeaLevel", "mean_sea_level"),
                                       ("River", "rivers"), ("Local River", "local_rivers"), ("Basin", "basins")):
                actual = headers_normalized.get(source_key.casefold().replace("_", " "))
                cell = (row.get(actual) or "").strip() if actual else ""
                if cell:
                    record[target].add(cell)
            lat_col = headers_normalized.get("latitude")
            lon_col = headers_normalized.get("longitude")
            if lat_col and lon_col and (row.get(lat_col) or "").strip() and (row.get(lon_col) or "").strip():
                record["coordinates"].add(((row.get(lat_col) or "").strip(), (row.get(lon_col) or "").strip()))
            record["records"].append((stamp, stamp_raw, row.get(level_column), value))
            for event_id, (start, end) in period_map.items():
                if stamp is None:
                    continue
                aware = stamp.tzinfo is not None
                if aware != (start.tzinfo is not None) or aware != (end.tzinfo is not None):
                    continue
                stamp_cmp, start_cmp, end_cmp = ((stamp.astimezone(timezone.utc), start.astimezone(timezone.utc), end.astimezone(timezone.utc))
                    if aware else (stamp, start, end))
                if start_cmp <= stamp_cmp <= end_cmp:
                    window = record["event_windows"].setdefault(event_id, {"count": 0, "valid_observation_count": 0, "missing_observation_count": 0, "invalid_observation_count": 0, "first": None, "last": None, "timestamps": [], "timezone_compatible": aware})
                    window["count"] += 1
                    window[{"valid": "valid_observation_count", "missing": "missing_observation_count", "invalid": "invalid_observation_count"}[value_status]] += 1
                    iso = stamp.isoformat()
                    window["timestamps"].append(iso)
                    window["first"] = min(window["first"], iso) if window["first"] else iso
                    window["last"] = max(window["last"], iso) if window["last"] else iso

    expected_seconds = expected_interval_hours * 3600
    station_reports = []
    stale_cutoff = now.replace(tzinfo=None) - timedelta(hours=14) - timedelta(seconds=expected_seconds * 3)
    for name, item in sorted(grouped.items(), key=lambda pair: pair[0].casefold()):
        by_time = {}
        stale_for_current = 0
        for stamp, raw_stamp, raw_value, value in item["records"]:
            if stamp is not None:
                if stamp.tzinfo is None and stamp < stale_cutoff:
                    stale_for_current += 1
                by_time.setdefault(stamp, []).append((raw_value, value))
        duplicate_groups = [rows for rows in by_time.values() if len(rows) > 1]
        unique_times = sorted(by_time)
        intervals = [(right - left).total_seconds() for left, right in zip(unique_times, unique_times[1:])]
        irregular = sum(delta != expected_seconds for delta in intervals)
        long_gaps = sum(delta > expected_seconds for delta in intervals)
        conflicting_duplicate_groups = sum(len({raw for raw, _ in rows}) > 1 for rows in duplicate_groups)
        extras = sum(len(rows) - 1 for rows in duplicate_groups)
        event_windows = {}
        for event in events:
            event_id = event.get("event_id")
            start, end = period_map.get(event_id, (None, None))
            window = item["event_windows"].get(event_id, {"count": 0, "valid_observation_count": 0, "missing_observation_count": 0, "invalid_observation_count": 0, "first": None, "last": None, "timestamps": []})
            expected_slots = int((end - start).total_seconds() // expected_seconds) + 1 if start and end and (start.tzinfo is None) == (end.tzinfo is None) else None
            event_windows[event_id] = {**window, "expected_calendar_clock_slots": expected_slots,
                "observed_timestamp_fraction_calendar_clock_only": round(window["count"] / expected_slots, 4) if expected_slots else None,
                "valid_observation_coverage_fraction_calendar_clock_only": round(window["valid_observation_count"] / expected_slots, 4) if expected_slots else None,
                "missing_expected_slots_calendar_clock_only": max(0, expected_slots - window["valid_observation_count"]) if expected_slots is not None else None,
                "timezone_compatible": bool(window.get("timezone_compatible")),
                "interpretation": "Timezone-qualified timestamps compared as instants." if window.get("timezone_compatible") else "Naive calendar-clock count only; source timezones are unknown, so this is not verified temporal coverage."}
        unit_header_declares_m = bool(level_column and "meter" in level_column.casefold())
        official_code_field = any("site code" in field.casefold() or "station code" in field.casefold() for field in source_fields)
        station_reports.append({
            "station_source_name": name, "source_station_identifier_type": "source station name; no official instrument code in telemetry",
            "source_row_count": item["rows"], "record_count": item["valid_measurements"],
            "valid_numeric_observation_count": item["valid_measurements"],
            "missing_observation_count": item["missing_measurements"], "invalid_observation_count": item["invalid_measurements"],
            "missing_timestamp_count": item["missing_timestamps"], "invalid_timestamp_count": item["invalid_timestamps"],
            "duplicate_timestamp_group_count": len(duplicate_groups), "duplicate_extra_record_count": extras,
            "conflicting_duplicate_value_group_count": conflicting_duplicate_groups,
            "unique_timestamp_count": len(unique_times), "interval_count": len(intervals),
            "irregular_interval_count_vs_expected_cadence": irregular, "intervals_longer_than_expected_count": long_gaps,
            "expected_interval_hours": expected_interval_hours,
            "coverage_start_original": unique_times[0].isoformat() if unique_times else None,
            "coverage_end_original": unique_times[-1].isoformat() if unique_times else None,
            "timestamp_timezone_status": "unknown; source does not state timezone",
            "stale_for_current_monitoring_count_conservative": stale_for_current,
            "unit_evidence": {"unit": "m" if unit_header_declares_m else None,
                "status": "declared_in_level_column_header" if unit_header_declares_m else "unknown",
                "per_record_unit_field_present": unit_column is not None,
                "observed_unit_values": sorted(item["units"]),
                "inconsistent_unit_count": None if unit_column is None else sum(unit.casefold() not in {"m", "meter", "meters"} for unit in item["units"])},
            "datum_evidence": {"status": "unresolved_cross_station_compatibility",
                "zero_gauge_values": sorted(item["zero_gauge"]), "mean_sea_level_values": sorted(item["mean_sea_level"]),
                "note": "Source fields are retained but do not establish compatible datum across stations or with event thresholds."},
            "station_metadata": {"river_values": sorted(item["rivers"]), "local_river_values": sorted(item["local_rivers"]), "basin_values": sorted(item["basins"]),
                "coordinate_pair_count": len(item["coordinates"]), "coordinate_conflict": len(item["coordinates"]) > 1,
                "official_station_code_field_present": official_code_field,
                "metadata_conflict": len(item["rivers"]) > 1 or len(item["basins"]) > 1 or len(item["coordinates"]) > 1},
            "event_window_coverage": event_windows,
        })
    return {"source_file": str(csv_path), "source_record_count": row_count,
        "field_missingness_counts": dict(sorted(field_missingness.items())),
        "measurement_field": level_column, "declared_unit": "m" if level_column and "meter" in level_column.casefold() else None,
        "source_timezone_status": "unknown; timestamps are naive and lack a documented common time basis",
        "official_station_code_field_present": any("site code" in field.casefold() or "station code" in field.casefold() for field in source_fields),
        "stations": station_reports,
        "quality_note": "Stale-for-current counts use a conservative timezone bound for current monitoring only; historical event matching uses separate calendar-clock counts and is not treated as time-verified."}


def _evaluation_eligibility(pair):
    reasons = []
    if "duplicate_event_identifier" in pair.get("exclusion_reasons", []):
        return {"status": "excluded", "reasons": ["Duplicate event identifier prevents an independent label."]}
    if pair.get("label", {}).get("status") == "event_label_unresolved":
        reasons.append("The event definition, threshold, validity, station, or source provenance is not fully verified.")
    temporal = pair.get("temporal_evidence", {})
    geography = pair.get("geographic_evidence", {})
    hydrology = pair.get("hydrological_relationship", {})
    if pair.get("label", {}).get("status") == "event_label_unresolved":
        return {"status": "unresolved", "reasons": reasons}
    identity = pair.get("station_identity", {})
    if (pair.get("match_status") == "verified" and temporal.get("exact_temporal_match_verified")
            and geography.get("status") == "verified_hydrological_connection" and not geography.get("station_metadata_conflict")
            and identity.get("status") == "verified"):
        return {"status": "eligible", "reasons": []}
    if geography.get("station_metadata_conflict"):
        reasons.append("Station metadata contains unresolved conflicts.")
    if pair.get("event_station_name") and pair.get("station_id") and not _same_name(pair["event_station_name"], pair["station_id"]):
        reasons.append("Event station name does not exactly match the telemetry source station name.")
    if temporal.get("calendar_interval_observation_count", 0):
        reasons.append("Calendar-clock observations overlap, but event and telemetry timezone compatibility is unverified.")
    else:
        reasons.append("No observation falls in the naive calendar-clock interval, but unknown timezones prevent exclusion as a true temporal mismatch.")
    if not hydrology.get("network_path_verified"):
        reasons.append("No verified hydrological path connects this event location to the gauge; distance and basin labels are insufficient.")
    if identity.get("status") != "verified":
        reasons.append("Event site code is not crosswalked to an official site code in the telemetry station record.")
    return {"status": "candidate" if temporal.get("calendar_interval_observation_count", 0) else "unresolved", "reasons": reasons}


def assess_event_station_pair(event, station, *, observation_window=None, network_evidence=None,
                              duplicate_event_id=False):
    """Assess one pair; geographic proximity and basin agreement never prove connectivity."""
    event_id = event.get("event_id")
    station_id = station.get("station") or station.get("source_id")
    event_location = _coordinates(event)
    station_location = _coordinates(station)
    distance = _distance_km(event_location, station_location) if event_location and station_location else None
    exact_station = _same_name(station_id, event.get("station"))

    event_basin = event.get("basin")
    station_basin = station.get("basin")
    same_basin = bool(event_basin and station_basin and _same_name(event_basin, station_basin))
    same_river = bool(event.get("river") and station.get("river") and _same_name(event["river"], station["river"]))
    network = network_evidence if isinstance(network_evidence, dict) else {}
    network_verified = (
        network.get("connected") is True
        and network.get("source_url")
        and network.get("flow_direction_known") is True
        and network.get("station_placement_known") is True
        and network.get("event_station_path_verified") is True
    )
    if not event_location or not station_location:
        geography_status = "insufficient_geographic_data"
    elif network_verified and same_basin:
        geography_status = "verified_hydrological_connection"
    elif same_basin:
        geography_status = "same_basin_unverified_connection"
    else:
        geography_status = "geographic_proximity_only"

    window = observation_window or {}
    count = int(window.get("count") or 0)
    window_time_verified = window.get("timezone_verified") is True
    if count and window_time_verified and event.get("timezone_verified") is True:
        temporal_status = "verified_common_time_basis_overlap"
    elif count:
        temporal_status = "calendar_interval_has_records_timezone_unverified"
    else:
        temporal_status = "temporal_mismatch_no_records_in_reported_interval"

    label = _label_status(event)
    reasons = []
    if duplicate_event_id:
        reasons.append("duplicate_event_identifier")
    if not exact_station:
        reasons.append("no_exact_station_identifier_match")
    if not event_location or not station_location:
        reasons.append("insufficient_geographic_metadata")
    elif not network_verified:
        reasons.append("authoritative_flow_direction_and_station_path_not_verified")
    if count and not event.get("timezone_verified", False):
        reasons.append("event_and_telemetry_timezone_basis_not_verified")
    if not count:
        reasons.append("no_observations_in_reported_event_interval")
    if label["status"] == "event_label_unresolved":
        reasons.append(label["reason"])

    if duplicate_event_id:
        match_status = "excluded"
    elif (exact_station and geography_status == "verified_hydrological_connection"
          and label["status"] != "event_label_unresolved"
          and event.get("timezone_verified") is True and window_time_verified):
        match_status = "verified"
    else:
        match_status = "unresolved"

    station_source = station.get("source_url") or station.get("source_name")
    return {
        "event_id": event_id,
        "event_source": event.get("source_url"),
        "event_station_name": event.get("station"),
        "station_id": station_id,
        "station_identifier_type": "source_station_name" if station_id else None,
        "station_source": station_source,
        "station_identity": {"event_station_code": event.get("station_code"),
            "telemetry_site_code": station.get("site_code") or station.get("station_code"),
            "source_station_name_exact_match": exact_station,
            "status": "verified" if (station.get("official_station_identity_verified") is True or
                (event.get("station_code") and (station.get("site_code") or station.get("station_code")) == event.get("station_code"))) else "unknown",
            "note": "Source station-name equality does not independently verify the official CWC site-code or instrument crosswalk."},
        "geographic_evidence": {
            "status": geography_status,
            "event_coordinates": list(event_location) if event_location else None,
            "station_coordinates": list(station_location) if station_location else None,
            "straight_line_distance_km": distance,
            "distance_interpretation": "proximity_only; no radius threshold implies a hydrological link",
            "event_basin": event_basin,
            "station_basin": station_basin,
            "same_basin": same_basin,
            "event_river": event.get("river"),
            "station_river": station.get("river"),
            "same_river_name": same_river,
            "station_metadata_conflict": station.get("metadata_conflict", False),
            "location_source": event.get("location_source_url"),
            "station_location_source": station.get("source_url"),
        },
        "temporal_evidence": {
            "event_start": event.get("start"),
            "event_end": event.get("end"),
            "event_time_basis": event.get("time_basis"),
            "timezone_verified": event.get("timezone_verified") is True,
            "station_coverage_start": station.get("record_time_start"),
            "station_coverage_end": station.get("record_time_end"),
            "calendar_interval_observation_count": count,
            "first_calendar_interval_observation": window.get("first"),
            "last_calendar_interval_observation": window.get("last"),
            "calendar_interval_observation_times": window.get("timestamps", []),
            "status": temporal_status,
            "exact_temporal_match_verified": bool(count and window_time_verified and event.get("timezone_verified") is True),
            "note": "Calendar-clock comparison only; NWDP telemetry timezone is unstated, so these rows do not establish exact interval alignment.",
        },
        "hydrological_relationship": {
            "status": geography_status,
            "network_source": network.get("source_url") or RIVER_NETWORK_URL,
            "network_path_verified": network_verified,
            "flow_direction_known": network.get("flow_direction_known") is True,
            "station_placement_known": network.get("station_placement_known") is True,
            "reason": "CWC records share Cauvery basin/river metadata, but the cataloged river geometry has not established this event-to-gauge path or flow direction." if same_basin else "No verified event-to-station river path is available.",
        },
        "label": label,
        "match_status": match_status,
        "exclusion_reasons": reasons if match_status != "verified" else [],
        "validation_eligibility": _evaluation_eligibility({
            "match_status": match_status, "exclusion_reasons": reasons if match_status != "verified" else [],
            "label": label, "temporal_evidence": {"calendar_interval_observation_count": count,
                "exact_temporal_match_verified": bool(count and window_time_verified and event.get("timezone_verified") is True)},
            "geographic_evidence": {"status": geography_status, "station_metadata_conflict": station.get("metadata_conflict", False)},
            "hydrological_relationship": {"network_path_verified": network_verified},
            "event_station_name": event.get("station"), "station_id": station_id,
            "station_identity": {"status": "verified" if (station.get("official_station_identity_verified") is True or
                (event.get("station_code") and (station.get("site_code") or station.get("station_code")) == event.get("station_code"))) else "unknown"},
        }),
    }


def backtesting_readiness(pairs, events, *, negative_label_count=0, prediction_rule_configured=False):
    verified_pairs = [pair for pair in pairs if pair.get("match_status") == "verified"]
    valid_events = {event.get("event_id") for event in events if _label_status(event)["status"] != "event_label_unresolved"}
    linked_event_ids = {pair.get("event_id") for pair in verified_pairs}
    linked_labels = linked_event_ids & valid_events
    missing = []
    if not verified_pairs:
        missing.append("No verified event-to-station links.")
    if not linked_labels:
        missing.append("No source-validated event labels apply to an acquired telemetry station.")
    if negative_label_count < 1:
        missing.append("No independently sourced negative event labels are available; unlabeled periods cannot be negatives.")
    if not prediction_rule_configured:
        missing.append("No predeclared prediction rule or threshold is configured for evaluation.")
    valid = bool(verified_pairs and linked_labels and negative_label_count > 0 and prediction_rule_configured)
    return {
        "valid": valid,
        "status": "ready" if valid else "blocked",
        "predictive_evaluation": "not_run",
        "metrics": None,
        "verified_pair_count": len(verified_pairs),
        "linked_labeled_event_count": len(linked_labels),
        "independent_negative_event_count": negative_label_count,
        "reason": "All required labels, links, negatives, and prediction protocol are present." if valid else "Predictive evaluation was not run because the evidence/design requirements are unmet.",
        "missing_evidence": missing,
    }


def build_event_linkage_audit(events, stations, *, observation_windows=None,
                              station_dataset_source=None, station_metadata_source=None,
                              river_network_source=RIVER_NETWORK_URL, negative_label_count=0,
                              prediction_rule_configured=False, data_quality=None):
    observation_windows = observation_windows or {}
    event_counts = Counter(event.get("event_id") for event in events if event.get("event_id"))
    pairs = []
    for event in events:
        duplicate = bool(event.get("event_id") and event_counts[event.get("event_id")] > 1)
        for station in stations:
            pair = assess_event_station_pair(
                event, {**station, "source_url": station.get("source_url") or station_dataset_source},
                observation_window=observation_windows.get((event.get("event_id"), station.get("station")), {}),
                duplicate_event_id=duplicate,
            )
            pair["hydrological_relationship"]["network_source"] = river_network_source
            pairs.append(pair)
    statuses = Counter(pair["match_status"] for pair in pairs)
    eligibility_counts = Counter(pair["validation_eligibility"]["status"] for pair in pairs)
    label_records = [{"event_id": event.get("event_id"), "station": event.get("station"), **_label_status(event)}
                     for event in events]
    coverage_starts = [s.get("record_time_start") for s in stations if s.get("record_time_start")]
    coverage_ends = [s.get("record_time_end") for s in stations if s.get("record_time_end")]
    backtest = backtesting_readiness(pairs, events, negative_label_count=negative_label_count,
                                     prediction_rule_configured=prediction_rule_configured)
    return {
        "report_version": 1,
        "report_type": "historical_event_station_linkage_audit",
        "events_examined": len(events),
        "unique_event_identifiers": len(event_counts),
        "duplicate_event_identifiers": sorted(event_id for event_id, count in event_counts.items() if count > 1),
        "stations_examined": len(stations),
        "candidate_event_station_pairs": len(pairs),
        "verified_links": statuses["verified"],
        "unresolved_links": statuses["unresolved"],
        "excluded_links": statuses["excluded"],
        "validation_eligibility_counts": {key: eligibility_counts[key] for key in ("eligible", "candidate", "unresolved", "excluded")},
        "verified_flood_event_count": 0,
        "event_classifications": [{"event_id": event.get("event_id"),
            "classification": "officially_reported_station_threshold_event_incomplete_linkage" if _label_status(event)["status"] != "event_label_unresolved" else "candidate_event_requires_verification",
            "station": event.get("station"), "station_code": event.get("station_code"),
            "start": event.get("start"), "end": event.get("end"), "time_basis": event.get("time_basis"),
            "threshold_type": event.get("threshold_type"), "threshold_value": event.get("threshold_value"),
            "threshold_unit": event.get("threshold_unit"), "source_url": event.get("source_url"),
            "location_source_url": event.get("location_source_url"),
            "impact_label_status": "not_available; source documents a gauge threshold period, not inundation or loss"} for event in events],
        "data_quality": data_quality or {},
        "evaluation": {"status": "blocked" if not backtest["valid"] else "ready_not_run",
            "predictive_evaluation": "not_run", "statistically_defensible": False,
            "metrics": None, "reason": "No eligible event-station pairs, no verified negative labels, and no predeclared prediction rule or temporal evaluation design." if not backtest["valid"] else backtest["reason"]},
        "label_definitions_and_provenance": label_records,
        "station_dataset_source": station_dataset_source,
        "station_metadata_source": station_metadata_source,
        "river_network_source": river_network_source,
        "temporal_and_geographic_coverage": {
            "station_coverage_start": min(coverage_starts) if coverage_starts else None,
            "station_coverage_end": max(coverage_ends) if coverage_ends else None,
            "station_timestamp_timezone": "unknown; source has no stated timezone",
            "event_to_station_distances": "straight-line distances in each pair; descriptive proximity only",
        },
        "milestones": {
            "historical_data_ingestion": "complete",
            "event_linking": "unresolved" if statuses["verified"] == 0 else "verified_links_available",
            "label_validation": "source_documented_event_but_not_linked_to_acquired_stations" if not linked_any_label(pairs) else "linked_labels_available",
            "predictive_evaluation": "blocked" if not backtest["valid"] else "ready_not_yet_run",
        },
        "backtesting": backtest,
        "next_actions": [
            "Acquire the CWC telemetry series for the Musiri site identifier CW1CAL000207 or an authoritative crosswalk proving an equivalent source station identifier.",
            "Obtain a documented timezone/time-basis for both telemetry and event-period timestamps.",
            "Integrate versioned CWC river geometry plus evidence of flow direction and station placement before claiming a connected gauge.",
            "Collect independently sourced event and negative-event labels, then predeclare event matching, detection windows, prediction rule, and chronological splits.",
        ] if not backtest["valid"] else [],
        "pairs": pairs,
        "risk_status_changed": False,
        "risk_status": "not_assessed",
    }


def linked_any_label(pairs):
    return any(pair.get("match_status") == "verified" and pair.get("label", {}).get("status") != "event_label_unresolved" for pair in pairs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-report", default="data/processed/cwc_cauvery_telemetry_2021_2025.prepared_report.json")
    parser.add_argument("--telemetry-csv", default="data/raw/cwc_cauvery_telemetry_2021_2025.csv")
    parser.add_argument("--events", default="historical_label_candidates.json")
    parser.add_argument("--output", default="data/processed/cwc_cauvery_event_linkage_audit.json")
    parser.add_argument("--river-network-source", default=RIVER_NETWORK_URL)
    args = parser.parse_args()
    with open(args.prepared_report, encoding="utf-8") as stream:
        prepared = json.load(stream)
    with open(args.events, encoding="utf-8") as stream:
        events = json.load(stream)
    station_source = prepared.get("provenance", {}).get("source_url")
    stations = prepared.get("stations", [])
    windows = summarize_event_window_observations(args.telemetry_csv, events)
    quality = audit_historical_telemetry(args.telemetry_csv, events)
    report = build_event_linkage_audit(
        events, stations, observation_windows=windows,
        station_dataset_source=station_source,
        station_metadata_source=events[0].get("location_source_url") if events else None,
        river_network_source=args.river_network_source,
        data_quality=quality,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "report_type", "events_examined", "stations_examined", "candidate_event_station_pairs",
        "verified_links", "unresolved_links", "excluded_links", "validation_eligibility_counts", "risk_status"
    )} | {"output": str(output_path), "backtesting": report["backtesting"]}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, csv.Error) as exc:
        print(f"Event-linkage audit failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
