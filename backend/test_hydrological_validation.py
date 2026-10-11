import unittest
from types import SimpleNamespace
from unittest.mock import mock_open, patch

from hydrological_validation import (
    assess_gauge_connection,
    confusion_metrics,
    coverage_summary,
    event_detection_lead_times,
    match_official_event_periods,
    prepare_historical_records,
    temporal_split,
    validation_readiness,
)
from prepare_historical import prepare_csv


class HydrologicalValidationTests(unittest.TestCase):
    def test_connection_statuses_follow_available_evidence(self):
        candidate = {"station_location": {"latitude": 10.1, "longitude": 78.1},
                     "basin": "Cauvery", "distance_from_selected_point_km": 5}
        self.assertEqual(assess_gauge_connection(candidate)["status"], "proximity_only")
        self.assertEqual(assess_gauge_connection(candidate, selected_basin="Cauvery")["status"],
                         "same_basin_unverified_connection")
        evidence = {"connected": True, "verified_by": "official river network", "source": "official",
                    "flow_direction_known": True, "station_placement_known": True}
        self.assertEqual(assess_gauge_connection(candidate, selected_basin="Cauvery",
                                                  network_evidence=evidence)["status"], "verified_connection")
        self.assertEqual(assess_gauge_connection({})["status"], "insufficient_geographic_data")
        self.assertEqual(assess_gauge_connection(candidate, selected_basin="Unknown",
                                                  network_evidence=evidence)["status"], "proximity_only")

    def test_records_normalize_time_preserve_missing_units_and_find_quality_issues(self):
        records = [
            {"source_id": "G1", "observed_at": "2026-01-01T00:00:00+05:30", "value": 2.0, "unit": "m"},
            {"source_id": "G1", "observed_at": "2025-12-31T18:30:00Z", "value": 2.0, "unit": "m"},
            {"source_id": "G1", "observed_at": "2026-01-01T02:00:00Z", "value": None, "unit": None},
            {"source_id": "G1", "observed_at": "bad", "value": 1, "unit": "m"},
            {"source_id": "G1", "observed_at": "2026-01-01T04:00:00Z", "value": 1, "unit": "ft"},
        ]
        result = prepare_historical_records(records, dataset_kind="gauge", expected_unit="m")
        self.assertEqual(result["record_count"], 2)
        self.assertEqual(result["records"][0]["observed_at_utc"], "2025-12-31T18:30:00+00:00")
        self.assertEqual(result["records"][0]["original_timezone"], "UTC+05:30")
        self.assertIsNone(result["records"][1]["value"])
        kinds = {issue["issue"] for issue in result["issues"]}
        self.assertTrue({"duplicate_record", "missing_or_invalid_timestamp", "unit_mismatch"}.issubset(kinds))
        conflict = prepare_historical_records([
            {"source_id": "G", "observed_at": "2026-01-01T00:00Z", "value": 1, "unit": "m"},
            {"source_id": "G", "observed_at": "2026-01-01T00:00Z", "value": 2, "unit": "m"},
        ], dataset_kind="gauge")
        self.assertIn("conflicting_duplicate_record", {item["issue"] for item in conflict["issues"]})

    def test_naive_time_is_preserved_as_unknown_and_temporal_gaps_are_reported(self):
        result = prepare_historical_records([
            {"source_id": "G", "observed_at": "2026-01-01T00:00", "value": 1, "unit": "m"},
            {"source_id": "G", "observed_at": "2026-01-01T00:00Z", "value": 1, "unit": "m"},
            {"source_id": "G", "observed_at": "2026-01-01T03:00Z", "value": 2, "unit": "m"},
        ], dataset_kind="gauge")
        self.assertIsNone(result["records"][0]["observed_at_utc"])
        self.assertEqual(result["coverage"]["timezone_unknown_count"], 1)
        self.assertEqual(result["temporal_gaps"][0]["gap_hours"], 3)

    def test_event_labels_require_explicit_boolean(self):
        result = prepare_historical_records([
            {"source_id": "E", "observed_at": "2026-01-01T00:00Z", "value": 1, "unit": "event", "flood_event": "yes"},
            {"source_id": "E", "observed_at": "2026-01-02T00:00Z", "value": None, "unit": None, "flood_event": False},
        ], dataset_kind="event")
        self.assertEqual(result["record_count"], 1)
        self.assertIn("invalid_or_missing_event_label", {item["issue"] for item in result["issues"]})

    def test_csv_ingestion_requires_and_records_explicit_provenance_and_verified_unit(self):
        with patch("builtins.open", mock_open(read_data="station,time,level\nG1,2026-01-01T00:00:00Z,2.5\n")), \
             patch("prepare_historical.Path.stat", return_value=SimpleNamespace(st_size=58)), \
             patch("prepare_historical.Path.read_bytes", return_value=b"fixture csv"):
            result = prepare_csv("history.csv", dataset_kind="gauge", source_name="CWC test fixture",
                                 source_url="https://example.invalid/dataset", dataset_period="fixture",
                                 id_column="station", timestamp_column="time", value_column="level",
                                 unit_column=None, declared_unit="m", expected_unit="m")
        self.assertEqual(result["records"][0]["unit"], "m")
        self.assertEqual(result["provenance"]["field_mapping"]["declared_unit"], "m")
        self.assertEqual(result["provenance"]["source_name"], "CWC test fixture")

    def test_event_group_cannot_cross_temporal_split_boundary(self):
        rows = [
            {"event_id": "A", "observed_at": "2020-01-01T00:00:00+00:00"},
            {"event_id": "B", "observed_at": "2020-01-03T00:00:00+00:00"},
            {"event_id": "C", "observed_at": "2020-01-05T00:00:00+00:00"},
        ]
        split = temporal_split(rows, train_end="2020-01-02T00:00:00Z", validation_end="2020-01-04T00:00:00Z")
        self.assertEqual([len(split[key]) for key in ("train", "validation", "test")], [1, 1, 1])
        with self.assertRaisesRegex(ValueError, "crosses temporal split"):
            temporal_split([rows[0], {**rows[0], "observed_at": "2020-01-03T00:00:00Z"}],
                           train_end="2020-01-02T00:00:00Z", validation_end="2020-01-04T00:00:00Z")
        with self.assertRaisesRegex(ValueError, "overlaps a temporal split boundary"):
            temporal_split([{**rows[0], "event_id": "A", "window_start_utc": "2020-01-01T23:00:00Z",
                             "window_end_utc": "2020-01-02T01:00:00Z"}],
                           train_end="2020-01-02T00:00:00Z", validation_end="2020-01-04T00:00:00Z")

    def test_confusion_metrics_use_negative_units_for_false_alarm_rate(self):
        result = confusion_metrics([True, True, False, False], [True, False, True, False])
        self.assertEqual((result["true_positive"], result["false_positive"],
                          result["true_negative"], result["false_negative"]), (1, 1, 1, 1))
        self.assertEqual(result["precision"], 0.5)
        self.assertEqual(result["recall"], 0.5)
        self.assertEqual(result["f1"], 0.5)
        self.assertEqual(result["false_alarm_rate"], 0.5)
        self.assertIsNone(confusion_metrics([True], [True])["false_alarm_rate"])

    def test_coverage_missingness_and_event_level_detection_lead(self):
        coverage = coverage_summary(4, [1.0, None, 3.0])
        self.assertEqual(coverage["coverage_fraction"], 0.5)
        self.assertEqual(coverage["missingness_fraction"], 0.5)
        self.assertFalse(coverage["missing_values_imputed"])
        result = event_detection_lead_times(
            [{"event_id": "F1", "onset_at": "2026-01-01T06:00:00Z"}],
            [{"event_id": "F1", "detected_at": "2026-01-01T04:00:00Z"}],
        )
        self.assertEqual(result[0]["lead_time_hours"], 2)
        self.assertTrue(result[0]["detected_before_onset"])

    def test_official_period_label_matching_requires_station_and_time_basis(self):
        observations = [
            {"source_id": "MUSIRI", "observed_at": "2024-08-01T00:00:00Z", "time_basis": "UTC", "timezone_verified": True},
            {"source_id": "OTHER", "observed_at": "2024-08-01T00:00:00Z", "time_basis": "UTC", "timezone_verified": True},
        ]
        event = {"event_id": "CWC-2024-MUSIRI-WARNING", "station": "Musiri",
                 "start": "2024-07-31T19:00:00Z", "end": "2024-08-07T02:00:00Z",
                 "time_basis": "UTC", "timezone_verified": True,
                 "definition": "Official CWC period above station warning level",
                 "source_url": "https://cwc.gov.in/example-report.pdf"}
        result = match_official_event_periods(observations, [event])
        self.assertEqual(len(result["linked_positive_observations"]), 1)
        self.assertEqual(result["linked_positive_observations"][0]["label"], True)
        self.assertEqual(result["negative_labels_created"], 0)
        missing = match_official_event_periods(observations, [{**event, "station": "No Such Station"}])
        self.assertEqual(missing["excluded_periods"][0]["reason"], "no_exact_station_match")
        timezone_unknown = match_official_event_periods(
            [{**observations[0], "time_basis": "unknown", "timezone_verified": False}], [event])
        self.assertEqual(timezone_unknown["excluded_periods"][0]["reason"], "time_basis_not_verified")

    def test_invalid_station_coordinates_are_excluded(self):
        result = prepare_historical_records([{
            "source_id": "G", "observed_at": "2026-01-01T00:00Z", "value": 1,
            "unit": "m", "latitude": 95, "longitude": 78,
        }], dataset_kind="gauge")
        self.assertEqual(result["record_count"], 0)
        self.assertIn("invalid_station_metadata", {item["issue"] for item in result["issues"]})

    def test_readiness_does_not_promote_nearest_station_or_create_category(self):
        report = validation_readiness({"river_gauges": {"status": "available", "stations": [
            {"station": "Near", "distance_from_selected_point_km": 1.2,
             "station_location": {"latitude": 10, "longitude": 78}, "freshness_status": "stale"}]}})
        self.assertFalse(report["evaluation_possible"])
        self.assertFalse(report["real_historical_validation_completed"])
        self.assertEqual(report["geography"]["gauge_connection_status"][0]["connection"]["status"], "proximity_only")
        self.assertEqual(report["geography"]["gauge_connection_status"][0]["freshness_status"], "stale")


if __name__ == "__main__":
    unittest.main()
