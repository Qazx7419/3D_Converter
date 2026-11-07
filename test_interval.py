"""Parsing for user-friendly test-fragment time ranges."""
from __future__ import annotations

import re
from typing import Optional


class TestIntervalError(ValueError):
    """Raised when the requested test-fragment interval is invalid."""


def _parse_endpoint(value: str) -> int:
    value = value.strip()
    if not value:
        raise TestIntervalError("Не указано время начала или окончания тестового фрагмента.")

    if ":" in value:
        parts = value.split(":")
        if len(parts) == 2:
            # MM:SS
            if not all(part.isdigit() for part in parts):
                raise TestIntervalError(f"Неверный формат времени: «{value}».")
            minutes, seconds = map(int, parts)
            if seconds > 59:
                raise TestIntervalError("В формате ММ:СС секунды должны быть от 00 до 59.")
            return minutes * 60 + seconds
        if len(parts) == 3:
            # HH:MM:SS
            if not all(part.isdigit() for part in parts):
                raise TestIntervalError(f"Неверный формат времени: «{value}».")
            hours, minutes, seconds = map(int, parts)
            if minutes > 59 or seconds > 59:
                raise TestIntervalError("В формате ЧЧ:ММ:СС минуты и секунды должны быть от 00 до 59.")
            return hours * 3600 + minutes * 60 + seconds
        raise TestIntervalError("Используйте формат ЧЧ:ММ:СС или ММ:СС.")

    try:
        minutes = float(value.replace(",", "."))
    except ValueError as error:
        raise TestIntervalError(
            "Введите число минут (например 25), диапазон (10 - 125) "
            "или время (0:23:53 - 1:15:01)."
        ) from error
    if minutes < 0:
        raise TestIntervalError("Количество минут не может быть отрицательным.")
    return int(round(minutes * 60))


def parse_test_interval(
    raw_value: str,
    *,
    default_duration_seconds: int = 120,
    media_duration_seconds: Optional[float] = None,
) -> tuple[int, int]:
    """Return (start_seconds, duration_seconds).

    Supported values:
      - ``25``: first 25 minutes of the source;
      - ``10 - 125``: interval from minute 10 to minute 125;
      - ``0:23:53 - 1:15:01``: interval between timestamps;
      - empty: first ``default_duration_seconds`` seconds.
    """
    value = (raw_value or "").strip()
    if not value:
        start_seconds, duration_seconds = 0, int(default_duration_seconds)
    else:
        range_match = re.fullmatch(r"\s*(.+?)\s*[-–—]\s*(.+?)\s*", value)
        if range_match:
            start_seconds = _parse_endpoint(range_match.group(1))
            end_seconds = _parse_endpoint(range_match.group(2))
            if end_seconds <= start_seconds:
                raise TestIntervalError("Время окончания должно быть позже времени начала.")
            duration_seconds = end_seconds - start_seconds
        else:
            start_seconds = 0
            duration_seconds = _parse_endpoint(value)

    if duration_seconds <= 0:
        raise TestIntervalError("Длительность тестового фрагмента должна быть больше нуля.")

    if media_duration_seconds is not None and media_duration_seconds > 0:
        end_seconds = start_seconds + duration_seconds
        if start_seconds >= media_duration_seconds:
            raise TestIntervalError("Начало тестового фрагмента находится за пределами видео.")
        if end_seconds > media_duration_seconds + 1:
            raise TestIntervalError(
                f"Конец тестового фрагмента ({_format_seconds(end_seconds)}) "
                f"выходит за длительность видео ({_format_seconds(int(media_duration_seconds))})."
            )
        duration_seconds = min(duration_seconds, max(1, int(media_duration_seconds - start_seconds)))

    return int(start_seconds), int(duration_seconds)


def _format_seconds(value: int) -> str:
    hours, remainder = divmod(int(value), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
