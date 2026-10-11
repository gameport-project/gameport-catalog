import json
import unittest

import prices

U = prices.UNAVAILABLE


def item(final=None, initial=None, free=False, end=None):
    out = {"success": 1}
    if free:
        out["is_free"] = True
    option = {}
    if final is not None:
        option["final_price_in_cents"] = str(final)
    if initial is not None:
        option["original_price_in_cents"] = str(initial)
    if end is not None:
        option["active_discounts"] = [{"discount_end_date": end}]
    out["best_purchase_option"] = option
    return out


class ReadingTest(unittest.TestCase):
    def test_the_price_and_the_one_before_a_discount(self):
        self.assertEqual((1499, 1999, None), prices.reading_of(item(1499, 1999)))

    def test_a_price_without_discount_is_its_own_initial_price(self):
        self.assertEqual((1999, 1999, None), prices.reading_of(item(1999)))

    def test_the_end_of_a_discount_is_read(self):
        self.assertEqual((599, 1499, 1792688400), prices.reading_of(item(599, 1499, end=1792688400)))

    def test_a_free_app_costs_nothing(self):
        self.assertEqual((0, 0, None), prices.reading_of(item(free=True)))

    def test_an_app_the_store_answers_for_but_does_not_sell_has_no_price(self):
        self.assertEqual(U, prices.reading_of(item()))

    def test_an_app_the_answer_says_nothing_of_is_unknown(self):
        self.assertIsNone(prices.reading_of(None))


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.history = {}
        self.pending = {}

    def rec(self, reading, day, app=5, country="us"):
        return prices.record(self.history, self.pending, app, country, reading, day)

    def test_the_first_price_is_kept(self):
        self.assertTrue(self.rec((1999, 1999, None), "2026-10-10"))
        self.assertEqual({"5": {"us": [["2026-10-10", 1999, 1999]]}}, self.history)

    def test_the_same_price_adds_nothing(self):
        self.rec((1999, 1999, None), "2026-10-10")
        self.assertFalse(self.rec((1999, 1999, None), "2026-10-11"))
        self.assertEqual(1, len(self.history["5"]["us"]))

    def test_a_change_adds_a_line_with_its_day(self):
        self.rec((1999, 1999, None), "2026-10-10")
        self.assertTrue(self.rec((999, 1999, 1792688400), "2026-10-12"))
        self.assertEqual(["2026-10-12", 999, 1999, 1792688400], self.history["5"]["us"][-1])

    def test_the_end_of_the_same_discount_is_refreshed_not_added(self):
        self.rec((999, 1999, 100), "2026-10-10")
        self.assertFalse(self.rec((999, 1999, 200), "2026-10-11"))
        self.assertEqual([["2026-10-10", 999, 1999, 200]], self.history["5"]["us"])

    def test_a_return_to_the_old_price_is_a_change_too(self):
        self.rec((1999, 1999, None), "2026-10-10")
        self.rec((999, 1999, None), "2026-10-12")
        self.assertTrue(self.rec((1999, 1999, None), "2026-10-20"))

    def test_the_countries_have_their_own_history(self):
        self.rec((1999, 1999, None), "2026-10-10", country="us")
        self.rec((1899, 1899, None), "2026-10-10", country="fr")
        self.assertEqual(["us", "fr"], list(self.history["5"]))

    def test_an_unknown_reading_changes_nothing(self):
        self.assertFalse(self.rec(None, "2026-10-10"))
        self.assertEqual({}, self.history)

    def test_an_app_never_sold_in_a_country_gets_no_line(self):
        self.assertFalse(self.rec(U, "2026-10-10"))
        self.assertEqual({}, self.history)

    def test_an_app_that_stops_being_sold_waits_for_a_second_reading(self):
        self.rec((1999, 1999, None), "2026-10-10")
        self.assertFalse(self.rec(U, "2026-10-11"))
        self.assertEqual(1, len(self.history["5"]["us"]))
        self.assertTrue(self.rec(U, "2026-10-11"))
        self.assertEqual(["2026-10-11", None, None], self.history["5"]["us"][-1])

    def test_the_first_reading_of_a_glitch_is_forgotten_when_the_price_is_back(self):
        self.rec((1999, 1999, None), "2026-10-10")
        self.rec(U, "2026-10-11")
        self.assertFalse(self.rec((1999, 1999, None), "2026-10-11"))
        self.assertEqual({}, self.pending)
        self.assertEqual(1, len(self.history["5"]["us"]))

    def test_a_paid_app_suddenly_free_waits_for_a_second_reading(self):
        self.rec((1999, 1999, None), "2026-10-10")
        self.assertFalse(self.rec((0, 0, None), "2026-10-11"))
        self.assertTrue(self.rec((0, 0, None), "2026-10-12"))
        # It goes in with the day it was first seen.
        self.assertEqual(["2026-10-11", 0, 0], self.history["5"]["us"][-1])

    def test_an_app_that_is_sold_again_goes_in_at_once(self):
        self.rec((1999, 1999, None), "2026-10-10")
        self.rec(U, "2026-10-11")
        self.rec(U, "2026-10-11")
        self.assertTrue(self.rec((1999, 1999, None), "2026-10-20"))


class StoreTest(unittest.TestCase):
    history = {
        "20": {"us": [["2026-10-10", 500, 500]], "fr": [["2026-10-10", 450, 600, 1792688400]]},
        "3": {"fr": [["2026-10-10", 700, 900], ["2026-10-12", None, None]]},
    }

    def test_the_file_of_an_app_reads_back(self):
        self.assertEqual({"v": 1, "appid": 20, "lines": self.history["20"]}, json.loads(prices.render_history("20", self.history["20"])))

    def test_the_file_of_a_country_has_the_price_now_of_each_app_and_leaves_out_the_ones_not_sold(self):
        now = json.loads(prices.render_now("fr", self.history))
        self.assertEqual({"20": [450, 600, 1792688400]}, now["prices"])
        self.assertEqual("EUR", now["currency"])

    def test_the_index_names_the_countries_and_the_day_of_the_last_change(self):
        index = json.loads(prices.render_index("2026-10-11"))
        self.assertEqual("2026-10-11", index["updated"])
        self.assertEqual(prices.COUNTRIES, index["currencies"])
        self.assertEqual(1, index["v"])

    def test_what_is_written_reads_back_and_a_second_write_changes_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            first = prices.write_store(self.history, "2026-10-11", root)
            self.assertTrue(first)
            self.assertEqual(self.history, prices.load(root))
            self.assertEqual([], prices.write_store(self.history, "2026-10-12", root))
            # With nothing changed, even the index keeps its day.
            self.assertEqual("2026-10-11", json.load(open(f"{root}/index.json"))["updated"])

    def test_only_the_files_that_changed_are_written_again(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            prices.write_store(self.history, "2026-10-11", root)
            changed = {**self.history, "20": {**self.history["20"], "us": [["2026-10-10", 500, 500], ["2026-10-13", 400, 500]]}}
            files = prices.write_store(changed, "2026-10-13", root)
            self.assertIn(f"{root}/history/20.json", files)
            self.assertIn(f"{root}/now/us.json", files)
            self.assertNotIn(f"{root}/history/3.json", files)
            self.assertEqual("2026-10-13", json.load(open(f"{root}/index.json"))["updated"])


if __name__ == "__main__":
    unittest.main()
