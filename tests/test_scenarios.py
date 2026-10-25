import unittest
from pydantic import ValidationError
from backend.scenarios import Parameters, PlaybackUpdate


class ParameterTests(unittest.TestCase):
    def test_zero_rain_and_closed_drains_are_valid_boundaries(self):
        values=Parameters(rain_mm_h=0,drain_capacity_lps=0,blockage_pct=100,wind_mps=0,gust_mps=0)
        self.assertEqual(values.rain_mm_h,0)
        self.assertEqual(values.blockage_pct,100)

    def test_gust_cannot_be_less_than_mean(self):
        with self.assertRaises(ValidationError):
            Parameters(wind_mps=20,gust_mps=10)

    def test_rejects_nonphysical_and_nonfinite_values(self):
        for invalid in ({"rain_mm_h":-1},{"blockage_pct":101},{"coastal_rise_m":float("nan")},{"roughness":0},{"extra":1}):
            with self.subTest(invalid=invalid),self.assertRaises(ValidationError):
                Parameters(**invalid)

    def test_playback_cannot_seek_beyond_data(self):
        with self.assertRaises(ValidationError):
            PlaybackUpdate(time_seconds=3601)


if __name__=='__main__':
    unittest.main()
