import unittest
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
from pydantic import ValidationError
from backend.scenarios import Parameters, PlaybackUpdate
from backend import scenarios


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


class LiveQueueTests(unittest.TestCase):
    def test_latest_edit_wins_and_keeps_playback(self):
        started,release=threading.Event(),threading.Event()
        calls=[]
        def calculate(parameters,folder):
            calls.append(parameters.rain_mm_h)
            if len(calls)==1:
                started.set();self.assertTrue(release.wait(3))
            folder.mkdir(parents=True)
        with tempfile.TemporaryDirectory() as temp,patch.object(scenarios,'DATA',Path(temp)),patch.object(scenarios,'calculate',calculate):
            Path(temp,'model-inputs.npz').touch()
            store=scenarios.ScenarioStore()
            playback=store.playback(PlaybackUpdate(time_seconds=1800,playing=False))['playback']
            try:
                store.submit(Parameters(rain_mm_h=50))
                self.assertTrue(started.wait(3))
                store.submit(Parameters(rain_mm_h=60))
                pending=store.submit(Parameters(rain_mm_h=70))
                self.assertEqual(pending['parameters']['rain_mm_h'],32)
                self.assertEqual(pending['pending_parameters']['rain_mm_h'],70)
            finally:
                release.set();store.executor.shutdown(wait=True)
            self.assertEqual(calls,[50,70])
            result=store.snapshot()
            self.assertEqual(result['parameters']['rain_mm_h'],70)
            self.assertEqual(result['revision'],2)
            self.assertEqual(result['playback'],playback)
            self.assertEqual(len(list(Path(temp,'scenarios').iterdir())),1)

    def test_return_to_current_values_discards_inflight_result(self):
        started,release=threading.Event(),threading.Event()
        def calculate(parameters,folder):
            started.set();release.wait(3);folder.mkdir(parents=True)
        with tempfile.TemporaryDirectory() as temp,patch.object(scenarios,'DATA',Path(temp)),patch.object(scenarios,'calculate',calculate):
            Path(temp,'model-inputs.npz').touch();store=scenarios.ScenarioStore()
            try:
                store.submit(Parameters(rain_mm_h=50));self.assertTrue(started.wait(3))
                state=store.submit(Parameters())
                self.assertEqual(state['status'],'ready')
            finally:
                release.set();store.executor.shutdown(wait=True)
            self.assertEqual(store.snapshot()['scenario_id'],'baseline')
            self.assertEqual(store.snapshot()['revision'],1)

    def test_failure_of_superseded_run_does_not_replace_latest(self):
        started,release=threading.Event(),threading.Event()
        def calculate(parameters,folder):
            if parameters.rain_mm_h==50:
                started.set();release.wait(3);raise RuntimeError('superseded failure')
            folder.mkdir(parents=True)
        with tempfile.TemporaryDirectory() as temp,patch.object(scenarios,'DATA',Path(temp)),patch.object(scenarios,'calculate',calculate):
            Path(temp,'model-inputs.npz').touch();store=scenarios.ScenarioStore()
            try:
                store.submit(Parameters(rain_mm_h=50));self.assertTrue(started.wait(3))
                store.submit(Parameters(rain_mm_h=60))
            finally:
                release.set();store.executor.shutdown(wait=True)
            state=store.snapshot()
            self.assertEqual(state['status'],'ready')
            self.assertEqual(state['parameters']['rain_mm_h'],60)
            self.assertIsNone(state['error'])


if __name__=='__main__':
    unittest.main()
