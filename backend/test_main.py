import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

import main


class FloodSenseApiTests(unittest.TestCase):
    def test_health_endpoint_is_backward_compatible_and_provider_health_is_request_driven(self):
        self.assertEqual(main.health(), {"status": "healthy", "service": "FloodSense API"})
        snapshot = main.provider_health_endpoint()
        self.assertEqual(snapshot["collection_mode"], "request_driven")
        self.assertEqual({row["key"] for row in snapshot["providers"]}, {
            "open_meteo_rainfall", "open_elevation", "ndma_sachet", "cwc_cauvery_gauges", "cwc_musiri_gauge"
        })
        self.assertIn("station_providers", snapshot)

    def test_station_registry_and_details_expose_metadata_without_claiming_official_codes(self):
        result = main.stations_endpoint()
        self.assertEqual(result["count"], 27)
        self.assertIn("MUSIRI", {item["official_name"]["value"] for item in result["stations"]})
        musiri = main.station_detail_endpoint("musiri")
        self.assertEqual(musiri["station_id_verification_status"], "unknown")
        self.assertEqual(musiri["threshold"]["applicability_status"], "unknown")
        with self.assertRaises(HTTPException) as raised:
            main.station_detail_endpoint("not-a-station")
        self.assertEqual(raised.exception.status_code, 404)

    def test_station_observation_endpoint_tracks_health_and_station_identity(self):
        payload = {"status": "available", "availability": "current_resource_recorded",
                   "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                   "freshness": {"status": "stale"},
                   "latest_observation": {"value": 81.2, "unit": "m", "observed_at": "10-10-2026 10:00", "quality_status": "valid_numeric_value"},
                   "observations": [{"value": 81.2, "unit": "m", "observed_at": "10-10-2026 10:00", "quality_status": "valid_numeric_value"}]}
        with patch("main.get_station_observations", return_value=payload) as fetch:
            result = main.station_observations_endpoint("musiri", 1)
        fetch.assert_called_once_with("MUSIRI", limit=1)
        self.assertEqual(result["station_id"], "musiri")
        self.assertEqual(result["observations"][0]["station_id"], "musiri")
        health = {item["key"]: item for item in main.provider_health_endpoint()["station_providers"]}
        self.assertEqual(health["cwc_station:musiri"]["observation_freshness"], "stale")

    def test_musiri_compatibility_endpoint_still_returns_guarded_assessment(self):
        gauge = {"status": "available", "retrieved_at_utc": "2026-10-11T12:00:00+00:00",
                 "station": {"name": "MUSIRI", "identity_verified": False},
                 "observation": {"value": 81.2, "unit": "m", "observed_at": "11-10-2026 11:00", "quality_status": "valid_numeric_value"},
                 "freshness": {"status": "unknown_timezone"}}
        with patch("main.get_musiri_gauge", return_value=gauge):
            result = main.musiri_station_status()
        self.assertEqual(result["risk_assessment"], "not_assessed")
        self.assertEqual(result["registry_station_id"], "musiri")
        self.assertEqual(result["station_id_verification_status"], "unknown")
        self.assertEqual(result["station"]["name"], "MUSIRI")
        self.assertNotEqual(result["threshold_monitoring"]["monitoring_status"], "below_threshold")

    def test_monitor_updates_provider_health_and_keeps_partial_success(self):
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0).isoformat()
        rainfall = {"source": "Open-Meteo", "retrieved_at_utc": now,
                    "current": {"time_utc": now, "rain_mm": 0}, "hourly": [],
                    "current_units": {"precipitation": "mm", "rain": "mm"},
                    "hourly_units": {"precipitation": "mm", "rain": "mm"},
                    "units": {"rain": "mm"}, "quality_status": "valid"}
        with patch("main.get_rainfall", return_value=rainfall), patch(
            "main.get_elevation", side_effect=TimeoutError("private stack detail")
        ), patch("main.get_official_alerts", return_value={"status": "available", "retrieved_at_utc": "2026-10-11T12:00:00+00:00", "alerts": []}), patch(
            "main.get_river_gauges", return_value={"status": "available", "retrieved_at_utc": "2026-10-11T12:00:00+00:00", "stations": []}
        ):
            result = main.collect_monitoring_data(10.79, 78.70)
        self.assertEqual(result["status"], "partial")
        health = {item["key"]: item for item in main.provider_health_endpoint()["providers"]}
        self.assertEqual(health["open_meteo_rainfall"]["status"], "healthy")
        self.assertEqual(health["open_elevation"]["status"], "unavailable")
        self.assertEqual(health["open_elevation"]["last_error_category"], "timeout")
        self.assertNotIn("private stack detail", str(health["open_elevation"]))

    def test_historical_event_validation_summary_endpoint_reads_report_without_provider_calls(self):
        payload = {"report_type": "historical_event_station_linkage_audit", "risk_status": "not_assessed"}
        with patch("main.Path.read_text", return_value=__import__("json").dumps(payload)):
            result = main.historical_event_validation_endpoint()
        self.assertEqual(result, payload)
        self.assertEqual(result["risk_status"], "not_assessed")

    def _available_official_providers(self):
        return (
            patch("main.get_official_alerts", return_value={"status": "available", "alerts": []}),
            patch("main.get_river_gauges", return_value={"status": "available", "stations": []}),
        )

    def test_monitor_returns_structured_provider_data(self):
        rainfall = {
            "source": "Open-Meteo",
            "retrieved_at_utc": "2026-10-11T10:00:00+00:00",
            "location": {"latitude": 10.79, "longitude": 78.70},
            "current": {"time_utc": "2026-10-11T10:00", "rain_mm": 0.1},
            "hourly": [{"time_utc": "2026-10-11T10:00", "rain_mm": 0.1}],
            "units": {"rain": "mm", "time": "UTC"},
        }
        elevation = {
            "source": "Open-Elevation",
            "retrieved_at_utc": "2026-10-11T10:00:00+00:00",
            "location": {"latitude": 10.805, "longitude": 78.72},
            "elevation_m": 75.06,
        }
        alert_patch, gauge_patch = self._available_official_providers()
        with patch("main.get_rainfall", return_value=rainfall), patch(
            "main.get_elevation", return_value=elevation
        ), alert_patch, gauge_patch:
            result = main.monitor_location(main.CoordinatesRequest(latitude=10.805, longitude=78.72))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["requested_coordinates"], {"latitude": 10.805, "longitude": 78.72})
        self.assertEqual(result["rainfall"], rainfall)
        self.assertEqual(result["elevation"], elevation)

    def test_monitor_reports_partial_provider_failure(self):
        alert_patch, gauge_patch = self._available_official_providers()
        with patch("main.get_rainfall", side_effect=RuntimeError("weather offline")), patch(
            "main.get_elevation", return_value={"source": "Open-Elevation", "elevation_m": 75.06}
        ), alert_patch, gauge_patch:
            result = main.monitor_location(main.CoordinatesRequest(latitude=10.805, longitude=78.72))

        self.assertEqual(result["status"], "partial")
        self.assertIn("weather offline", result["rainfall"]["error"])
        self.assertEqual(result["elevation"]["elevation_m"], 75.06)

    def test_monitor_reports_total_provider_failure(self):
        with patch("main.get_rainfall", side_effect=RuntimeError("weather offline")), patch(
            "main.get_elevation", side_effect=RuntimeError("elevation offline")
        ), patch("main.get_official_alerts", side_effect=RuntimeError("alerts offline")), patch(
            "main.get_river_gauges", side_effect=RuntimeError("gauges offline")
        ):
            result = main.monitor_location(main.CoordinatesRequest(latitude=10.805, longitude=78.72))

        self.assertEqual(result["status"], "failed")
        self.assertIn("weather offline", result["rainfall"]["error"])
        self.assertIn("elevation offline", result["elevation"]["error"])

    def test_validation_readiness_endpoint_reports_sources_without_validation_claim(self):
        alert_patch, gauge_patch = self._available_official_providers()
        with patch("main.get_rainfall", return_value={"status": "available"}), patch(
            "main.get_elevation", return_value={"status": "available"}
        ), alert_patch, gauge_patch:
            result = main.validation_readiness_endpoint(
                main.CoordinatesRequest(latitude=10.805, longitude=78.72)
            )
        self.assertEqual(result["validation_status"], "not_ready")
        self.assertFalse(result["evaluation_possible"])
        self.assertFalse(result["real_historical_validation_completed"])
        self.assertEqual(result["historical_sources"][0]["status"], "cataloged_downloadable_not_ingested")
        self.assertEqual(result["provider_status"], "success")

    def test_assess_risk_endpoint_preserves_coordinates_and_withholds_category(self):
        rainfall = {
            "source": "Open-Meteo",
            "retrieved_at_utc": "2026-10-11T12:00:00+00:00",
            "location": {"latitude": 10.79, "longitude": 78.70},
            "current": {"time_utc": "2026-10-11T12:00", "rain_mm": 0.1},
            "hourly": [],
            "units": {"rain": "mm", "time": "UTC"},
        }
        elevation = {
            "source": "Open-Elevation",
            "retrieved_at_utc": "2026-10-11T12:00:00+00:00",
            "location": {"latitude": 10.805, "longitude": 78.72},
            "elevation_m": 75.06,
            "units": {"elevation": "metres"},
        }
        alert_patch, gauge_patch = self._available_official_providers()
        with patch("main.get_rainfall", return_value=rainfall), patch(
            "main.get_elevation", return_value=elevation
        ), alert_patch, gauge_patch:
            response = main.assess_risk_endpoint(
                main.CoordinatesRequest(latitude=10.805, longitude=78.72)
            )

        self.assertEqual(response["data_status"], "success")
        self.assertEqual(response["assessment"]["status"], "not_assessed")
        self.assertIsNone(response["assessment"]["risk_category"])
        self.assertEqual(response["assessment"]["location"]["requested_latitude"], 10.805)
        self.assertEqual(
            response["assessment"]["location"]["rainfall_provider_coordinates"],
            rainfall["location"],
        )

    def test_assess_risk_endpoint_preserves_partial_provider_errors(self):
        alert_patch, gauge_patch = self._available_official_providers()
        with patch("main.get_rainfall", side_effect=RuntimeError("weather offline")), patch(
            "main.get_elevation", return_value={"source": "Open-Elevation", "elevation_m": 75.06}
        ), alert_patch, gauge_patch:
            response = main.assess_risk_endpoint(
                main.CoordinatesRequest(latitude=10.805, longitude=78.72)
            )

        self.assertEqual(response["data_status"], "partial")
        self.assertEqual(response["rainfall"]["error"], "RuntimeError: weather offline")
        self.assertEqual(response["assessment"]["status"], "not_assessed")

    def test_request_models_reject_missing_and_invalid_values(self):
        with self.assertRaises(ValidationError):
            main.CoordinatesRequest(latitude=10.805)
        with self.assertRaises(ValidationError):
            main.CoordinatesRequest(latitude=91, longitude=78.72)
        with self.assertRaises(ValidationError):
            main.AskRequest(question="Hello", latitude=10.805, longitude=181)

    def test_ask_keeps_conversational_endpoint_functional(self):
        with patch("main.ask_floodsense", return_value="Retrieved data.") as ask:
            response = main.ask_floodsense_endpoint(
                main.AskRequest(question="Hello", latitude=10.805, longitude=78.72)
            )

        self.assertEqual(response.answer, "Retrieved data.")
        self.assertIn("Latitude: 10.805", ask.call_args.args[0])
        with self.assertRaises(HTTPException) as error:
            main.ask_floodsense_endpoint(
                main.AskRequest(question="   ", latitude=10.805, longitude=78.72)
            )
        self.assertEqual(error.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
