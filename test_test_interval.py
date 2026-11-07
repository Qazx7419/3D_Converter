import unittest

from test_interval import TestIntervalError, parse_test_interval


class TestIntervalParserTests(unittest.TestCase):
    def test_single_number_is_minutes_from_start(self):
        self.assertEqual(parse_test_interval("25"), (0, 1500))

    def test_minute_range(self):
        self.assertEqual(parse_test_interval("10 - 125"), (600, 6900))

    def test_clock_range(self):
        self.assertEqual(parse_test_interval("0:23:53 - 1:15:01"), (1433, 3068))

    def test_mm_ss_range(self):
        self.assertEqual(parse_test_interval("01:20 - 03:10"), (80, 110))

    def test_default_two_minutes(self):
        self.assertEqual(parse_test_interval(""), (0, 120))

    def test_range_end_must_be_after_start(self):
        with self.assertRaises(TestIntervalError):
            parse_test_interval("125 - 10")

    def test_range_must_fit_video(self):
        with self.assertRaises(TestIntervalError):
            parse_test_interval("10 - 125", media_duration_seconds=3600)

    def test_time_fields_must_be_valid(self):
        with self.assertRaises(TestIntervalError):
            parse_test_interval("0:75:00 - 1:15:00")


if __name__ == "__main__":
    unittest.main()
