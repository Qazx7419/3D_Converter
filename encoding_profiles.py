"""FFmpeg command profiles ported from the user's four reference BAT files.

The TAB->SBS QSV profile is a derived profile: it keeps the exact TAB geometry
filter from the NVENC BAT and applies the Intel QSV encoder/rate-control settings
from the Intel BAT. Its split/crop/scale/hstack geometry filters use system-memory
frames; QSV performs the final H.264 encoding.

The Full SBS -> Half SBS QSV profile uses a fully hardware-resident Intel path:
HEVC QSV decode -> vpp_qsv scaling -> H.264 QSV encode. This path was verified
with the user's Intel Iris Xe / oneVPL setup using a 10-bit HEVC source.

The Full SBS and Half TAB -> Half SBS NVENC profiles can use CUDA-resident
NVIDIA processing. The TAB path crops the two views as hardware-frame metadata,
scales both views with scale_cuda, and joins them with overlay_cuda.

The Half TAB -> Half SBS QSV profile can use Intel VPP to crop/scale both views
and hstack_qsv to join them, keeping frames in QSV surfaces. If a required filter
is absent, the command builder keeps the previously tested software geometry
fallback for that encoder.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional
import math
import re
import shutil
import subprocess
from functools import lru_cache

Encoder = Literal["nvenc", "qsv"]
Operation = Literal[
    "remux_2d_to_ts",
    "full_sbs_to_half_sbs",
    "half_tab_to_half_sbs",
]
QualityMode = Literal["auto", "vbr_mbps", "vbr_mbytes", "target_size", "quality", "cbr_mbps"]
ScalingMode = Literal["auto", "gpu", "cpu"]


@dataclass(frozen=True)
class SourceInfo:
    height: int
    bitrate_bps: int
    duration_seconds: float


@dataclass(frozen=True)
class QualitySettings:
    mode: QualityMode = "auto"
    value: Optional[float] = None
    preset: Optional[str] = None


@dataclass(frozen=True)
class ConversionSettings:
    input_path: str
    operation: Operation
    encoder: Optional[Encoder] = None
    container: str = "ts"
    audio_mode: str = "ac3"  # ac3 | copy
    quality: QualitySettings = QualitySettings()
    test_duration_seconds: Optional[int] = None
    test_start_seconds: Optional[int] = None
    output_path: Optional[str] = None
    ffmpeg_path: Optional[str] = None
    scaling_mode: ScalingMode = "auto"  # auto = GPU when available; gpu = require GPU; cpu = software Lanczos


@dataclass(frozen=True)
class ConversionPlan:
    command: list[str]
    output_path: str
    operation: str
    encoder: str
    summary: str


class ProfileError(ValueError):
    """Raised when the selected profile/settings are inconsistent."""


@lru_cache(maxsize=16)
def _ffmpeg_supports_filter(ffmpeg_path: str, filter_name: str) -> bool:
    """Check whether the selected FFmpeg build exposes a named video filter.

    The result is cached because FFmpeg's filter list does not change while
    the application is running. A failed probe returns False so the caller
    can choose a compatible fallback instead of breaking conversion.
    """
    try:
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False

    filter_output = f"{result.stdout or ''}\n{result.stderr or ''}"
    for line in filter_output.splitlines():
        fields = line.strip().split()
        if len(fields) >= 2 and fields[1] == filter_name:
            return True
    return False


OPERATION_LABELS = {
    "remux_2d_to_ts": "2D → TS (перепаковка без перекодирования)",
    "full_sbs_to_half_sbs": "Full SBS → Half SBS",
    "half_tab_to_half_sbs": "Half TAB → Half SBS",
}


def _validate_positive(value: Optional[float], label: str) -> float:
    if value is None or not math.isfinite(float(value)) or float(value) <= 0:
        raise ProfileError(f"Укажите положительное значение для параметра «{label}».")
    return float(value)


def _auto_quality_args(encoder: Encoder, source: SourceInfo) -> tuple[list[str], str, str]:
    """Return codec-specific auto-quality args, preset, and explanation.

    Thresholds and values mirror the AUTO_MAGIC branches in the source BATs.
    """
    high_source = source.height > 1080 or int(source.bitrate_bps / 1_000_000) >= 20
    if encoder == "nvenc":
        cq, preset = (19, "p6") if high_source else (21, "p5")
        reason = "высокое разрешение/битрейт" if high_source else "стандартный HD/Full HD"
        return ["-rc", "vbr", "-cq", str(cq), "-b:v", "0"], preset, f"CQ {cq}, preset {preset}; {reason}"

    global_quality = 15 if high_source else 16
    reason = "высокое разрешение/битрейт" if high_source else "стандартный HD/Full HD"
    return ["-global_quality", str(global_quality)], "slower", f"global_quality {global_quality}, preset slower; {reason}"


def _rate_control_args(encoder: Encoder, quality: QualitySettings, source: SourceInfo) -> tuple[list[str], str, str]:
    mode = quality.mode
    if mode == "auto":
        return _auto_quality_args(encoder, source)

    if mode == "quality":
        default = 21 if encoder == "nvenc" else 16
        value = int(round(_validate_positive(quality.value if quality.value is not None else default, "CQ / ICQ")))
        if encoder == "nvenc":
            if not 15 <= value <= 28:
                raise ProfileError("Для NVENC значение CQ должно быть от 15 до 28 (как в BAT-файле).")
            return ["-rc", "vbr", "-cq", str(value), "-b:v", "0"], quality.preset or "p5", f"NVENC CQ {value}"
        if not 1 <= value <= 51:
            raise ProfileError("Для Intel QSV значение global_quality должно быть от 1 до 51.")
        return ["-global_quality", str(value)], quality.preset or "slower", f"QSV global_quality {value}"

    if mode == "vbr_mbps":
        mbps = _validate_positive(quality.value, "Mbps")
        bitrate_k = max(1, int(round(mbps * 1000)))
    elif mode == "vbr_mbytes":
        mbytes_per_second = _validate_positive(quality.value, "MB/s")
        bitrate_k = max(1, int(round(mbytes_per_second * 8 * 1000)))
    elif mode == "target_size":
        target_mb = _validate_positive(quality.value, "Размер файла (МБ)")
        if source.duration_seconds <= 0:
            raise ProfileError("Не удалось определить длительность для расчёта битрейта по размеру файла.")
        # This is the formula used in the BAT scripts: MB * 8192 / seconds.
        bitrate_k = max(1, int((target_mb * 8192) / source.duration_seconds))
    elif mode == "cbr_mbps":
        mbps = _validate_positive(quality.value, "CBR Mbps")
        bitrate_k = max(1, int(round(mbps * 1000)))
    else:
        raise ProfileError(f"Неизвестный режим качества: {mode}")

    if mode == "cbr_mbps":
        args = ["-b:v", f"{bitrate_k}k", "-maxrate", f"{bitrate_k}k",
                "-minrate", f"{bitrate_k}k", "-bufsize", f"{bitrate_k}k"]
        if encoder == "nvenc":
            args = ["-rc", "cbr", *args]
        return args, quality.preset or ("p5" if encoder == "nvenc" else "slower"), f"CBR {bitrate_k} kbit/s"

    maxrate_k = bitrate_k + bitrate_k // 2
    bufsize_k = maxrate_k * 2
    args = ["-b:v", f"{bitrate_k}k", "-maxrate", f"{maxrate_k}k", "-bufsize", f"{bufsize_k}k"]
    if encoder == "nvenc":
        args = ["-rc", "vbr", *args]
    else:
        # The Intel BAT uses extbrc=1 for its dynamic VBR/target-size mode.
        args.append("-extbrc")
        args.append("1")
    return args, quality.preset or ("p5" if encoder == "nvenc" else "slower"), f"VBR {bitrate_k} kbit/s (max {maxrate_k} kbit/s)"


def _validate_preset(encoder: Encoder, preset: str) -> str:
    value = preset.strip().lower()
    if encoder == "nvenc":
        allowed = {f"p{i}" for i in range(1, 8)}
    else:
        allowed = {"veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"}
    if value not in allowed:
        raise ProfileError(
            "Неверный пресет. Для NVENC доступны p1–p7; для QSV: "
            "veryfast, faster, fast, medium, slow, slower, veryslow."
        )
    return value


def _filter_for(
    operation: Operation,
    encoder: Encoder,
    *,
    use_cuda_scale: bool = False,
    use_qsv_scale: bool = False,
) -> str:
    if operation == "full_sbs_to_half_sbs":
        if encoder == "qsv":
            if use_qsv_scale:
                # Intel VPP keeps decoded frames on QSV surfaces.
                return "vpp_qsv=w=1920:h=1080:format=nv12"
            return "scale=1920:1080:flags=lanczos,format=nv12"
        if use_cuda_scale:
            # NVIDIA CUDA path: CUDA decode -> scale_cuda -> NVENC.
            return "scale_cuda=1920:1080:interp_algo=lanczos:format=nv12"
        return "scale=1920:1080:flags=lanczos,format=yuv420p"

    if operation == "half_tab_to_half_sbs":
        pixel_format = "yuv420p" if encoder == "nvenc" else "nv12"
        # Compatibility / explicitly software geometry graph.
        return (
            "split[top][bottom];"
            "[top]crop=iw:ih/2:0:0,scale=960:1080:flags=lanczos[left];"
            "[bottom]crop=iw:ih/2:0:ih/2,scale=960:1080:flags=lanczos[right];"
            f"[left][right]hstack,setsar=1:1,format={pixel_format}"
        )
    raise ProfileError(f"Для операции {operation} фильтр видео не требуется или не поддерживается.")


def _cuda_half_tab_filter_graph() -> str:
    """Crop/scale both OU views and join them without downloading CUDA frames."""
    return (
        "[0:v]split=3[base_src][top_src][bottom_src];"
        "[base_src]scale_cuda=1920:1080:format=nv12[canvas];"
        "[top_src]crop=iw:ih/2:0:0,"
        "scale_cuda=960:1080:interp_algo=lanczos:format=nv12[top_eye];"
        "[bottom_src]crop=iw:ih/2:0:ih/2,"
        "scale_cuda=960:1080:interp_algo=lanczos:format=nv12[bottom_eye];"
        "[canvas][top_eye]overlay_cuda=x=0:y=0[tmp];"
        "[tmp][bottom_eye]overlay_cuda=x=960:y=0[vout]"
    )


def _qsv_half_tab_filter_graph() -> str:
    """Crop/scale both OU views with Intel VPP and join QSV surfaces."""
    return (
        "[0:v]split=2[top_src][bottom_src];"
        "[top_src]vpp_qsv=cw=iw:ch=ih/2:cx=0:cy=0:"
        "w=960:h=1080:format=nv12[top_eye];"
        "[bottom_src]vpp_qsv=cw=iw:ch=ih/2:cx=0:cy=ih/2:"
        "w=960:h=1080:format=nv12[bottom_eye];"
        "[top_eye][bottom_eye]hstack_qsv=inputs=2:height=1080[vout]"
    )


def _output_stem_for_sbs(source: Path) -> str:
    """Remove a trailing source-layout marker before adding the target layout.

    Examples:
      video_Full_SBS.mkv -> video_Half_SBS.ts
      movie-HalfTAB.mkv  -> movie_Half_SBS.ts

    Descriptive names where the layout marker is not a trailing token are kept
    intact, avoiding accidental removal of meaningful title text.
    """
    stem = source.stem
    layout_suffix = re.compile(
        r"(?i)(?:[_ .-]+(?:3d[_ .-]+)?(?:full|half)[_ .-]*(?:sbs|tab|ou|tb))$"
    )
    cleaned = layout_suffix.sub("", stem).rstrip(" _.-")
    return cleaned or stem


def _default_output_path(settings: ConversionSettings) -> str:
    source = Path(settings.input_path)
    if settings.operation == "remux_2d_to_ts":
        return str(source.with_name(f"{source.stem}_Remux.ts"))

    ext = settings.container.lower()
    if settings.operation in ("full_sbs_to_half_sbs", "half_tab_to_half_sbs"):
        # Output naming describes the resulting stereoscopic layout only;
        # encoder choice (NVENC/QSV) must not alter the filename.
        output_stem = _output_stem_for_sbs(source)
        return str(source.with_name(f"{output_stem}_Half_SBS.{ext}"))

    raise ProfileError(f"Неизвестная операция для формирования имени файла: {settings.operation}")


def build_conversion_plan(settings: ConversionSettings, source: SourceInfo) -> ConversionPlan:
    """Build a safely-quoted argv list for FFmpeg. No shell is used."""
    ffmpeg = settings.ffmpeg_path or shutil.which("ffmpeg")
    if not ffmpeg:
        raise ProfileError("FFmpeg не найден в PATH.")

    input_path = str(Path(settings.input_path))
    if not Path(input_path).is_file():
        raise ProfileError(f"Исходный файл не найден: {input_path}")

    # The first BAT is a true remux: no video/audio encoding and TS only.
    if settings.operation == "remux_2d_to_ts":
        output = settings.output_path or _default_output_path(settings)
        command = [ffmpeg, "-hide_banner", "-loglevel", "error"]
        if settings.test_start_seconds is not None:
            if settings.test_start_seconds < 0:
                raise ProfileError("Время начала тестового фрагмента не может быть отрицательным.")
            if settings.test_start_seconds:
                command += ["-ss", str(int(settings.test_start_seconds))]
        command += ["-i", input_path, "-c:v", "copy", "-c:a", "copy"]
        if settings.test_duration_seconds is not None:
            if settings.test_duration_seconds <= 0:
                raise ProfileError("Длительность тестового фрагмента должна быть больше нуля.")
            command += ["-t", str(int(settings.test_duration_seconds))]
        command += ["-progress", "pipe:1", "-nostats", "-y", output]
        return ConversionPlan(command, output, settings.operation, "copy", "Video/audio copy; MPEG-TS remux")

    if settings.operation not in ("full_sbs_to_half_sbs", "half_tab_to_half_sbs"):
        raise ProfileError(f"Неизвестная операция: {settings.operation}")
    if settings.encoder not in ("nvenc", "qsv"):
        raise ProfileError("Для этих профилей выберите проверенный NVIDIA NVENC или Intel QSV.")
    container = settings.container.lower()
    if container not in ("mp4", "ts"):
        raise ProfileError("Доступны только контейнеры MP4 и MPEG-TS.")
    if settings.audio_mode not in ("ac3", "copy"):
        raise ProfileError("Неизвестный режим аудио. Используйте AC3 448k или Copy.")

    quality_args, default_preset, quality_description = _rate_control_args(settings.encoder, settings.quality, source)
    preset = _validate_preset(settings.encoder, settings.quality.preset or default_preset)
    output = settings.output_path or _default_output_path(settings)

    command = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    is_full_sbs = settings.operation == "full_sbs_to_half_sbs"
    is_half_tab = settings.operation == "half_tab_to_half_sbs"

    scaling_mode = str(settings.scaling_mode or "auto").strip().lower()
    if scaling_mode not in {"auto", "gpu", "cpu"}:
        raise ProfileError("Неизвестный режим масштабирования. Выберите Авто (GPU), GPU или CPU.")

    wants_gpu_scaling = scaling_mode in {"auto", "gpu"}
    use_cuda_scale = (
        wants_gpu_scaling and settings.encoder == "nvenc" and is_full_sbs
        and _ffmpeg_supports_filter(ffmpeg, "scale_cuda")
    )
    use_cuda_tab = (
        wants_gpu_scaling and settings.encoder == "nvenc" and is_half_tab
        and _ffmpeg_supports_filter(ffmpeg, "scale_cuda")
        and _ffmpeg_supports_filter(ffmpeg, "overlay_cuda")
    )
    use_qsv_scale = (
        wants_gpu_scaling and settings.encoder == "qsv" and is_full_sbs
        and _ffmpeg_supports_filter(ffmpeg, "vpp_qsv")
    )
    use_qsv_tab = (
        wants_gpu_scaling and settings.encoder == "qsv" and is_half_tab
        and _ffmpeg_supports_filter(ffmpeg, "vpp_qsv")
        and _ffmpeg_supports_filter(ffmpeg, "hstack_qsv")
    )
    hardware_scaling_available = any((use_cuda_scale, use_cuda_tab, use_qsv_scale, use_qsv_tab))

    if scaling_mode == "gpu" and not hardware_scaling_available:
        if settings.encoder == "nvenc":
            required_filters = "scale_cuda" if is_full_sbs else "scale_cuda и overlay_cuda"
        else:
            required_filters = "vpp_qsv" if is_full_sbs else "vpp_qsv и hstack_qsv"
        raise ProfileError(
            "Принудительное GPU-масштабирование недоступно для выбранной операции. "
            f"В установленной сборке FFmpeg необходимы фильтры: {required_filters}. "
            "Выберите «Авто (GPU)» для автоматического перехода на CPU или «CPU» вручную."
        )

    if use_cuda_scale or use_cuda_tab:
        # Keep NVDEC output in CUDA memory through scaling and composition.
        command += ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
    elif settings.encoder == "nvenc":
        # NVDEC may still decode in hardware; only scaling is forced to CPU.
        command += ["-hwaccel", "nvdec"]
    elif use_qsv_scale or use_qsv_tab:
        # QSV decode -> Intel VPP -> QSV encode; keep frames on QSV surfaces.
        command += ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"]
    # CPU mode (or Auto fallback) keeps frames in system memory for Lanczos.

    if settings.test_start_seconds is not None:
        if settings.test_start_seconds < 0:
            raise ProfileError("Время начала тестового фрагмента не может быть отрицательным.")
        if settings.test_start_seconds:
            command += ["-ss", str(int(settings.test_start_seconds))]
    command += ["-i", input_path]
    if use_cuda_tab:
        command += [
            "-filter_complex", _cuda_half_tab_filter_graph(),
            "-map", "[vout]", "-map", "0:a?", "-map", "0:s?",
        ]
    elif use_qsv_tab:
        command += [
            "-filter_complex", _qsv_half_tab_filter_graph(),
            "-map", "[vout]", "-map", "0:a?", "-map", "0:s?",
        ]
    else:
        command += [
            "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?",
            "-vf", _filter_for(
                settings.operation, settings.encoder,
                use_cuda_scale=use_cuda_scale, use_qsv_scale=use_qsv_scale
            ),
        ]

    if settings.encoder == "nvenc":
        command += ["-c:v", "h264_nvenc", "-preset", preset, "-tune", "hq"]
    else:
        command += ["-c:v", "h264_qsv", "-preset", preset]
    command += quality_args
    command += ["-profile:v", "high", "-aspect", "16:9",
                "-metadata:s:v:0", "stereo_mode=left_right"]
    if settings.encoder == "nvenc":
        # Keep the NVIDIA reference BAT's explicit H.264 level.
        command += ["-level", "4.1"]
    # QSV selects a compatible H.264 level automatically. The verified
    # 1920x1080@60 test negotiated Level 4.2; a forced Level 4.1 can be too
    # restrictive for that frame rate.

    if settings.audio_mode == "ac3":
        command += ["-c:a", "ac3", "-b:a", "448k"]
    else:
        command += ["-c:a", "copy"]

    if container == "mp4":
        command += ["-c:s", "mov_text", "-brand", "mp42", "-movflags", "+faststart"]
    else:
        command += ["-c:s", "copy"]

    if settings.test_duration_seconds is not None:
        if settings.test_duration_seconds <= 0:
            raise ProfileError("Длительность тестового фрагмента должна быть больше нуля.")
        command += ["-t", str(int(settings.test_duration_seconds))]

    command += ["-progress", "pipe:1", "-nostats", "-y", output]
    encoder_name = "NVIDIA NVENC" if settings.encoder == "nvenc" else "Intel Quick Sync Video (QSV)"
    if use_cuda_scale:
        processing_path = "NVIDIA CUDA hardware scaling"
    elif use_cuda_tab:
        processing_path = "NVIDIA CUDA crop + scaling + composition"
    elif use_qsv_scale:
        processing_path = "Intel VPP hardware scaling"
    elif use_qsv_tab:
        processing_path = "Intel VPP crop/scale + hstack_qsv"
    elif scaling_mode == "cpu":
        processing_path = "software Lanczos scaling (user-selected CPU)"
    else:
        processing_path = "software Lanczos fallback (GPU scaling unavailable)"
    return ConversionPlan(
        command, output, settings.operation, encoder_name,
        f"{OPERATION_LABELS[settings.operation]}; {encoder_name}; "
        f"{processing_path}; {quality_description}; {container.upper()}"
    )
