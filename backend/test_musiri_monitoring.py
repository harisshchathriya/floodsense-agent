import unittest
from datetime import datetime, timezone

from musiri_monitoring import (
    MUSIRI_WARNING_THRESHOLD_REFERENCE,
    evaluate_musiri_threshold,
    replay_musiri_observations,
)


class MusiriThresholdTests(unittest.TestCase):
    NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)

    def setUp(self):
        self.verified = {**MUSIRI_WARNING_THRESHOLD_REFERENCE, "threshold_verification_status": "verified",
                         "decision_eligible": True, "station_identity_verified": True, "datum_verified": True}
        self.station = {"name": "Musiri", "identity_verified": True}

    def evaluate(self, value, *, timestamp="2026-10-11T11:00:00+00:00", unit="m", station=None, threshold=None):
        return evaluate_musiri_threshold(station or self.station,
            {"value": value, "unit": unit, "observed_at_utc": timestamp}, threshold or self.verified, now=self.NOW)

    def test_below_equal_and_above_are_consistent(self):
        self.assertEqual(self.evaluate(82.11)["monitoring_status"], "below_documented_threshold")
        self.assertEqual(self.evaluate(82.12)["monitoring_status"], "below_documented_threshold")
        self.assertEqual(self.evaluate(82.1201)["monitoring_status"], "threshold_exceeded")

    def test_unverified_threshold_blocks_any_decision(self):
        result = evaluate_musiri_threshold(self.station, {"value": 100, "unit": "m", "observed_at_utc": "2026-10-11T11:00:00+00:00"}, MUSIRI_WARNING_THRESHOLD_REFERENCE, now=self.NOW)
        self.assertEqual(result["monitoring_status"], "threshold_not_verified")

    def test_stale_missing_invalid_wrong_station_and_units_never_mean_below(self):
        self.assertEqual(self.evaluate(80, timestamp="2026-10-11T01:00:00+00:00")["monitoring_status"], "stale_data")
        self.assertEqual(self.evaluate(None)["monitoring_status"], "unknown")
        self.assertEqual(self.evaluate(float("nan"))["monitoring_status"], "invalid_data")
        self.assertEqual(self.evaluate(80, unit="ft")["monitoring_status"], "invalid_data")
        self.assertEqual(self.evaluate(80, station={"name": "Other gauge", "identity_verified": True})["monitoring_status"], "invalid_data")

    def test_missing_and_naive_timestamp_are_not_treated_as_fresh(self):
        self.assertEqual(self.evaluate(80, timestamp=None)["monitoring_status"], "invalid_data")
        self.assertEqual(self.evaluate(80, timestamp="2026-10-11T11:00:00")["monitoring_status"], "invalid_data")

    def test_replay_counts_transitions_and_gaps(self):
        rows = [
            {"observed_at": "2026-01-01 00:00", "value": 81.0},
            {"observed_at": "2026-01-01 01:00", "value": 83.0},
            {"observed_at": "2026-01-01 02:00", "value": 84.0},
            {"observed_at": "2026-01-01 04:00", "value": 81.0},
        ]
        result = replay_musiri_observations(rows, self.verified)
        self.assertEqual(result["status"], "replayed")
        self.assertEqual(len(result["crossings"]), 1)
        self.assertEqual(len(result["returns_below"]), 1)
        self.assertEqual(result["gap_count"], 1)
        blocked = replay_musiri_observations(rows, MUSIRI_WARNING_THRESHOLD_REFERENCE)
        self.assertEqual(blocked["status"], "threshold_not_verified")
        self.assertIsNone(blocked["crossings"])


if __name__ == "__main__":
    unittest.main()
