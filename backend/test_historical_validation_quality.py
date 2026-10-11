from datetime import datetime, timezone
import json
import unittest
from unittest.mock import mock_open, patch

from event_linkage_audit import audit_historical_telemetry, assess_event_station_pair, backtesting_readiness


def event(**updates):
    return {"event_id": "E1", "station": "Gauge X", "station_code": "SITE-1",
            "latitude": 10.0, "longitude": 78.0, "river": "Cauvery", "basin": "Cauvery",
            "start": "2024-08-01T00:00:00", "end": "2024-08-01T02:00:00",
            "time_basis": "unknown", "timezone_verified": False,
            "threshold_type": "warning", "threshold_value": 5.0, "threshold_unit": "m",
            "threshold_station": "Gauge X", "threshold_validity_year": 2024,
            "source_claims_period_above_threshold": True,
            "source_url": "https://example.gov/event", "location_source_url": "https://example.gov/stations",
            **updates}


class HistoricalValidationQualityTests(unittest.TestCase):
    def test_timezone_unknown_candidate_is_not_promoted_or_excluded(self):
        pair = assess_event_station_pair(event(), {"station": "Gauge X", "site_code": "SITE-1",
            "latitude": 10, "longitude": 78, "basin": "Cauvery"}, observation_window={"count": 2})
        self.assertEqual(pair["validation_eligibility"]["status"], "candidate")
        self.assertFalse(pair["temporal_evidence"]["exact_temporal_match_verified"])
        self.assertEqual(pair["temporal_evidence"]["status"], "calendar_interval_has_records_timezone_unverified")

    def test_no_calendar_observations_with_unknown_timezone_is_unresolved(self):
        pair = assess_event_station_pair(event(), {"station": "Gauge X"}, observation_window={"count": 0})
        self.assertEqual(pair["match_status"], "unresolved")
        self.assertEqual(pair["validation_eligibility"]["status"], "unresolved")
        self.assertIn("unknown timezones prevent exclusion", " ".join(pair["validation_eligibility"]["reasons"]))

    def test_duplicate_and_conflicting_records_missing_values_and_station_metadata(self):
        rows = [
            "Station,Data Acquisition Time,River Water Level Telemetry Hourly (meter),River,Basin,Latitude,Longitude,RL_of_zeroGauge,MeanSeaLevel",
            "Gauge X,01-08-2024 00:00,2.0,Cauvery,Cauvery,10,78,0,0",
            "Gauge X,01-08-2024 00:00,2.5,Cauvery,Cauvery,10,78,0,0",
            "Gauge X,01-08-2024 01:00,,Cauvery,Cauvery,10,78,0,0",
            "Gauge X,01-08-2024 02:00,broken,Cauvery,Other,11,78,0,0",
            "Gauge X,invalid-time,4,Cauvery,Cauvery,10,78,0,0",
        ]
        with patch("builtins.open", mock_open(read_data="\n".join(rows) + "\n")):
            report = audit_historical_telemetry("history.csv", [event()], now=datetime(2024, 8, 2, tzinfo=timezone.utc))
        station = report["stations"][0]
        self.assertEqual(station["source_row_count"], 5)
        self.assertEqual(station["missing_observation_count"], 1)
        self.assertEqual(station["invalid_observation_count"], 1)
        self.assertEqual(station["duplicate_timestamp_group_count"], 1)
        self.assertEqual(station["conflicting_duplicate_value_group_count"], 1)
        self.assertTrue(station["station_metadata"]["metadata_conflict"])
        self.assertEqual(station["timestamp_timezone_status"], "unknown; source does not state timezone")
        self.assertEqual(station["datum_evidence"]["status"], "unresolved_cross_station_compatibility")

    def test_bad_schema_is_reported_not_silently_accepted(self):
        with patch("builtins.open", mock_open(read_data="name,time,level\nA,2024-01-01,1\n")):
            with self.assertRaisesRegex(ValueError, "missing station, timestamp, or meter-level"):
                audit_historical_telemetry("bad.csv", [])

    def test_insufficient_verified_samples_never_returns_metrics(self):
        report = backtesting_readiness([], [event()], negative_label_count=0, prediction_rule_configured=False)
        self.assertFalse(report["valid"])
        self.assertEqual(report["predictive_evaluation"], "not_run")
        self.assertIsNone(report["metrics"])
        self.assertGreaterEqual(len(report["missing_evidence"]), 4)


if __name__ == "__main__":
    unittest.main()
