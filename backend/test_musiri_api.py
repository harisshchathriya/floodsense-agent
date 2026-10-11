import unittest
from unittest.mock import patch

import main
from musiri_monitoring import MUSIRI_WARNING_THRESHOLD_REFERENCE


class MusiriApiTests(unittest.TestCase):
    def test_status_uses_real_provider_payload_and_keeps_threshold_reference_only(self):
        payload = {"status": "available", "source": "CWC/NWIC", "source_url": "https://example.invalid/feed",
                   "retrieved_at_utc": "2026-10-11T12:00:00+00:00",
                   "station": {"name": "Musiri", "identity_verified": False},
                   "observation": {"value": 81.047, "unit": "m", "observed_at": "03-06-2026 23:00",
                                   "quality_status": "valid_numeric_value"},
                   "freshness": {"status": "stale", "is_stale": True}}
        with patch("main.get_musiri_gauge", return_value=payload):
            result = main.musiri_station_status()
        self.assertEqual(result["observation"]["value"], 81.047)
        self.assertEqual(result["threshold_monitoring"]["monitoring_status"], "stale_data")
        self.assertEqual(result["risk_assessment"], "not_assessed")
        self.assertIn("not a flood forecast", result["notice"])

    def test_provider_exception_returns_structured_unavailable_status(self):
        with patch("main.get_musiri_gauge", side_effect=TimeoutError("feed timeout")):
            result = main.musiri_station_status()
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["observation"])
        self.assertEqual(result["threshold_monitoring"]["monitoring_status"], "unknown")


if __name__ == "__main__":
    unittest.main()
