import unittest
from unittest.mock import mock_open, patch

from event_linkage_audit import (
    RIVER_NETWORK_URL,
    assess_event_station_pair,
    backtesting_readiness,
    build_event_linkage_audit,
    summarize_event_window_observations,
)
from hydrological_validation import temporal_split


def event(**updates):
    value = {
        "event_id": "CWC-2024-MUSIRI-WARNING",
        "station": "Musiri",
        "station_code": "CW1CAL000207",
        "latitude": 10.943,
        "longitude": 78.435,
        "river": "Cauvery",
        "basin": "Cauvery",
        "start": "2024-07-31T19:00:00+00:00",
        "end": "2024-08-07T02:00:00+00:00",
        "time_basis": "UTC",
        "timezone_verified": True,
        "threshold_type": "warning",
        "threshold_value": 82.12,
        "threshold_unit": "m",
        "threshold_station": "Musiri",
        "threshold_validity_year": 2024,
        "source_claims_period_above_threshold": True,
        "source_url": "https://cwc.gov.in/example-event.pdf",
        "location_source_url": "https://cwc.gov.in/example-stations.pdf",
    }
    return {**value, **updates}


def station(**updates):
    value = {
        "station": "Musiri",
        "site_code": "CW1CAL000207",
        "latitude": 10.943,
        "longitude": 78.435,
        "river": "Cauvery",
        "basin": "Cauvery",
        "record_time_start": "2021-01-01T00:00:00",
        "record_time_end": "2025-12-31T23:00:00",
        "metadata_conflict": False,
    }
    return {**value, **updates}


class EventLinkageAuditTests(unittest.TestCase):
    def test_verified_event_station_link_requires_all_evidence(self):
        pair = assess_event_station_pair(
            event(), station(), observation_window={"count": 4, "timezone_verified": True},
            network_evidence={"connected": True, "source_url": "https://cwc.gov.in/network.geojson",
                              "flow_direction_known": True, "station_placement_known": True,
                              "event_station_path_verified": True},
        )
        self.assertEqual(pair["match_status"], "verified")
        self.assertTrue(pair["temporal_evidence"]["exact_temporal_match_verified"])
        self.assertEqual(pair["label"]["status"], "documented_station_warning_threshold_exceeded")
        self.assertEqual(pair["exclusion_reasons"], [])

    def test_same_basin_is_unverified_without_network_path(self):
        pair = assess_event_station_pair(event(), station(), observation_window={"count": 1})
        self.assertEqual(pair["geographic_evidence"]["status"], "same_basin_unverified_connection")
        self.assertEqual(pair["match_status"], "unresolved")
        self.assertIn("authoritative_flow_direction_and_station_path_not_verified", pair["exclusion_reasons"])

    def test_coordinates_alone_are_proximity_only(self):
        pair = assess_event_station_pair(event(), station(basin="Unknown"), observation_window={"count": 1})
        self.assertEqual(pair["geographic_evidence"]["status"], "geographic_proximity_only")
        self.assertIsNotNone(pair["geographic_evidence"]["straight_line_distance_km"])
        self.assertEqual(pair["match_status"], "unresolved")

    def test_event_or_station_without_coordinates_is_insufficient(self):
        pair = assess_event_station_pair(event(latitude=None, longitude=None), station(), observation_window={"count": 1})
        self.assertEqual(pair["geographic_evidence"]["status"], "insufficient_geographic_data")
        self.assertIn("insufficient_geographic_metadata", pair["exclusion_reasons"])

    def test_temporal_mismatch_is_excluded_with_reason(self):
        pair = assess_event_station_pair(event(), station(), observation_window={"count": 0})
        self.assertEqual(pair["match_status"], "unresolved")
        self.assertEqual(pair["temporal_evidence"]["status"], "temporal_mismatch_no_records_in_reported_interval")
        self.assertIn("no_observations_in_reported_event_interval", pair["exclusion_reasons"])

    def test_unverified_warning_threshold_does_not_create_valid_label(self):
        pair = assess_event_station_pair(event(threshold_unit=None), station(), observation_window={"count": 1})
        self.assertEqual(pair["label"]["status"], "event_label_unresolved")
        self.assertIn("threshold_source_unit_station_or_validity_not_verified", pair["exclusion_reasons"])
        self.assertNotEqual(pair["match_status"], "verified")

    def test_unmatched_event_station_pairs_remain_in_report(self):
        stations = [station(station=name) for name in ("AKKIHEBBAL", "BHAVANI BRIDGE")]
        windows = {(event()["event_id"], name): {"count": 2} for name in ("AKKIHEBBAL", "BHAVANI BRIDGE")}
        report = build_event_linkage_audit([event()], stations, observation_windows=windows,
                                           station_dataset_source="CWC CSV")
        self.assertEqual(report["candidate_event_station_pairs"], 2)
        self.assertEqual(report["verified_links"], 0)
        self.assertEqual(report["unresolved_links"], 2)
        self.assertEqual(len(report["pairs"]), 2)
        self.assertEqual(report["pairs"][0]["station_source"], "CWC CSV")

    def test_duplicate_event_identifiers_are_reported_and_not_linked(self):
        report = build_event_linkage_audit([event(), event()], [station()], observation_windows={
            ("CWC-2024-MUSIRI-WARNING", "Musiri"): {"count": 1, "timezone_verified": True}
        })
        self.assertEqual(report["duplicate_event_identifiers"], ["CWC-2024-MUSIRI-WARNING"])
        self.assertEqual(report["candidate_event_station_pairs"], 2)
        self.assertTrue(all(pair["match_status"] == "excluded" for pair in report["pairs"]))
        self.assertTrue(all("duplicate_event_identifier" in pair["exclusion_reasons"] for pair in report["pairs"]))

    def test_missing_station_metadata_is_preserved_as_insufficient_evidence(self):
        pair = assess_event_station_pair(event(), station(latitude=None, longitude=None, basin=None, river=None),
                                         observation_window={"count": 1})
        self.assertEqual(pair["match_status"], "unresolved")
        self.assertEqual(pair["geographic_evidence"]["status"], "insufficient_geographic_data")

    def test_exclusion_reasons_cover_identity_timezone_and_network_gaps(self):
        pair = assess_event_station_pair(event(timezone_verified=False), station(station="Gauge A"),
                                         observation_window={"count": 5})
        self.assertEqual(pair["match_status"], "unresolved")
        self.assertIn("no_exact_station_identifier_match", pair["exclusion_reasons"])
        self.assertIn("event_and_telemetry_timezone_basis_not_verified", pair["exclusion_reasons"])
        self.assertIn("authoritative_flow_direction_and_station_path_not_verified", pair["exclusion_reasons"])
        self.assertEqual(pair["hydrological_relationship"]["network_source"], RIVER_NETWORK_URL)

    def test_backtesting_refuses_unlinked_labels_and_missing_negative_events(self):
        pair = assess_event_station_pair(event(), station(station="Different station"),
                                         observation_window={"count": 1})
        result = backtesting_readiness([pair], [event()], negative_label_count=0,
                                       prediction_rule_configured=False)
        self.assertFalse(result["valid"])
        self.assertIsNone(result["metrics"])
        self.assertEqual(result["predictive_evaluation"], "not_run")
        self.assertTrue(any("No verified event-to-station" in item for item in result["missing_evidence"]))
        self.assertTrue(any("negative event labels" in item for item in result["missing_evidence"]))

    def test_calendar_counts_preserve_naive_time_without_claiming_alignment(self):
        with patch("builtins.open", mock_open(read_data=(
                "Station,Data Acquisition Time\nMusiri,01-08-2024 00:00\nMusiri,09-08-2024 00:00\n"))):
            result = summarize_event_window_observations("history.csv", [
                event(start="2024-08-01T00:00", end="2024-08-02T00:00")])
        self.assertEqual(result[("CWC-2024-MUSIRI-WARNING", "Musiri")]["count"], 1)
        # The function reports counts only; the pair builder never implies a verified clock basis.
        pair = assess_event_station_pair(event(start="2024-08-01T00:00", end="2024-08-02T00:00"), station(),
                                         observation_window=result[("CWC-2024-MUSIRI-WARNING", "Musiri")])
        self.assertFalse(pair["temporal_evidence"]["exact_temporal_match_verified"])

    def test_aware_event_and_observation_timestamps_compare_as_instants(self):
        with patch("builtins.open", mock_open(read_data=(
                "Station,Data Acquisition Time\nGauge,2024-08-01T00:30:00Z\n"))):
            result = summarize_event_window_observations("history.csv", [event(
                station="Gauge", start="2024-08-01T02:00:00+02:00", end="2024-08-01T03:00:00+02:00")])
        window = result[("CWC-2024-MUSIRI-WARNING", "Gauge")]
        self.assertEqual(window["count"], 1)
        self.assertTrue(window["timezone_verified"])

    def test_event_windows_must_not_leak_across_chronological_splits(self):
        records = [{"event_id": "E1", "observed_at": "2024-01-01T00:00:00Z",
                    "window_start_utc": "2024-01-01T23:00:00Z", "window_end_utc": "2024-01-02T01:00:00Z"}]
        with self.assertRaisesRegex(ValueError, "overlaps a temporal split boundary"):
            temporal_split(records, train_end="2024-01-02T00:00:00Z", validation_end="2024-02-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
