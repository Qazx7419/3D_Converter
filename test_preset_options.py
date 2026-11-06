import unittest

from preset_options import (
    NVENC_PRESETS,
    QSV_PRESETS,
    preset_options_for_encoder,
    recommended_preset,
)


class PresetOptionsTests(unittest.TestCase):
    def test_nvenc_options(self):
        self.assertEqual(preset_options_for_encoder("nvenc"), NVENC_PRESETS)
        self.assertEqual(NVENC_PRESETS, ("p1", "p2", "p3", "p4", "p5", "p6", "p7"))

    def test_qsv_options(self):
        self.assertEqual(preset_options_for_encoder("qsv"), QSV_PRESETS)
        self.assertEqual(QSV_PRESETS, ("veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"))

    def test_qsv_default_is_slower(self):
        self.assertEqual(recommended_preset("qsv"), "slower")

    def test_qsv_default_stays_slower_for_manual_quality_modes(self):
        for mode in ("manual", "auto"):
            with self.subTest(mode=mode):
                self.assertEqual(recommended_preset("qsv", quality_mode=mode, height=2160, bitrate_bps=50_000_000), "slower")

    def test_nvenc_auto_magic_high_source_uses_p6(self):
        self.assertEqual(recommended_preset("nvenc", quality_mode="auto", height=1080, bitrate_bps=25_000_000), "p6")

    def test_nvenc_auto_magic_high_resolution_uses_p6(self):
        self.assertEqual(recommended_preset("nvenc", quality_mode="auto", height=2160, bitrate_bps=8_000_000), "p6")

    def test_nvenc_auto_magic_standard_source_uses_p5(self):
        self.assertEqual(recommended_preset("nvenc", quality_mode="auto", height=1080, bitrate_bps=12_000_000), "p5")

    def test_nvenc_manual_quality_modes_use_p5(self):
        self.assertEqual(recommended_preset("nvenc", quality_mode="manual", height=2160, bitrate_bps=50_000_000), "p5")

    def test_defaults_are_valid_for_their_encoders(self):
        for encoder in ("nvenc", "qsv"):
            value = recommended_preset(encoder)
            self.assertIn(value, preset_options_for_encoder(encoder))


if __name__ == "__main__":
    unittest.main()
