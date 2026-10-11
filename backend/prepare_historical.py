"""Prepare an explicitly mapped historical CSV as a provenance-bearing JSON dataset.

Input is never fetched automatically. The operator supplies a documented file
and metadata, making the source version and field mapping auditable.
"""
import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from hydrological_validation import _parse_time, match_official_event_periods, prepare_historical_records


def prepare_csv(path, *, dataset_kind, source_name, source_url, dataset_period,
                id_column, timestamp_column, value_column, unit_column,
                event_label_column=None, record_type_column=None, expected_unit=None,
                declared_unit=None, latitude_column=None, longitude_column=None,
                metadata_columns=None):
    records = []
    raw_row_count = 0
    field_missingness = Counter()
    station_metadata = defaultdict(list)
    with open(path, newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        for row in reader:
            raw_row_count += 1
            for field, raw in row.items():
                if raw is None or not raw.strip() or raw.strip() in {"-", "NA", "N/A"}:
                    field_missingness[field] += 1
            raw_value = row.get(value_column) if value_column else None
            normalized_value = raw_value.strip() if isinstance(raw_value, str) else raw_value
            if normalized_value is None or normalized_value.casefold() in {"", "na", "n/a", "null", "none", "--"}:
                value = None
            else:
                try:
                    value = float(normalized_value)
                except (TypeError, ValueError):
                    value = normalized_value
            label = None
            if event_label_column:
                raw_label = (row.get(event_label_column) or "").strip().casefold()
                label = True if raw_label in {"true", "1", "yes", "flood"} else False if raw_label in {"false", "0", "no", "no_flood"} else None
            station_id = (row.get(id_column) or "").strip()
            metadata = {key: (row.get(column) or "").strip() or None
                        for key, column in (metadata_columns or {}).items() if column}
            if station_id:
                signature = {**metadata,
                             "latitude": (row.get(latitude_column) or "").strip() or None if latitude_column else None,
                             "longitude": (row.get(longitude_column) or "").strip() or None if longitude_column else None}
                if any(value is not None for value in signature.values()):
                    station_metadata[station_id].append(signature)
            record = {"source_id": station_id,
                            "observed_at": (row.get(timestamp_column) or "").strip(),
                            "value": value,
                            "unit": ((row.get(unit_column) or "").strip() or None
                                     if unit_column else declared_unit),
                            "record_type": ((row.get(record_type_column) or "").strip().casefold()
                                            if record_type_column else "observation"),
                            **({"flood_event": label} if dataset_kind == "event" else {})}
            if latitude_column:
                record["latitude"] = record["station_latitude"] = (row.get(latitude_column) or "").strip() or None
            if longitude_column:
                record["longitude"] = record["station_longitude"] = (row.get(longitude_column) or "").strip() or None
            record.update(metadata)
            records.append(record)
    prepared = prepare_historical_records(records, dataset_kind=dataset_kind, expected_unit=expected_unit)
    station_counts = Counter(record["source_id"] for record in prepared["records"])
    station_times = defaultdict(list)
    station_missing_measurements = Counter()
    gaps_by_station = Counter(gap["source_id"] for gap in prepared["temporal_gaps"])
    for record in prepared["records"]:
        station_id = record["source_id"]
        station_missing_measurements[station_id] += record["value"] is None
        instant = _parse_time(record["observed_at_original"])
        if instant is None:
            continue
        station_times[station_id].append(instant)
    station_inventory = []
    metadata_conflicts = []
    for station_id, entries in sorted(station_metadata.items()):
        merged = {}
        for key in entries[0]:
            observed = sorted({entry.get(key) for entry in entries if entry.get(key) not in (None, "-")})
            if len(observed) > 1:
                metadata_conflicts.append({"station": station_id, "field": key, "values": observed})
            merged[key] = observed[0] if observed else None
        try:
            latitude = float(merged["latitude"]) if merged.get("latitude") is not None else None
            longitude = float(merged["longitude"]) if merged.get("longitude") is not None else None
        except (ValueError, TypeError):
            latitude = longitude = None
        station_inventory.append({"station": station_id, **merged,
                                  "record_count": station_counts.get(station_id, 0),
                                  "missing_measurement_count": station_missing_measurements.get(station_id, 0),
                                  "record_time_start": min(station_times[station_id]).isoformat() if station_times.get(station_id) else None,
                                  "record_time_end": max(station_times[station_id]).isoformat() if station_times.get(station_id) else None,
                                  "temporal_gap_count": gaps_by_station.get(station_id, 0),
                                  "metadata_conflict": any(item["station"] == station_id for item in metadata_conflicts),
                                  "coordinates_valid": latitude is not None and longitude is not None and
                                      -90 <= latitude <= 90 and -180 <= longitude <= 180})
    prepared["provenance"] = {"source_name": source_name, "source_url": source_url,
                              "dataset_period": dataset_period, "input_file": path,
                              "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                              "file_size_bytes": Path(path).stat().st_size,
                              "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                              "field_mapping": {"id": id_column, "timestamp": timestamp_column,
                                                "value": value_column, "unit": unit_column,
                                                "declared_unit": declared_unit,
                                                "event_label": event_label_column,
                                                "record_type": record_type_column,
                                                "latitude": latitude_column,
                                                "longitude": longitude_column,
                                                "station_metadata": metadata_columns or {}}}
    prepared["source_row_count"] = raw_row_count
    prepared["source_field_missingness"] = dict(field_missingness)
    prepared["stations"] = station_inventory
    prepared["station_metadata_conflicts"] = metadata_conflicts
    prepared["data_quality_status"] = "valid_with_issues" if prepared["issues"] or metadata_conflicts or prepared["temporal_gaps"] else "valid"
    prepared["backtesting"] = {"possible": False, "metrics": None,
                                "reason": "No verified event labels are station-matched to this telemetry dataset."}
    return prepared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file")
    parser.add_argument("--kind", choices=("gauge", "event"), required=True)
    parser.add_argument("--source-name", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--period", required=True, help="Published coverage period/version")
    parser.add_argument("--id-column", required=True)
    parser.add_argument("--timestamp-column", required=True)
    parser.add_argument("--value-column")
    parser.add_argument("--unit-column")
    parser.add_argument("--declared-unit", help="Explicit unit verified from the source metadata when no unit column exists")
    parser.add_argument("--event-label-column")
    parser.add_argument("--record-type-column", help="Column containing observation or forecast")
    parser.add_argument("--latitude-column")
    parser.add_argument("--longitude-column")
    parser.add_argument("--agency-column")
    parser.add_argument("--state-column")
    parser.add_argument("--district-column")
    parser.add_argument("--river-column")
    parser.add_argument("--basin-column")
    parser.add_argument("--zero-gauge-column")
    parser.add_argument("--mean-sea-level-column")
    parser.add_argument("--label-candidates", help="JSON file of source-documented event periods to link by station/time")
    parser.add_argument("--expected-unit")
    parser.add_argument("--output", default="prepared_historical.json")
    args = parser.parse_args()
    if args.kind == "event" and not args.event_label_column:
        parser.error("--event-label-column is required for event datasets")
    source_path = Path(args.csv_file)
    station_metadata_columns = {
        "agency": args.agency_column, "state": args.state_column,
        "district": args.district_column, "river": args.river_column,
        "basin": args.basin_column, "zero_gauge_level": args.zero_gauge_column,
        "mean_sea_level": args.mean_sea_level_column,
    }
    result = prepare_csv(args.csv_file, dataset_kind=args.kind,
                         source_name=args.source_name, source_url=args.source_url,
                         dataset_period=args.period, id_column=args.id_column,
                         timestamp_column=args.timestamp_column, value_column=args.value_column,
                         unit_column=args.unit_column, event_label_column=args.event_label_column,
                         record_type_column=args.record_type_column,
                         expected_unit=args.expected_unit, declared_unit=args.declared_unit,
                         latitude_column=args.latitude_column, longitude_column=args.longitude_column,
                         metadata_columns=station_metadata_columns)
    if args.label_candidates:
        with open(args.label_candidates, encoding="utf-8") as stream:
            candidate_periods = json.load(stream)
        result["label_sources_reviewed"] = candidate_periods
        result["label_linkage"] = match_official_event_periods(result["records"], candidate_periods)
        result["backtesting"] = {
            "possible": False,
            "metrics": None,
            "reason": "Official candidate label periods are not station/time linked to the observation data.",
        }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
    report = {key: value for key, value in result.items() if key != "records"}
    report_path = output_path.with_name(f"{output_path.stem}_report.json")
    with open(report_path, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
    print(json.dumps({"output": str(output_path), "report": str(report_path),
                      "source_rows": result["source_row_count"],
                      "prepared_records": result["record_count"],
                      "stations": len(result["stations"]),
                      "exclusions": result["invalid_or_excluded_count"],
                      "temporal_gaps": len(result["temporal_gaps"]),
                      "backtesting_possible": result["backtesting"]["possible"]}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, csv.Error) as exc:
        print(f"Historical dataset preparation failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
