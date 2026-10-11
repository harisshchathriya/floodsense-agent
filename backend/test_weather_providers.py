import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tools import elevation, rainfall


class Response:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


class Client:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, *args, **kwargs):
        return self.response


class RainfallProviderTests(unittest.TestCase):
    def payload(self):
        current = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        previous = current - timedelta(hours=1)
        return {"latitude": 10.79, "longitude": 78.70,
                "current": {"time": current.isoformat(), "precipitation": 0, "rain": 0},
                "current_units": {"precipitation": "mm", "rain": "mm"},
                "hourly": {"time": [previous.isoformat(), current.isoformat()], "precipitation": [0, 0.2], "rain": [0, 0.1]},
                "hourly_units": {"precipitation": "mm", "rain": "mm"}}

    def fetch(self, payload):
        with patch("tools.rainfall.httpx.Client", return_value=Client(Response(payload))):
            return rainfall.get_rainfall(10.79, 78.70)

    def test_zero_values_are_preserved_as_valid_observations(self):
        result = self.fetch(self.payload())
        self.assertEqual(result["current"]["rain_mm"], 0)
        self.assertEqual(result["hourly"][0]["rain_mm"], 0)
        self.assertEqual(result["quality_status"], "valid")
        self.assertTrue(result["retrieved_at_utc"])

    def test_malformed_numbers_and_times_are_null_with_raw_values_preserved(self):
        payload = self.payload()
        payload["current"].update(time="2026-02-30T11:00", rain=float("nan"))
        payload["hourly"]["rain"][0] = -0.1
        payload["hourly"]["time"][1] = "not-a-time"
        result = self.fetch(payload)
        self.assertIsNone(result["current"]["time_utc"])
        self.assertEqual(result["current"]["raw_rain"], "nan")
        self.assertIsNone(result["current"]["rain_mm"])
        self.assertIsNone(result["hourly"][0]["rain_mm"])
        self.assertIsNone(result["hourly"][1]["time_utc"])
        self.assertEqual(result["quality_status"], "partial")

    def test_malformed_root_fails_cleanly(self):
        with patch("tools.rainfall.httpx.Client", return_value=Client(Response([]))):
            with self.assertRaisesRegex(ValueError, "JSON object"):
                rainfall.get_rainfall(10, 78)

    def test_future_current_timestamp_is_not_accepted_as_an_observation(self):
        payload = self.payload()
        payload["current"]["time"] = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        result = self.fetch(payload)
        self.assertIsNone(result["current"]["time_utc"])
        self.assertIn("invalid_or_missing_timestamp_or_measurement", result["quality_issues"])


class ElevationProviderTests(unittest.TestCase):
    def fetch(self, data):
        with patch("tools.elevation.httpx.Client", return_value=Client(Response(data))):
            return elevation.get_elevation(10.79, 78.70)

    def test_valid_zero_elevation_is_not_treated_as_missing(self):
        result = self.fetch({"results": [{"latitude": 10.79, "longitude": 78.70, "elevation": 0}]})
        self.assertEqual(result["elevation_m"], 0)
        self.assertEqual(result["quality_status"], "valid")

    def test_empty_nonfinite_and_malformed_results_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "no elevation"):
            self.fetch({"results": []})
        with self.assertRaisesRegex(ValueError, "non-finite"):
            self.fetch({"results": [{"latitude": 10, "longitude": 78, "elevation": float("inf")}]})
        with self.assertRaisesRegex(ValueError, "invalid coordinates"):
            self.fetch({"results": [{"latitude": 91, "longitude": 78, "elevation": 1}]})


if __name__ == "__main__":
    unittest.main()
