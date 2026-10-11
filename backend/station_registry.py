"""Source-derived CWC station metadata and coverage for the Cauvery dashboard.

The CWC CSVs publish station names, not stable station codes. ``station_id`` is
therefore a deterministic local key; it must not be represented as an official
CWC identifier or as proof of physical instrument identity.
"""
from collections import defaultdict
from datetime import datetime
from functools import lru_cache
import csv
import json
import math
from pathlib import Path
import re
import unicodedata


DATA_DIR = Path(__file__).resolve().parent / "data" / "raw"
TELEMETRY_FILES = (
    ("cwc_cauvery_telemetry_2021_2025.csv", "historical_2021_2025"),
    ("cwc_cauvery_telemetry_2026_2030.csv", "current_2026_2030"),
)
TELEMETRY_SOURCE_URL = (
    "https://nwdp.nwic.gov.in/dataset/68600163-c5a0-4327-aa1a-fa7157b86cce/"
    "resource/d027c5ac-379d-4ac2-8ced-97b02b6edbc0/download/"
    "rwl_tel_hr_cwc_009_2026_2030.csv"
)
HISTORICAL_SOURCE_URL = (
    "https://nwdp.nwic.gov.in/en/dataset/river-water-level-telemetry-hourly-"
    "central-water-commission-cwc/resource/b5776a31-0583-4c27-a2d9-ee81e3b3a03a"
)
MUSIRI_THRESHOLD = {
    "value": 82.12,
    "unit": "m",
    "status": "verified_historical_value",
    "value_verification_status": "verified",
    "applicability_status": "unknown",
    "source": "CWC Appraisal Report 2024, Annex XIII",
    "source_url": "https://cwc.gov.in/sites/default/files/appraisal-report-2024-publicationcompressed.pdf",
    "note": "Historical reference only; current site identity, datum compatibility, and validity are unresolved.",
}


def normalize_station_id(value):
    """Convert a source name into a predictable route-safe local key."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Station name must be a non-empty string")
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    if not slug:
        raise ValueError("Station name does not produce a valid station key")
    return slug


def _parse_source_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        # This feed's timestamps are timezone-naive; preserve that fact.
        return datetime.strptime(value.strip(), "%d-%m-%Y %H:%M")
    except ValueError:
        return None


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def classify_hydrological_relationship(*, verified_network_connection=False, basin_status="unknown", basin=None, coordinates_available=False):
    """Classify only evidence supplied by authoritative basin/network metadata."""
    if verified_network_connection:
        return "verified_river_network_connection"
    if basin_status == "verified" and isinstance(basin, str) and basin.casefold() == "cauvery":
        return "same_basin_no_verified_river_connection"
    if coordinates_available:
        return "proximity_only"
    return "insufficient_geographic_data"


def _field(values):
    values = sorted({value.strip() for value in values if isinstance(value, str) and value.strip()})
    return {"value": values[0] if len(values) == 1 else values or None,
            "status": "verified" if len(values) == 1 else "conflicting" if values else "not_available"}


@lru_cache(maxsize=1)
def _registry():
    grouped = defaultdict(lambda: {"rows": [], "files": defaultdict(list)})
    for filename, dataset_key in TELEMETRY_FILES:
        path = DATA_DIR / filename
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            for row in reader:
                source_name = (row.get("Station") or "").strip()
                if not source_name:
                    continue
                entry = grouped[normalize_station_id(source_name)]
                entry["rows"].append(row)
                entry["files"][dataset_key].append(row)

    result = {}
    for station_id, entry in grouped.items():
        rows = entry["rows"]
        names = sorted({(row.get("Station") or "").strip() for row in rows})
        if len(names) != 1:
            raise ValueError(f"Duplicate normalized station identifier {station_id!r}: {names!r}")
        river = _field(row.get("River") or row.get("Local River") for row in rows)
        basin = _field(row.get("Basin") for row in rows)
        lat_field = _field(row.get("Latitude") for row in rows)
        lon_field = _field(row.get("Longitude") for row in rows)
        lat = _number(lat_field["value"]) if lat_field["status"] == "verified" else None
        lon = _number(lon_field["value"]) if lon_field["status"] == "verified" else None
        coord_status = "verified" if lat is not None and lon is not None else "conflicting" if "conflicting" in {lat_field["status"], lon_field["status"]} else "not_available"
        conflicts = []
        if station_id == "musiri":
            verification_path = Path(__file__).resolve().parent / "data" / "processed" / "musiri_station_verification.json"
            verification = {}
            if verification_path.is_file():
                try:
                    report = json.loads(verification_path.read_text(encoding="utf-8"))
                    verification = next((field for field in report.get("fields", []) if field.get("field") == "coordinates"), {})
                except (OSError, ValueError, TypeError):
                    verification = {}
            if verification.get("status") in {"verified", "conflicting", "unknown", "not_available"}:
                coord_status = verification["status"]
            if verification.get("status") == "conflicting":
                conflicts.append({"field": "coordinates", "status": "conflicting", "telemetry": verification.get("telemetry"), "inventory": verification.get("inventory"), "distance_km": verification.get("separation_km"), "note": verification.get("note")})
        if river["status"] == "conflicting":
            conflicts.append({"field": "river", "status": "conflicting", "values": river["value"]})

        timestamps, invalid_times, missing_levels = [], 0, 0
        gaps = 0
        coverage = {}
        for dataset_key, dataset_rows in entry["files"].items():
            times = []
            for row in dataset_rows:
                stamp = _parse_source_time(row.get("Data Acquisition Time"))
                if stamp is None:
                    invalid_times += 1
                else:
                    times.append(stamp)
                    timestamps.append(stamp)
                if not (row.get("River Water Level Telemetry Hourly (meter)") or "").strip():
                    missing_levels += 1
            unique_times = sorted(set(times))
            gaps += sum(1 for left, right in zip(unique_times, unique_times[1:]) if (right - left).total_seconds() > 2 * 3600)
            coverage[dataset_key] = {
                "record_count": len(dataset_rows),
                "first_observation": min(times).isoformat() if times else None,
                "last_observation": max(times).isoformat() if times else None,
                "timestamp_timezone": "unknown (source timestamps are naive)",
            }
        timestamps.sort()
        connection = classify_hydrological_relationship(
            basin_status=basin["status"], basin=basin["value"], coordinates_available=lat is not None and lon is not None
        )
        if connection == "same_basin_no_verified_river_connection":
            connection_note = "CWC basin metadata names Cauvery; available files do not provide a verified river-network crosswalk or flow order."
        elif connection == "proximity_only":
            connection_note = "Coordinates are available, but a basin or river-network relationship is not verified."
        else:
            connection_note = "There is insufficient verified basin or coordinate metadata to describe a relationship."
        if station_id == "musiri":
            connection_note += " The feed coordinates conflict with the CWC inventory reference; this does not establish a network connection."

        result[station_id] = {
            "station_id": station_id,
            "station_id_type": "local_normalized_source_name",
            "station_id_verification_status": "unknown",
            "source_station_identifier": names[0] if len(names) == 1 else None,
            "source_station_identifier_status": "verified" if len(names) == 1 else "conflicting",
            "official_name": {"value": names[0] if len(names) == 1 else names, "status": "verified" if len(names) == 1 else "conflicting", "basis": "station name as published in CWC/NWIC telemetry; no independent crosswalk"},
            "river": river,
            "basin": basin,
            "latitude": {"value": lat, "status": coord_status},
            "longitude": {"value": lon, "status": coord_status},
            "coordinates_source": "CWC/NWIC telemetry CSV",
            "source": "Central Water Commission via NWIC",
            "source_url": TELEMETRY_SOURCE_URL,
            "metadata_verification_status": "conflicting" if conflicts or coord_status == "conflicting" or river["status"] == "conflicting" else "partial",
            "units": {"value": "m", "status": "verified", "basis": "meter in telemetry column header"},
            "reference_datum": {"value": None, "status": "unknown", "note": "Raw zero-gauge and mean-sea-level fields are available per observation but do not verify datum compatibility."},
            "first_observation": min(timestamps).isoformat() if timestamps else None,
            "last_observation": max(timestamps).isoformat() if timestamps else None,
            "timestamp_timezone": "unknown (source timestamps are naive)",
            "coverage": coverage,
            "snapshot_availability": {
                "status": "present_in_2026_2030_snapshot" if "current_2026_2030" in coverage else "historical_snapshot_only",
                "current_snapshot_record_count": coverage.get("current_2026_2030", {}).get("record_count", 0),
                "note": "Describes the preserved local files only; the live CWC query is performed separately per selected station.",
            },
            "known_gaps": {"intervals_over_2h": gaps, "missing_level_rows": missing_levels, "invalid_timestamp_rows": invalid_times, "note": "Gap count is computed within each preserved file from its parsed timestamps; source timezone is undocumented."},
            "threshold": MUSIRI_THRESHOLD.copy() if station_id == "musiri" else {"value": None, "unit": None, "status": "not_available", "value_verification_status": "not_available", "applicability_status": "not_available", "provenance": None},
            "hydrological_relationship": {"status": connection, "basis": "CWC telemetry basin field" if connection.startswith("same_basin") else "available source metadata", "note": connection_note, "flow_order": "not_available"},
            "known_conflicts": conflicts,
            "unresolved_fields": ["official CWC site code", "instrument identity crosswalk", "timezone", "measurement datum", "validated river-network connection", "operational thresholds"],
            "observation_count": len(rows),
        }
    return result


def list_stations():
    stations = [dict(station) for station in sorted(_registry().values(), key=lambda item: item["official_name"]["value"].casefold())]
    validate_station_registry(stations)
    return stations


def get_station(station_id):
    if not isinstance(station_id, str):
        return None
    station = _registry().get(station_id.casefold())
    return dict(station) if station else None


def source_name_for_station(station_id):
    station = get_station(station_id)
    if not station:
        return None
    return station.get("source_station_identifier")


def validate_station_registry(stations):
    """Validate unique route IDs and retain explicit metadata conflicts."""
    seen = set()
    for station in stations:
        station_id = station.get("station_id") if isinstance(station, dict) else None
        if not isinstance(station_id, str) or not station_id:
            raise ValueError("Every registry station must have a non-empty station_id")
        if station_id in seen:
            raise ValueError(f"Duplicate station identifier: {station_id}")
        seen.add(station_id)
        for key in ("latitude", "longitude"):
            coordinate = station.get(key)
            if not isinstance(coordinate, dict) or coordinate.get("status") not in {"verified", "conflicting", "unknown", "not_available"}:
                raise ValueError(f"Station {station_id} has an invalid {key} verification state")
    return True


def reset_station_registry_for_tests():
    _registry.cache_clear()
