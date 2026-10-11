import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from assessment_readiness import build_assessment_readiness


NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def station(**updates):
    value = {
        "station_id": "test-gauge", "station_id_type": "local_normalized_source_name",
        "source_station_identifier": "TEST GAUGE", "station_id_verification_status": "unknown",
        "official_name": {"value": "TEST GAUGE"}, "source": "CWC/NWIC",
        "source_url": "https://example.test/stations", "latitude": {"value": 10.0, "status": "verified"},
        "longitude": {"value": 78.0, "status": "verified"},
        "reference_datum": {"value": None, "status": "unknown"}, "known_conflicts": [],
        "threshold": {"value": None, "unit": None, "applicability_status": "not_available"},
    }
    value.update(updates)
    return value


def observation(*, observed=NOW - timedelta(minutes=20), value=81.2, quality="valid_numeric_value", unit="m", source_name="TEST GAUGE", timezone_known=True):
    return {"status": "available", "availability": "current_resource_recorded", "source": "CWC/NWIC", "source_url": "https://example.test/telemetry", "retrieved_at_utc": NOW.isoformat(),
        "freshness": {"status": "fresh"}, "latest_observation": None if observed is None else {
            "source_station_identifier": source_name, "observed_at": observed.isoformat(),
            "observed_at_utc": observed.isoformat() if timezone_known else None, "value": value,
            "raw_value": str(value), "unit": unit,
            "unit_status": "verified_from_telemetry_column_header" if unit == "m" else "unknown",
            "quality_status": quality, "record_type": "provider_observation"}}


def monitoring(*, alerts=None, rainfall=None, elevation=None):
    return {"rainfall": rainfall or {"source": "Open-Meteo", "retrieved_at_utc": NOW.isoformat(), "current": {"time_utc": NOW.isoformat(), "rain_mm": 0}, "units": {"rain": "mm"}},
        "elevation": elevation or {"source": "Open-Elevation", "retrieved_at_utc": NOW.isoformat(), "elevation_m": 74.0},
        "official_alerts": alerts or {"status": "available", "active_matched_count": 0, "alerts": [], "source": "NDMA SACHET"}}


class AssessmentReadinessTests(unittest.TestCase):
    def build(self, s=None, obs=None, mon=None, match=None, relationships=None):
        return build_assessment_readiness(s or station(), obs or observation(), mon or monitoring(),
            match or {"match_status": "verified_match"}, relationships or {"relationships": [], "topology_available": False}, generated_at=NOW).model_dump()

    def test_fresh_source_matched_observation_is_summary_eligible_only(self):
        result = self.build()
        self.assertTrue(result["eligibility"]["eligible_for_observation_summary"]["eligible"])
        self.assertFalse(result["eligibility"]["eligible_for_threshold_comparison"]["eligible"])
        self.assertEqual(result["status"], "not_assessed")

    def test_stale_observation_withholds_summary(self):
        result = self.build(obs=observation(observed=NOW - timedelta(hours=5)))
        self.assertEqual(result["latest_observation"]["freshness_status"], "stale")
        self.assertFalse(result["eligibility"]["eligible_for_observation_summary"]["eligible"])

    def test_missing_or_unzoned_timestamp_is_unknown(self):
        self.assertEqual(self.build(obs=observation(observed=None))["latest_observation"], None)
        result = self.build(obs=observation(timezone_known=False))
        self.assertEqual(result["latest_observation"]["freshness_status"], "unknown")

    def test_invalid_or_nonfinite_observation_is_not_summary_eligible(self):
        for obs in (observation(value=None, quality="missing_value"), observation(value="NaN", quality="invalid_numeric_value")):
            self.assertFalse(self.build(obs=obs)["eligibility"]["eligible_for_observation_summary"]["eligible"])

    def test_unknown_units_and_datum_withhold_threshold_comparison(self):
        s = station(reference_datum={"value": None, "status": "unknown"}, threshold={"value": 82, "unit": "m", "applicability_status": "verified", "value_verification_status": "verified", "decision_eligible": True, "datum_verified": True, "station_identity_verified": True})
        result = self.build(s=s, obs=observation(unit="unknown"))
        self.assertFalse(result["eligibility"]["eligible_for_observation_summary"]["eligible"])
        self.assertFalse(result["eligibility"]["eligible_for_threshold_comparison"]["eligible"])
        self.assertIn("measurement_datum", [item["input"] for item in result["missing_inputs"]])

    def test_unverified_station_identity_is_explicit(self):
        result = self.build()
        self.assertEqual(result["selected_station"]["station_identity"]["official_station_code"], "unknown")
        self.assertTrue(result["eligibility"]["eligible_for_threshold_comparison"]["reasons"])

    def test_fully_verified_threshold_gates_can_pass_independently(self):
        s = station(station_id_verification_status="verified", reference_datum={"value": "local datum", "status": "verified"},
            threshold={"value": 82.0, "unit": "m", "applicability_status": "verified", "value_verification_status": "verified", "decision_eligible": True, "datum_verified": True, "station_identity_verified": True, "source": "official threshold", "source_url": "https://example.test/threshold"})
        result = self.build(s=s)
        self.assertTrue(result["eligibility"]["eligible_for_threshold_comparison"]["eligible"])
        self.assertFalse(result["eligibility"]["eligible_for_location_specific_assessment"]["eligible"])
        self.assertEqual(result["status"], "not_assessed")

    def test_conflicting_coordinates_and_station_records_are_carried_through(self):
        s = station(latitude={"value": 10.0, "status": "conflicting"}, known_conflicts=[{"field": "coordinates", "status": "conflicting"}])
        result = self.build(s=s)
        self.assertEqual(result["conflicts"][0]["field"], "coordinates")
        self.assertFalse(result["eligibility"]["eligible_for_location_specific_assessment"]["eligible"])

    def test_absent_network_topology_is_not_upstream_downstream(self):
        result = self.build(match={"match_status": "candidate_match"}, relationships={"relationships": [], "topology_available": False})
        self.assertEqual(result["river_network_evidence"]["verified_station_connections"], [])
        self.assertFalse(result["eligibility"]["eligible_for_location_specific_assessment"]["eligible"])

    def test_confirmed_alert_is_reported_with_missing_telemetry(self):
        mon = monitoring(alerts={"status": "available", "active_matched_count": 1, "alerts": [{"validity_status": "active", "source": "NDMA SACHET", "effective_utc": NOW.isoformat()}]})
        result = self.build(obs={"status": "unavailable", "freshness": {"status": "unknown"}}, mon=mon)
        self.assertEqual(result["official_alert_evidence"]["status"], "confirmed_official_alert")
        self.assertFalse(result["eligibility"]["eligible_for_observation_summary"]["eligible"])
        self.assertEqual(result["status"], "not_assessed")

    def test_missing_providers_and_no_alert_are_unknown_not_all_clear(self):
        result = self.build(mon={"rainfall": {"error": "offline"}, "elevation": {"error": "offline"}, "official_alerts": {"status": "unavailable", "error": "offline"}})
        self.assertEqual(result["official_alert_evidence"]["status"], "unknown")
        self.assertIn("not proof", result["official_alert_evidence"]["message"])

    def test_api_schema_and_unknown_station_backward_compatibility(self):
        client = TestClient(main.app)
        with patch("main.get_station_observations", return_value=observation()), patch("main.collect_monitoring_data", return_value=monitoring()):
            response = client.get("/assessment/readiness", params={"station_id": "musiri"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "not_assessed")
        self.assertIn("eligible_for_location_specific_assessment", payload["eligibility"])
        self.assertTrue(payload["provenance"])
        self.assertEqual(client.get("/assessment/readiness", params={"station_id": "unknown"}).status_code, 404)
        self.assertEqual(client.get("/stations").status_code, 200)

    def test_api_reports_location_matched_official_alert_with_unavailable_telemetry(self):
        s = station(station_id="api-test")
        obs = {"status": "unavailable", "freshness": {"status": "unknown"}, "latest_observation": None}
        mon = monitoring(alerts={"status": "available", "active_matched_count": 1, "alerts": [{"validity_status": "active", "source": "NDMA SACHET"}]})
        with patch("main.get_station", return_value=s), patch("main.get_station_observations", return_value=obs), patch("main.collect_monitoring_data", return_value=mon), patch("main.river_network_relationships", return_value={"relationships": [], "topology_available": False}), patch("main.station_reach_matches", return_value=[{"station_id": "api-test", "match_status": "candidate_match"}]):
            response = TestClient(main.app).get("/assessment/readiness", params={"station_id": "api-test"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["official_alert_evidence"]["status"], "confirmed_official_alert")
        self.assertEqual(response.json()["status"], "not_assessed")

    def test_musiri_historical_threshold_remains_ineligible(self):
        s = station(station_id="musiri", station_id_verification_status="unknown", threshold={"value": 82.12, "unit": "m", "applicability_status": "unknown", "value_verification_status": "verified", "decision_eligible": False})
        result = self.build(s=s, obs=observation(value=81.047))
        self.assertFalse(result["eligibility"]["eligible_for_threshold_comparison"]["eligible"])
        self.assertEqual(result["threshold_evidence"]["value"], 82.12)
        self.assertEqual(result["status"], "not_assessed")


if __name__ == "__main__":
    unittest.main()
