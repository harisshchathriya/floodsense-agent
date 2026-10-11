import unittest
from datetime import datetime, timedelta, timezone

from risk_assessment import assess_flood_risk, calculate_rainfall_features


AS_OF = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


def rainfall_sample(values, *, cutoff=AS_OF, forecast=()):
    records = [
        {
            "time_utc": (cutoff - timedelta(hours=len(values) - index - 1)).isoformat(),
            "rain_mm": value,
            "precipitation_mm": value,
        }
        for index, value in enumerate(values)
    ]
    records.extend({"time_utc": time.isoformat(), "rain_mm": value} for time, value in forecast)
    return {
        "source": "Open-Meteo",
        "retrieved_at_utc": AS_OF.isoformat(),
        "location": {"latitude": 10.79, "longitude": 78.70},
        "current": {"time_utc": cutoff.isoformat(), "rain_mm": values[-1] if values else None},
        "hourly": records,
        "units": {"rain": "mm", "time": "UTC"},
    }


class RainfallFeatureTests(unittest.TestCase):
    def test_complete_windows_sum_observed_rain_and_exclude_forecast(self):
        future = AS_OF + timedelta(hours=1)
        features = calculate_rainfall_features(
            rainfall_sample([0.5] * 24, forecast=[(future, 9)]), assessed_at=AS_OF
        )

        self.assertEqual(features["windows"]["1h"]["accumulation"], 0.5)
        self.assertEqual(features["windows"]["3h"]["accumulation"], 1.5)
        self.assertEqual(features["windows"]["24h"]["accumulation"], 12.0)
        self.assertEqual(features["forecast_record_count"], 1)
        self.assertEqual(features["unit"], "mm")

    def test_incomplete_window_keeps_partial_sum_separate(self):
        features = calculate_rainfall_features(rainfall_sample([1] * 23), assessed_at=AS_OF)

        self.assertFalse(features["windows"]["24h"]["complete"])
        self.assertIsNone(features["windows"]["24h"]["accumulation"])
        self.assertEqual(features["windows"]["24h"]["partial_accumulation"], 23.0)
        self.assertEqual(features["windows"]["24h"]["valid_observations"], 23)

    def test_invalid_values_are_not_coerced_to_zero(self):
        rainfall = rainfall_sample([1, 2, 3])
        rainfall["hourly"][0]["rain_mm"] = float("nan")
        rainfall["hourly"][1]["rain_mm"] = -1
        rainfall["hourly"][2]["rain_mm"] = None

        features = calculate_rainfall_features(rainfall, assessed_at=AS_OF)

        self.assertEqual(features["invalid_numeric_record_count"], 3)
        self.assertEqual(features["observed_record_count"], 0)
        self.assertIsNone(features["windows"]["1h"]["accumulation"])

    def test_duplicate_timestamps_are_deduplicated_and_conflicts_are_excluded(self):
        rainfall = rainfall_sample([1, 2, 3])
        rainfall["hourly"].append(dict(rainfall["hourly"][1]))
        rainfall["hourly"].append({**rainfall["hourly"][2], "rain_mm": 4})

        features = calculate_rainfall_features(rainfall, assessed_at=AS_OF)

        self.assertEqual(features["duplicate_record_count"], 2)
        self.assertEqual(features["ambiguous_duplicate_timestamp_count"], 1)
        self.assertEqual(features["observed_record_count"], 2)

    def test_out_of_order_records_are_sorted_for_trend(self):
        rainfall = rainfall_sample([0.1, 0.1, 0.1, 0.5, 0.5, 0.5])
        rainfall["hourly"].reverse()

        features = calculate_rainfall_features(rainfall, assessed_at=AS_OF)

        self.assertEqual(features["trend"]["status"], "available")
        self.assertEqual(features["trend"]["direction"], "increasing")
        self.assertAlmostEqual(features["trend"]["change_mm_per_hour"], 0.4)

    def test_stale_and_missing_timestamps_are_reported(self):
        old_cutoff = AS_OF - timedelta(hours=5)
        features = calculate_rainfall_features(
            rainfall_sample([1], cutoff=old_cutoff), assessed_at=AS_OF
        )
        missing = calculate_rainfall_features({"hourly": []}, assessed_at=AS_OF)

        self.assertTrue(features["is_stale"])
        self.assertTrue(any("more than 3 hours old" in item for item in features["missing_data"]))
        self.assertIsNone(missing["observation_cutoff_utc"])
        self.assertTrue(any("timestamp is missing" in item for item in missing["missing_data"]))

    def test_naive_current_timestamp_uses_provider_utc_contract(self):
        rainfall = rainfall_sample([0.25])
        rainfall["current"]["time_utc"] = AS_OF.strftime("%Y-%m-%dT%H:%M:%S")

        features = calculate_rainfall_features(rainfall, assessed_at=AS_OF)

        self.assertEqual(features["observation_cutoff_utc"], AS_OF.isoformat())
        self.assertEqual(features["windows"]["1h"]["unit"], "mm")

    def test_unsupported_units_do_not_produce_mm_totals(self):
        rainfall = rainfall_sample([1] * 24)
        rainfall["units"]["rain"] = "inch"

        features = calculate_rainfall_features(rainfall, assessed_at=AS_OF)

        self.assertIsNone(features["windows"]["24h"]["accumulation"])
        self.assertIsNone(features["unit"])
        self.assertTrue(any("unsupported" in item for item in features["missing_data"]))


class RiskAssessmentTests(unittest.TestCase):
    def test_assessment_always_withholds_unvalidated_category(self):
        result = assess_flood_risk(
            {
                "requested_coordinates": {"latitude": 10.805, "longitude": 78.72},
                "rainfall": rainfall_sample([0] * 24),
                "elevation": {
                    "source": "Open-Elevation",
                    "elevation_m": 75.06,
                    "location": {"latitude": 10.805, "longitude": 78.72},
                    "units": {"elevation": "metres"},
                },
            },
            assessed_at=AS_OF,
        )

        self.assertEqual(result["status"], "not_assessed")
        self.assertIsNone(result["risk_category"])
        self.assertEqual(result["location"]["requested_latitude"], 10.805)
        self.assertEqual(result["location"]["rainfall_provider_coordinates"], {"latitude": 10.79, "longitude": 78.70})
        self.assertTrue(any(item["input"] == "river_gauge_levels" for item in result["missing_inputs"]))

    def test_provider_failure_and_empty_data_remain_not_assessed(self):
        result = assess_flood_risk(
            {
                "requested_coordinates": {"latitude": 0, "longitude": 0},
                "rainfall": {"error": "weather offline"},
                "elevation": {"error": "elevation offline"},
            },
            assessed_at=AS_OF,
        )

        self.assertEqual(result["status"], "not_assessed")
        self.assertIsNone(result["risk_category"])
        self.assertTrue(any("weather offline" in item["reason"] for item in result["missing_inputs"]))
        self.assertTrue(any("elevation offline" in item["reason"] for item in result["missing_inputs"]))

    def test_official_alert_and_nearest_gauge_remain_distinct_and_unscored(self):
        result = assess_flood_risk(
            {
                "requested_coordinates": {"latitude": 10.5, "longitude": 78.5},
                "rainfall": rainfall_sample([0] * 24),
                "elevation": {"elevation_m": 20, "units": {"elevation": "metres"}},
                "official_alerts": {
                    "status": "available",
                    "alerts": [{
                        "headline": "Flood warning", "severity": "Severe",
                        "description": "Issuer wording", "validity_status": "active",
                        "source": "NDMA SACHET", "match_reason": "CAP polygon",
                    }],
                },
                "river_gauges": {
                    "status": "available",
                    "stations": [{
                        "station": "Gauge A", "observation": {"value": 4.2, "unit": "m", "observed_at": "11-10-2026 11:00"},
                        "is_stale": None, "station_location": {"latitude": 10.7, "longitude": 78.8},
                        "distance_from_selected_point_km": 32.1, "match_reason": "Nearest candidate; relation unverified",
                        "source": "CWC via NWIC",
                    }],
                },
            },
            assessed_at=AS_OF,
        )

        self.assertEqual(result["status"], "not_assessed")
        self.assertIsNone(result["risk_category"])
        alert = next(item for item in result["evidence"] if item["kind"] == "official_alert")
        gauge = next(item for item in result["evidence"] if item["kind"] == "context_only_gauge_candidate")
        self.assertEqual(alert["severity"], "Severe")
        self.assertEqual(gauge["unit"], "m")
        self.assertFalse(gauge["relation_verified"])
        self.assertTrue(any(item["input"] == "river_gauge_location_relationship" for item in result["missing_inputs"]))

    def test_alert_provider_failure_is_not_reported_as_no_alert(self):
        result = assess_flood_risk(
            {"official_alerts": {"status": "unavailable", "error": "feed timeout"},
             "river_gauges": {"status": "unavailable", "error": "CSV timeout"}},
            assessed_at=AS_OF,
        )
        self.assertEqual(result["status"], "not_assessed")
        self.assertTrue(any(item["input"] == "official_flood_alerts" and "timeout" in item["reason"] for item in result["missing_inputs"]))
        self.assertTrue(any(item["input"] == "river_gauge_levels" and "timeout" in item["reason"] for item in result["missing_inputs"]))


if __name__ == "__main__":
    unittest.main()
