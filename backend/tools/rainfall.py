
from datetime import datetime, timedelta, timezone
from math import isfinite

import httpx


OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def _finite_nonnegative(value: object) -> float | None:
    # JSON measurements should be numbers. Accept numeric strings defensively,
    # but narrow the generic JSON value before passing it to float().
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) and number >= 0 else None


def _preserve_raw(value: object) -> str | int | float | bool | None:
    if isinstance(value, float) and not isfinite(value):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _valid_open_meteo_time(value: object, *, reject_future: bool = False) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if reject_future and parsed > datetime.now(timezone.utc) + timedelta(minutes=15):
            return False
        return True
    except ValueError:
        return False


def _as_mapping(value: object) -> dict[str, object]:
    """Return a string-keyed mapping for a decoded JSON object."""
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _as_list(value: object) -> list[object]:
    """Narrow a decoded JSON value to a list safe for indexed access."""
    return value if isinstance(value, list) else []


def get_rainfall(latitude: float, longitude: float) -> dict:
    """
    Retrieve current and hourly precipitation data from Open-Meteo.

    Args:
        latitude: Latitude between -90 and 90.
        longitude: Longitude between -180 and 180.

    Returns:
        Dictionary containing location, current rainfall, hourly data,
        retrieval timestamp, source, and measurement units.

    Note:
        This function retrieves weather data. It does not calculate
        flood risk or predict flooding.
    """
    # Validate coordinates.
    if not -90 <= latitude <= 90:
        raise ValueError("Latitude must be between -90 and 90.")

    if not -180 <= longitude <= 180:
        raise ValueError("Longitude must be between -180 and 180.")

    # Configure the Open-Meteo request.
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "precipitation,rain",
        "hourly": "precipitation,rain",
        "past_days": 1,
        "forecast_days": 2,
        "timezone": "UTC",
    }

    # Retrieve weather data.
    with httpx.Client(timeout=20.0) as client:
        response = client.get(OPEN_METEO_URL, params=params)
        response.raise_for_status()
        raw_data = response.json()

    if not isinstance(raw_data, dict):
        raise ValueError("Open-Meteo response must be a JSON object")
    data = _as_mapping(raw_data)
    # Extract current conditions and hourly forecast/history, preserving a
    # partial result when one optional series is malformed.
    current = _as_mapping(data.get("current"))
    hourly = _as_mapping(data.get("hourly"))

    times = _as_list(hourly.get("time"))
    precipitation = _as_list(hourly.get("precipitation"))
    rain = _as_list(hourly.get("rain"))
    current_units = _as_mapping(data.get("current_units"))
    hourly_units = _as_mapping(data.get("hourly_units"))
    units_valid = all(current_units.get(key) == "mm" for key in ("precipitation", "rain")) and all(
        hourly_units.get(key) == "mm" for key in ("precipitation", "rain")
    )

    # Combine hourly arrays into individual records safely.
    record_count = min(
        len(times),
        len(precipitation),
        len(rain),
    )

    invalid_record_count = 0
    hourly_records = []
    for i in range(record_count):
        time_valid = _valid_open_meteo_time(times[i])
        precip_value = _finite_nonnegative(precipitation[i]) if units_valid else None
        rain_value = _finite_nonnegative(rain[i]) if units_valid else None
        if not time_valid or precip_value is None or rain_value is None:
            invalid_record_count += 1
        hourly_records.append({
            "time_utc": times[i] if time_valid else None,
            "raw_time": times[i],
            "precipitation_mm": precip_value,
            "rain_mm": rain_value,
            "raw_precipitation": _preserve_raw(precipitation[i]),
            "raw_rain": _preserve_raw(rain[i]),
        })

    current_time = current.get("time")
    current_time_valid = _valid_open_meteo_time(current_time, reject_future=True)
    current_precip = _finite_nonnegative(current.get("precipitation")) if units_valid else None
    current_rain = _finite_nonnegative(current.get("rain")) if units_valid else None
    raw_latitude = data.get("latitude")
    raw_longitude = data.get("longitude")
    resolved_lat: float | None = None
    resolved_lon: float | None = None
    coordinates_valid = False
    if (
        not isinstance(raw_latitude, bool)
        and isinstance(raw_latitude, (int, float, str))
        and not isinstance(raw_longitude, bool)
        and isinstance(raw_longitude, (int, float, str))
    ):
        try:
            resolved_lat = float(raw_latitude)
            resolved_lon = float(raw_longitude)
            coordinates_valid = (
                isfinite(resolved_lat)
                and isfinite(resolved_lon)
                and -90 <= resolved_lat <= 90
                and -180 <= resolved_lon <= 180
            )
        except ValueError:
            pass
    quality_issues = []
    if not isinstance(data.get("current"), dict) or not isinstance(data.get("hourly"), dict):
        quality_issues.append("missing_current_or_hourly_object")
    if len(times) != len(precipitation) or len(times) != len(rain):
        quality_issues.append("hourly_series_length_mismatch")
    if invalid_record_count or not current_time_valid or current_precip is None or current_rain is None:
        quality_issues.append("invalid_or_missing_timestamp_or_measurement")
    if not units_valid:
        quality_issues.append("unexpected_measurement_unit")
    if not coordinates_valid:
        quality_issues.append("invalid_resolved_coordinates")

    # Return normalized data for the FloodSense application.
    return {
        "source": "Open-Meteo",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "location": {
            "latitude": resolved_lat,
            "longitude": resolved_lon,
        },
        "current": {
            "time_utc": current_time if current_time_valid else None,
            "raw_time": current_time,
            "precipitation_mm": current_precip,
            "rain_mm": current_rain,
            "raw_precipitation": _preserve_raw(current.get("precipitation")),
            "raw_rain": _preserve_raw(current.get("rain")),
        },
        "hourly": hourly_records,
        "units": {
            "precipitation": "mm",
            "rain": "mm",
            "time": "UTC",
        },
        "quality_status": "partial" if quality_issues else "valid",
        "quality_issues": quality_issues,
        "invalid_hourly_record_count": invalid_record_count,
    }


if __name__ == "__main__":
    # Approximate coordinates for central Tiruchirappalli.
    latitude = 10.7905
    longitude = 78.7047

    try:
        result = get_rainfall(latitude, longitude)

        print(f"Source: {result['source']}")
        print(f"Retrieved at (UTC): {result['retrieved_at_utc']}")
        print(f"Location: {result['location']}")
        print(f"Current weather: {result['current']}")
        print(f"Hourly records retrieved: {len(result['hourly'])}")

        print("\nFirst three hourly records:")
        for record in result["hourly"][:3]:
            print(record)

    except httpx.HTTPError as error:
        print(f"Could not retrieve Open-Meteo data: {error}")
    except ValueError as error:
        print(f"Invalid input: {error}")
