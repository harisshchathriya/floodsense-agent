import unittest
from datetime import datetime, timezone

from provider_health import get_provider_health, record_provider_result, reset_provider_health_for_tests


class ProviderHealthTests(unittest.TestCase):
    def setUp(self):
        reset_provider_health_for_tests()

    def tearDown(self):
        reset_provider_health_for_tests()

    def find(self, key):
        return next(item for item in get_provider_health()["providers"] if item["key"] == key)

    def test_unqueried_providers_are_unknown_and_health_is_request_driven(self):
        report = get_provider_health()
        self.assertEqual(report["collection_mode"], "request_driven")
        self.assertTrue(all(row["status"] == "unknown" for row in report["providers"]))
        self.assertIn("no scheduled", report["message"])

    def test_failure_recovery_is_reported_without_exception_details(self):
        start = datetime(2026, 10, 11, 10, tzinfo=timezone.utc)
        record_provider_result("open_meteo_rainfall", start,
                               error=TimeoutError("secret token=do-not-return"), now=start)
        failed = self.find("open_meteo_rainfall")
        self.assertEqual(failed["status"], "unavailable")
        self.assertEqual(failed["last_error_category"], "timeout")
        self.assertNotIn("secret", str(failed))
        record_provider_result("open_meteo_rainfall", datetime(2026, 10, 11, 11, tzinfo=timezone.utc),
            result={"retrieved_at_utc": "2026-10-11T11:00:02+00:00", "current": {"time_utc": "2026-10-11T10:00:00+00:00"}, "quality_status": "valid"},
            now=datetime(2026, 10, 11, 11, tzinfo=timezone.utc))
        recovered = self.find("open_meteo_rainfall")
        self.assertEqual(recovered["status"], "healthy")
        self.assertTrue(recovered["recovered_since_failure"])
        self.assertIsNone(recovered["last_error_category"])

    def test_successful_fetch_with_old_observation_is_stale(self):
        now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
        record_provider_result("open_meteo_rainfall", now,
            result={"retrieved_at_utc": "2026-10-11T12:00:00+00:00", "current": {"time_utc": "2026-10-11T08:00:00+00:00"}}, now=now)
        item = self.find("open_meteo_rainfall")
        self.assertEqual(item["status"], "stale")
        self.assertEqual(item["last_result_status"], "available")
        self.assertEqual(item["observation_freshness"], "stale")

    def test_older_concurrent_attempt_cannot_overwrite_newer_health(self):
        newer = datetime(2026, 10, 11, 11, tzinfo=timezone.utc)
        older = datetime(2026, 10, 11, 10, tzinfo=timezone.utc)
        record_provider_result("open_elevation", newer,
            result={"retrieved_at_utc": "2026-10-11T11:00:01+00:00", "elevation_m": 12}, now=newer)
        applied = record_provider_result("open_elevation", older,
            error=RuntimeError("late old failure"), now=datetime(2026, 10, 11, 12, tzinfo=timezone.utc))
        self.assertFalse(applied)
        item = self.find("open_elevation")
        self.assertEqual(item["status"], "healthy")
        self.assertEqual(item["last_attempted_at"], newer.isoformat())
        self.assertIsNone(item["last_error_category"])


if __name__ == "__main__":
    unittest.main()
