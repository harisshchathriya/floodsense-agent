"""Official Indian flood alerts and CWC river-level observations.

The alert provider consumes NDMA's published CAP RSS feed. The gauge provider
consumes the public CWC Cauvery telemetry CSV exposed by NWIC. Gauge proximity
is reported as a candidate only; no hydrological connection is inferred.
"""

import csv
import io
import math
import re
from pathlib import Path
from threading import Lock
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import httpx


SACHET_RSS_URL = "https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml"
CWC_CAUVERY_CSV_URL = (
    "https://nwdp.nwic.gov.in/dataset/68600163-c5a0-4327-aa1a-fa7157b86cce/"
    "resource/d027c5ac-379d-4ac2-8ced-97b02b6edbc0/download/"
    "rwl_tel_hr_cwc_009_2026_2030.csv"
)
HISTORICAL_CWC_SOURCE_URL = (
    "https://nwdp.nwic.gov.in/en/dataset/river-water-level-telemetry-hourly-"
    "central-water-commission-cwc/resource/b5776a31-0583-4c27-a2d9-ee81e3b3a03a"
)
SACHET_SOURCE = "NDMA SACHET India CAP RSS"
CWC_SOURCE = "Central Water Commission (CWC), via NWIC"
CWC_MUSIRI_REFERENCE = {
    "station": "Musiri",
    "site_code": "CW1CAL000207",
    "latitude": 10.943,
    "longitude": 78.435,
    "river": "Cauvery",
    "basin": "Cauvery",
    "state": "Tamil Nadu",
    "district": "Tiruchirapalli",
}
MAX_GAUGE_AGE_SECONDS = 3 * 60 * 60
_SACHET_CACHE = {"etag": None, "content": None, "updated_at": None}
_SACHET_CACHE_LOCK = Lock()


def _cache_sachet_response(etag, content, request_started):
    """Commit a response only if it came from a request no older than cache."""
    if not etag:
        return False
    with _SACHET_CACHE_LOCK:
        cache_updated = _SACHET_CACHE.get("updated_at")
        if cache_updated is not None and request_started < cache_updated:
            return False
        _SACHET_CACHE.update(etag=etag, content=content, updated_at=request_started)
        return True


def _local_name(tag):
    return tag.rsplit("}", 1)[-1].split(":")[-1].lower()


def _text(element, name):
    for child in element.iter():
        if _local_name(child.tag) == name.lower() and child.text:
            return child.text.strip()
    return None


def _parse_datetime(value):
    if not value:
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text.replace(" IST ", " +0530 "), "%a %b %d %H:%M:%S %z %Y")
        except ValueError:
            try:
                parsed = datetime.strptime(text, "%a, %d %b %Y %H:%M:%S %z")
            except ValueError:
                return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _inside_polygon(latitude, longitude, points):
    """Ray-cast a CAP/GeoRSS polygon given source-encoded lat/lon pairs."""
    if len(points) < 3:
        return False
    inside = False
    previous = points[-1]
    for current in points:
        lat1, lon1 = previous
        lat2, lon2 = current
        if (lat1 > latitude) != (lat2 > latitude):
            crossing_lon = (lon2 - lon1) * (latitude - lat1) / (lat2 - lat1) + lon1
            if longitude < crossing_lon:
                inside = not inside
        previous = current
    return inside


def _geometry_match(element, latitude, longitude):
    areas = [node for node in element.iter() if _local_name(node.tag) == "area"]
    geo_nodes = [node for node in element.iter() if _local_name(node.tag) in {"point", "polygon", "circle"}]
    for area in areas:
        for node in area.iter():
            kind = _local_name(node.tag)
            values = (node.text or "").strip().split()
            if kind == "polygon" and values:
                try:
                    points = [tuple(map(float, pair.split(","))) for pair in values]
                    if _inside_polygon(latitude, longitude, points):
                        return True, "Selected coordinates fall inside the alert's published CAP polygon."
                except (ValueError, TypeError):
                    continue
            if kind == "circle" and values:
                try:
                    center_lat, center_lon = map(float, values[0].split(","))
                    # CAP 1.2 defines the radius in kilometres.
                    radius_km = float(values[1])
                    delta_lat = math.radians(latitude - center_lat)
                    delta_lon = math.radians(longitude - center_lon)
                    haversine = 2 * 6371.0088 * math.asin(math.sqrt(
                        math.sin(delta_lat / 2) ** 2
                        + math.cos(math.radians(center_lat)) * math.cos(math.radians(latitude))
                        * math.sin(delta_lon / 2) ** 2
                    ))
                    if haversine <= radius_km:
                        return True, "Selected coordinates fall inside the alert's published CAP circle."
                except (ValueError, IndexError):
                    continue
    for node in geo_nodes:
        if _local_name(node.tag) != "point" or not node.text:
            continue
        try:
            point_lat, point_lon = map(float, node.text.replace(",", " ").split()[:2])
            if abs(latitude - point_lat) < 1e-6 and abs(longitude - point_lon) < 1e-6:
                return True, "Selected coordinates exactly match the alert's published point."
        except (ValueError, TypeError):
            continue
    return False, None


def _alert_items(root):
    items = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
    if items:
        return items
    alerts = [node for node in root.iter() if _local_name(node.tag) == "alert"]
    return alerts or [root]


def get_official_alerts(latitude: float, longitude: float, *, now=None) -> dict:
    """Fetch SACHET's public CAP RSS and only match source-provided geometry."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    request_started = datetime.now(timezone.utc)
    try:
        with httpx.Client(timeout=12.0, follow_redirects=True) as client:
            with _SACHET_CACHE_LOCK:
                etag = _SACHET_CACHE["etag"]
                cached_content = _SACHET_CACHE["content"]
            response = client.get(
                SACHET_RSS_URL,
                headers={"If-None-Match": etag} if etag else {},
            )
            if response.status_code == 304 and cached_content is not None:
                content = cached_content
            elif response.status_code == 304:
                response = client.get(SACHET_RSS_URL)
                response.raise_for_status()
                content = response.content
            else:
                response.raise_for_status()
                content = response.content
            root = ET.fromstring(content)
            if not any(_local_name(node.tag) in {"item", "entry", "alert"} for node in root.iter()):
                raise ValueError("SACHET feed contains no alert entries")
            new_etag = response.headers.get("ETag") if response.status_code != 304 else etag
            _cache_sachet_response(new_etag, content, request_started)
            with _SACHET_CACHE_LOCK:
                cache_updated = _SACHET_CACHE.get("updated_at")
                if cache_updated is not None and cache_updated > request_started:
                    content = _SACHET_CACHE["content"]
                    root = ET.fromstring(content)
    except (httpx.HTTPError, ET.ParseError, ValueError) as exc:
        return {
            "status": "unavailable", "source": SACHET_SOURCE,
            "source_url": SACHET_RSS_URL, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "error": f"{type(exc).__name__}: {exc}", "alerts": [],
            "location_status": "unknown",
            "message": "Alert data is unavailable; this is not confirmation that no alerts exist.",
        }

    matched, unmatched = [], 0
    active_count = 0
    for item in _alert_items(root):
        is_match, reason = _geometry_match(item, latitude, longitude)
        if not is_match:
            unmatched += 1
            continue
        effective_raw = _text(item, "effective")
        sent_raw = _text(item, "sent")
        effective = _parse_datetime(effective_raw or sent_raw or _text(item, "pubdate"))
        expires = _parse_datetime(_text(item, "expires"))
        cap_status = _text(item, "status")
        message_type = _text(item, "msgtype")
        is_actual = (cap_status or "").strip().lower() == "actual"
        normalized_type = (message_type or "").strip().lower()
        if normalized_type == "cancel":
            validity = "cancelled"
        elif normalized_type == "update":
            validity = "updated_status_unresolved"
        elif normalized_type in {"ack", "error"}:
            validity = "not_an_alert"
        elif normalized_type == "alert" and is_actual and effective and expires and effective <= now < expires:
            validity = "active"
        elif expires and expires <= now:
            validity = "expired"
        else:
            validity = "validity_unknown"
        if validity == "active":
            active_count += 1
        link = next((node.text.strip() for node in item.iter() if _local_name(node.tag) == "link" and node.text), None)
        matched.append({
            "identifier": _text(item, "identifier") or _text(item, "guid"),
            "cap_status": cap_status,
            "message_type": message_type,
            "headline": _text(item, "headline") or _text(item, "title"),
            "event": _text(item, "event"),
            "description": _text(item, "description"),
            "instruction": _text(item, "instruction"),
            "sender": _text(item, "sendername") or _text(item, "sender"),
            "severity": _text(item, "severity"),
            "urgency": _text(item, "urgency"),
            "certainty": _text(item, "certainty"),
            "effective_utc": effective.isoformat() if effective else None,
            "effective_time_source": "effective" if effective_raw else "sent (CAP default)" if sent_raw else "published timestamp fallback" if _text(item, "pubdate") else None,
            "expires_utc": expires.isoformat() if expires else None,
            "validity_status": validity,
            "area_description": _text(item, "areadesc"),
            "link": link,
            "matched_location": {"latitude": latitude, "longitude": longitude},
            "match_reason": reason,
            "source": SACHET_SOURCE,
        })
    active_found = active_count > 0
    retrieved = datetime.now(timezone.utc).isoformat()
    return {
        "status": "available", "source": SACHET_SOURCE,
        "source_url": SACHET_RSS_URL, "retrieved_at_utc": retrieved,
        "location_status": "active_matching_alerts" if active_found else "no_active_matching_alert",
        "feed_record_count": len(_alert_items(root)), "unmatched_record_count": unmatched,
        "matched_record_count": len(matched),
        "active_matched_count": active_count, "alerts": matched,
        "message": (
            "No active matching CAP alert with verified validity was identified in the retrieved feed; this does not establish an all-clear."
            if not active_found else "Matching alert records are shown with the issuer's original wording and severity."
        ),
    }


def _header_map(row):
    return {re.sub(r"[^a-z0-9]", "", key.lower()): key for key in row if key}


def _value(row, headers, *names):
    for name in names:
        key = headers.get(re.sub(r"[^a-z0-9]", "", name.lower()))
        if key is not None:
            return (row.get(key) or "").strip()
    return ""


def _parse_cwc_time(value):
    """Parse CWC's displayed date-time while retaining undocumented timezone."""
    parsed = _parse_datetime(value)
    if parsed:
        return parsed.replace(tzinfo=None), parsed.isoformat(), True
    for pattern in ("%d-%m-%Y %H:%M", "%d-%m-%Y %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), pattern), value.strip(), False
        except ValueError:
            pass
    return None, value.strip() or None, False


def _distance_km(lat1, lon1, lat2, lon2):
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    angle = 2 * math.asin(math.sqrt(
        math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    ))
    return 6371.0088 * angle


def get_river_gauges(latitude: float, longitude: float, *, now=None) -> dict:
    """Return nearest CWC Cauvery telemetry station candidates, without
    claiming that proximity establishes a local/upstream/downstream relation.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            response = client.get(CWC_CAUVERY_CSV_URL)
            response.raise_for_status()
        reader = csv.DictReader(io.StringIO(response.text.lstrip("\ufeff")))
        if not reader.fieldnames:
            raise ValueError("CWC CSV has no header row")
        headers = _header_map({name: name for name in reader.fieldnames})
        required = ("station", "latitude", "longitude", "dataacquisitiontime", "riverwaterleveltelemetryhourlymeter")
        if any(name not in headers for name in required):
            raise ValueError("CWC CSV is missing required station, coordinate, time, or level columns")
        latest = {}
        row_count = 0
        invalid_row_count = 0
        for row in reader:
            row_count += 1
            try:
                station = _value(row, headers, "Station")
                station_lat = float(_value(row, headers, "Latitude"))
                station_lon = float(_value(row, headers, "Longitude"))
                observed, observed_raw, timezone_known = _parse_cwc_time(
                    _value(row, headers, "Data Acquisition Time")
                )
            except (ValueError, TypeError):
                invalid_row_count += 1
                continue
            if not station or not observed or not -90 <= station_lat <= 90 or not -180 <= station_lon <= 180:
                invalid_row_count += 1
                continue
            if (timezone_known and observed.replace(tzinfo=timezone.utc) > now + timedelta(minutes=15)) or (not timezone_known and observed > now.replace(tzinfo=None) + timedelta(hours=14, minutes=15)):
                invalid_row_count += 1
                continue
            raw_level = _value(row, headers, "River Water Level Telemetry Hourly (meter)")
            try:
                level = float(raw_level) if raw_level and raw_level.casefold() not in {"nan", "na", "n/a", "null", "-"} else None
                if level is not None and not math.isfinite(level):
                    level = None
                    quality = "missing_value" if raw_level.casefold() in {"nan", "na", "n/a", "null", "-"} else "invalid_numeric_value"
                elif level is None:
                    quality = "missing_value"
                else:
                    quality = "valid_numeric_value"
            except (ValueError, TypeError):
                level = None
                quality = "invalid_numeric_value"
            if quality == "invalid_numeric_value":
                invalid_row_count += 1
            station_key = (station, station_lat, station_lon,
                           _value(row, headers, "State"), _value(row, headers, "District"))
            if station_key not in latest or observed > latest[station_key]["observed"]:
                latest[station_key] = {
                    "station": station, "agency": _value(row, headers, "Agency") or "CWC",
                    "state": _value(row, headers, "State"), "district": _value(row, headers, "District"),
                    "river": _value(row, headers, "River") or _value(row, headers, "Local River"),
                    "basin": _value(row, headers, "Basin"), "latitude": station_lat,
                    "longitude": station_lon, "observed": observed,
                    "observed_raw": observed_raw, "timezone_known": timezone_known,
                    "level_m": level, "raw_level": raw_level or None,
                    "quality_status": quality, "conflicting_latest_rows": False,
                }
            elif observed == latest[station_key]["observed"] and (level != latest[station_key]["level_m"] or quality != latest[station_key]["quality_status"]):
                latest[station_key]["level_m"] = None
                latest[station_key]["quality_status"] = "conflicting_latest_rows"
                latest[station_key]["conflicting_latest_rows"] = True
                invalid_row_count += 1
        if row_count and not latest:
            raise ValueError("CWC CSV rows did not contain any valid, georeferenced level observations")
        if not row_count:
            raise ValueError("CWC CSV contains no station observations")
        stations = sorted(latest.values(), key=lambda station: _distance_km(
            latitude, longitude, station["latitude"], station["longitude"]
        ))[:5]
    except (httpx.HTTPError, csv.Error, UnicodeError, ValueError) as exc:
        return {
            "status": "unavailable", "source": CWC_SOURCE,
            "source_url": CWC_CAUVERY_CSV_URL, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "error": f"{type(exc).__name__}: {exc}", "stations": [],
            "location_status": "unknown",
            "message": "Gauge data is unavailable; no reading or local relationship is inferred.",
        }

    output = []
    for station in stations:
        observed_utc = station["observed"].replace(tzinfo=timezone.utc) if station["timezone_known"] else None
        age = max(0, int((now - observed_utc).total_seconds())) if observed_utc else None
        if observed_utc:
            stale = age > MAX_GAUGE_AGE_SECONDS
            freshness_status = "stale" if stale else "fresh"
        else:
            # A naive local timestamp cannot yield an exact age. It can still
            # be called stale when it is older than the threshold under even
            # a deliberately conservative 14-hour civil-time offset bound.
            apparent_age = (now.replace(tzinfo=None) - station["observed"]).total_seconds()
            definitely_stale = apparent_age - 14 * 60 * 60 > MAX_GAUGE_AGE_SECONDS
            stale = True if definitely_stale else None
            freshness_status = "stale" if definitely_stale else "unknown_timezone"
        output.append({
            "station": station["station"], "agency": station["agency"],
            "state": station["state"] or None, "district": station["district"] or None,
            "river": station["river"] or None, "basin": station["basin"] or None,
            "station_location": {"latitude": station["latitude"], "longitude": station["longitude"]},
            "distance_from_selected_point_km": round(_distance_km(
                latitude, longitude, station["latitude"], station["longitude"]
            ), 2),
            "observation": {
                "value": station["level_m"], "unit": "m",
                "raw_value": station["raw_level"],
                "observed_at": station["observed_raw"],
                "observed_at_utc": observed_utc.isoformat() if observed_utc else None,
                "timezone_convention": "UTC offset provided by source" if observed_utc else "not specified in verified source metadata",
                "quality_status": station["quality_status"],
            },
            "age_seconds": age,
            "is_stale": stale,
            "freshness_status": freshness_status,
            "relevance": "nearest_station_candidate_only",
            "match_reason": "Ranked by straight-line distance using the published station coordinates; hydrological connection is not verified.",
            "source": CWC_SOURCE,
        })
    retrieved = datetime.now(timezone.utc).isoformat()
    return {
        "status": "available", "source": CWC_SOURCE,
        "source_url": CWC_CAUVERY_CSV_URL, "retrieved_at_utc": retrieved,
        "location_status": "candidate_stations_found" if output else "no_georeferenced_station",
        "coverage": "Cauvery basin CWC telemetry station dataset only",
        "quality_status": "partial" if invalid_row_count else "valid",
        "invalid_row_count": invalid_row_count,
        "stations": output,
        "message": "Nearest stations are geographic candidates only; distance does not prove that the selected point drains to or is affected by a station.",
    }


def get_musiri_gauge(*, now=None) -> dict:
    """Fetch the exact MUSIRI row from CWC's current Cauvery resource.

    The provider row does not publish the CWC site code, so the source's
    station-name row is returned with identity limitations instead of being
    silently joined to a nearby station or the code in the inventory.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            response = client.get(CWC_CAUVERY_CSV_URL)
            response.raise_for_status()
        reader = csv.DictReader(io.StringIO(response.text.lstrip("\ufeff")))
        if not reader.fieldnames:
            raise ValueError("CWC CSV has no header row")
        headers = _header_map({name: name for name in reader.fieldnames})
        required = ("station", "state", "district", "river", "basin", "latitude", "longitude",
                    "rl of zerogauge", "meansealevel", "dataacquisitiontime",
                    "riverwaterleveltelemetryhourlymeter")
        if any(re.sub(r"[^a-z0-9]", "", name) not in headers for name in required):
            raise ValueError("CWC CSV is missing Musiri identity, datum, timestamp, or level fields")

        matches = []
        malformed_timestamp_rows = 0
        for row in reader:
            source_station = _value(row, headers, "Station")
            if source_station.strip().casefold() != CWC_MUSIRI_REFERENCE["station"].casefold():
                continue
            observed, observed_raw, timezone_known = _parse_cwc_time(
                _value(row, headers, "Data Acquisition Time")
            )
            if observed is None:
                malformed_timestamp_rows += 1
                continue
            raw_level = _value(row, headers, "River Water Level Telemetry Hourly (meter)")
            try:
                level = float(raw_level) if raw_level and raw_level.casefold() not in {"nan", "na", "n/a", "null", "-"} else None
                if level is not None and not math.isfinite(level):
                    level = None
                    raw_level = raw_level or None
            except (TypeError, ValueError):
                level = None
            matches.append({
                "observed": observed, "observed_raw": observed_raw,
                "timezone_known": timezone_known, "raw_level": raw_level or None,
                "level": level, "station": source_station,
                "agency": _value(row, headers, "Agency"),
                "state": _value(row, headers, "State"),
                "district": _value(row, headers, "District"),
                "river": _value(row, headers, "River"),
                "basin": _value(row, headers, "Basin"),
                "latitude": _value(row, headers, "Latitude"),
                "longitude": _value(row, headers, "Longitude"),
                "zero_gauge": _value(row, headers, "RL_of_zeroGauge"),
                "mean_sea_level": _value(row, headers, "MeanSeaLevel"),
            })
        if not matches:
            raise ValueError("No timestamped MUSIRI station rows were found in the current CWC resource")

        latest_time = max(row["observed"] for row in matches)
        latest_rows = [row for row in matches if row["observed"] == latest_time]
        signatures = {(row["raw_level"], row["latitude"], row["longitude"], row["river"], row["district"])
                      for row in latest_rows}
        if len(signatures) > 1:
            raise ValueError("Conflicting MUSIRI rows share the latest observation timestamp")
        latest = latest_rows[0]
        if (latest["timezone_known"] and latest_time.replace(tzinfo=timezone.utc) > now + timedelta(minutes=15)) or (
            not latest["timezone_known"] and latest_time > now.replace(tzinfo=None) + timedelta(hours=14, minutes=15)
        ):
            raise ValueError("Latest MUSIRI observation timestamp is implausibly far in the future")
        try:
            latitude_value = float(latest["latitude"])
            longitude_value = float(latest["longitude"])
            coordinates_valid = (-90 <= latitude_value <= 90 and -180 <= longitude_value <= 180)
        except (TypeError, ValueError):
            latitude_value = longitude_value = None
            coordinates_valid = False
        if not coordinates_valid:
            raise ValueError("Latest MUSIRI row has missing or invalid station coordinates")
        coordinate_offset_km = round(_distance_km(
            CWC_MUSIRI_REFERENCE["latitude"], CWC_MUSIRI_REFERENCE["longitude"],
            latitude_value, longitude_value,
        ), 3)
        metadata_matches = (
            latest["state"].casefold() == CWC_MUSIRI_REFERENCE["state"].casefold()
            and latest["district"].casefold() in {"tiruchirapalli", "thiruchirapalli"}
            and latest["river"].casefold() == CWC_MUSIRI_REFERENCE["river"].casefold()
            and latest["basin"].casefold() == CWC_MUSIRI_REFERENCE["basin"].casefold()
        )

        retrieved = datetime.now(timezone.utc).isoformat()
        if latest["timezone_known"]:
            observed_utc = latest["observed"].astimezone(timezone.utc)
            age_seconds = max(0, int((now - observed_utc).total_seconds()))
            is_stale = age_seconds > MAX_GAUGE_AGE_SECONDS
            freshness = "stale" if is_stale else "fresh"
        else:
            observed_utc = None
            age_seconds = None
            apparent_age = (now.replace(tzinfo=None) - latest["observed"]).total_seconds()
            is_stale = apparent_age - 14 * 60 * 60 > MAX_GAUGE_AGE_SECONDS
            freshness = "stale" if is_stale else "unknown_timezone"
        if latest["raw_level"] is None or latest["raw_level"].strip().casefold() in {"", "nan", "na", "n/a", "null", "-"}:
            quality = "missing_value"
        elif latest["level"] is None:
            quality = "invalid_numeric_value"
        else:
            quality = "valid_numeric_value"
        site = {
            "name": latest["station"], "source_station_identifier": latest["station"],
            "source_station_code": None,
            "reference_site_code": CWC_MUSIRI_REFERENCE["site_code"],
            "reference_site_code_in_telemetry_source": False,
            "identity_status": "name_and_administrative_metadata_match_code_not_published" if metadata_matches else "metadata_mismatch",
            "identity_verified": False,
            "identity_note": "The current CSV names MUSIRI and matches state, district, river and basin, but does not publish the CWC site code; its coordinates differ from the 2025 inventory location.",
            "state": latest["state"], "district": latest["district"],
            "river": latest["river"], "basin": latest["basin"],
            "latitude": latitude_value, "longitude": longitude_value,
            "reference_latitude": CWC_MUSIRI_REFERENCE["latitude"],
            "reference_longitude": CWC_MUSIRI_REFERENCE["longitude"],
            "distance_from_reference_km": coordinate_offset_km,
            "coordinates_match_reference": coordinate_offset_km < 1.0,
            "source": CWC_SOURCE,
            "source_url": CWC_CAUVERY_CSV_URL,
            "metadata_source_url": "https://cwc.gov.in/sites/default/files/ho-metadata-book-2025final-publishedsigned.pdf",
        }


        observation = {
            "value": latest["level"], "raw_value": latest["raw_level"], "unit": "m",
            "observed_at": latest["observed_raw"],
            "observed_at_utc": observed_utc.isoformat() if observed_utc else None,
            "timezone_verified": latest["timezone_known"],
            "timezone_convention": "source timestamp has no stated timezone" if not latest["timezone_known"] else "source timestamp includes UTC offset",
            "raw_zero_of_gauge": latest["zero_gauge"],
            "raw_mean_sea_level_field": latest["mean_sea_level"],
            "quality_status": quality,
        }
        return {
            "status": "available", "source": CWC_SOURCE,
            "source_url": CWC_CAUVERY_CSV_URL, "retrieved_at_utc": retrieved,
            "record_count_for_station": len(matches),
            "malformed_timestamp_row_count": malformed_timestamp_rows,
            "station": site, "observation": observation,
            "freshness": {"status": freshness, "is_stale": is_stale,
                          "age_seconds": age_seconds, "max_age_seconds": MAX_GAUGE_AGE_SECONDS},
            "message": "This is the latest MUSIRI row published in the current CWC resource. A source row is not a live guarantee, and the telemetry does not expose the CWC site code.",
        }
    except (httpx.HTTPError, csv.Error, UnicodeError, ValueError) as exc:
        return {
            "status": "unavailable", "source": CWC_SOURCE,
            "source_url": CWC_CAUVERY_CSV_URL,
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "error": f"{type(exc).__name__}: {exc}", "station": None,
            "observation": None, "freshness": {"status": "unknown", "is_stale": None,
                                                  "age_seconds": None,
                                                  "max_age_seconds": MAX_GAUGE_AGE_SECONDS},
            "message": "Musiri-specific CWC observations are unavailable; no other station is substituted.",
        }

def get_station_observations(source_station_name: str, *, limit=24, now=None) -> dict:
    """Fetch exact-name records for one registry station; never mix station rows.

    The current resource does not publish a station code. Exact source names
    are therefore used as the selection key and disclosed as an identity limit.
    If the current feed has no row for a known historical-only station, the
    preserved 2021-2025 CSV supplies explicitly historical observations.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    if not isinstance(source_station_name, str) or not source_station_name.strip():
        return {"status": "unavailable", "reason": "invalid_station_identity", "observations": [],
                "message": "A source station name is required; no station was substituted."}

    def parse_rows(text, source, source_url, historical=False):
        reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
        if not reader.fieldnames:
            raise ValueError("CWC CSV has no header row")
        headers = _header_map({name: name for name in reader.fieldnames})
        required = ("station", "dataacquisitiontime", "riverwaterleveltelemetryhourlymeter")
        if any(name not in headers for name in required):
            raise ValueError("CWC CSV is missing station, timestamp, or level columns")
        observations = []
        station_names = set()
        invalid_timestamp_count = 0
        for row_order, row in enumerate(reader):
            row_station = _value(row, headers, "Station")
            if not row_station:
                continue
            station_names.add(row_station.casefold())
            # The source name is the only identity column in these files.
            if row_station.casefold() != source_station_name.strip().casefold():
                continue
            observed, observed_raw, timezone_known = _parse_cwc_time(_value(row, headers, "Data Acquisition Time"))
            if observed is None:
                invalid_timestamp_count += 1
            raw_level = _value(row, headers, "River Water Level Telemetry Hourly (meter)")
            try:
                value = float(raw_level) if raw_level and raw_level.casefold() not in {"nan", "na", "n/a", "null", "-"} else None
                if value is not None and not math.isfinite(value):
                    value = None
                    quality = "invalid_numeric_value"
                elif value is None:
                    quality = "missing_value"
                else:
                    quality = "valid_numeric_value"
            except (TypeError, ValueError):
                value, quality = None, "invalid_numeric_value"
            if observed is None:
                quality = "invalid_timestamp"
            if observed is not None and ((timezone_known and observed.replace(tzinfo=timezone.utc) > now + timedelta(minutes=15)) or
                                         (not timezone_known and observed > now.replace(tzinfo=None) + timedelta(hours=14, minutes=15))):
                observed = None
                quality = "invalid_timestamp"
                invalid_timestamp_count += 1
            observations.append({
                "station_id": None,
                "source_station_identifier": row_station,
                "observed_at": observed_raw,
                "observed_at_utc": observed.astimezone(timezone.utc).isoformat() if observed is not None and timezone_known else None,
                "timezone_verified": timezone_known,
                "value": value,
                "raw_value": raw_level or None,
                "unit": "m",
                "unit_status": "verified_from_telemetry_column_header",
                "quality_status": quality,
                "raw_zero_of_gauge": _value(row, headers, "RL_of_zeroGauge") or None,
                "raw_mean_sea_level": _value(row, headers, "MeanSeaLevel") or None,
                "source": source,
                "source_url": source_url,
                "record_type": "historical_snapshot" if historical else "provider_observation",
                "_sort_time": observed,
                "_timezone_known": timezone_known,
                "_source_row_order": row_order,
            })
        if observations:
            observations.sort(key=lambda item: item["_sort_time"] or datetime.min, reverse=True)
            newest_time = observations[0]["_sort_time"]
            same_time = [item for item in observations if item["_sort_time"] == newest_time]
            if newest_time is not None and len({(item["raw_value"], item["quality_status"]) for item in same_time}) > 1:
                for item in same_time:
                    item["value"] = None
                    item["quality_status"] = "conflicting_latest_rows"
            latest = observations[0]
            latest_valid_row_order = max((item["_source_row_order"] for item in observations if item["_sort_time"] is not None), default=-1)
            trailing_unparseable = [item for item in observations if item["_sort_time"] is None and item["_source_row_order"] > latest_valid_row_order]
            if trailing_unparseable:
                latest = max(trailing_unparseable, key=lambda item: item["_source_row_order"])
                latest["value"] = None
                latest["quality_status"] = "invalid_timestamp_latest_order_unknown"
                observations.remove(latest)
                observations.insert(0, latest)
                freshness = "unknown"
                freshness_note = "The latest source row for this station has an invalid timestamp; no older reading is promoted as current."
                age_seconds = None
            elif latest["_sort_time"] is None:
                freshness = "unknown"
                freshness_note = "The selected station has no parseable observation timestamp."
                age_seconds = None
            elif latest["_timezone_known"]:
                age_seconds = max(0, int((now - latest["_sort_time"].astimezone(timezone.utc)).total_seconds()))
                freshness = "stale" if age_seconds > MAX_GAUGE_AGE_SECONDS else "fresh"
                freshness_note = "Application freshness window is three hours; this is not a CWC service-level guarantee."
            else:
                apparent_age = (now.replace(tzinfo=None) - latest["_sort_time"]).total_seconds()
                age_seconds = None
                freshness = "stale" if apparent_age - 14 * 3600 > MAX_GAUGE_AGE_SECONDS else "unknown_timezone"
                freshness_note = "The source does not state a timezone; freshness is unknown unless the record is unambiguously older than three hours under a conservative offset bound."
        else:
            freshness, freshness_note, age_seconds = "unknown", "No observation was found for this exact source station name.", None
        for item in observations:
            item.pop("_sort_time", None)
            item.pop("_timezone_known", None)
            item.pop("_source_row_order", None)
        return observations, invalid_timestamp_count, freshness, freshness_note, age_seconds, station_names

    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            response = client.get(CWC_CAUVERY_CSV_URL)
            response.raise_for_status()
        observations, invalid_times, freshness, freshness_note, age_seconds, _ = parse_rows(
            response.text, CWC_SOURCE, CWC_CAUVERY_CSV_URL
        )
        if observations:
            retrieved = datetime.now(timezone.utc).isoformat()
            return {"status": "available", "availability": "current_resource_recorded", "source": CWC_SOURCE,
                    "source_url": CWC_CAUVERY_CSV_URL, "retrieved_at_utc": retrieved,
                    "station_identity_validation": "exact_source_station_name_match; official station code is not published",
                    "freshness": {"status": freshness, "age_seconds": age_seconds, "max_age_seconds": MAX_GAUGE_AGE_SECONDS, "note": freshness_note},
                    "invalid_timestamp_count": invalid_times, "observation_count": len(observations),
                    "latest_observation": observations[0], "observations": observations[:limit],
                    "message": "Records are selected by exact source station name. Station code and telemetry datum are not verified."}

        # A few stations occur only in the preserved 2021-2025 source file.
        historic_path = Path(__file__).resolve().parents[1] / "data" / "raw" / "cwc_cauvery_telemetry_2021_2025.csv"
        if historic_path.is_file():
            historical_text = historic_path.read_text(encoding="utf-8-sig")
            historical, invalid_times, freshness, freshness_note, age_seconds, _ = parse_rows(
                historical_text, CWC_SOURCE, HISTORICAL_CWC_SOURCE_URL, historical=True
            )
            if historical:
                retrieved = datetime.now(timezone.utc).isoformat()
                return {"status": "available", "availability": "historical_only", "source": CWC_SOURCE,
                        "source_url": HISTORICAL_CWC_SOURCE_URL, "retrieved_at_utc": retrieved,
                        "station_identity_validation": "exact_source_station_name_match; official station code is not published",
                        "freshness": {"status": freshness, "age_seconds": age_seconds, "max_age_seconds": MAX_GAUGE_AGE_SECONDS, "note": freshness_note},
                        "invalid_timestamp_count": invalid_times, "observation_count": len(historical),
                        "latest_observation": historical[0], "observations": historical[:limit],
                        "message": "The current resource has no record for this station; these are preserved historical rows and must not be shown as current."}
        return {"status": "unavailable", "availability": "not_present_in_available_feeds",
                "source": CWC_SOURCE, "source_url": CWC_CAUVERY_CSV_URL,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "freshness": {"status": "unknown"},
                "observations": [], "latest_observation": None,
                "message": "No observation for this exact station name exists in the current or preserved historical feeds; no other station is substituted."}
    except (httpx.HTTPError, csv.Error, UnicodeError, OSError, ValueError) as exc:
        return {"status": "unavailable", "availability": "provider_error", "source": CWC_SOURCE,
                "source_url": CWC_CAUVERY_CSV_URL, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "freshness": {"status": "unknown"}, "observations": [], "latest_observation": None,
                "error": f"{type(exc).__name__}: {exc}",
                "message": "Station observations could not be retrieved. No cached or other-station reading is represented as current."}
