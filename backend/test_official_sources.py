import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import httpx

from tools import official_sources as providers


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
ALERT_FEED = b'''<?xml version="1.0"?>
<rss xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2"><channel><item>
<title>Flood warning, original wording</title><cap:identifier>flood-1</cap:identifier>
<cap:status>Actual</cap:status><cap:msgType>Alert</cap:msgType>
<cap:senderName>Central Water Commission</cap:senderName><cap:event>Flood</cap:event>
<cap:severity>Severe</cap:severity><cap:urgency>Immediate</cap:urgency>
<cap:effective>2026-10-11T11:00:00+00:00</cap:effective>
<cap:expires>2026-10-11T13:00:00+00:00</cap:expires>
<cap:info><cap:area><cap:areaDesc>Sample district</cap:areaDesc>
<cap:polygon>10.0,78.0 11.0,78.0 11.0,79.0 10.0,79.0</cap:polygon>
</cap:area></cap:info><description>Move to higher ground.</description>
</item></channel></rss>'''


class FakeResponse:
    def __init__(self, *, content=b"", text="", error=None, status_code=200, headers=None):
        self.content = content
        self.text = text
        self.error = error
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.error:
            raise self.error


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.get_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, *args, **kwargs):
        self.get_calls.append((args, kwargs))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def cwc_csv(timestamp="11-10-2026 11:00"):
    header = (
        "Station,Agency,State,District,River,Basin,Latitude,Longitude,"
        "Data Acquisition Time,River Water Level Telemetry Hourly (meter)\n"
    )
    return header + f"Tiruchirappalli,CWC,Tamil Nadu,Tiruchirappalli,Cauvery,Cauvery,10.79,78.70,{timestamp},4.25\n"


class OfficialAlertProviderTests(unittest.TestCase):
    def test_matches_active_cap_alert_and_preserves_issuer_fields(self):
        with patch("tools.official_sources.httpx.Client", return_value=FakeClient(FakeResponse(content=ALERT_FEED))):
            result = providers.get_official_alerts(10.5, 78.5, now=NOW)

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["active_matched_count"], 1)
        alert = result["alerts"][0]
        self.assertEqual(alert["severity"], "Severe")
        self.assertEqual(alert["sender"], "Central Water Commission")
        self.assertEqual(alert["validity_status"], "active")
        self.assertIn("published CAP polygon", alert["match_reason"])

    def test_unrelated_alert_is_not_matched(self):
        with patch("tools.official_sources.httpx.Client", return_value=FakeClient(FakeResponse(content=ALERT_FEED))):
            result = providers.get_official_alerts(20.0, 70.0, now=NOW)

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["location_status"], "no_active_matching_alert")
        self.assertEqual(result["matched_record_count"], 0)
        self.assertEqual(result["alerts"], [])
        self.assertIn("not establish an all-clear", result["message"])

    def test_stale_or_missing_validity_does_not_claim_active(self):
        feed = ALERT_FEED.replace(b"2026-10-11T13:00:00+00:00", b"2026-10-11T11:30:00+00:00")
        with patch("tools.official_sources.httpx.Client", return_value=FakeClient(FakeResponse(content=feed))):
            result = providers.get_official_alerts(10.5, 78.5, now=NOW)
        self.assertEqual(result["alerts"][0]["validity_status"], "expired")
        self.assertEqual(result["active_matched_count"], 0)

        feed = ALERT_FEED.replace(b"<cap:expires>2026-10-11T13:00:00+00:00</cap:expires>", b"")
        with patch("tools.official_sources.httpx.Client", return_value=FakeClient(FakeResponse(content=feed))):
            result = providers.get_official_alerts(10.5, 78.5, now=NOW)
        self.assertEqual(result["alerts"][0]["validity_status"], "validity_unknown")

    def test_test_or_exercise_message_is_never_marked_active(self):
        feed = ALERT_FEED.replace(b"<cap:status>Actual</cap:status>", b"<cap:status>Test</cap:status>")
        with patch("tools.official_sources.httpx.Client", return_value=FakeClient(FakeResponse(content=feed))):
            result = providers.get_official_alerts(10.5, 78.5, now=NOW)
        self.assertEqual(result["alerts"][0]["cap_status"], "Test")
        self.assertEqual(result["alerts"][0]["validity_status"], "validity_unknown")
        self.assertEqual(result["active_matched_count"], 0)

    def test_cancel_message_is_not_presented_as_active_warning(self):
        feed = ALERT_FEED.replace(b"<cap:msgType>Alert</cap:msgType>", b"<cap:msgType>Cancel</cap:msgType>")
        with patch("tools.official_sources.httpx.Client", return_value=FakeClient(FakeResponse(content=feed))):
            result = providers.get_official_alerts(10.5, 78.5, now=NOW)
        self.assertEqual(result["alerts"][0]["validity_status"], "cancelled")
        self.assertEqual(result["active_matched_count"], 0)

    def test_timeout_http_error_and_malformed_feed_are_unavailable(self):
        request = httpx.Request("GET", providers.SACHET_RSS_URL)
        failure = httpx.HTTPStatusError("503", request=request, response=httpx.Response(503, request=request))
        for response in (
            httpx.TimeoutException("timeout"),
            FakeResponse(error=failure),
            FakeResponse(content=b"<rss><broken>"),
        ):
            with patch("tools.official_sources.httpx.Client", return_value=FakeClient(response)):
                result = providers.get_official_alerts(10.5, 78.5, now=NOW)
            self.assertEqual(result["status"], "unavailable")
            self.assertFalse(result["alerts"])
            self.assertIn("not confirmation", result["message"])

    def test_empty_valid_rss_is_unavailable_not_a_no_alert_result(self):
        with patch("tools.official_sources.httpx.Client", return_value=FakeClient(FakeResponse(content=b"<rss><channel /></rss>"))):
            result = providers.get_official_alerts(10.5, 78.5, now=NOW)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["location_status"], "unknown")
        self.assertIn("no alert entries", result["error"])

    def test_provider_uses_public_feed_without_credentials(self):
        with patch.dict("os.environ", {}, clear=True), patch(
            "tools.official_sources.httpx.Client", return_value=FakeClient(FakeResponse(content=ALERT_FEED))
        ) as client:
            result = providers.get_official_alerts(10.5, 78.5, now=NOW)
        self.assertEqual(result["status"], "available")
        self.assertNotIn("Authorization", client.call_args.kwargs.get("headers", {}))

    def test_conditional_request_uses_cached_feed_after_not_modified(self):
        providers._SACHET_CACHE.update(etag=None, content=None, updated_at=None)
        with patch("tools.official_sources.httpx.Client", return_value=FakeClient(
            FakeResponse(content=ALERT_FEED, headers={"ETag": '"v1"'})
        )):
            first = providers.get_official_alerts(10.5, 78.5, now=NOW)
        self.assertEqual(first["status"], "available")

        client = FakeClient(FakeResponse(status_code=304))
        with patch("tools.official_sources.httpx.Client", return_value=client):
            second = providers.get_official_alerts(10.5, 78.5, now=NOW)
        self.assertEqual(second["status"], "available")
        self.assertEqual(client.get_calls[0][1]["headers"]["If-None-Match"], '"v1"')
        providers._SACHET_CACHE.update(etag=None, content=None, updated_at=None)

    def test_older_inflight_response_cannot_replace_newer_sachet_cache(self):
        from datetime import timedelta
        newer = NOW
        older = NOW - timedelta(minutes=1)
        providers._SACHET_CACHE.update(etag='"new"', content=b"newer", updated_at=newer)
        applied = providers._cache_sachet_response('"old"', b"older", older)
        self.assertFalse(applied)
        self.assertEqual(providers._SACHET_CACHE["etag"], '"new"')
        self.assertEqual(providers._SACHET_CACHE["content"], b"newer")
        providers._SACHET_CACHE.update(etag=None, content=None, updated_at=None)


class CwcGaugeProviderTests(unittest.TestCase):
    def _musiri_csv(self, latest="81.047"):
        header = ("Station,Agency,State,District,River,Basin,Latitude,Longitude,RL_of_zeroGauge,MeanSeaLevel,"
                  "Data Acquisition Time,River Water Level Telemetry Hourly (meter)\n")
        rows = ["MUSIRI,CWC,Tamil Nadu,Tiruchirapalli,Cauvery,Cauvery,10.93805556,78.44027778,78.0000,92.00,"
                "03-06-2026 22:00,81.05",
                f"MUSIRI,CWC,Tamil Nadu,Tiruchirapalli,Cauvery,Cauvery,10.93805556,78.44027778,78.0000,92.00,"
                f"03-06-2026 23:00,{latest}"]
        return header + "\n".join(rows) + "\n"

    def test_musiri_provider_uses_exact_station_and_latest_row_without_code_claim(self):
        with patch("tools.official_sources.httpx.Client", return_value=FakeClient(FakeResponse(text=self._musiri_csv()))):
            result = providers.get_musiri_gauge(now=NOW)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["station"]["name"], "MUSIRI")
        self.assertEqual(result["observation"]["value"], 81.047)
        self.assertEqual(result["observation"]["raw_value"], "81.047")
        self.assertFalse(result["station"]["identity_verified"])
        self.assertTrue(result["freshness"]["is_stale"])

    def test_musiri_latest_missing_does_not_fall_back_to_older_value(self):
        with patch("tools.official_sources.httpx.Client", return_value=FakeClient(FakeResponse(text=self._musiri_csv("NaN")))):
            result = providers.get_musiri_gauge(now=NOW)
        self.assertIsNone(result["observation"]["value"])
        self.assertEqual(result["observation"]["raw_value"], "NaN")
        self.assertEqual(result["observation"]["quality_status"], "missing_value")

    def test_musiri_feed_timeout_is_structured_unavailable(self):
        with patch("tools.official_sources.httpx.Client", return_value=FakeClient(httpx.TimeoutException("timeout"))):
            result = providers.get_musiri_gauge(now=NOW)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["observation"])

    def test_returns_level_units_location_and_raw_timestamp_without_assuming_timezone(self):
        response = FakeResponse(text=cwc_csv())
        with patch("tools.official_sources.httpx.Client", return_value=FakeClient(response)):
            result = providers.get_river_gauges(10.80, 78.71, now=NOW)

        self.assertEqual(result["status"], "available")
        station = result["stations"][0]
        self.assertEqual(station["station"], "Tiruchirappalli")
        self.assertEqual(station["observation"]["value"], 4.25)
        self.assertEqual(station["observation"]["unit"], "m")
        self.assertEqual(station["observation"]["observed_at"], "11-10-2026 11:00")
        self.assertIsNone(station["is_stale"])
        self.assertFalse(station["relevance"] == "verified_local_station")
        self.assertIn("not verified", station["match_reason"])

    def test_latest_missing_gauge_observation_is_not_replaced_by_an_older_value(self):
        data = cwc_csv("11-10-2026 10:00") + "Tiruchirappalli,CWC,Tamil Nadu,Tiruchirappalli,Cauvery,Cauvery,10.79,78.70,11-10-2026 11:00,NaN\n"
        with patch("tools.official_sources.httpx.Client", return_value=FakeClient(FakeResponse(text=data))):
            result = providers.get_river_gauges(10.8, 78.7, now=NOW)
        observation = result["stations"][0]["observation"]
        self.assertIsNone(observation["value"])
        self.assertEqual(observation["raw_value"], "NaN")
        self.assertEqual(observation["quality_status"], "missing_value")
        self.assertEqual(observation["observed_at"], "11-10-2026 11:00")

    def test_old_naive_local_timestamp_is_stale_without_claiming_exact_age(self):
        response = FakeResponse(text=cwc_csv("03-06-2026 21:00"))
        with patch("tools.official_sources.httpx.Client", return_value=FakeClient(response)):
            result = providers.get_river_gauges(10.80, 78.71, now=NOW)
        station = result["stations"][0]
        self.assertTrue(station["is_stale"])
        self.assertEqual(station["freshness_status"], "stale")
        self.assertIsNone(station["age_seconds"])

    def test_known_timezone_supports_age_staleness_and_candidate_distance(self):
        response = FakeResponse(text=cwc_csv("2026-10-11T08:00:00+00:00"))
        with patch("tools.official_sources.httpx.Client", return_value=FakeClient(response)):
            result = providers.get_river_gauges(10.80, 78.71, now=NOW)

        station = result["stations"][0]
        self.assertTrue(station["is_stale"])
        self.assertEqual(station["age_seconds"], 4 * 60 * 60)
        self.assertGreaterEqual(station["distance_from_selected_point_km"], 0)

    def test_timeout_http_failure_and_bad_csv_are_unavailable(self):
        request = httpx.Request("GET", providers.CWC_CAUVERY_CSV_URL)
        failure = httpx.HTTPStatusError("503", request=request, response=httpx.Response(503, request=request))
        for response in (
            httpx.TimeoutException("timeout"),
            FakeResponse(error=failure),
            FakeResponse(text=""),
        ):
            with patch("tools.official_sources.httpx.Client", return_value=FakeClient(response)):
                result = providers.get_river_gauges(10.8, 78.7, now=NOW)
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["stations"], [])

    def test_malformed_csv_schema_is_unavailable(self):
        with patch("tools.official_sources.httpx.Client", return_value=FakeClient(FakeResponse(text="station,water_level\nA,3.2\n"))):
            result = providers.get_river_gauges(10.8, 78.7, now=NOW)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("missing required", result["error"])


if __name__ == "__main__":
    unittest.main()
