import unittest

from quality_value_help import QualityFieldHelp, apply_quality_field_state


class FakeEntry:
    """Mimics the relevant CTkEntry state rules and records call order."""
    def __init__(self, state="disabled", placeholder="old auto hint", value=""):
        self.state = state
        self.placeholder = placeholder
        self.value = value
        self.calls = []

    def configure(self, **kwargs):
        self.calls.append(("configure", dict(kwargs)))
        if "state" in kwargs:
            self.state = kwargs["state"]
        if "placeholder_text" in kwargs:
            self.placeholder = kwargs["placeholder_text"]

    def delete(self, start, end):
        self.calls.append(("delete", start, end, self.state))
        if self.state == "disabled":
            raise RuntimeError("cannot delete from a disabled entry")
        self.value = ""


class QualityEntryStateTests(unittest.TestCase):
    def test_auto_to_manual_enables_before_clearing_and_updates_placeholder(self):
        entry = FakeEntry(state="disabled", placeholder="Автоматический подбор — ввод не требуется")
        info = QualityFieldHelp("Например: 12 Mbps", True, "VBR help")

        apply_quality_field_state(entry, info, clear=True)

        self.assertEqual(entry.value, "")
        self.assertEqual(entry.placeholder, "Например: 12 Mbps")
        self.assertEqual(entry.state, "normal")
        self.assertEqual(entry.calls[0], ("configure", {"state": "normal"}))
        self.assertEqual(entry.calls[1], ("delete", 0, "end", "normal"))

    def test_manual_to_auto_clears_and_disables(self):
        entry = FakeEntry(state="normal", placeholder="Например: 12 Mbps", value="12")
        info = QualityFieldHelp("Автоматический подбор — ввод не требуется", False, "Auto help")

        apply_quality_field_state(entry, info)

        self.assertEqual(entry.value, "")
        self.assertEqual(entry.placeholder, "Автоматический подбор — ввод не требуется")
        self.assertEqual(entry.state, "disabled")

    def test_manual_refresh_preserves_user_value(self):
        entry = FakeEntry(state="normal", placeholder="old", value="14")
        info = QualityFieldHelp("Например: 2 MB/s", True, "VBR MB/s help")

        apply_quality_field_state(entry, info)

        self.assertEqual(entry.value, "14")
        self.assertEqual(entry.placeholder, "Например: 2 MB/s")
        self.assertEqual(entry.state, "normal")


if __name__ == "__main__":
    unittest.main()
