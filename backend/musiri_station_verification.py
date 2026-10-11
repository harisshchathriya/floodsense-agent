"""Reproducible field-by-field reconciliation of the Musiri CWC sources."""
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_CSV = ROOT / "data/raw/cwc_cauvery_telemetry_2026_2030.csv"
RAW_MANIFEST = ROOT / "data/raw/cwc_cauvery_telemetry_2026_2030.manifest.json"
OUTPUT = ROOT / "data/processed/musiri_station_verification.json"
INVENTORY_URL = "https://cwc.gov.in/sites/default/files/ho-metadata-book-2025final-publishedsigned.pdf"
APPRAISAL_URL = "https://cwc.gov.in/sites/default/files/appraisal-report-2024-publicationcompressed.pdf"
STAGE_MANUAL_URL = "https://cwc.gov.in/sites/default/files/stage-dischargecompressed.pdf"


def build_report(telemetry, *, feed_sha256=None, retrieved_at_utc=None):
    inventory = {"station": "Musiri", "site_code": "CW1CAL000207", "latitude": 10.943,
                 "longitude": 78.435, "river": "Cauvery", "district": "Tiruchirapalli"}
    telemetry_coordinates = None
    try:
        telemetry_coordinates = {"latitude": float(telemetry["Latitude"]), "longitude": float(telemetry["Longitude"])}
    except (KeyError, TypeError, ValueError):
        pass
    coordinate_delta_km = None
    if telemetry_coordinates:
        from math import asin, cos, radians, sin, sqrt
        lat1, lon1 = radians(inventory["latitude"]), radians(inventory["longitude"])
        lat2, lon2 = radians(telemetry_coordinates["latitude"]), radians(telemetry_coordinates["longitude"])
        coordinate_delta_km = round(6371.0088 * 2 * asin(sqrt(sin((lat2-lat1)/2)**2 + cos(lat1)*cos(lat2)*sin((lon2-lon1)/2)**2)), 3)
    telemetry_name = (telemetry.get("Station") or "").strip()
    telemetry_river = (telemetry.get("River") or "").strip()
    telemetry_district = (telemetry.get("District") or "").strip()
    fields = [
        {"field": "station_name", "status": "verified" if telemetry_name.casefold() == "musiri" and inventory["station"].casefold() == "musiri" else "conflicting",
         "telemetry": telemetry_name or None, "inventory": inventory["station"], "note": "Names match; this alone does not prove the instrument/site-code crosswalk."},
        {"field": "station_identifier", "status": "unknown", "telemetry": None, "inventory": inventory["site_code"],
         "note": "The current telemetry CSV has no CWC site-code column; no code-to-row mapping is asserted."},
        {"field": "coordinates", "status": "conflicting" if coordinate_delta_km is not None and coordinate_delta_km > 0.05 else "unknown",
         "telemetry": telemetry_coordinates, "inventory": {"latitude": inventory["latitude"], "longitude": inventory["longitude"]},
         "separation_km": coordinate_delta_km, "note": "Both source coordinate pairs are preserved; they are not snapped or treated as equivalent."},
        {"field": "river", "status": "verified" if telemetry_river.casefold() == inventory["river"].casefold() else "conflicting",
         "telemetry": telemetry_river or None, "inventory": inventory["river"]},
        {"field": "district", "status": "verified" if telemetry_district.casefold() == inventory["district"].casefold() else "unknown" if telemetry_district.casefold() in {"tiruchirappalli", "tiruchirapalli", "thiruchirapalli"} else "conflicting",
         "telemetry": telemetry_district or None, "inventory": inventory["district"],
         "note": "The source spellings differ; this report does not treat a spelling variant as a verified administrative crosswalk."},
        {"field": "level_unit", "status": "verified", "telemetry": "m (CSV header)", "threshold_source": "m",
         "note": "Matching units do not demonstrate matching vertical reference datums."},
        {"field": "telemetry_datum", "status": "unknown", "telemetry_fields": {"RL_of_zeroGauge": telemetry.get("RL_of_zeroGauge"), "MeanSeaLevel": telemetry.get("MeanSeaLevel")},
         "threshold_datum": None, "note": "The CSV fields do not specify a usable datum/crosswalk for this feed row and warning threshold."},
        {"field": "warning_threshold_value", "status": "verified", "value": 82.12, "unit": "m", "severity": "warning",
         "source_year": 2024, "note": "Verified only as a historical value printed for Musiri in the cited appraisal."},
        {"field": "threshold_station_applicability", "status": "unknown", "note": "Exact feed site-code crosswalk is absent and telemetry datum compatibility is unresolved."},
        {"field": "threshold_current_validity", "status": "unknown", "note": "The cited appraisal does not establish that this threshold remains operational in 2026."},
        {"field": "observation_timezone", "status": "unknown", "telemetry": "not specified in DD-MM-YYYY HH:MM field"},
    ]
    sources = [
        {"name": "CWC/NWDP Cauvery telemetry resource", "url": "https://nwdp.nwic.gov.in/en/dataset/river-water-level-telemetry-hourly-central-water-commission-cwc/resource/d027c5ac-379d-4ac2-8ced-97b02b6edbc0"},
        {"name": "CWC Hydro-Meteorological Observation Stations Book 2025", "url": INVENTORY_URL},
        {"name": "CWC Appraisal Report 2024, Annex XIII", "url": APPRAISAL_URL},
        {"name": "CWC Stage Discharge Manual", "url": STAGE_MANUAL_URL},
    ]
    return {"report_name": "Musiri station identity and level-datum reconciliation", "generated_at_utc": retrieved_at_utc or datetime.now(timezone.utc).isoformat(),
            "overall_status": "threshold_reference_only", "telemetry_source_sha256": feed_sha256,
            "fields": fields, "sources": sources,
            "decision": "Keep 82.12 m informational. Do not evaluate threshold alerts until the exact station identity, threshold validity, and measurement datum are verified."}


def latest_musiri_row(path=RAW_CSV):
    latest = None
    latest_time = None
    with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if (row.get("Station") or "").strip().casefold() != "musiri":
                continue
            try:
                stamp = datetime.strptime(row.get("Data Acquisition Time", ""), "%d-%m-%Y %H:%M")
            except ValueError:
                continue
            if latest_time is None or stamp > latest_time:
                latest, latest_time = row, stamp
    if latest is None:
        raise ValueError("No timestamped MUSIRI row found in the local CWC resource")
    return latest


def main():
    manifest = json.loads(RAW_MANIFEST.read_text(encoding="utf-8"))
    sha256 = hashlib.sha256(RAW_CSV.read_bytes()).hexdigest()
    report = build_report(latest_musiri_row(), feed_sha256=sha256)
    report["source_manifest"] = manifest
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "overall_status": report["overall_status"], "field_statuses": {item["field"]: item["status"] for item in report["fields"]}}, indent=2))


if __name__ == "__main__":
    main()
