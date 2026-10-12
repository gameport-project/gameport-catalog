import io
import json
import os
import tempfile
import unittest

from PIL import Image

import announce_new as a


def app(appid, installable=True, **extra):
    return {"appid": appid, "name": f"App {appid}", "type": "game", "vr": True, "installable": installable, "store": f"https://store.steampowered.com/app/{appid}", "cover": f"https://img/{appid}.jpg", **extra}


def count(component):
    """How many components Discord counts in this one: itself, what it holds, and its picture."""
    return 1 + sum(count(c) for c in component.get("components", [])) + (1 if component.get("accessory") else 0)


class NewAppsTest(unittest.TestCase):
    def test_an_app_that_arrives_installable_is_new(self):
        self.assertEqual([2], [x["appid"] for x in a.new_apps([app(1)], [app(1), app(2)])])

    def test_an_announced_app_whose_build_is_published_is_new(self):
        self.assertEqual([1], [x["appid"] for x in a.new_apps([app(1, installable=False)], [app(1)])])

    def test_an_app_already_installable_or_only_announced_is_not_new(self):
        self.assertEqual([], a.new_apps([app(1), app(2, installable=False)], [app(1), app(2, installable=False), app(3, installable=False)]))

    def test_an_app_that_leaves_the_catalog_is_not_new(self):
        self.assertEqual([], a.new_apps([app(1), app(2)], [app(1)]))

    def test_without_a_previous_catalog_nothing_is_announced(self):
        self.assertEqual([], a.new_apps([], [app(1), app(2)]))

    def test_the_newest_app_comes_first(self):
        self.assertEqual([9, 5, 3], [x["appid"] for x in a.new_apps([app(1)], [app(1), app(3), app(9), app(5)])])


class PriceTest(unittest.TestCase):
    def test_a_price_is_written_with_its_symbol(self):
        self.assertEqual("€9.75", a.price_text((975, 975), "€"))
        self.assertEqual("$0.86", a.price_text((86, 86), "$"))

    def test_zero_is_free(self):
        self.assertEqual("Free", a.price_text((0, 0), "€"))

    def test_a_discount_shows_the_old_price_struck_out_and_the_share_off(self):
        self.assertEqual("€5.99 ~~€14.99~~ (-60%)", a.price_text((599, 1499), "€"))

    def test_an_app_without_a_price_has_none(self):
        self.assertIsNone(a.price_text(None, "€"))

    def test_the_prices_of_a_country_are_read_from_its_file(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "fr.json"), "w", encoding="utf-8") as file:
                json.dump({"v": 1, "country": "fr", "currency": "EUR", "prices": {"418650": [599, 1499, 1792688400], "438100": [0, 0], "5": None}}, file)
            symbol, prices = a.read_prices("fr", folder)
        self.assertEqual("€", symbol)
        self.assertEqual({418650: (599, 1499), 438100: (0, 0)}, prices)

    def test_a_currency_without_a_symbol_is_written_with_its_code_and_a_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "pl.json"), "w", encoding="utf-8") as file:
                json.dump({"currency": "PLN", "prices": {"1": [1000, 1000]}}, file)
            self.assertEqual(("PLN ", {1: (1000, 1000)}), a.read_prices("pl", folder))
            self.assertEqual(("", {}), a.read_prices("zz", folder))


class LineTest(unittest.TestCase):
    def test_a_line_has_the_name_as_a_link_the_kind_where_it_is_played_and_the_price(self):
        self.assertEqual("**[App 5](https://store.steampowered.com/app/5)**\nDemo · Flat · Free", a.line(app(5, type="demo", vr=False), (0, 0), "€"))

    def test_without_a_price_the_line_has_none(self):
        self.assertEqual("**[App 5](https://store.steampowered.com/app/5)**\nGame · VR", a.line(app(5)))

    def test_a_name_cannot_break_the_link_or_run_on(self):
        text = a.line(app(5, name="A [b] " + "x" * 300))
        self.assertIn("**[A (b) ", text)
        self.assertLess(len(text), 260)

    def test_a_line_without_a_link_points_to_steam(self):
        self.assertIn("(https://store.steampowered.com/app/8)", a.line({"appid": 8, "name": "X", "type": "application", "vr": False}))


class MessagesTest(unittest.TestCase):
    def test_the_cover_of_an_app_is_on_the_right_of_its_text(self):
        (message,) = a.messages([app(1)])
        container = message["components"][0]
        section = next(c for c in container["components"] if c["type"] == 9)
        self.assertEqual("https://img/1.jpg", section["accessory"]["media"]["url"])
        self.assertEqual(11, section["accessory"]["type"])

    def test_an_app_without_any_picture_is_only_its_text(self):
        (message,) = a.messages([{"appid": 8, "name": "X", "type": "demo", "vr": False}])
        self.assertEqual([], [c for c in message["components"][0]["components"] if c["type"] == 9])
        self.assertTrue(any(c["type"] == 10 and "[X]" in c["content"] for c in message["components"][0]["components"]))

    def test_the_cover_falls_back_on_another_picture_of_the_app(self):
        (message,) = a.messages([app(1, cover=None, header="https://img/h.jpg")])
        section = next(c for c in message["components"][0]["components"] if c["type"] == 9)
        self.assertEqual("https://img/h.jpg", section["accessory"]["media"]["url"])

    def test_the_price_is_in_the_line_of_the_app(self):
        (message,) = a.messages([app(1)], prices={1: (599, 1499)}, symbol="€")
        text = next(c for c in message["components"][0]["components"] if c["type"] == 9)["components"][0]["content"]
        self.assertIn("€5.99 ~~€14.99~~ (-60%)", text)

    def test_one_message_for_nine_apps_and_it_fits_in_what_discord_counts(self):
        bodies = a.messages([app(i) for i in range(1, 10)])
        self.assertEqual(1, len(bodies))
        self.assertLessEqual(sum(count(c) for c in bodies[0]["components"]), 40)

    def test_the_apps_go_nine_by_nine_and_only_the_first_message_notifies(self):
        bodies = a.messages([app(i) for i in range(1, 26)])
        self.assertEqual(3, len(bodies))
        for body in bodies:
            self.assertLessEqual(sum(count(c) for c in body["components"]), 40)
            self.assertTrue(body["flags"] & a.COMPONENTS_V2)
        self.assertEqual([False, True, True], [bool(b["flags"] & a.SILENT) for b in bodies])
        title = bodies[0]["components"][0]["components"][0]["content"]
        self.assertIn("**25 new Android versions on Steam**", title)

    def test_one_app_is_told_in_the_singular(self):
        (message,) = a.messages([app(1)])
        self.assertIn("**1 new Android version on Steam**", message["components"][0]["components"][0]["content"])

    def test_more_than_the_limit_are_cut_and_the_rest_is_told(self):
        bodies = a.messages([app(i) for i in range(1, 46)])
        sections = sum(1 for b in bodies for c in b["components"][0]["components"] if c["type"] == 9)
        self.assertEqual(a.MAX_ANNOUNCED, sections)
        self.assertIn("and 15 more", bodies[-1]["components"][0]["components"][-1]["content"])

    def test_a_role_is_mentioned_once_and_nobody_else_can_be(self):
        bodies = a.messages([app(i) for i in range(1, 13)], role_id="123456789")
        self.assertIn("<@&123456789> ", bodies[0]["components"][0]["components"][0]["content"])
        self.assertEqual({"parse": [], "roles": ["123456789"]}, bodies[0]["allowed_mentions"])
        self.assertEqual({"parse": [], "roles": []}, bodies[1]["allowed_mentions"])
        self.assertNotIn("<@&", json.dumps(bodies[1]))

    def test_without_a_role_no_mention_is_allowed(self):
        (message,) = a.messages([app(1)])
        self.assertEqual({"parse": [], "roles": []}, message["allowed_mentions"])
        self.assertNotIn("<@", json.dumps(message))

    def test_the_flags_are_the_ones_discord_knows(self):
        self.assertEqual(4096, a.SILENT)
        self.assertEqual(32768, a.COMPONENTS_V2)

    def test_a_message_carries_no_content_or_embed_which_the_layout_does_not_allow(self):
        for body in a.messages([app(i) for i in range(1, 12)]):
            self.assertNotIn("content", body)
            self.assertNotIn("embeds", body)


def png(width, height, color):
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "PNG")
    return out.getvalue()


class ThumbnailTest(unittest.TestCase):
    def test_the_square_holds_the_whole_vertical_cover_in_its_middle(self):
        square = Image.open(io.BytesIO(a.thumbnail(png(300, 450, (200, 20, 20))))).convert("RGB")
        self.assertEqual((a.THUMBNAIL, a.THUMBNAIL), square.size)
        self.assertEqual((200, 20, 20), square.getpixel((a.THUMBNAIL // 2, a.THUMBNAIL // 2)))
        # The cover is 2/3 as wide as the square is high: its edge is at 1/6 of the width, and what is beyond is the blurred copy, darker.
        corner = square.getpixel((3, a.THUMBNAIL // 2))
        self.assertNotEqual((200, 20, 20), corner)
        self.assertLess(corner[0], 200)

    def test_a_wide_picture_is_whole_in_the_square_too(self):
        square = Image.open(io.BytesIO(a.thumbnail(png(460, 215, (10, 200, 10))))).convert("RGB")
        self.assertEqual((a.THUMBNAIL, a.THUMBNAIL), square.size)
        self.assertEqual((10, 200, 10), square.getpixel((a.THUMBNAIL // 2, a.THUMBNAIL // 2)))

    def test_the_cover_of_each_app_is_made_with_the_other_pictures_as_a_fallback(self):
        asked = []

        def get(url):
            asked.append(url)
            if "cover" in url:
                raise OSError("404")
            return png(100, 150, (5, 5, 200))
        made = a.thumbnails([app(1, cover="https://img/cover", header="https://img/header"), app(2, cover="https://img/cover2", header=None, capsule=None, banner=None)], get)
        self.assertEqual({1}, set(made))
        self.assertEqual(["https://img/cover", "https://img/header", "https://img/cover2"], asked)

    def test_an_app_with_a_thumbnail_points_at_its_attachment_and_the_others_at_their_cover(self):
        (message,) = a.messages([app(1), app(2)], thumbs={1: b"x"})
        sections = [c for c in message["components"][0]["components"] if c["type"] == 9]
        self.assertEqual(["attachment://thumb-1.png", "https://img/2.jpg"], [c["accessory"]["media"]["url"] for c in sections])

    def test_a_message_carries_only_the_pictures_it_uses(self):
        thumbs = {i: f"png{i}".encode() for i in range(1, 12)}
        bodies = a.messages([app(i) for i in range(1, 12)], thumbs=thumbs)
        self.assertEqual({f"thumb-{i}.png" for i in range(1, 10)}, set(a.files_of(bodies[0], thumbs)))
        self.assertEqual({"thumb-10.png", "thumb-11.png"}, set(a.files_of(bodies[1], thumbs)))

    def test_the_pictures_go_with_the_message_in_one_call(self):
        body, content_type = a.multipart({"content": "hi"}, {"thumb-1.png": b"AAA", "thumb-2.png": b"BBB"})
        boundary = content_type.split("boundary=")[1]
        self.assertIn(b'name="payload_json"', body)
        self.assertIn(b'name="files[0]"; filename="thumb-1.png"', body)
        self.assertIn(b'name="files[1]"; filename="thumb-2.png"', body)
        self.assertTrue(body.index(b"AAA") < body.index(b"BBB"))
        self.assertTrue(body.endswith(f"--{boundary}--\r\n".encode()))


class TrialTest(unittest.TestCase):
    def test_the_newest_apps_by_day_then_by_id_stand_for_the_new_ones(self):
        apps = [app(1, androidSince="2026-10-10"), app(2, androidSince="2026-10-12"), app(3, androidSince="2026-10-12"), app(4, installable=False, androidSince="2026-10-13"), app(5)]
        self.assertEqual([3, 2, 1], [x["appid"] for x in a.newest(apps, 3)])
        self.assertEqual([3], [x["appid"] for x in a.newest(apps, 1)])

    def test_a_trial_is_marked_as_one(self):
        (message,) = a.messages([app(1)], test=True)
        self.assertTrue(message["components"][0]["components"][0]["content"].startswith("**Test** "))
        self.assertEqual({"parse": [], "roles": []}, message["allowed_mentions"])

    def test_an_announcement_is_not_marked_as_a_trial(self):
        self.assertNotIn("**Test**", a.messages([app(1)])[0]["components"][0]["components"][0]["content"])


class ReadTest(unittest.TestCase):
    def test_a_missing_or_broken_catalog_file_is_an_empty_catalog(self):
        self.assertEqual([], a.read("does-not-exist.json"))


if __name__ == "__main__":
    unittest.main()
