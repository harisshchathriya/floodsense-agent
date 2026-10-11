"""Build a reproducible Musiri replay report from the preserved CWC CSV."""
import csv
import hashlib
import json
import math
from pathlib import Path

from musiri_monitoring import MUSIRI_WARNING_THRESHOLD_REFERENCE, replay_musiri_observations

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data/raw/cwc_cauvery_telemetry_2026_2030.csv"
MANIFEST = ROOT / "data/raw/cwc_cauvery_telemetry_2026_2030.manifest.json"
OUTPUT = ROOT / "data/processed/musiri_2026_telemetry_replay.json"


def build_report():
    observations = []
    metadata = None
    with SOURCE.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("Station", "").strip().casefold() != "musiri":
                continue
            metadata = {key: row.get(key) for key in ("Station", "Agency", "State", "District", "River", "Basin", "Latitude", "Longitude", "RL_of_zeroGauge", "MeanSeaLevel")}
            raw = (row.get("River Water Level Telemetry Hourly (meter)") or "").strip()
            try:
                value = float(raw) if raw.casefold() not in {"", "nan", "na", "n/a", "null", "-"} else None
            except ValueError:
                value = raw
            observations.append({"observed_at": row.get("Data Acquisition Time"), "value": value,
                                 "raw_value": raw or None, "unit": "m", "source_station": row.get("Station")})
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    report = replay_musiri_observations(observations, MUSIRI_WARNING_THRESHOLD_REFERENCE)
    numeric = [row["value"] for row in observations if isinstance(row["value"], (int, float)) and not isinstance(row["value"], bool) and math.isfinite(row["value"])]
    report.update({"station_metadata": metadata, "source_manifest": manifest,
                   "computed_raw_sha256": raw_hash,
                   "valid_numeric_count": len(numeric),
                   "observed_level_min_m": min(numeric) if numeric else None,
                   "observed_level_max_m": max(numeric) if numeric else None,
                   "timestamp_note": "Source timestamps have no stated timezone; replay uses them only for relative ordering and gap estimates.",
                   "threshold_note": "Historical 82.12 m warning threshold is reference-only; no crossings or returns are computed because current applicability and datum alignment are unverified."})
    return report


if __name__ == "__main__":
    result = build_report()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "observation_count", "valid_numeric_count", "missing_count", "invalid_count", "observed_level_min_m", "observed_level_max_m", "coverage_start", "coverage_end", "gap_count", "crossings", "returns_below", "computed_raw_sha256")}, indent=2))
