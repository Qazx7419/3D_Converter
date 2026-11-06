import unittest

from quality_value_help import build_quality_field_help


class QualityValueHelpTests(unittest.TestCase):
    def test_auto_qsv_uses_current_source_threshold_and_device(self):
        help_info = build_quality_field_help(
            "Авто (Magic)", "qsv", "Intel Iris Xe Graphics",
            height=1080, bitrate_bps=25_418_000,
        )
        self.assertFalse(help_info.enabled)
        self.assertIn("Intel Iris Xe Graphics", help_info.tooltip)
        self.assertIn("global_quality = 15", help_info.tooltip)
        self.assertEqual(help_info.placeholder, "Автоматический подбор — ввод не требуется")

    def test_auto_without_source_explains_the_threshold_instead_of_guessing(self):
        help_info = build_quality_field_help(
            "Авто (Magic)", "qsv", "Intel Iris Xe Graphics"
        )
        self.assertIn("После выбора видео", help_info.tooltip)
        self.assertIn("битрейте от 20 Mbps", help_info.tooltip)

    def test_auto_qsv_standard_source_uses_16(self):
        help_info = build_quality_field_help(
            "Авто (Magic)", "qsv", "Intel Iris Xe Graphics",
            height=1080, bitrate_bps=12_000_000,
        )
        self.assertIn("global_quality = 16", help_info.tooltip)

    def test_auto_nvenc_high_source_uses_19_p6(self):
        help_info = build_quality_field_help(
            "Авто (Magic)", "nvenc", "NVIDIA GeForce RTX",
            height=2160, bitrate_bps=8_000_000,
        )
        self.assertIn("CQ = 19", help_info.tooltip)
        self.assertIn("preset = p6", help_info.tooltip)
        self.assertIn("NVIDIA GeForce RTX", help_info.tooltip)

    def test_vbr_mbps_is_encoder_specific(self):
        qsv = build_quality_field_help("VBR Mbps", "qsv", "Intel Iris Xe Graphics")
        nvenc = build_quality_field_help("VBR Mbps", "nvenc", "NVIDIA GeForce RTX")
        self.assertIn("extbrc=1", qsv.tooltip)
        self.assertIn("-rc vbr", nvenc.tooltip)
        self.assertTrue(qsv.enabled)
        self.assertEqual(qsv.placeholder, "Например: 12 Mbps")

    def test_megabytes_per_second_explains_conversion(self):
        help_info = build_quality_field_help("VBR MB/s", "qsv", "Intel Iris Xe Graphics")
        self.assertIn("1 MB/s = 8 Mbps", help_info.tooltip)
        self.assertEqual(help_info.placeholder, "Например: 2 MB/s")

    def test_target_size_warns_about_audio_and_container_overhead(self):
        help_info = build_quality_field_help(
            "Размер файла (МБ)", "qsv", "Intel Iris Xe Graphics", duration_seconds=3600
        )
        self.assertIn("Аудио и служебные данные контейнера", help_info.tooltip)
        self.assertIn("10.24 Mbps", help_info.tooltip)

    def test_quality_ranges_differ_by_encoder(self):
        qsv = build_quality_field_help("CQ / ICQ", "qsv", "Intel Iris Xe Graphics")
        nvenc = build_quality_field_help("CQ / ICQ", "nvenc", "NVIDIA GeForce RTX")
        self.assertIn("1–51", qsv.placeholder)
        self.assertIn("15–28", nvenc.placeholder)
        self.assertIn("-global_quality", qsv.tooltip)
        self.assertIn("-cq N", nvenc.tooltip)

    def test_cbr_tooltip_is_encoder_specific(self):
        qsv = build_quality_field_help("CBR Mbps", "qsv", "Intel Iris Xe Graphics")
        nvenc = build_quality_field_help("CBR Mbps", "nvenc", "NVIDIA GeForce RTX")
        self.assertIn("minrate", qsv.tooltip)
        self.assertIn("-rc cbr", nvenc.tooltip)

    def test_remux_disables_quality_value(self):
        help_info = build_quality_field_help(
            "VBR Mbps", "qsv", "Intel Iris Xe Graphics", remux=True
        )
        self.assertFalse(help_info.enabled)
        self.assertIn("без перекодирования", help_info.tooltip)


if __name__ == "__main__":
    unittest.main()
