import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_2d_to_ts_test_fragment_honors_start_and_duration(self):
        plan = self.plan(
            "remux_2d_to_ts", test_start_seconds=600, test_duration_seconds=6900
        )
        self.assertLess(plan.command.index("-ss"), plan.command.index("-i"))
        self.assertEqual(plan.command[plan.command.index("-ss") + 1], "600")
        self.assertEqual(plan.command[plan.command.index("-t") + 1], "6900")

    def test_full_sbs_nvenc_uses_cuda_decode_scaling_and_auto_quality(self):
        with patch("encoding_profiles._ffmpeg_supports_filter", return_value=True):
            plan = self.plan("full_sbs_to_half_sbs", "nvenc")
        self.assertEqual(plan.command[plan.command.index("-hwaccel") + 1], "cuda")
        self.assertEqual(
            plan.command[plan.command.index("-hwaccel_output_format") + 1], "cuda"
        )
        self.assertIn(
            "scale_cuda=1920:1080:interp_algo=lanczos:format=nv12", plan.command
        )
        self.assertIn("h264_nvenc", plan.command)
        self.assertIn("-cq", plan.command)
        self.assertIn("21", plan.command)
        self.assertIn("p5", plan.command)

    def test_full_sbs_qsv_uses_hardware_decode_vpp_and_hardware_encode(self):
        with patch("encoding_profiles._ffmpeg_supports_filter", return_value=True):
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
        with patch("encoding_profiles._ffmpeg_supports_filter", return_value=True):
            plan = self.plan("full_sbs_to_half_sbs", "nvenc")
        self.assertIn(
            "scale_cuda=1920:1080:interp_algo=lanczos:format=nv12", plan.command
        )
        self.assertNotIn("vpp_qsv=w=1920:h=1080:format=nv12", plan.command)
        self.assertIn("h264_nvenc", plan.command)

    def test_full_sbs_nvenc_falls_back_when_scale_cuda_is_unavailable(self):
        with patch("encoding_profiles._ffmpeg_supports_filter", return_value=False):
            plan = self.plan("full_sbs_to_half_sbs", "nvenc")
        self.assertEqual(plan.command[plan.command.index("-hwaccel") + 1], "nvdec")
        self.assertNotIn("-hwaccel_output_format", plan.command)
        self.assertIn("scale=1920:1080:flags=lanczos,format=yuv420p", plan.command)
        self.assertIn("software Lanczos fallback (GPU scaling unavailable)", plan.summary)

    def test_half_tab_nvenc_uses_cuda_crop_scaling_and_composition(self):
        def supports(_ffmpeg, filter_name):
            return filter_name in {"scale_cuda", "overlay_cuda"}

        with patch("encoding_profiles._ffmpeg_supports_filter", side_effect=supports):
            plan = self.plan("half_tab_to_half_sbs", "nvenc")
        self.assertEqual(plan.command[plan.command.index("-hwaccel") + 1], "cuda")
        self.assertEqual(
            plan.command[plan.command.index("-hwaccel_output_format") + 1], "cuda"
        )
        graph = plan.command[plan.command.index("-filter_complex") + 1]
        self.assertIn("split=3[base_src][top_src][bottom_src]", graph)
        self.assertIn("crop=iw:ih/2:0:0", graph)
        self.assertIn("crop=iw:ih/2:0:ih/2", graph)
        self.assertEqual(graph.count("scale_cuda="), 3)
        self.assertEqual(graph.count("overlay_cuda="), 2)
        self.assertIn("interp_algo=lanczos", graph)
        self.assertIn("-map", plan.command)
        self.assertIn("[vout]", plan.command)
        self.assertIn("h264_nvenc", plan.command)
        self.assertIn("CUDA crop + scaling + composition", plan.summary)

    def test_half_tab_nvenc_falls_back_when_cuda_composition_is_unavailable(self):
        def supports(_ffmpeg, filter_name):
            return filter_name == "scale_cuda"  # overlay_cuda deliberately missing

        with patch("encoding_profiles._ffmpeg_supports_filter", side_effect=supports):
            plan = self.plan("half_tab_to_half_sbs", "nvenc")
        self.assertEqual(plan.command[plan.command.index("-hwaccel") + 1], "nvdec")
        self.assertNotIn("-hwaccel_output_format", plan.command)
        graph = plan.command[plan.command.index("-vf") + 1]
        self.assertIn("split[top][bottom]", graph)
        self.assertIn("scale=960:1080:flags=lanczos", graph)
        self.assertIn("software Lanczos fallback (GPU scaling unavailable)", plan.summary)

    def test_half_tab_nvenc_software_fallback_when_cuda_filters_are_missing(self):
        with patch("encoding_profiles._ffmpeg_supports_filter", return_value=False):
            plan = self.plan("half_tab_to_half_sbs", "nvenc")
        graph = plan.command[plan.command.index("-vf") + 1]
        self.assertIn("crop=iw:ih/2:0:0", graph)
        self.assertIn("crop=iw:ih/2:0:ih/2", graph)
        self.assertIn("hstack", graph)
        self.assertIn("format=yuv420p", graph)
        self.assertIn("h264_nvenc", plan.command)

    def test_half_tab_qsv_uses_vpp_crop_scaling_and_hstack_qsv(self):
        def supports(_ffmpeg, filter_name):
            return filter_name in {"vpp_qsv", "hstack_qsv"}

        with patch("encoding_profiles._ffmpeg_supports_filter", side_effect=supports):
            plan = self.plan("half_tab_to_half_sbs", "qsv")
        self.assertEqual(plan.command[plan.command.index("-hwaccel") + 1], "qsv")
        self.assertEqual(
            plan.command[plan.command.index("-hwaccel_output_format") + 1], "qsv"
        )
        graph = plan.command[plan.command.index("-filter_complex") + 1]
        self.assertIn("split=2[top_src][bottom_src]", graph)
        self.assertIn("vpp_qsv=cw=iw:ch=ih/2:cx=0:cy=0:w=960:h=1080:format=nv12", graph)
        self.assertIn("cy=ih/2:w=960:h=1080:format=nv12", graph)
        self.assertIn("hstack_qsv=inputs=2:height=1080", graph)
        self.assertIn("[vout]", plan.command)
        self.assertIn("h264_qsv", plan.command)
        self.assertIn("Intel VPP crop/scale + hstack_qsv", plan.summary)

    def test_half_tab_qsv_falls_back_to_software_geometry_when_stack_filter_missing(self):
        def supports(_ffmpeg, filter_name):
            return filter_name == "vpp_qsv"  # hstack_qsv deliberately missing

        with patch("encoding_profiles._ffmpeg_supports_filter", side_effect=supports):
            plan = self.plan("half_tab_to_half_sbs", "qsv")
        self.assertNotIn("-hwaccel", plan.command)
        graph = plan.command[plan.command.index("-vf") + 1]
        self.assertIn("split[top][bottom]", graph)
        self.assertIn("scale=960:1080:flags=lanczos", graph)
        self.assertIn("h264_qsv", plan.command)
        self.assertIn("software Lanczos fallback (GPU scaling unavailable)", plan.summary)

    def test_scaling_mode_defaults_to_automatic_hardware_when_available(self):
        with patch("encoding_profiles._ffmpeg_supports_filter", return_value=True):
            plan = self.plan("full_sbs_to_half_sbs", "nvenc")
        self.assertIn("scale_cuda=1920:1080:interp_algo=lanczos:format=nv12", plan.command)
        self.assertIn("NVIDIA CUDA hardware scaling", plan.summary)

    def test_nvenc_software_scaling_can_be_forced(self):
        with patch("encoding_profiles._ffmpeg_supports_filter", side_effect=AssertionError("filter probe should be skipped")):
            plan = self.plan("full_sbs_to_half_sbs", "nvenc", scaling_mode="cpu")
        self.assertEqual(plan.command[plan.command.index("-hwaccel") + 1], "nvdec")
        self.assertIn("scale=1920:1080:flags=lanczos,format=yuv420p", plan.command)
        self.assertNotIn("scale_cuda", " ".join(plan.command))
        self.assertIn("software Lanczos scaling (user-selected CPU)", plan.summary)

    def test_qsv_software_scaling_can_be_forced(self):
        with patch("encoding_profiles._ffmpeg_supports_filter", side_effect=AssertionError("filter probe should be skipped")):
            plan = self.plan("full_sbs_to_half_sbs", "qsv", scaling_mode="cpu")
        self.assertNotIn("-hwaccel", plan.command)
        self.assertIn("scale=1920:1080:flags=lanczos,format=nv12", plan.command)
        self.assertIn("h264_qsv", plan.command)
        self.assertIn("software Lanczos scaling (user-selected CPU)", plan.summary)

    def test_nvenc_half_tab_software_scaling_can_be_forced(self):
        with patch("encoding_profiles._ffmpeg_supports_filter", side_effect=AssertionError("filter probe should be skipped")):
            plan = self.plan("half_tab_to_half_sbs", "nvenc", scaling_mode="cpu")
        graph = plan.command[plan.command.index("-vf") + 1]
        self.assertIn("crop=iw:ih/2:0:0", graph)
        self.assertIn("scale=960:1080:flags=lanczos", graph)
        self.assertIn("hstack", graph)
        self.assertNotIn("-filter_complex", plan.command)
        self.assertIn("software Lanczos scaling (user-selected CPU)", plan.summary)

    def test_qsv_half_tab_software_scaling_can_be_forced(self):
        with patch("encoding_profiles._ffmpeg_supports_filter", side_effect=AssertionError("filter probe should be skipped")):
            plan = self.plan("half_tab_to_half_sbs", "qsv", scaling_mode="cpu")
        graph = plan.command[plan.command.index("-vf") + 1]
        self.assertIn("crop=iw:ih/2:0:0", graph)
        self.assertIn("scale=960:1080:flags=lanczos", graph)
        self.assertIn("format=nv12", graph)
        self.assertIn("h264_qsv", plan.command)
        self.assertIn("software Lanczos scaling (user-selected CPU)", plan.summary)

    def test_gpu_scaling_can_be_forced(self):
        with patch("encoding_profiles._ffmpeg_supports_filter", return_value=True):
            plan = self.plan("full_sbs_to_half_sbs", "nvenc", scaling_mode="gpu")
        self.assertIn("scale_cuda=1920:1080:interp_algo=lanczos:format=nv12", plan.command)
        self.assertIn("NVIDIA CUDA hardware scaling", plan.summary)

    def test_forced_gpu_scaling_fails_clearly_when_filter_missing(self):
        with patch("encoding_profiles._ffmpeg_supports_filter", return_value=False):
            with self.assertRaisesRegex(ValueError, "Принудительное GPU-масштабирование недоступно"):
                self.plan("full_sbs_to_half_sbs", "nvenc", scaling_mode="gpu")

    def test_auto_scaling_falls_back_to_cpu_when_hardware_filters_are_missing(self):
        with patch("encoding_profiles._ffmpeg_supports_filter", return_value=False):
            plan = self.plan("full_sbs_to_half_sbs", "nvenc", scaling_mode="auto")
        self.assertIn("scale=1920:1080:flags=lanczos,format=yuv420p", plan.command)
        self.assertIn("GPU scaling unavailable", plan.summary)

    def test_invalid_scaling_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Неизвестный режим масштабирования"):
            self.plan("full_sbs_to_half_sbs", "nvenc", scaling_mode="gpu_magic")

    def test_test_interval_start_is_before_input_and_duration_is_exact(self):
        plan = self.plan(
            "full_sbs_to_half_sbs", "nvenc",
            test_start_seconds=1433, test_duration_seconds=3068,
        )
        self.assertLess(plan.command.index("-ss"), plan.command.index("-i"))
        self.assertEqual(plan.command[plan.command.index("-ss") + 1], "1433")
        self.assertEqual(plan.command[plan.command.index("-t") + 1], "3068")

    def test_qsv_full_sbs_falls_back_when_vpp_filter_is_unavailable(self):
        with patch("encoding_profiles._ffmpeg_supports_filter", return_value=False):
            plan = self.plan("full_sbs_to_half_sbs", "qsv")
        self.assertNotIn("-hwaccel", plan.command)
        self.assertIn("scale=1920:1080:flags=lanczos,format=nv12", plan.command)
        self.assertIn("software Lanczos fallback (GPU scaling unavailable)", plan.summary)

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
