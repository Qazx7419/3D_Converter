"""Regression tests for the manual Full SBS operation selection."""
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

# main.py imports CustomTkinter at module import time. Stub only the UI toolkit
# so the operation resolver can be exercised in a headless test environment.
ctk_stub = types.ModuleType("customtkinter")
class _CTkStub:
    pass
ctk_stub.CTk = _CTkStub
ctk_stub.set_appearance_mode = lambda *_args, **_kwargs: None
ctk_stub.set_default_color_theme = lambda *_args, **_kwargs: None
sys.modules.setdefault("customtkinter", ctk_stub)

import main  # noqa: E402


class _Variable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class ResolveOperationTests(unittest.TestCase):
    def make_app_stub(self, selected, detected_type, width=3840, height=1080):
        return SimpleNamespace(
            operation_var=_Variable(selected),
            video_info={"type": detected_type, "width": width, "height": height},
        )

    def test_probable_full_sbs_manual_selection_needs_no_extra_warning(self):
        app = self.make_app_stub("Full SBS → Half SBS", "Full SBS (предположительно)")
        with patch.object(main.messagebox, "askyesno") as ask:
            result = main.App._resolve_operation(app)
        self.assertEqual(result, "full_sbs_to_half_sbs")
        ask.assert_not_called()

    def test_explicit_full_sbs_does_not_need_confirmation(self):
        app = self.make_app_stub("Full SBS → Half SBS", "Full SBS")
        with patch.object(main.messagebox, "askyesno") as ask:
            result = main.App._resolve_operation(app)
        self.assertEqual(result, "full_sbs_to_half_sbs")
        ask.assert_not_called()

    def test_ambiguous_format_is_still_rejected_for_geometry_conversion(self):
        app = self.make_app_stub(
            "Full SBS → Half SBS",
            "Не определено: возможно 2D, Half SBS или Half TAB",
        )
        with self.assertRaises(main.ProfileError):
            main.App._resolve_operation(app)

    def test_half_tab_confirmed_path_is_unchanged(self):
        app = self.make_app_stub("Half TAB → Half SBS", "Half TAB")
        self.assertEqual(main.App._resolve_operation(app), "half_tab_to_half_sbs")

    def test_manual_2d_remux_remains_available_for_ambiguous_source(self):
        app = self.make_app_stub(
            "2D → TS",
            "Не определено: возможно 2D, Half SBS или Half TAB",
        )
        self.assertEqual(main.App._resolve_operation(app), "remux_2d_to_ts")


if __name__ == "__main__":
    unittest.main(verbosity=2)
