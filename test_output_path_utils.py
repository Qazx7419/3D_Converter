import unittest
from output_path_utils import ensure_required_extension, next_available_variant


class OutputPathUtilsTests(unittest.TestCase):
    def test_suggests_second_variant_when_free(self):
        path = next_available_variant(r"C:\\Videos\\video_Half_SBS.ts", exists=lambda p: False)
        self.assertEqual(path, r"C:\\Videos\\video_Half_SBS_2.ts")

    def test_skips_existing_variants(self):
        existing = {
            r"C:\\Videos\\video_Half_SBS_2.ts",
            r"C:\\Videos\\video_Half_SBS_3.ts",
        }
        path = next_available_variant(
            r"C:\\Videos\\video_Half_SBS.ts",
            exists=lambda candidate: candidate in existing,
        )
        self.assertEqual(path, r"C:\\Videos\\video_Half_SBS_4.ts")

    def test_keeps_required_extension(self):
        self.assertEqual(
            ensure_required_extension(r"C:\\Videos\\custom.mp4", ".ts"),
            r"C:\\Videos\\custom.ts",
        )

    def test_adds_extension_when_missing(self):
        self.assertEqual(
            ensure_required_extension(r"C:\\Videos\\custom", ".mp4"),
            r"C:\\Videos\\custom.mp4",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
