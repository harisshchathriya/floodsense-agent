import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
import river_network as network


class RiverNetworkTests(unittest.TestCase):
    def setUp(self):
        network.reset_network_cache_for_tests()

    def tearDown(self):
        network.reset_network_cache_for_tests()

    @staticmethod
    def station(*, station_id="test", lat=10.0, lon=78.0, river="Cauvery", basin="Cauvery", coordinate_status="verified", conflicts=None):
        return {
            "station_id": station_id,
            "station_id_type": "local_normalized_source_name",
            "station_id_verification_status": "unknown",
            "source_station_identifier": station_id.upper(),
            "source": "fixture",
            "source_url": "https://example.test/stations.csv",
            "latitude": {"value": lat, "status": coordinate_status},
            "longitude": {"value": lon, "status": coordinate_status},
            "coordinates_source": "fixture coordinates",
            "river": {"value": river, "status": "verified" if river else "not_available"},
            "basin": {"value": basin, "status": "verified" if basin else "not_available"},
            "metadata_verification_status": "conflicting" if conflicts else "verified",
            "known_conflicts": conflicts or [],
        }

    @staticmethod
    def reach(name="Cauvery River", reach_id="r1"):
        return {"type": "Feature", "properties": {"reach_id": reach_id, "river_name": name},
                "geometry": {"type": "LineString", "coordinates": [[78.0, 10.0], [78.01, 10.0]]}}

    def test_line_geometry_rejects_bad_and_accepts_wgs84_lines(self):
        self.assertTrue(network.validate_line_geometry(self.reach()["geometry"]))
        self.assertFalse(network.validate_line_geometry({"type": "LineString", "coordinates": [[181, 10], [78, 10]]}))
        self.assertFalse(network.validate_line_geometry({"type": "Point", "coordinates": [78, 10]}))
        self.assertFalse(network.validate_line_geometry({"type": "LineString", "coordinates": [[78, 10]]}))

    def test_source_archives_reproject_and_preserve_raw_hashes(self):
        river_hash = network._sha256(network.RIVER_ARCHIVE)
        basin_hash = network._sha256(network.BASIN_ARCHIVE)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            manifest = network.build_network_files(
                river_output=out / "river.geojson", basin_output=out / "basin.geojson",
                manifest_output=out / "manifest.json")
            river = json.loads((out / "river.geojson").read_text(encoding="utf-8"))
            basin = json.loads((out / "basin.geojson").read_text(encoding="utf-8"))
        self.assertEqual(manifest["output_crs"], "EPSG:4326 (WGS 84 longitude/latitude)")
        self.assertEqual(manifest["river_geometry_format"], "ESRI Shapefile polyline, extracted from the original ZIP without modifying source archive")
        self.assertEqual(river["type"], "FeatureCollection")
        self.assertTrue(all(network.validate_line_geometry(item["geometry"]) for item in river["features"]))
        self.assertTrue(all(network._valid_polygon_geometry(item["geometry"]) for item in basin["features"]))
        self.assertEqual((river_hash, basin_hash), (network._sha256(network.RIVER_ARCHIVE), network._sha256(network.BASIN_ARCHIVE)))

    def test_station_name_basin_and_spatial_evidence_verify_candidate_reach(self):
        result = network.classify_station_reach_match(self.station(), [self.reach()],
            {"type": "Polygon", "coordinates": [[[77.9, 9.9], [78.1, 9.9], [78.1, 10.1], [77.9, 10.1], [77.9, 9.9]]]})
        self.assertEqual(result["match_status"], "verified_match")
        self.assertEqual(result["candidate_reach"]["reach_id"], "r1")

    def test_proximity_without_river_name_corroboration_is_candidate(self):
        result = network.classify_station_reach_match(self.station(river="Bhavani"), [self.reach()],
            {"type": "Polygon", "coordinates": [[[77.9, 9.9], [78.1, 9.9], [78.1, 10.1], [77.9, 10.1], [77.9, 9.9]]]})
        self.assertEqual(result["match_status"], "candidate_match")

    def test_conflicting_coordinates_or_metadata_are_not_overridden(self):
        result = network.classify_station_reach_match(self.station(coordinate_status="conflicting"), [self.reach()],
            {"type": "Polygon", "coordinates": [[[77.9, 9.9], [78.1, 9.9], [78.1, 10.1], [77.9, 10.1], [77.9, 9.9]]]})
        self.assertEqual(result["match_status"], "conflicting_metadata")
        self.assertEqual(result["original_coordinates"]["longitude"], 78.0)

    def test_missing_or_invalid_processed_files_are_reported_unavailable(self):
        with patch.object(network, "RIVER_GEOJSON", Path("missing-river.json")), patch.object(network, "BASIN_GEOJSON", Path("missing-basin.json")), patch.object(network, "NETWORK_MANIFEST", Path("missing-manifest.json")):
            network.reset_network_cache_for_tests()
            status = network.river_network_status()
            self.assertEqual(status["status"], "insufficient_network_data")
            self.assertEqual(status["river_reach_count"], 0)
            self.assertFalse(status["flow_direction_available"])
        network.reset_network_cache_for_tests()

    def test_same_basin_without_topology_is_not_a_connected_reach(self):
        left = self.station(station_id="left")
        right = self.station(station_id="right", lon=78.02)
        result = network._relationship(left, right, {}, ())
        self.assertEqual(result["connection_status"], "same_basin_no_verified_river_connection")
        self.assertIsNone(result["upstream_station_id"])

    def test_explicit_undirected_and_directed_edges_need_provenance(self):
        left, right = self.station(station_id="left"), self.station(station_id="right")
        matches = {"left": {"match_status": "verified_match", "candidate_reach": {"reach_id": "a"}},
                   "right": {"match_status": "verified_match", "candidate_reach": {"reach_id": "b"}}}
        source = {"name": "authoritative fixture", "url": "https://example.test/topology"}
        undirected = network._explicit_edge_relationship(left, right, matches,
            [{"from_reach_id": "a", "to_reach_id": "b", "direction": "undirected", "source": source}])
        self.assertEqual(undirected["connection_status"], "verified_connected_reaches")
        self.assertEqual(undirected["flow_direction_status"], "unknown")
        directed = network._explicit_edge_relationship(left, right, matches,
            [{"from_reach_id": "a", "to_reach_id": "b", "direction": "upstream_to_downstream", "source": source}])
        self.assertEqual(directed["upstream_station_id"], "left")
        self.assertEqual(directed["downstream_station_id"], "right")
        self.assertIsNone(network._explicit_edge_relationship(left, right, matches,
            [{"from_reach_id": "a", "to_reach_id": "b", "direction": "undirected", "source": {}}]))

    def test_api_endpoints_expose_network_and_keep_station_contract(self):
        client = TestClient(main.app)
        status = client.get("/river-network/status")
        stations = client.get("/river-network/stations")
        relationships = client.get("/river-network/relationships", params={"station_id": "musiri"})
        legacy = client.get("/stations")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(stations.status_code, 200)
        self.assertEqual(relationships.status_code, 200)
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(stations.json()["count"], 27)
        self.assertIn("river_network_match", legacy.json()["stations"][0])
        self.assertEqual(client.get("/river-network/relationships", params={"station_id": "not-a-station"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()

