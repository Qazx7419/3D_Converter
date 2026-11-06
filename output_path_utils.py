"""Pure helpers for safe output naming in the 3D Converter UI."""
from __future__ import annotations

import os
from typing import Callable


def next_available_variant(output_path: str, exists: Callable[[str], bool] = os.path.exists) -> str:
    """Return the first unused sibling name: name_2.ext, name_3.ext, etc."""
    folder, filename = os.path.split(output_path)
    stem, extension = os.path.splitext(filename)
    index = 2
    while True:
        candidate = os.path.join(folder, f"{stem}_{index}{extension}")
        if not exists(candidate):
            return candidate
        index += 1


def ensure_required_extension(path: str, required_extension: str) -> str:
    """Keep the chosen container extension consistent with the selected output format."""
    if not required_extension.startswith("."):
        required_extension = "." + required_extension
    base, extension = os.path.splitext(path)
    if extension.lower() == required_extension.lower():
        return path
    if extension:
        return base + required_extension
    return path + required_extension
