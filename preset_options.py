"""Encoder preset lists and UI defaults for the 3D Converter."""
from __future__ import annotations

from typing import Optional

NVENC_PRESETS = ("p1", "p2", "p3", "p4", "p5", "p6", "p7")
QSV_PRESETS = ("veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")


def preset_options_for_encoder(encoder: Optional[str]) -> tuple[str, ...]:
    """Return valid preset names for the selected encoder."""
    if encoder == "nvenc":
        return NVENC_PRESETS
    if encoder == "qsv":
        return QSV_PRESETS
    # The application currently requires NVENC or QSV for 3D encoding.
    # Keep a neutral visible value if no supported hardware encoder was found.
    return ("slower",)


def recommended_preset(
    encoder: Optional[str],
    *,
    quality_mode: str = "auto",
    height: int = 0,
    bitrate_bps: int = 0,
) -> str:
    """Return the default preset matching the existing BAT/profile logic.

    NVENC Auto Magic uses p6 for high-resolution/high-bitrate sources and p5
    for standard HD/Full HD. Its other quality modes default to p5.
    QSV profiles use slower for all currently supported quality modes.
    """
    if encoder == "nvenc":
        high_source = int(height or 0) > 1080 or int(bitrate_bps or 0) // 1_000_000 >= 20
        if quality_mode == "auto" and high_source:
            return "p6"
        return "p5"
    return "slower"
