import unittest

from musiri_station_verification import build_report


class MusiriStationVerificationTests(unittest.TestCase):
    def test_current_feed_metadata_is_reported_as_conflict_or_unknown_as_appropriate(self):
        telemetry = {"Station": "MUSIRI", "District": "Tiruchirappalli", "River": "Cauvery",
                     "Latitude": "10.93805556", "Longitude": "78.44027778",
                     "RL_of_zeroGauge": "78.0000", "MeanSeaLevel": "92.00"}
        report = build_report(telemetry, feed_sha256="fixture-sha")
        fields = {item["field"]: item for item in report["fields"]}
        self.assertEqual(fields["station_name"]["status"], "verified")
        self.assertEqual(fields["station_identifier"]["status"], "unknown")
        self.assertEqual(fields["coordinates"]["status"], "conflicting")
        self.assertAlmostEqual(fields["coordinates"]["separation_km"], 0.8, places=1)
        self.assertEqual(fields["river"]["status"], "verified")
        self.assertEqual(fields["district"]["status"], "unknown")
        self.assertEqual(fields["level_unit"]["status"], "verified")
        self.assertEqual(fields["telemetry_datum"]["status"], "unknown")
        self.assertEqual(fields["threshold_station_applicability"]["status"], "unknown")
        self.assertEqual(fields["threshold_current_validity"]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
