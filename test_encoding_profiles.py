import tempfile
import unittest
from pathlib import Path

from encoding_profiles import (
    ConversionSettings,
    QualitySettings,
    SourceInfo,
    build_conversion_plan,
)


class EncodingProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.input_path = str(Path(self.temp.name) / "sample.mkv")
        Path(self.input_path).write_bytes(b"placeholder")
        self.source = SourceInfo(height=1080, bitrate_bps=12_000_000, duration_seconds=600)

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, operation, encoder=None, **kwargs):
        settings = ConversionSettings(
            input_path=self.input_path,
            operation=operation,
            encoder=encoder,
            ffmpeg_path="ffmpeg",
            **kwargs,
        )
        return build_conversion_plan(settings, self.source)

    def test_2d_to_ts_is_stream_copy(self):
        plan = self.plan("remux_2d_to_ts")
        self.assertIn("-c:v", plan.command)
        self.assertIn("copy", plan.command)
        self.assertEqual(Path(plan.output_path).suffix, ".ts")
        self.assertNotIn("h264_qsv", plan.command)

    def test_2d_to_ts_test_fragment_honors_duration(self):
        plan = self.plan("remux_2d_to_ts", test_duration_seconds=120)
        self.assertIn("-t", plan.command)
        self.assertEqual(plan.command[plan.command.index("-t") + 1], "120")

    def test_full_sbs_nvenc_uses_reference_scale_and_auto_quality(self):
        plan = self.plan("full_sbs_to_half_sbs", "nvenc")
        self.assertIn("scale=1920:1080:flags=lanczos,format=yuv420p", plan.command)
        self.assertIn("h264_nvenc", plan.command)
        self.assertIn("-cq", plan.command)
        self.assertIn("21", plan.command)
        self.assertIn("p5", plan.command)

    def test_full_sbs_qsv_uses_hardware_decode_vpp_and_hardware_encode(self):
        plan = self.plan("full_sbs_to_half_sbs", "qsv")
        self.assertEqual(
            plan.command[plan.command.index("-hwaccel") + 1], "qsv"
        )
        self.assertEqual(
            plan.command[plan.command.index("-hwaccel_output_format") + 1], "qsv"
        )
        graph = plan.command[plan.command.index("-vf") + 1]
        self.assertEqual(graph, "vpp_qsv=w=1920:h=1080:format=nv12")
        self.assertNotIn("scale=1920:1080:flags=lanczos", plan.command)
        self.assertIn("h264_qsv", plan.command)
        self.assertIn("-global_quality", plan.command)
        self.assertIn("16", plan.command)
        # Let QSV choose a valid level for the source frame rate (e.g. 4.2 for 1080p60).
        self.assertNotIn("-level", plan.command)


    def test_full_sbs_nvenc_does_not_use_qsv_vpp(self):
        plan = self.plan("full_sbs_to_half_sbs", "nvenc")
        self.assertIn("scale=1920:1080:flags=lanczos,format=yuv420p", plan.command)
        self.assertNotIn("vpp_qsv=w=1920:h=1080:format=nv12", plan.command)
        self.assertIn("h264_nvenc", plan.command)

    def test_half_tab_nvenc_uses_original_geometry_filter(self):
        plan = self.plan("half_tab_to_half_sbs", "nvenc")
        graph = plan.command[plan.command.index("-vf") + 1]
        self.assertIn("split[top][bottom]", graph)
        self.assertIn("crop=iw:ih/2:0:0", graph)
        self.assertIn("crop=iw:ih/2:0:ih/2", graph)
        self.assertIn("hstack", graph)
        self.assertIn("format=yuv420p", graph)
        self.assertIn("h264_nvenc", plan.command)

    def test_half_tab_qsv_uses_same_geometry_and_nv12_encoder_input(self):
        plan = self.plan("half_tab_to_half_sbs", "qsv")
        graph = plan.command[plan.command.index("-vf") + 1]
        self.assertIn("split[top][bottom]", graph)
        self.assertIn("crop=iw:ih/2:0:0", graph)
        self.assertIn("crop=iw:ih/2:0:ih/2", graph)
        self.assertIn("hstack", graph)
        self.assertIn("format=nv12", graph)
        self.assertIn("h264_qsv", plan.command)
        self.assertIn("-global_quality", plan.command)
        self.assertNotIn("-hwaccel", plan.command)  # software filter path, QSV encode

    def test_qsv_vbr_size_mode_has_extbrc(self):
        plan = self.plan(
            "half_tab_to_half_sbs", "qsv",
            quality=QualitySettings(mode="target_size", value=4500),
        )
        self.assertIn("-extbrc", plan.command)
        self.assertIn("1", plan.command)
        self.assertIn("-maxrate", plan.command)

    def test_nvenc_cbr_has_rate_control_flag(self):
        plan = self.plan(
            "full_sbs_to_half_sbs", "nvenc",
            quality=QualitySettings(mode="cbr_mbps", value=15),
        )
        self.assertIn("cbr", plan.command)
        self.assertIn("-minrate", plan.command)

    def test_full_sbs_qsv_output_name_uses_target_layout(self):
        original = self.input_path
        self.input_path = str(Path(self.temp.name) / "video_Full_SBS.mkv")
        Path(self.input_path).write_bytes(b"placeholder")
        plan = self.plan("full_sbs_to_half_sbs", "qsv")
        self.assertEqual(Path(plan.output_path).name, "video_Half_SBS.ts")
        self.input_path = original

    def test_full_sbs_nvenc_and_qsv_share_output_naming(self):
        original = self.input_path
        self.input_path = str(Path(self.temp.name) / "video_Full_SBS.mkv")
        Path(self.input_path).write_bytes(b"placeholder")
        qsv = self.plan("full_sbs_to_half_sbs", "qsv")
        nvenc = self.plan("full_sbs_to_half_sbs", "nvenc")
        self.assertEqual(Path(qsv.output_path).name, Path(nvenc.output_path).name)
        self.input_path = original

    def test_half_tab_output_name_uses_target_layout(self):
        original = self.input_path
        self.input_path = str(Path(self.temp.name) / "movie-HalfTAB.mkv")
        Path(self.input_path).write_bytes(b"placeholder")
        plan = self.plan("half_tab_to_half_sbs", "qsv")
        self.assertEqual(Path(plan.output_path).name, "movie_Half_SBS.ts")
        self.input_path = original

    def test_mp4_and_audio_copy_options(self):
        plan = self.plan(
            "half_tab_to_half_sbs", "qsv",
            container="mp4", audio_mode="copy",
        )
        self.assertIn("mov_text", plan.command)
        self.assertIn("+faststart", plan.command)
        self.assertEqual(plan.command[plan.command.index("-c:a") + 1], "copy")


if __name__ == "__main__":
    unittest.main(verbosity=2)
