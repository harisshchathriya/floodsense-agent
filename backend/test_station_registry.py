import unittest

from station_registry import classify_hydrological_relationship, list_stations, normalize_station_id, validate_station_registry


class StationRegistryTests(unittest.TestCase):
    def test_registry_is_built_from_actual_files_and_ids_are_unique(self):
        stations = list_stations()
        self.assertEqual(len(stations), 27)
        self.assertEqual(len({item["station_id"] for item in stations}), 27)
        self.assertEqual(sum(item["snapshot_availability"]["status"] == "present_in_2026_2030_snapshot" for item in stations), 24)
        self.assertEqual(sum(item["snapshot_availability"]["status"] == "historical_snapshot_only" for item in stations), 3)
        self.assertTrue(validate_station_registry(stations))

    def test_duplicate_station_identifiers_are_rejected(self):
        entry = {"station_id": "same", "latitude": {"status": "unknown"}, "longitude": {"status": "not_available"}}
        with self.assertRaisesRegex(ValueError, "Duplicate station identifier"):
            validate_station_registry([entry, dict(entry)])

    def test_station_key_is_normalized_but_not_claimed_as_official_identifier(self):
        self.assertEqual(normalize_station_id("PALLIPALAYAM/Erode"), "pallipalayam-erode")
        musiri = next(item for item in list_stations() if item["station_id"] == "musiri")
        self.assertEqual(musiri["station_id_verification_status"], "unknown")
        self.assertEqual(musiri["latitude"]["status"], "conflicting")
        self.assertEqual(musiri["threshold"]["applicability_status"], "unknown")
        self.assertEqual(musiri["hydrological_relationship"]["flow_order"], "not_available")

    def test_km_vadi_river_label_conflict_is_preserved(self):
        station = next(item for item in list_stations() if item["station_id"] == "k-m-vadi")
        self.assertEqual(station["river"]["status"], "conflicting")
        self.assertIn("river", [item["field"] for item in station["known_conflicts"]])

    def test_missing_metadata_states_are_explicit_and_accepted(self):
        entry = {"station_id": "unknown", "latitude": {"status": "not_available"}, "longitude": {"status": "unknown"}}
        self.assertTrue(validate_station_registry([entry]))

    def test_hydrological_relationship_evidence_levels_are_distinct(self):
        self.assertEqual(classify_hydrological_relationship(verified_network_connection=True), "verified_river_network_connection")
        self.assertEqual(classify_hydrological_relationship(basin_status="verified", basin="Cauvery", coordinates_available=True), "same_basin_no_verified_river_connection")
        self.assertEqual(classify_hydrological_relationship(basin_status="unknown", coordinates_available=True), "proximity_only")
        self.assertEqual(classify_hydrological_relationship(basin_status="unknown", coordinates_available=False), "insufficient_geographic_data")


if __name__ == "__main__":
    unittest.main()
