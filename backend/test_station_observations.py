import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from tools import official_sources


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


class Response:
    status_code = 200
    headers = {}

    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class Client:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, *args, **kwargs):
        return self.response


def csv_rows(rows):
    header = "Station,Agency,State,District,River,Basin,Latitude,Longitude,Data Acquisition Time,River Water Level Telemetry Hourly (meter),RL_of_zeroGauge,MeanSeaLevel\n"
    return header + "".join(row + "\n" for row in rows)


class StationObservationTests(unittest.TestCase):
    def test_exact_station_filter_never_returns_other_station_rows(self):
        body = csv_rows([
            "UPPER ANICUT,CWC,Tamil Nadu,Tiruchirappalli,Cauvery,Cauvery,10.8,78.5,11-10-2026 10:00,80,0,0",
        ])
        with patch("tools.official_sources.httpx.Client", return_value=Client(Response(body))):
            result = official_sources.get_station_observations("MUSIRI", now=NOW)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["observations"], [])
        self.assertIsNone(result["latest_observation"])

    def test_newest_missing_value_does_not_fall_back_to_older_numeric_value(self):
        body = csv_rows([
            "MUSIRI,CWC,Tamil Nadu,Tiruchirappalli,Cauvery,Cauvery,10.9,78.4,11-10-2026 09:00,81,0,0",
            "MUSIRI,CWC,Tamil Nadu,Tiruchirappalli,Cauvery,Cauvery,10.9,78.4,11-10-2026 10:00,,0,0",
        ])
        with patch("tools.official_sources.httpx.Client", return_value=Client(Response(body))):
            result = official_sources.get_station_observations("MUSIRI", now=NOW)
        self.assertEqual(result["status"], "available")
        self.assertIsNone(result["latest_observation"]["value"])
        self.assertEqual(result["latest_observation"]["quality_status"], "missing_value")
        self.assertEqual(result["observations"][1]["value"], 81.0)

    def test_timezone_unknown_and_stale_are_separate(self):
        body = csv_rows(["MUSIRI,CWC,Tamil Nadu,Tiruchirappalli,Cauvery,Cauvery,10.9,78.4,11-10-2026 10:00,81,0,0"])
        with patch("tools.official_sources.httpx.Client", return_value=Client(Response(body))):
            result = official_sources.get_station_observations("MUSIRI", now=NOW)
        self.assertEqual(result["freshness"]["status"], "unknown_timezone")
        self.assertIsNone(result["latest_observation"]["observed_at_utc"])

    def test_malformed_timestamps_are_reported_without_relabeling_data(self):
        body = csv_rows([
            "MUSIRI,CWC,Tamil Nadu,Tiruchirappalli,Cauvery,Cauvery,10.9,78.4,not-a-time,81,0,0",
            "MUSIRI,CWC,Tamil Nadu,Tiruchirappalli,Cauvery,Cauvery,10.9,78.4,11-10-2026 10:00,82,0,0",
        ])
        with patch("tools.official_sources.httpx.Client", return_value=Client(Response(body))):
            result = official_sources.get_station_observations("MUSIRI", now=NOW)
        self.assertEqual(result["invalid_timestamp_count"], 1)
        self.assertEqual(len(result["observations"]), 2)
        self.assertEqual(result["observations"][1]["quality_status"], "invalid_timestamp")

    def test_trailing_unparseable_row_does_not_promote_an_older_level(self):
        body = csv_rows([
            "MUSIRI,CWC,Tamil Nadu,Tiruchirappalli,Cauvery,Cauvery,10.9,78.4,11-10-2026 10:00,82,0,0",
            "MUSIRI,CWC,Tamil Nadu,Tiruchirappalli,Cauvery,Cauvery,10.9,78.4,broken timestamp,81,0,0",
        ])
        with patch("tools.official_sources.httpx.Client", return_value=Client(Response(body))):
            result = official_sources.get_station_observations("MUSIRI", now=NOW)
        self.assertEqual(result["freshness"]["status"], "unknown")
        self.assertIsNone(result["latest_observation"]["value"])
        self.assertEqual(result["latest_observation"]["quality_status"], "invalid_timestamp_latest_order_unknown")
        self.assertEqual(result["observations"][1]["value"], 82.0)

    def test_historical_only_station_uses_explicitly_historical_records(self):
        live = csv_rows(["MUSIRI,CWC,Tamil Nadu,Tiruchirappalli,Cauvery,Cauvery,10.9,78.4,11-10-2026 10:00,82,0,0"])
        history = csv_rows(["BILIGUNDULU,CWC,Tamil Nadu,Dharmapuri,Cauvery,Cauvery,12.18,77.72,31-12-2022 23:00,78,0,0"])
        with patch("tools.official_sources.httpx.Client", return_value=Client(Response(live))), patch.object(
            official_sources.Path, "read_text", return_value=history
        ):
            result = official_sources.get_station_observations("BILIGUNDULU", now=NOW)
        self.assertEqual(result["availability"], "historical_only")
        self.assertEqual(result["latest_observation"]["record_type"], "historical_snapshot")
        self.assertEqual(result["freshness"]["status"], "stale")

    def test_unknown_level_unit_header_is_rejected(self):
        body = "Station,Data Acquisition Time,River Water Level Telemetry Hourly (feet)\nMUSIRI,11-10-2026 10:00,81\n"
        with patch("tools.official_sources.httpx.Client", return_value=Client(Response(body))):
            result = official_sources.get_station_observations("MUSIRI", now=NOW)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["availability"], "provider_error")
        self.assertEqual(result["observations"], [])


if __name__ == "__main__":
    unittest.main()
