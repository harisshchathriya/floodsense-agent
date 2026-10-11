
from datetime import datetime, timezone
from math import isfinite

import httpx


OPEN_ELEVATION_URL = "https://api.open-elevation.com/api/v1/lookup"


def get_elevation(latitude: float, longitude: float) -> dict:
    """
    Retrieve terrain elevation for a coordinate using Open-Elevation.

    Returns elevation in metres above the API's reference datum.
    This function retrieves terrain data; it does not calculate flood risk.
    """
    if not -90 <= latitude <= 90:
        raise ValueError("Latitude must be between -90 and 90.")

    if not -180 <= longitude <= 180:
        raise ValueError("Longitude must be between -180 and 180.")

    params = {
        "locations": f"{latitude},{longitude}",
    }

    with httpx.Client(timeout=30.0) as client:
        response = client.get(OPEN_ELEVATION_URL, params=params)
        response.raise_for_status()
        data = response.json()

    if not isinstance(data, dict):
        raise ValueError("Open-Elevation response must be a JSON object")
    # JSON response values are dynamically typed. Normalize them to a typed
    # mapping before accessing nested fields so static analyzers can verify
    # the validation below.
    data = {str(key): value for key, value in data.items()}
    raw_results = data.get("results")
    results: list[object] = raw_results if isinstance(raw_results, list) else []

    if not results:
        raise RuntimeError("Open-Elevation returned no elevation results.")

    raw_result = results[0]
    if not isinstance(raw_result, dict):
        raise ValueError("Open-Elevation result must be an object")
    result: dict[str, object] = {str(key): value for key, value in raw_result.items()}
    raw_elevation = result.get("elevation")

    if isinstance(raw_elevation, bool) or not isinstance(raw_elevation, (int, float, str)):
        raise ValueError("Open-Elevation returned a non-numeric elevation")
    try:
        elevation = float(raw_elevation)
    except (TypeError, ValueError) as exc:
        raise ValueError("Open-Elevation returned a missing or non-numeric elevation") from exc
    if not isfinite(elevation):
        raise ValueError("Open-Elevation returned a non-finite elevation")
    try:
        raw_latitude = result.get("latitude")
        raw_longitude = result.get("longitude")
        if (
            isinstance(raw_latitude, bool)
            or isinstance(raw_longitude, bool)
            or not isinstance(raw_latitude, (int, float, str))
            or not isinstance(raw_longitude, (int, float, str))
        ):
            raise ValueError
        resolved_lat = float(raw_latitude)
        resolved_lon = float(raw_longitude)
        if not isfinite(resolved_lat) or not isfinite(resolved_lon) or not -90 <= resolved_lat <= 90 or not -180 <= resolved_lon <= 180:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("Open-Elevation returned invalid coordinates") from exc

    return {
        "source": "Open-Elevation",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "location": {
            "latitude": resolved_lat,
            "longitude": resolved_lon,
        },
        "elevation_m": elevation,
        "quality_status": "valid",
        "units": {
            "elevation": "metres",
            "time": "UTC",
        },
        "interpretation": (
            "Terrain elevation is contextual information, not a flood "
            "prediction. Local drainage, river levels, and other factors "
            "must also be considered."
        ),
    }


if __name__ == "__main__":
    # Approximate coordinates for central Tiruchirappalli.
    result = get_elevation(10.7905, 78.7047)

    print(f"Source: {result['source']}")
    print(f"Retrieved at (UTC): {result['retrieved_at_utc']}")
    print(f"Location: {result['location']}")
    print(f"Elevation: {result['elevation_m']} metres")
    print(result["interpretation"])
