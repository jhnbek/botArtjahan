import unittest

import numpy as np

from knowledge_bot.scenario_chart_geometry import (
    chart_color_masks, chart_stem_peaks, fit_chart_lattice, fit_price_geometry, price_from_chart_y,
)


class ScenarioChartGeometryTests(unittest.TestCase):
    def test_narrow_stems_preserve_all_time_slots(self):
        expected = np.arange(80)
        xs = np.rint(3 + 5.95*expected).astype(int)
        red = np.zeros((50, 490), dtype=bool)
        green = np.zeros_like(red)
        for i, x in enumerate(xs):
            (red if i % 2 else green)[5+i % 7:30+i % 9, x] = True
        peaks, pitch = chart_stem_peaks({'red': red, 'green': green})
        np.testing.assert_array_equal(peaks, xs)
        _, refined, slots = fit_chart_lattice(peaks, pitch)
        np.testing.assert_array_equal(slots, expected)
        self.assertAlmostEqual(refined, 5.95, delta=.02)

    def test_normal_chart_preserves_legacy_peak_detection(self):
        from knowledge_bot.scenario_image_features import _bar_peaks
        green = np.zeros((60, 400), dtype=bool)
        for x in range(8, 398, 13):
            green[12:40, x] = True
        masks = {'red': np.zeros_like(green), 'green': green}
        old_peaks, old_pitch = _bar_peaks(masks)
        peaks, pitch = chart_stem_peaks(masks)
        np.testing.assert_array_equal(peaks, old_peaks)
        self.assertEqual(pitch, old_pitch)

    def test_compressed_turquoise_wick_is_not_truncated(self):
        # Observed RGB samples from the lower stem of original 1H_27 at x329.
        rgb = np.array([[[72, 119, 109]], [[79, 120, 116]], [[83, 117, 118]],
                        [[76, 121, 118]], [[80, 121, 117]]], dtype=np.uint8)
        self.assertTrue(chart_color_masks(rgb)['green'].all())

    def test_volume_background_and_blue_level_are_not_stems(self):
        rgb = np.array([[[128, 185, 181], [219, 219, 219], [45, 91, 170],
                         [0, 0, 0]]], dtype=np.uint8)
        masks = chart_color_masks(rgb)
        self.assertFalse(masks['green'].any())
        self.assertFalse(masks['red'].any())

    def test_fractional_spacing_does_not_insert_time_slots(self):
        expected = np.arange(50)
        peaks = np.rint(6.1 + expected * 17.35)
        origin, pitch, slots = fit_chart_lattice(peaks, 17)
        np.testing.assert_array_equal(slots, expected)
        self.assertAlmostEqual(pitch, 17.35, delta=.02)
        self.assertLess(np.max(abs(peaks-(origin+slots*pitch))), .6)

    def test_missing_stems_retain_elapsed_slots(self):
        expected = np.delete(np.arange(40), [2, 3, 9, 10, 11, 25])
        peaks = np.rint(9.3 + expected * 16.47)
        _, pitch, slots = fit_chart_lattice(peaks, 16)
        np.testing.assert_array_equal(slots, expected)
        self.assertAlmostEqual(pitch, 16.47, delta=.02)

    def test_duplicate_or_unordered_stems_rejected(self):
        for peaks in ([0, 12, 12], [0, 12, 8]):
            with self.assertRaises(ValueError):
                fit_chart_lattice(np.array(peaks), 12)

    def test_linear_scale_recovers_prices(self):
        pixels = np.linspace(-500, -10, 60)
        prices = 900+pixels*1.2
        geometry = fit_price_geometry(pixels, prices, 40)
        self.assertEqual(geometry['price_axis_scale'], 'linear')
        self.assertLess(geometry['p90_range_error'], 1e-10)
        self.assertAlmostEqual(price_from_chart_y(geometry, 125), 750)

    def test_logarithmic_scale_recovers_prices(self):
        pixels = np.linspace(-500, -10, 60)
        prices = np.exp(7+pixels*.003)
        geometry = fit_price_geometry(pixels, prices, 40)
        self.assertEqual(geometry['price_axis_scale'], 'log')
        self.assertLess(geometry['p90_range_error'], 1e-10)
        self.assertAlmostEqual(price_from_chart_y(geometry, 125), np.exp(6.625))

    def test_wrong_geometry_remains_measurably_wrong(self):
        pixels = np.linspace(-500, -10, 60)
        prices = np.exp(7+pixels*.003)
        prices[::2] *= 1.5
        geometry = fit_price_geometry(pixels, prices, 40)
        self.assertGreater(geometry['p90_range_error'], .35)


if __name__ == '__main__':
    unittest.main()
