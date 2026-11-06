"""Encoder-aware hints and placeholders for the quality value field."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class QualityFieldHelp:
    placeholder: str
    enabled: bool
    tooltip: str


_MODE_KEYS = {
    "Авто (Magic)": "auto",
    "VBR Mbps": "vbr_mbps",
    "VBR MB/s": "vbr_mbytes",
    "Размер файла (МБ)": "target_size",
    "CQ / ICQ": "quality",
    "CBR Mbps": "cbr_mbps",
}


def build_quality_field_help(
    mode_label: str,
    encoder: Optional[str],
    device_name: str,
    *,
    height: int = 0,
    bitrate_bps: int = 0,
    duration_seconds: float = 0.0,
    remux: bool = False,
) -> QualityFieldHelp:
    """Build a mode-specific description matching the command builder semantics."""
    if remux:
        return QualityFieldHelp(
            placeholder="Не используется для 2D → TS",
            enabled=False,
            tooltip=(
                "Операция 2D → TS выполняет перепаковку без перекодирования видео. "
                "Параметр качества и видеокодировщик не используются; потоки копируются как есть."
            ),
        )

    mode = _MODE_KEYS.get(mode_label)
    if mode is None:
        return QualityFieldHelp("Выберите режим качества", False, "Сначала выберите режим качества.")

    encoder_label = {
        "qsv": "Intel Quick Sync Video (QSV)",
        "nvenc": "NVIDIA NVENC",
    }.get(encoder, "Аппаратный кодировщик не выбран")
    device_line = f"Устройство: {device_name}." if device_name else f"Кодировщик: {encoder_label}."
    intro = f"Кодировщик: {encoder_label}.\n{device_line}\n\n"

    source_loaded = bool(height or bitrate_bps or duration_seconds)
    high_source = int(height or 0) > 1080 or int(int(bitrate_bps or 0) / 1_000_000) >= 20
    source_kind = "высокое разрешение/битрейт" if high_source else "стандартный HD/Full HD"

    if mode == "auto":
        if encoder == "qsv":
            chosen_quality = 15 if high_source else 16
            setting = f"global_quality = {chosen_quality}, preset = slower"
            if source_loaded:
                explanation = (
                    f"Для текущего исходника ({source_kind}) Auto Magic выберет {setting}. "
                    "Меньшее global_quality обычно означает более высокое качество и больший файл. "
                    "Вводить значение не нужно."
                )
            else:
                explanation = (
                    "После выбора видео Auto Magic подберёт значение автоматически: "
                    "global_quality = 15 при высоте больше 1080 или битрейте от 20 Mbps, "
                    "иначе global_quality = 16. Пресет QSV — slower. Вводить значение не нужно."
                )
        elif encoder == "nvenc":
            cq, preset = (19, "p6") if high_source else (21, "p5")
            setting = f"CQ = {cq}, preset = {preset}"
            if source_loaded:
                explanation = (
                    f"Для текущего исходника ({source_kind}) Auto Magic выберет {setting}. "
                    "Меньшее CQ обычно даёт более высокое качество и больший файл. "
                    "Вводить значение не нужно."
                )
            else:
                explanation = (
                    "После выбора видео Auto Magic подберёт CQ автоматически: 19 / preset p6 "
                    "при высоте больше 1080 или битрейте от 20 Mbps; иначе CQ 21 / preset p5. "
                    "Вводить значение не нужно."
                )
        else:
            explanation = "Автоматический аппаратный кодировщик не определён. Выберите NVENC или QSV."
        return QualityFieldHelp("Автоматический подбор — ввод не требуется", False, intro + explanation)

    if mode == "vbr_mbps":
        codec_note = (
            "Для QSV дополнительно включается extbrc=1."
            if encoder == "qsv" else "Для NVENC используется режим -rc vbr."
        )
        return QualityFieldHelp(
            "Например: 12 Mbps",
            True,
            intro +
            "Введите целевой средний битрейт ВИДЕО в мегабитах в секунду (Mbps).\n"
            "Пример: 12 означает 12 Mbps = 12 000 kbit/s.\n"
            "В профиле FFmpeg максимальный битрейт задаётся примерно в 1,5 раза выше среднего, "
            "а размер буфера — в 2 раза выше maxrate. " + codec_note +
            "\nЭто не гарантирует одинаковый визуальный результат для разных кодировщиков."
        )

    if mode == "vbr_mbytes":
        codec_note = (
            "Для QSV дополнительно включается extbrc=1."
            if encoder == "qsv" else "Для NVENC используется режим -rc vbr."
        )
        return QualityFieldHelp(
            "Например: 2 MB/s",
            True,
            intro +
            "Введите средний битрейт ВИДЕО в мегабайтах в секунду (MB/s). "
            "Программа переводит его в Mbps по правилу: 1 MB/s = 8 Mbps.\n"
            "Пример: 2 означает 16 Mbps = 16 000 kbit/s.\n"
            "Максимальный битрейт — примерно 1,5× среднего; буфер — 2× maxrate. " + codec_note
        )

    if mode == "target_size":
        formula = "Битрейт видео рассчитывается по формуле: размер (МБ) × 8192 / длительность (сек)."
        if duration_seconds > 0:
            example_mb = 4500
            estimated_kbps = int((example_mb * 8192) / duration_seconds)
            time_note = (
                f"\nДля текущей длительности {duration_seconds:.1f} сек пример 4500 МБ даст "
                f"примерно {estimated_kbps / 1000:.2f} Mbps видеобитрейта."
            )
        else:
            time_note = "\nСначала выберите видео, чтобы программа могла рассчитать битрейт по его длительности."
        return QualityFieldHelp(
            "Например: 4500 МБ",
            True,
            intro +
            "Введите желаемый размер выходного файла в мегабайтах (МБ).\n" + formula +
            "\nВажно: текущая формула рассчитывает битрейт видеопотока. Аудио и служебные данные контейнера "
            "добавляются отдельно, поэтому фактический файл может получиться больше заданного размера." + time_note
        )

    if mode == "quality":
        if encoder == "qsv":
            return QualityFieldHelp(
                "Например: 16 (диапазон 1–51)",
                True,
                intro +
                "Введите значение ICQ/global_quality для Intel QSV от 1 до 51.\n"
                "Пример: 16 — значение, использованное в проверках этого проекта; 15 обычно задаёт более высокое качество, "
                "чем 16, но может увеличить размер файла. В FFmpeg передаётся как -global_quality N.\n"
                "Обычно меньшее значение означает более высокое качество."
            )
        if encoder == "nvenc":
            return QualityFieldHelp(
                "Например: 21 (диапазон 15–28)",
                True,
                intro +
                "Введите CQ для NVIDIA NVENC от 15 до 28.\n"
                "Пример: 21 — стандартное значение ручного режима; 19 задаёт более высокое качество, "
                "чем 21, обычно ценой большего файла. Используется -rc vbr -cq N -b:v 0.\n"
                "Обычно меньшее значение CQ означает более высокое качество."
            )
        return QualityFieldHelp("Сначала выберите NVENC или QSV", False, intro + "Нет доступного аппаратного кодировщика.")

    if mode == "cbr_mbps":
        codec_note = (
            "Для QSV фиксируются b:v, minrate и maxrate на одном значении."
            if encoder == "qsv" else "Для NVENC используется -rc cbr с одинаковыми b:v/minrate/maxrate."
        )
        return QualityFieldHelp(
            "Например: 10 Mbps",
            True,
            intro +
            "Введите постоянный битрейт ВИДЕО в Mbps.\n"
            "Пример: 10 означает 10 000 kbit/s.\n" + codec_note +
            "\nРазмер файла становится более предсказуемым, но сложные сцены не смогут свободно "
            "получать дополнительный битрейт, как в VBR."
        )

    return QualityFieldHelp("Выберите режим качества", False, intro + "Неизвестный режим качества.")


def apply_quality_field_state(entry, help_info: QualityFieldHelp, *, clear: bool = False) -> None:
    """Safely refresh a CTkEntry, including transitions from its disabled Auto state.

    CTkEntry placeholder text can remain visually stale if it is reconfigured while
    disabled. Re-enable it before clearing/replacing the placeholder, then apply
    the requested final state.
    """
    entry.configure(state="normal")
    if clear or not help_info.enabled:
        entry.delete(0, "end")

    # Force CustomTkinter to deactivate any old placeholder and activate the new one.
    entry.configure(placeholder_text="")
    entry.configure(placeholder_text=help_info.placeholder)
    entry.configure(state="normal" if help_info.enabled else "disabled")
