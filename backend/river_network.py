"""Evidence-qualified Cauvery river geometry and station relationships.

The NWDP River Network is a linear geometry layer, not a routable graph. This
module deliberately keeps line matching separate from station connectivity
and flow direction. Raw source archives remain untouched under ``data/raw``;
the build command writes reprojected GeoJSON and provenance under
``data/processed``.
"""
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

import shapefile
from pyproj import CRS, Transformer

from station_registry import list_stations


BACKEND_DIR = Path(__file__).resolve().parent
RAW_DIR = BACKEND_DIR / "data" / "raw"
PROCESSED_DIR = BACKEND_DIR / "data" / "processed"
RIVER_ARCHIVE = RAW_DIR / "nwdp_cwc_river_network_raw.zip"
BASIN_ARCHIVE = RAW_DIR / "nwdp_cwc_basin_boundaries_raw.zip"
RIVER_GEOJSON = PROCESSED_DIR / "cauvery_river_network.geojson"
BASIN_GEOJSON = PROCESSED_DIR / "cauvery_basin_boundary.geojson"
NETWORK_MANIFEST = PROCESSED_DIR / "cauvery_river_network_manifest.json"

RIVER_DATASET_URL = "https://nwdp.nwic.gov.in/dataset/river-line"
RIVER_GEOJSON_RESOURCE_URL = (
    "https://nwdp.nwic.gov.in/dataset/3209962f-d0ff-45b8-910a-209bf69a0ccf/"
    "resource/6e552705-842d-40a4-92b2-8506bb66df2a/download/river_network.geojson"
)
RIVER_SHP_RESOURCE_URL = (
    "https://nwdp.nwic.gov.in/dataset/3209962f-d0ff-45b8-910a-209bf69a0ccf/"
    "resource/67c49daa-4eec-4747-baff-a7eacdb5bef9/download/river_network_shape.zip"
)
BASIN_DATASET_URL = "https://nwdp.nwic.gov.in/dataset/basin-cwc"
BASIN_RESOURCE_URL = (
    "https://nwdp.nwic.gov.in/dataset/ec216ae7-1beb-4365-8473-d60a7fc4a98c/"
    "resource/a6838033-06cd-4e32-bc46-e47ce5135a06/download/basin_cwc_geojson.zip"
)

VERIFIED_MATCH_TOLERANCE_M = 250.0
CANDIDATE_MATCH_TOLERANCE_M = 2500.0
PROXIMITY_ONLY_TOLERANCE_KM = 25.0

_NETWORK_LIMITATION = (
    "The CWC River Network catalog documents linear river geometry and names, "
    "but does not provide a verified routable topology or flow-direction "
    "contract. No upstream/downstream order is inferred from line vertex order, "
    "reach identifiers, confluence labels, or station proximity."
)


def _finite_coordinate_pair(value):
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        lon, lat = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(lon) or not math.isfinite(lat) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
        return None
    return [lon, lat]


def validate_line_geometry(geometry):
    """Return True only for non-empty, finite GeoJSON line coordinates."""
    if not isinstance(geometry, dict):
        return False
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if kind == "LineString":
        lines = [coordinates]
    elif kind == "MultiLineString":
        lines = coordinates
    else:
        return False
    if not isinstance(lines, (list, tuple)) or not lines:
        return False
    for line in lines:
        if not isinstance(line, (list, tuple)) or len(line) < 2:
            return False
        if any(_finite_coordinate_pair(point) is None for point in line):
            return False
    return True


def _transform_line_geometry(shape, transformer):
    parts = list(shape.parts) + [len(shape.points)]
    lines = []
    for start, end in zip(parts, parts[1:]):
        points = shape.points[start:end]
        if len(points) < 2:
            continue
        xs, ys = transformer.transform(
            [point[0] for point in points], [point[1] for point in points]
        )
        line = []
        for lon, lat in zip(xs, ys):
            coordinate = _finite_coordinate_pair([lon, lat])
            if coordinate is None:
                raise ValueError("River geometry contains an invalid transformed coordinate")
            line.append(coordinate)
        lines.append(line)
    if not lines:
        return None
    geometry = {"type": "LineString", "coordinates": lines[0]} if len(lines) == 1 else {
        "type": "MultiLineString", "coordinates": lines
    }
    if not validate_line_geometry(geometry):
        raise ValueError("River geometry is invalid after coordinate transformation")
    return geometry


def _transform_polygon_coordinates(value, transformer):
    if not isinstance(value, list):
        raise ValueError("Basin geometry coordinates must be arrays")
    if value and isinstance(value[0], (int, float)):
        if len(value) < 2:
            raise ValueError("Basin coordinate has fewer than two ordinates")
        lon, lat = transformer.transform(float(value[0]), float(value[1]))
        point = _finite_coordinate_pair([lon, lat])
        if point is None:
            raise ValueError("Basin geometry contains an invalid transformed coordinate")
        return point
    return [_transform_polygon_coordinates(item, transformer) for item in value]


def _valid_polygon_geometry(geometry):
    if not isinstance(geometry, dict) or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        return False
    coordinates = geometry.get("coordinates")
    polygons = [coordinates] if geometry.get("type") == "Polygon" else coordinates
    if not isinstance(polygons, list) or not polygons:
        return False
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            return False
        for ring in polygon:
            if not isinstance(ring, list) or len(ring) < 4:
                return False
            points = [_finite_coordinate_pair(point) for point in ring]
            if any(point is None for point in points) or points[0] != points[-1]:
                return False
    return True


def point_in_ring(longitude, latitude, ring):
    """Ray-cast a WGS84 point against a closed ring."""
    inside = False
    previous = ring[-1]
    for current in ring:
        x1, y1 = previous[:2]
        x2, y2 = current[:2]
        if (y1 > latitude) != (y2 > latitude):
            crossing_x = (x2 - x1) * (latitude - y1) / (y2 - y1) + x1
            if longitude < crossing_x:
                inside = not inside
        previous = current
    return inside


def point_in_polygon(longitude, latitude, geometry):
    """Test Polygon/MultiPolygon membership, including interior rings."""
    if not _valid_polygon_geometry(geometry):
        return None
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    for polygon in polygons:
        if point_in_ring(longitude, latitude, polygon[0]) and not any(
            point_in_ring(longitude, latitude, hole) for hole in polygon[1:]
        ):
            return True
    return False


def _safe_float(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _normalize_hydronym(value):
    if not isinstance(value, str):
        return ""
    normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    return re.sub(r"\s+river$", "", normalized)


def _iter_lines(geometry):
    if geometry.get("type") == "LineString":
        yield geometry["coordinates"]
    elif geometry.get("type") == "MultiLineString":
        yield from geometry["coordinates"]


def _distance_station_to_line_m(latitude, longitude, geometry):
    """Local tangent-plane distance; used only for nearby candidate ranking."""
    latitude_radians = math.radians(latitude)
    x_scale = 111_320.0 * math.cos(latitude_radians)
    y_scale = 110_574.0
    best = math.inf
    for line in _iter_lines(geometry):
        for first, second in zip(line, line[1:]):
            ax = (first[0] - longitude) * x_scale
            ay = (first[1] - latitude) * y_scale
            bx = (second[0] - longitude) * x_scale
            by = (second[1] - latitude) * y_scale
            dx, dy = bx - ax, by - ay
            denominator = dx * dx + dy * dy
            fraction = 0.0 if denominator == 0 else max(
                0.0, min(1.0, -(ax * dx + ay * dy) / denominator)
            )
            best = min(best, math.hypot(ax + fraction * dx, ay + fraction * dy))
    return best


def classify_station_reach_match(station, reaches, basin_geometry):
    """Match one registry record without turning distance into connectivity."""
    latitude = _safe_float((station.get("latitude") or {}).get("value"))
    longitude = _safe_float((station.get("longitude") or {}).get("value"))
    coordinate_status = (station.get("latitude") or {}).get("status")
    basin = station.get("basin") or {}
    river = station.get("river") or {}
    basin_name = basin.get("value") if isinstance(basin, dict) else None
    river_name = river.get("value") if isinstance(river, dict) else None
    basin_membership = (
        point_in_polygon(longitude, latitude, basin_geometry)
        if latitude is not None and longitude is not None else None
    )
    basin_label_matches = (
        isinstance(basin_name, str) and basin_name.casefold() == "cauvery"
        and basin.get("status") == "verified"
    )

    candidates = []
    if latitude is not None and longitude is not None:
        for feature in reaches:
            properties = feature.get("properties") or {}
            geometry = feature.get("geometry")
            if not validate_line_geometry(geometry):
                continue
            distance = _distance_station_to_line_m(latitude, longitude, geometry)
            candidates.append({
                "reach_id": properties.get("reach_id"),
                "source_river_id": properties.get("source_river_id"),
                "river_name": properties.get("river_name"),
                "sub_basin": properties.get("sub_basin"),
                "basin_name": properties.get("basin_name"),
                "distance_m": round(distance, 1),
                "source_feature_id": properties.get("source_feature_id"),
            })
    candidates.sort(key=lambda item: item["distance_m"])
    nearby = [item for item in candidates if item["distance_m"] <= CANDIDATE_MATCH_TOLERANCE_M]
    candidate = candidates[0] if candidates else None
    conflicts = station.get("known_conflicts") or []
    metadata_conflicts = (
        station.get("metadata_verification_status") == "conflicting"
        or coordinate_status == "conflicting"
        or bool(conflicts)
    )
    direct_river_evidence = bool(
        candidate and _normalize_hydronym(river_name)
        and _normalize_hydronym(river_name) == _normalize_hydronym(candidate["river_name"])
    )
    spatial_evidence = bool(candidate and candidate["distance_m"] <= VERIFIED_MATCH_TOLERANCE_M)
    corroborated = basin_label_matches and basin_membership is True and direct_river_evidence

    if metadata_conflicts:
        status = "conflicting_metadata"
    elif latitude is None or longitude is None or not reaches or basin_geometry is None:
        status = "insufficient_network_data"
    elif basin_membership is False or not basin_label_matches:
        status = "unmatched"
    elif not nearby:
        status = "unmatched"
    elif spatial_evidence and corroborated and coordinate_status == "verified":
        status = "verified_match"
    else:
        status = "candidate_match"

    if status == "verified_match":
        note = (
            "The published station river and basin labels corroborate the named reach, "
            "the source coordinate falls inside the published Cauvery basin, and the "
            "nearest reach is within the strict spatial tolerance. This verifies a "
            "station-coordinate-to-reach match only; official instrument-code identity "
            "and a directed network connection remain unverified."
        )
    elif status == "conflicting_metadata":
        note = "A known station metadata conflict prevents verification; the nearest reach is retained only as a candidate."
    elif status == "candidate_match":
        note = "Spatial proximity and basin membership support a candidate only; available river-name evidence is insufficient for a verified reach match."
    elif status == "unmatched":
        note = "No reach satisfies the documented basin and candidate-distance criteria."
    else:
        note = "Required station coordinates or source network geometry are unavailable or invalid."

    return {
        "station_id": station.get("station_id"),
        "station_id_type": station.get("station_id_type", "local_normalized_source_name"),
        "station_id_verification_status": station.get("station_id_verification_status", "unknown"),
        "source_station_identifier": station.get("source_station_identifier"),
        "source": station.get("source"),
        "source_url": station.get("source_url"),
        "original_coordinates": {"latitude": latitude, "longitude": longitude, "source": station.get("coordinates_source"), "status": coordinate_status},
        "match_status": status,
        "candidate_reach": candidate if nearby else None,
        "candidate_reaches": nearby[:3],
        "candidate_distance_tolerance_m": CANDIDATE_MATCH_TOLERANCE_M,
        "verified_distance_tolerance_m": VERIFIED_MATCH_TOLERANCE_M,
        "matching_method": "nearest point-to-line segment distance in a local metric plane; basin-polygon membership and source river/basin labels are evaluated separately",
        "river_name_evidence": {"station_value": river_name, "status": river.get("status") if isinstance(river, dict) else "unknown", "candidate_reach_value": candidate.get("river_name") if candidate else None, "name_match": direct_river_evidence},
        "basin_evidence": {"station_value": basin_name, "status": basin.get("status") if isinstance(basin, dict) else "unknown", "published_polygon": "Cauvery" if basin_geometry else None, "point_in_polygon": basin_membership, "label_match": basin_label_matches},
        "known_conflicts": conflicts,
        "uncertainty": note + " " + "Station source coordinates and source-name keys are not an independent CWC instrument-code crosswalk." if status != "verified_match" else note,
        "network_source": {"name": "CWC River Network via NWDP", "url": RIVER_DATASET_URL},
    }


def _read_geojson(path, geometry_validator):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        return None
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        return None
    if any(not isinstance(feature, dict) or not geometry_validator(feature.get("geometry")) for feature in features):
        return None
    return payload


@lru_cache(maxsize=1)
def _load_processed_data():
    river_collection = _read_geojson(RIVER_GEOJSON, validate_line_geometry)
    basin_collection = _read_geojson(BASIN_GEOJSON, _valid_polygon_geometry)
    manifest = {}
    try:
        manifest = json.loads(NETWORK_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return river_collection, basin_collection, manifest


def reset_network_cache_for_tests():
    _load_processed_data.cache_clear()
    station_reach_matches.cache_clear()


def river_network_status():
    river_collection, basin_collection, manifest = _load_processed_data()
    available = river_collection is not None and basin_collection is not None
    stations = station_reach_matches() if available else []
    counts = {}
    for station in stations:
        status = station["match_status"]
        counts[status] = counts.get(status, 0) + 1
    return {
        "status": "available" if available else "insufficient_network_data",
        "source": {"name": "Central Water Commission via National Water Data Portal", "river_dataset_url": RIVER_DATASET_URL, "river_resource_url": RIVER_SHP_RESOURCE_URL, "basin_dataset_url": BASIN_DATASET_URL, "basin_resource_url": BASIN_RESOURCE_URL},
        "river_reach_count": len(river_collection["features"]) if river_collection else 0,
        "basin_feature_count": len(basin_collection["features"]) if basin_collection else 0,
        "coordinate_reference_system": manifest.get("output_crs", "WGS 84 longitude/latitude (EPSG:4326)" if available else None),
        "source_river_crs": manifest.get("river_source_crs"),
        "source_basin_crs": manifest.get("basin_source_crs"),
        "coverage": "Cauvery basin feature subset from the national CWC River Network and Basin layers" if available else None,
        "station_match_counts": counts,
        "topology_available": False,
        "flow_direction_available": False,
        "verified_station_connections": 0,
        "limitation": _NETWORK_LIMITATION,
        "message": "Network layers are loaded; station matches are evidence-qualified." if available else "Official derived network files are unavailable or invalid. Run `python river_network.py --build` from backend to process the downloaded raw archives.",
    }


@lru_cache(maxsize=1)
def station_reach_matches():
    river_collection, basin_collection, _ = _load_processed_data()
    if river_collection is None or basin_collection is None:
        return []
    reaches = river_collection["features"]
    basin_feature = basin_collection["features"][0]
    basin_geometry = basin_feature["geometry"]
    return [classify_station_reach_match(station, reaches, basin_geometry) for station in list_stations()]


def river_network_stations():
    matches = station_reach_matches()
    return {
        "status": "available" if matches else "insufficient_network_data",
        "count": len(matches),
        "stations": matches,
        "limitation": _NETWORK_LIMITATION,
    }


def river_network_geometry():
    river_collection, basin_collection, _ = _load_processed_data()
    features = []
    if basin_collection:
        features.extend(basin_collection["features"])
    if river_collection:
        features.extend(river_collection["features"])
    return {"type": "FeatureCollection", "features": features}


def _haversine_km(left, right):
    lat1, lon1 = math.radians(left[0]), math.radians(left[1])
    lat2, lon2 = math.radians(right[0]), math.radians(right[1])
    delta_lat, delta_lon = lat2 - lat1, lon2 - lon1
    haversine = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(haversine)))


def _explicit_edge_relationship(left, right, matches, topology_edges):
    left_match = matches.get(left["station_id"], {})
    right_match = matches.get(right["station_id"], {})
    if left_match.get("match_status") != "verified_match" or right_match.get("match_status") != "verified_match":
        return None
    left_reach = (left_match.get("candidate_reach") or {}).get("reach_id")
    right_reach = (right_match.get("candidate_reach") or {}).get("reach_id")
    if not left_reach or not right_reach:
        return None
    for edge in topology_edges:
        source = edge.get("source") if isinstance(edge, dict) else None
        if not isinstance(source, dict) or not source.get("name") or not source.get("url"):
            continue
        from_reach, to_reach = edge.get("from_reach_id"), edge.get("to_reach_id")
        if {str(from_reach), str(to_reach)} != {str(left_reach), str(right_reach)}:
            continue
        direction = edge.get("direction")
        if direction == "upstream_to_downstream":
            upstream_id, downstream_id = (left["station_id"], right["station_id"]) if str(from_reach) == str(left_reach) else (right["station_id"], left["station_id"])
            return {"connection_status": "verified_upstream_downstream", "flow_direction_status": "verified", "upstream_station_id": upstream_id, "downstream_station_id": downstream_id, "source": source}
        if direction == "undirected":
            return {"connection_status": "verified_connected_reaches", "flow_direction_status": "unknown", "upstream_station_id": None, "downstream_station_id": None, "source": source}
    return None


def _relationship(left, right, matches, topology_edges):
    explicit = _explicit_edge_relationship(left, right, matches, topology_edges)
    if explicit:
        return explicit
    left_coord = (_safe_float((left.get("latitude") or {}).get("value")), _safe_float((left.get("longitude") or {}).get("value")))
    right_coord = (_safe_float((right.get("latitude") or {}).get("value")), _safe_float((right.get("longitude") or {}).get("value")))
    distance = _haversine_km(left_coord, right_coord) if all(value is not None for value in (*left_coord, *right_coord)) else None
    left_basin, right_basin = left.get("basin") or {}, right.get("basin") or {}
    same_basin = (
        isinstance(left_basin, dict) and isinstance(right_basin, dict)
        and left_basin.get("status") == right_basin.get("status") == "verified"
        and isinstance(left_basin.get("value"), str)
        and left_basin.get("value", "").casefold() == right_basin.get("value", "").casefold()
    )
    if same_basin:
        status = "same_basin_no_verified_river_connection"
    elif distance is not None and distance <= PROXIMITY_ONLY_TOLERANCE_KM:
        status = "proximity_only"
    else:
        status = "unknown"
    return {
        "connection_status": status,
        "flow_direction_status": "unavailable",
        "upstream_station_id": None,
        "downstream_station_id": None,
        "distance_km": round(distance, 2) if distance is not None else None,
        "source": None,
        "evidence": "Verified basin labels establish same-basin membership only; the available river-line layer does not provide a station crosswalk or routable topology." if same_basin else "Straight-line distance is a geographic candidate only; hydrological connection is unverified." if distance is not None else "Insufficient station coordinate or basin evidence.",
    }


def river_network_relationships(station_id=None, topology_edges=()):
    registry = {station["station_id"]: station for station in list_stations()}
    if station_id is not None and station_id not in registry:
        return None
    matches = {match["station_id"]: match for match in station_reach_matches()}
    pairs = []
    if station_id is not None:
        left = registry[station_id]
        others = (station for key, station in registry.items() if key != station_id)
        for right in others:
            pairs.append({"station_a_id": station_id, "station_b_id": right["station_id"], **_relationship(left, right, matches, topology_edges)})
    else:
        stations = list(registry.values())
        for index, left in enumerate(stations):
            for right in stations[index + 1:]:
                pairs.append({"station_a_id": left["station_id"], "station_b_id": right["station_id"], **_relationship(left, right, matches, topology_edges)})
    return {
        "status": "available" if station_reach_matches() else "insufficient_network_data",
        "station_id": station_id,
        "count": len(pairs),
        "relationships": pairs,
        "topology_available": bool(topology_edges),
        "flow_direction_available": any(edge.get("direction") == "upstream_to_downstream" for edge in topology_edges if isinstance(edge, dict)),
        "limitation": _NETWORK_LIMITATION,
    }


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_member(archive, suffix):
    names = [name for name in archive.namelist() if Path(name).suffix.casefold() == suffix.casefold()]
    if len(names) != 1:
        raise ValueError(f"Expected one {suffix} file in source archive; found {len(names)}")
    return names[0]


def build_network_files(river_archive=RIVER_ARCHIVE, basin_archive=BASIN_ARCHIVE,
                        river_output=RIVER_GEOJSON, basin_output=BASIN_GEOJSON,
                        manifest_output=NETWORK_MANIFEST):
    """Build the ignored derived Cauvery layers from unchanged source archives."""
    river_archive, basin_archive = Path(river_archive), Path(basin_archive)
    river_output, basin_output, manifest_output = map(Path, (river_output, basin_output, manifest_output))
    if not river_archive.is_file() or not basin_archive.is_file():
        raise FileNotFoundError("Both raw NWDP river-network and basin archives are required")

    river_features = []
    with ZipFile(river_archive) as archive, TemporaryDirectory(prefix="floodsense-river-") as temporary:
        source_paths = {}
        for suffix in (".shp", ".shx", ".dbf", ".prj"):
            member = _archive_member(archive, suffix)
            destination = Path(temporary) / ("River_Network" + suffix)
            with archive.open(member) as source, destination.open("wb") as target:
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    target.write(block)
            source_paths[suffix] = destination

        source_crs = CRS.from_wkt(source_paths[".prj"].read_text(encoding="utf-8"))
        if not source_crs.is_projected or not source_crs.axis_info or any(axis.unit_name.casefold() not in {"metre", "meter"} for axis in source_crs.axis_info[:2]):
            raise ValueError("River shapefile CRS must be a projected metric CRS")
        river_transformer = Transformer.from_crs(source_crs, CRS.from_epsg(4326), always_xy=True)
        reader = shapefile.Reader(str(source_paths[".shp"]), encoding="utf-8")
        river_source_feature_count = len(reader)
        try:
            for shape_record in reader.iterShapeRecords():
                properties = shape_record.record.as_dict()
                basin_name = properties.get("ba_name")
                if not isinstance(basin_name, str) or basin_name.strip().casefold() != "cauvery":
                    continue
                geometry = _transform_line_geometry(shape_record.shape, river_transformer)
                if geometry is None:
                    continue
                source_feature_id = properties.get("OBJECTID")
                reach_id = f"cwc-objectid-{source_feature_id}" if source_feature_id is not None else None
                river_features.append({
                    "type": "Feature",
                    "properties": {
                        "geometry_role": "river_reach",
                        "reach_id": str(reach_id) if reach_id is not None else None,
                        "source_river_id": str(properties.get("UID_River")) if properties.get("UID_River") is not None else None,
                        "river_name": properties.get("rivname"),
                        "sub_basin": properties.get("sub_basin"),
                        "basin_name": basin_name,
                        "length_km": _safe_float(properties.get("length_km")),
                        "source_feature_id": source_feature_id,
                        "source_confluence_label": properties.get("Confluence"),
                        "source_origin_label": properties.get("origin"),
                    },
                    "geometry": geometry,
                })
        finally:
            reader.close()

    basin_features = []
    with ZipFile(basin_archive) as archive:
        member = _archive_member(archive, ".geojson")
        basin_payload = json.loads(archive.read(member).decode("utf-8"))
    if not isinstance(basin_payload, dict) or basin_payload.get("type") != "FeatureCollection":
        raise ValueError("Basin source must be a GeoJSON FeatureCollection")
    source_crs_value = (basin_payload.get("crs") or {}).get("properties", {}).get("name")
    if not source_crs_value:
        raise ValueError("Basin GeoJSON does not declare its source CRS")
    basin_source_crs = CRS.from_user_input(source_crs_value)
    basin_transformer = Transformer.from_crs(basin_source_crs, CRS.from_epsg(4326), always_xy=True)
    for feature in basin_payload.get("features", []):
        properties = feature.get("properties") or {}
        if str(properties.get("ba_name") or "").strip().casefold() != "cauvery":
            continue
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue
        transformed = {"type": geometry.get("type"), "coordinates": _transform_polygon_coordinates(geometry.get("coordinates"), basin_transformer)}
        if not _valid_polygon_geometry(transformed):
            raise ValueError("Cauvery basin polygon is invalid after reprojection")
        basin_features.append({
            "type": "Feature",
            "properties": {
                "geometry_role": "basin_boundary",
                "basin_name": properties.get("ba_name"),
                "basin_code": properties.get("ba_code", properties.get("bacode")),
                "area_sqkm": _safe_float(properties.get("area_sqkm")),
                "source_agency": properties.get("src_agency"),
            },
            "geometry": transformed,
        })

    if not river_features:
        raise ValueError("No Cauvery river features were found in the official network archive")
    if not basin_features:
        raise ValueError("No Cauvery basin feature was found in the official basin archive")

    river_collection = {"type": "FeatureCollection", "features": river_features}
    basin_collection = {"type": "FeatureCollection", "features": basin_features}
    for path, payload in ((river_output, river_collection), (basin_output, basin_collection)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    manifest = {
        "source_accessed_at": "2026-10-11",
        "river_dataset_url": RIVER_DATASET_URL,
        "river_resource_url": RIVER_SHP_RESOURCE_URL,
        "river_resource_last_updated": "2025-11-17",
        "river_license": "Other (Open), as stated on NWDP resource page",
        "river_source_crs": source_crs.to_wkt(),
        "basin_dataset_url": BASIN_DATASET_URL,
        "basin_resource_url": BASIN_RESOURCE_URL,
        "basin_resource_last_updated": "2026-01-13",
        "basin_source_crs": basin_source_crs.to_string(),
        "basin_license": "Other (Open), as stated on NWDP resource page",
        "output_crs": "EPSG:4326 (WGS 84 longitude/latitude)",
        "river_archive_sha256": _sha256(river_archive),
        "basin_archive_sha256": _sha256(basin_archive),
        "river_source_feature_count": river_source_feature_count,
        "cauvery_river_feature_count": len(river_features),
        "cauvery_basin_feature_count": len(basin_features),
        "river_geometry_format": "ESRI Shapefile polyline, extracted from the original ZIP without modifying source archive",
        "basin_geometry_format": "GeoJSON FeatureCollection inside original ZIP",
        "topology_available": False,
        "flow_direction_available": False,
        "topology_limitation": _NETWORK_LIMITATION,
        "field_names": ["OBJECTID", "rivname", "sub_basin", "ba_name", "length_km", "origin", "major_trib", "st_pt_lat", "st_pt_long", "en_pt_lat", "en_pt_long", "UID_River", "Confluence"],
    }
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    reset_network_cache_for_tests()
    return manifest


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build ignored Cauvery river-network GeoJSON from raw NWDP archives.")
    parser.add_argument("--build", action="store_true", help="reproject official raw source archives and write derived files")
    args = parser.parse_args()
    if not args.build:
        parser.error("Use --build to create the derived network files")
    print(json.dumps(build_network_files(), indent=2))
