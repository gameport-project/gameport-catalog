import unittest

import backfill


def entry(ts, amount, regular=None, currency="EUR"):
    return {"timestamp": ts, "shop": {"id": 61}, "deal": {"price": {"amountInt": amount, "currency": currency}, "regular": {"amountInt": regular or amount, "currency": currency}}}


class LinesTest(unittest.TestCase):
    def test_the_answer_is_turned_oldest_first_with_the_price_before_a_discount(self):
        lines = backfill.lines_of([entry("2026-10-01T21:08:00+02:00", 599, 1499), entry("2024-10-11T02:00:00+02:00", 1499)], "EUR")
        self.assertEqual([["2024-10-11", 1499, 1499], ["2026-10-01", 599, 1499]], lines)

    def test_a_price_that_did_not_change_adds_nothing(self):
        lines = backfill.lines_of([entry("2025-01-01T10:00:00+00:00", 999), entry("2025-02-01T10:00:00+00:00", 999)], "EUR")
        self.assertEqual([["2025-01-01", 999, 999]], lines)

    def test_a_day_that_changed_twice_keeps_its_last_price(self):
        lines = backfill.lines_of([entry("2025-01-01T08:00:00+00:00", 999), entry("2025-01-01T20:00:00+00:00", 499, 999)], "EUR")
        self.assertEqual([["2025-01-01", 499, 999]], lines)

    def test_a_price_in_another_currency_is_ignored(self):
        self.assertEqual([], backfill.lines_of([entry("2025-01-01T10:00:00+00:00", 999, currency="USD")], "EUR"))


class MergeTest(unittest.TestCase):
    def test_the_past_goes_before_our_first_line_and_nothing_of_ours_is_touched(self):
        ours = [["2026-10-10", 599, 1499, 1792688400]]
        past = [["2025-01-01", 1499, 1499], ["2026-10-01", 599, 1499], ["2026-10-10", 599, 1499]]
        merged = backfill.merge(ours, past)
        # The price our first line carries on is the one of the 10-01 line: that day is kept, and the end of the discount stays.
        self.assertEqual([["2025-01-01", 1499, 1499], ["2026-10-01", 599, 1499, 1792688400]], merged)

    def test_a_past_that_ends_on_another_price_is_kept_whole(self):
        ours = [["2026-10-10", 999, 999]]
        past = [["2025-01-01", 1499, 1499]]
        self.assertEqual([["2025-01-01", 1499, 1499], ["2026-10-10", 999, 999]], backfill.merge(ours, past))

    def test_an_app_with_no_line_of_ours_gets_the_past(self):
        self.assertEqual([["2025-01-01", 1, 1]], backfill.merge([], [["2025-01-01", 1, 1]]))

    def test_the_past_after_our_first_day_is_dropped(self):
        ours = [["2026-10-10", 999, 999]]
        self.assertEqual(ours, backfill.merge(ours, [["2026-11-01", 5, 5]]))


class ThrottleTest(unittest.TestCase):
    def test_it_waits_when_the_window_is_full_and_not_before(self):
        now = [0.0]
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            now[0] += seconds

        throttle = backfill.Throttle(calls=3, window=10, clock=lambda: now[0], sleep=sleep)
        for _ in range(3):
            throttle.wait()
            now[0] += 1
        self.assertEqual([], slept)
        throttle.wait()
        # The first call was at 0 and it is 3 now: the window frees up in 7 seconds.
        self.assertEqual([7.0], slept)

    def test_calls_spread_over_more_than_a_window_never_wait(self):
        now = [0.0]
        slept = []
        throttle = backfill.Throttle(calls=2, window=10, clock=lambda: now[0], sleep=slept.append)
        for _ in range(10):
            throttle.wait()
            now[0] += 6
        self.assertEqual([], slept)


class PendingTest(unittest.TestCase):
    def test_the_pairs_not_read_yet_are_listed_by_app_then_country(self):
        marks = {"us": [1], "fr": []}
        self.assertEqual([(1, "fr"), (2, "us"), (2, "fr")], backfill.pending([1, 2], marks, ["us", "fr"]))

    def test_a_new_app_is_pending_everywhere_and_a_done_one_nowhere(self):
        marks = {"us": [1, 2], "fr": [1, 2]}
        self.assertEqual([(3, "us"), (3, "fr")], backfill.pending([1, 2, 3], marks, ["us", "fr"]))

    def test_a_new_country_is_pending_for_every_app(self):
        marks = {"us": [1, 2]}
        self.assertEqual([(1, "ch"), (2, "ch")], backfill.pending([1, 2], marks, ["us", "ch"]))


if __name__ == "__main__":
    unittest.main()
