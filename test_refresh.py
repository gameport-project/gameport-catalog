import json
import unittest
import urllib.parse

import refresh


def depot(oslist, public=True, dlc=None, size=1000, download=400):
    d = {"config": {"oslist": oslist}}
    if public:
        d["manifests"] = {"public": {"gid": "7", "size": str(size), "download": str(download)}}
    if dlc:
        d["dlcappid"] = str(dlc)
    return d


def app(kind="game", depots=None, **common):
    return {"common": {"name": "A game", "type": kind, **common}, "depots": depots or {}}


class DemoIdsTest(unittest.TestCase):
    def setUp(self):
        self.get_json, self.sleep = refresh.get_json, refresh.time.sleep
        refresh.time.sleep = lambda seconds: None

    def tearDown(self):
        refresh.get_json, refresh.time.sleep = self.get_json, self.sleep

    def test_the_demos_are_read_a_page_at_a_time_until_there_is_no_more(self):
        pages = {0: [5, 3, 9], 3: [7, 3, 11], 6: [20]}
        asked = []

        def fake(url):
            query = json.loads(urllib.parse.unquote(url.split("input_json=")[1]))["query"]
            asked.append(query["start"])
            self.assertTrue(query["filters"]["type_filters"]["include_demos"])
            self.assertFalse(query["filters"]["type_filters"]["include_games"])
            return {"response": {"metadata": {"total_matching_records": 7}, "ids": [{"appid": a} for a in pages[query["start"]]]}}

        refresh.get_json = fake
        self.assertEqual([3, 5, 7, 9, 11, 20], refresh.demo_ids())
        self.assertEqual([0, 3, 6], asked)

    def test_an_empty_page_ends_the_list_even_when_the_total_says_more(self):
        refresh.get_json = lambda url: {"response": {"metadata": {"total_matching_records": 50}, "ids": []}}
        self.assertEqual([], refresh.demo_ids())

    def test_a_list_that_cannot_be_read_stops_the_run(self):
        def broken(url):
            raise OSError("down")
        refresh.get_json = broken
        with self.assertRaises(OSError):
            refresh.demo_ids()


class ClassifyTest(unittest.TestCase):
    def test_a_game_with_a_published_android_depot_is_ready(self):
        entry = refresh.classify(5, app(depots={"11": depot("android")}))
        self.assertEqual("ready", entry["android"])
        self.assertTrue(entry["installable"])
        self.assertEqual(1000, entry["installBytes"])
        self.assertEqual(400, entry["downloadBytes"])

    def test_an_app_without_an_android_depot_is_not_listed(self):
        self.assertIsNone(refresh.classify(5, app(depots={"11": depot("windows"), "12": depot("linux")})))
        self.assertIsNone(refresh.classify(5, app()))

    def test_a_depot_with_nothing_published_is_a_future_version(self):
        entry = refresh.classify(5, app(depots={"11": depot("android", public=False)}))
        self.assertEqual("unreleased", entry["android"])
        self.assertFalse(entry["installable"])
        self.assertEqual(0, entry["installBytes"])

    def test_depots_of_an_add_on_alone_are_not_enough(self):
        entry = refresh.classify(5, app(depots={"11": depot("android", dlc=99)}))
        self.assertEqual("unreleased", entry["android"])

    def test_only_games_demos_betas_and_applications_are_listed(self):
        for kind in ("game", "demo", "beta", "application"):
            self.assertIsNotNone(refresh.classify(5, app(kind, {"11": depot("android")})))
        for kind in ("dlc", "tool", "video", "music"):
            self.assertIsNone(refresh.classify(5, app(kind, {"11": depot("android")})))

    def channels_app(self, manifests, dlc_manifests=None, branches=None):
        """An app with an Android depot of the game on the given channels, and the words Steam gives for the channels."""
        depots = {"11": {"config": {"oslist": "android"}, "manifests": {name: {"gid": "7", "size": "1000", "download": "400"} for name in manifests}}}
        if dlc_manifests:
            depots["12"] = {"config": {"oslist": "android"}, "dlcappid": "99", "manifests": {name: {"gid": "8", "size": "5", "download": "5"} for name in dlc_manifests}}
        depots["branches"] = branches or {}
        return app(depots=depots)

    def test_the_test_channels_that_hold_an_android_build_are_listed_with_their_day(self):
        entry = refresh.classify(5, self.channels_app(["public", "beta", "alpha"], branches={"beta": {"timeupdated": "1790000000"}, "alpha": {"timeupdated": "1780000000"}}))
        self.assertEqual([{"name": "alpha", "updated": refresh.day(1780000000)}, {"name": "beta", "updated": refresh.day(1790000000)}], entry["channels"])
        self.assertEqual("ready", entry["android"])
        self.assertFalse(entry["channelOnly"])

    def test_a_channel_that_asks_a_password_is_not_listed(self):
        entry = refresh.classify(5, self.channels_app(["public", "beta", "private"], branches={"private": {"pwdrequired": "1"}, "beta": {"pwdrequired": "0"}}))
        self.assertEqual(["beta"], [c["name"] for c in entry["channels"]])

    def test_a_game_with_nothing_on_the_normal_channel_but_a_test_channel_is_channel_only(self):
        entry = refresh.classify(5, self.channels_app(["beta"], branches={"beta": {"timeupdated": "1790000000"}}))
        self.assertEqual("unreleased", entry["android"])
        self.assertFalse(entry["installable"])
        self.assertTrue(entry["channelOnly"])
        self.assertEqual(["beta"], [c["name"] for c in entry["channels"]])

    def test_a_channel_that_only_holds_an_add_on_does_not_count(self):
        entry = refresh.classify(5, self.channels_app(["public"], dlc_manifests=["beta"]))
        self.assertEqual([], entry["channels"])
        self.assertFalse(entry["channelOnly"])

    def test_a_game_with_a_published_android_depot_and_no_other_channel_has_none(self):
        entry = refresh.classify(5, app(depots={"11": depot("android")}))
        self.assertEqual([], entry["channels"])
        self.assertFalse(entry["channelOnly"])

    def test_the_os_list_may_name_several_systems(self):
        self.assertIsNotNone(refresh.classify(5, app(depots={"11": depot("linux, android")})))

    def test_vr_comes_from_the_categories_or_the_tag(self):
        self.assertTrue(refresh.classify(5, app(depots={"11": depot("android")}, category={"category_53": "1"}))["vr"])
        self.assertTrue(refresh.classify(5, app(depots={"11": depot("android")}, store_tags={"0": "21978"}))["vr"])
        self.assertFalse(refresh.classify(5, app(depots={"11": depot("android")}))["vr"])

    def test_images_are_complete_links_with_the_names_steam_publishes(self):
        entry = refresh.classify(5, app(depots={"11": depot("android")}, library_assets_full={
            "library_capsule": {"image": {"english": "abc/library_capsule.jpg"}},
            "library_hero": {"image": {"english": "def/library_hero.jpg"}},
        }, header_image={"english": "ghi/header.jpg"}))
        self.assertEqual(f"{refresh.IMAGES}/5/abc/library_capsule.jpg", entry["cover"])
        self.assertEqual(f"{refresh.IMAGES}/5/def/library_hero.jpg", entry["banner"])
        self.assertEqual(f"{refresh.IMAGES}/5/ghi/header.jpg", entry["header"])
        self.assertEqual("https://store.steampowered.com/app/5", entry["store"])

    def test_vr_only_is_told_from_vr_supported(self):
        only = refresh.classify(5, app(depots={"11": depot("android")}, category={"category_54": "1"}))
        both = refresh.classify(5, app(depots={"11": depot("android")}, category={"category_53": "1"}))
        tag = refresh.classify(5, app(depots={"11": depot("android")}, store_tags={"0": "21978"}))
        flat = refresh.classify(5, app(depots={"11": depot("android")}))
        self.assertEqual(["only", "supported", "supported", "none"], [e["vrMode"] for e in (only, both, tag, flat)])
        self.assertEqual([True, True, True, False], [e["vr"] for e in (only, both, tag, flat)])

    def test_the_facts_a_store_shows(self):
        entry = refresh.classify(5, app(
            depots={"11": depot("android")},
            associations={"0": {"type": "developer", "name": "Dev"}, "1": {"type": "publisher", "name": "Pub"}, "2": {"type": "franchise", "name": "F"}},
            review_percentage="94", metacritic_score="81", controller_support="full", oslist="windows,android",
            category={"category_2": "1", "category_38": "1"},
            supported_languages={"english": {"supported": "true"}, "french": {"supported": "true", "full_audio": "true"}, "german": {"subtitles": "true"}},
            steam_frame_compatibility={"category": "3"}, steam_deck_compatibility={"category": "2"},
        ))
        self.assertEqual(["Dev"], entry["developer"])
        self.assertEqual(["Pub"], entry["publisher"])
        self.assertEqual(94, entry["reviewPercent"])
        self.assertEqual(81, entry["metacritic"])
        self.assertEqual("full", entry["controller"])
        self.assertEqual(["windows", "android"], entry["platforms"])
        self.assertEqual(["single", "coop"], entry["modes"])
        self.assertEqual(["en", "fr"], entry["languages"])
        self.assertEqual(["fr"], entry["audio"])
        self.assertEqual(["de"], entry["subtitles"])
        self.assertEqual("verified", entry["frame"])
        self.assertEqual("playable", entry["deck"])

    def test_what_steam_does_not_say_is_null_or_empty(self):
        entry = refresh.classify(5, app(depots={"11": depot("android")}))
        for key in ("reviewPercent", "metacritic", "controller", "frame", "deck", "capsule", "runtime", "release"):
            self.assertIsNone(entry[key], key)
        for key in ("developer", "publisher", "modes", "languages", "audio", "subtitles", "platforms", "tags"):
            self.assertEqual([], entry[key], key)

    def test_the_old_file_names_are_the_default_when_steam_names_none(self):
        entry = refresh.classify(5, app(depots={"11": depot("android")}))
        self.assertTrue(entry["cover"].endswith("/5/library_600x900.jpg"))
        self.assertTrue(entry["banner"].endswith("/5/library_hero.jpg"))
        self.assertTrue(entry["header"].endswith("/5/header.jpg"))

    def test_the_runtime_valve_recommends_is_found_wherever_it_is_kept(self):
        data = app(depots={"11": depot("android")})
        data["config"] = {"steamframe": {"recommended_runtime": "lepton-stable"}}
        self.assertEqual("lepton-stable", refresh.classify(5, data)["runtime"])

    def test_the_release_is_a_date(self):
        entry = refresh.classify(5, app(depots={"11": depot("android")}, steam_release_date="1507822440"))
        self.assertEqual("2017-10-12", entry["release"])

    def test_a_demo_points_to_its_game(self):
        self.assertEqual(9, refresh.classify(5, app("demo", {"11": depot("android")}, parent="9"))["parent"])
        self.assertIsNone(refresh.classify(5, app("game", {"11": depot("android")}, parent="9"))["parent"])


class TrackTest(unittest.TestCase):
    def entry(self, appid, status="ready"):
        return refresh.classify(appid, app(depots={"11": depot("android", public=status == "ready")}))

    def test_without_a_file_nothing_is_known_of_the_past(self):
        entries = refresh.track([self.entry(1)], None, "2026-10-10")
        self.assertIsNone(entries[0]["androidSince"])

    def test_an_app_that_arrives_gets_the_day_of_its_arrival(self):
        entries = refresh.track([self.entry(2)], {1: {"appid": 1, "android": "ready", "androidSince": None}}, "2026-10-10")
        self.assertEqual("2026-10-10", entries[0]["androidSince"])

    def test_an_app_that_was_only_announced_gets_the_day_it_became_installable(self):
        entries = refresh.track([self.entry(1)], {1: {"appid": 1, "android": "unreleased", "androidSince": None}}, "2026-10-10")
        self.assertEqual("2026-10-10", entries[0]["androidSince"])

    def test_an_app_already_installable_keeps_its_date_even_when_empty(self):
        before = {1: {"appid": 1, "android": "ready", "androidSince": "2026-10-03"}, 2: {"appid": 2, "android": "ready", "androidSince": None}}
        entries = refresh.track([self.entry(1), self.entry(2)], before, "2026-10-10")
        self.assertEqual(["2026-10-03", None], [e["androidSince"] for e in entries])

    def test_an_announced_app_has_no_date(self):
        entries = refresh.track([self.entry(1, "unreleased")], {}, "2026-10-10")
        self.assertIsNone(entries[0]["androidSince"])

    def test_the_last_update_of_the_build_is_a_date(self):
        data = app(depots={"11": depot("android"), "branches": {"public": {"buildid": "1", "timeupdated": "1507822440"}}})
        self.assertEqual("2017-10-12", refresh.classify(5, data)["updated"])
        self.assertIsNone(refresh.classify(5, app(depots={"11": depot("android")}))["updated"])


class TagsAndVerdictsTest(unittest.TestCase):
    def test_the_heaviest_tags_come_first_and_are_limited(self):
        tags = {str(i): str(100 + i) for i in range(12)}
        tags["0"], tags["1"] = "21978", "19"
        entry = refresh.classify(5, app(depots={"11": depot("android")}, store_tags=tags))
        self.assertEqual([21978, 19, 102, 103, 104, 105, 106, 107], entry["tags"])

    def test_valve_ratings_are_words(self):
        for number, word in ((1, "unsupported"), (2, "playable"), (3, "verified")):
            entry = refresh.classify(5, app(depots={"11": depot("android")}, steam_frame_compatibility={"category": str(number)}))
            self.assertEqual(word, entry["frame"])
        entry = refresh.classify(5, app(depots={"11": depot("android")}, steam_deck_compatibility={"category": "0"}))
        self.assertIsNone(entry["deck"])


class TextsTest(unittest.TestCase):
    def setUp(self):
        a = refresh.classify(20, app(depots={"11": depot("android")}, store_tags={"0": "19", "1": "21978"}))
        b = refresh.classify(10, app(depots={"11": depot("android")}, store_tags={"0": "21"}))
        self.apps = [a, b]

    def test_a_language_file_holds_the_tags_the_catalog_uses_and_the_summaries(self):
        items = {20: {"basic_info": {"short_description": "Vingt"}}, 10: {"basic_info": {"short_description": "Dix"}}}
        texts = refresh.language_texts(self.apps, items, {19: "Action", 21: "Aventure", 21978: "VR", 99: "Unused"}, "fr")
        self.assertEqual({"19": "Action", "21": "Aventure", "21978": "VR"}, texts["tags"])
        self.assertEqual({"10": "Dix", "20": "Vingt"}, texts["summaries"])
        self.assertEqual(["10", "20"], list(texts["summaries"]))

    def test_an_app_without_a_text_has_no_summary(self):
        texts = refresh.language_texts(self.apps, {20: {"basic_info": {"short_description": "Vingt"}}}, {}, "fr")
        self.assertEqual({"20": "Vingt"}, texts["summaries"])

    def test_the_text_file_is_valid_json_one_entry_a_line(self):
        texts = {"lang": "fr", "tags": {"19": "Action"}, "summaries": {"10": "Dix \"cités\"", "20": "Vingt"}}
        text = refresh.render_texts(texts)
        self.assertEqual(texts, {k: v for k, v in json.loads(text).items() if k != "v"})
        self.assertIn('\n"10":"Dix \\"cités\\"",\n', text)
        self.assertIn('\n"20":"Vingt"\n', text)

    def test_a_file_is_not_rewritten_when_nothing_changed(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "text", "fr.json")
            self.assertTrue(refresh.write_if_changed(path, "a\n"))
            self.assertFalse(refresh.write_if_changed(path, "a\n"))
            self.assertTrue(refresh.write_if_changed(path, "b\n"))

    def test_review_counts_come_from_the_store(self):
        refresh.add_store_facts(self.apps, {20: {"reviews": {"summary_filtered": {"review_count": 2949}}}})
        self.assertEqual({20: 2949, 10: None}, {a["appid"]: a["reviewCount"] for a in self.apps})

    def test_the_store_is_asked_by_batches_with_a_pause(self):
        calls, pauses = [], []
        original = (refresh.get_json, refresh.time.sleep)
        refresh.get_json = lambda url: calls.append(url) or {"response": {"store_items": [{"appid": 1, "success": 1}, {"appid": 2, "success": 2}]}}
        refresh.time.sleep = pauses.append
        try:
            items = refresh.store_items("K", list(range(250)), "french")
        finally:
            refresh.get_json, refresh.time.sleep = original
        self.assertEqual(3, len(calls))
        self.assertEqual([refresh.PAUSE] * 3, pauses)
        self.assertEqual([1], list(items))


class MediaTest(unittest.TestCase):
    HASH_A = "a" * 40
    HASH_B = "b" * 40
    PATH_A = f"{HASH_A}/ss_{HASH_A}"
    PATH_B = f"ss_{HASH_B}"

    def item(self):
        return {
            "screenshots": {"all_ages_screenshots": [
                {"filename": f"steam/apps/10/{self.HASH_A}/ss_{self.HASH_A}.jpg?t=1", "ordinal": 1},
                {"filename": f"steam/apps/10/ss_{self.HASH_B}.jpg?t=1", "ordinal": 2},
                {"filename": f"steam/apps/10/{self.HASH_A}/ss_{self.HASH_A}.jpg?t=2", "ordinal": 3},
            ]},
            "trailers": {"highlights": [
                {"trailer_name": "Trailer", "all_ages": True, "screenshot_medium": "9/abc/movie_600x337.jpg",
                 "microtrailer": [{"filename": "10/1/x/microtrailer.webm", "type": "video/webm"}, {"filename": "10/1/x/microtrailer.mp4", "type": "video/mp4"}],
                 "adaptive_trailers": [{"cdn_path": "10/1/x/dash_h264.mpd", "encoding": "dash_h264"}, {"cdn_path": "10/1/x/hls_264_master.m3u8", "encoding": "hls_h264"}]},
            ]},
        }

    def test_a_screenshot_is_its_path_and_a_trailer_the_paths_the_front_needs(self):
        entry = refresh.media_entry(self.item())
        # One in a folder of its own, one directly under the app (the older apps): both kept as they are, without the extension.
        self.assertEqual([self.PATH_A, self.PATH_B], entry["s"])
        self.assertEqual([{"n": "Trailer", "p": "9/abc/movie_600x337.jpg", "h": "10/1/x/hls_264_master.m3u8", "m": "10/1/x/microtrailer.mp4"}], entry["t"])

    def test_a_trailer_not_for_all_ages_or_without_a_stream_is_left_out(self):
        item = self.item()
        item["trailers"]["highlights"] = [{"trailer_name": "Mature", "all_ages": False, "microtrailer": [{"filename": "x.mp4", "type": "video/mp4"}]},
                                          {"trailer_name": "Empty", "all_ages": True}]
        self.assertNotIn("t", refresh.media_entry(item))

    def test_an_app_with_nothing_has_no_entry(self):
        self.assertIsNone(refresh.media_entry({}))
        self.assertIsNone(refresh.media_entry({"screenshots": {"all_ages_screenshots": []}, "trailers": {"highlights": []}}))

    def test_at_most_three_trailers(self):
        item = self.item()
        item["trailers"]["highlights"] = item["trailers"]["highlights"] * 5
        self.assertEqual(refresh.MAX_TRAILERS, len(refresh.media_entry(item)["t"]))

    def test_the_file_has_one_app_a_line_in_order_of_id(self):
        text = refresh.render_media({20: {"s": ["b"]}, 3: {"s": ["a"]}})
        self.assertEqual('{"v":1,"count":2,"apps":{\n"3":{"s":["a"]},\n"20":{"s":["b"]}\n}}\n', text)
        self.assertEqual({"3": {"s": ["a"]}, "20": {"s": ["b"]}}, json.loads(text)["apps"])


class RenderTest(unittest.TestCase):
    def entries(self):
        a = refresh.classify(20, app(depots={"11": depot("android")}, name='B "quoted"'))
        b = refresh.classify(10, app(depots={"11": depot("android", public=False)}))
        return [a, b]

    def test_the_file_is_valid_json_sorted_by_app_id_with_counts(self):
        data = json.loads(refresh.render(self.entries()))
        self.assertEqual([10, 20], [e["appid"] for e in data["apps"]])
        self.assertEqual({"v": 1, "count": 2, "ready": 1, "unreleased": 1}, {k: data[k] for k in ("v", "count", "ready", "unreleased")})

    def test_the_same_data_always_gives_the_same_file(self):
        self.assertEqual(refresh.render(self.entries()), refresh.render(list(reversed(self.entries()))))

    def test_one_app_a_line(self):
        self.assertEqual(2 + 2, len(refresh.render(self.entries()).strip().splitlines()))


class KeepRemovedTest(unittest.TestCase):
    """Nothing is ever dropped from the catalog: an app Steam no longer lists stays, with its name and pictures."""

    def entry(self, appid, status="ready", **common):
        return refresh.classify(appid, app(depots={"11": depot("android", public=status == "ready")}, name=f"Game {appid}", **common))

    def previous(self, *entries):
        return {e["appid"]: e for e in entries}

    def test_an_app_that_steam_no_longer_lists_stays_as_it_was_marked_delisted_and_not_installable(self):
        gone = self.entry(5, parent=3)
        gone["androidSince"] = "2026-10-20"
        kept = refresh.keep_removed([self.entry(1)], self.previous(self.entry(1), gone), set(), "2026-10-27")
        self.assertEqual([5], [e["appid"] for e in kept])
        self.assertEqual("Game 5", kept[0]["name"])
        self.assertEqual(gone["cover"], kept[0]["cover"])
        self.assertEqual("2026-10-20", kept[0]["androidSince"])
        self.assertEqual(["2026-10-27", False], [kept[0]["delisted"], kept[0]["installable"]])

    def test_the_day_it_was_first_missing_is_kept_on_the_following_runs(self):
        already = dict(self.entry(5), delisted="2026-10-20", installable=False)
        kept = refresh.keep_removed([], self.previous(already), set(), "2026-10-27")
        self.assertEqual("2026-10-20", kept[0]["delisted"])

    def test_an_app_that_could_not_be_read_is_kept_exactly_as_it_was_without_the_mark(self):
        unread = self.entry(5)
        kept = refresh.keep_removed([], self.previous(unread), {5}, "2026-10-27")
        self.assertEqual(unread, kept[0])
        self.assertNotIn("delisted", kept[0])

    def test_an_app_that_is_found_again_is_not_kept_and_so_loses_the_mark(self):
        back = self.entry(5)
        previous = self.previous(dict(self.entry(5), delisted="2026-10-20", installable=False))
        self.assertEqual([], refresh.keep_removed([back], previous, set(), "2026-10-27"))
        self.assertNotIn("delisted", back)

    def test_an_app_that_goes_and_comes_back_keeps_the_day_its_android_version_arrived(self):
        gone = refresh.keep_removed([], self.previous(dict(self.entry(5), androidSince="2026-10-03")), set(), "2026-10-27")
        entries = refresh.track([self.entry(5)], self.previous(*gone), "2026-11-02")
        self.assertEqual("2026-10-03", entries[0]["androidSince"])

    def test_nothing_is_kept_without_a_file_before(self):
        self.assertEqual([], refresh.keep_removed([self.entry(1)], None, set(), "2026-10-27"))

    def test_the_file_counts_the_live_apps_apart_from_the_ones_kept_as_gone(self):
        gone = dict(self.entry(5), delisted="2026-10-27", installable=False)
        data = json.loads(refresh.render([self.entry(1), self.entry(2, "unreleased"), gone]))
        self.assertEqual([1, 2, 5], [e["appid"] for e in data["apps"]])
        self.assertEqual({"count": 3, "ready": 1, "unreleased": 1, "delisted": 1}, {k: data[k] for k in ("count", "ready", "unreleased", "delisted")})
        plain = json.loads(refresh.render([self.entry(1)]))
        self.assertNotIn("delisted", plain)

    def test_the_text_of_a_kept_app_is_the_one_the_file_had_and_its_tags_still_count(self):
        live = self.entry(20, store_tags={"0": "19"})
        gone = dict(self.entry(10, store_tags={"0": "21"}), delisted="2026-10-27", installable=False)
        texts = refresh.language_texts([live], {20: {"basic_info": {"short_description": "Vingt"}}}, {19: "Action", 21: "Aventure"}, "fr", [gone], {"10": "Dix"})
        self.assertEqual({"10": "Dix", "20": "Vingt"}, texts["summaries"])
        self.assertEqual(["10", "20"], list(texts["summaries"]))
        self.assertEqual({"19": "Action", "21": "Aventure"}, texts["tags"])

    def test_a_kept_app_without_a_text_before_has_none(self):
        gone = dict(self.entry(10), delisted="2026-10-27", installable=False)
        self.assertEqual({}, refresh.language_texts([], {}, {}, "fr", [gone], {})["summaries"])

    def test_the_media_of_a_kept_app_is_the_one_the_file_had(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "media.json")
            open(path, "w").write(refresh.render_media({10: {"s": ["ss_" + "a" * 40]}, 20: {"s": ["ss_" + "b" * 40]}}))
            gone = dict(self.entry(10), delisted="2026-10-27", installable=False)
            refresh.write_media([self.entry(20)], {}, path, [gone])
            data = json.loads(open(path).read())
            self.assertEqual(["10"], list(data["apps"]))
            self.assertEqual({"s": ["ss_" + "a" * 40]}, data["apps"]["10"])


if __name__ == "__main__":
    unittest.main()


class MendArtworkTest(unittest.TestCase):
    def entry(self, appid, parent=None, cover=None, banner=None):
        return {"appid": appid, "parent": parent, "cover": cover or refresh.image(appid, "library_600x900.jpg"), "banner": banner or refresh.image(appid, "library_hero.jpg")}

    def test_a_cover_that_exists_is_left_alone_without_asking_for_the_parent(self):
        asked = []
        entry = self.entry(1, parent=2)
        refresh.mend_artwork([entry], exists=lambda url: True, parents=lambda ids: asked.append(ids) or {})
        self.assertEqual([], asked)
        self.assertTrue(entry["cover"].endswith("/1/library_600x900.jpg"))

    def test_a_cover_Steam_named_is_trusted_and_not_checked(self):
        checked = []
        entry = self.entry(1, cover=refresh.image(1, "abc123/library_capsule.jpg"), banner=refresh.image(1, "def456/library_hero.jpg"))
        self.assertEqual(0, refresh.mend_artwork([entry], exists=lambda url: checked.append(url) or False))
        self.assertEqual([], checked)

    def test_an_older_app_gets_the_other_name_of_its_cover(self):
        entry = self.entry(1)
        has = {refresh.image(1, "portrait.png"), refresh.image(1, "library_hero.jpg")}
        self.assertEqual(1, refresh.mend_artwork([entry], exists=lambda url: url in has))
        self.assertTrue(entry["cover"].endswith("/1/portrait.png"))

    def test_a_demo_without_a_cover_takes_the_cover_of_its_full_game(self):
        entry = self.entry(3748900, parent=3530820)
        parent_common = {"library_assets_full": {"library_capsule": {"image": {"english": "hash/library_600x900.jpg"}}}}
        has = {refresh.image(3530820, "hash/library_600x900.jpg"), refresh.image(3748900, "library_hero.jpg")}
        asked = []
        mended = refresh.mend_artwork([entry], exists=lambda url: url in has, parents=lambda ids: asked.append(ids) or {3530820: {"common": parent_common}})
        self.assertEqual(1, mended)
        self.assertEqual([[3530820]], asked)
        self.assertEqual(refresh.image(3530820, "hash/library_600x900.jpg"), entry["cover"])
        self.assertTrue(entry["banner"].endswith("/3748900/library_hero.jpg"))

    def test_its_own_older_cover_comes_before_the_one_of_its_full_game(self):
        entry = self.entry(5, parent=6)
        has = {refresh.image(5, "portrait.png"), refresh.image(5, "library_hero.jpg")}
        refresh.mend_artwork([entry], exists=lambda url: url in has, parents=lambda ids: self.fail("the parent was not needed"))
        self.assertTrue(entry["cover"].endswith("/5/portrait.png"))

    def test_an_app_with_no_cover_anywhere_keeps_what_it_has(self):
        entry = self.entry(7, parent=8)
        before = dict(entry)
        refresh.mend_artwork([entry], exists=lambda url: False, parents=lambda ids: {8: {"common": {}}})
        self.assertEqual(before, entry)

    def test_an_app_without_a_parent_and_without_a_file_keeps_what_it_has_and_asks_nobody(self):
        entry = self.entry(9)
        before = dict(entry)
        refresh.mend_artwork([entry], exists=lambda url: False, parents=lambda ids: self.fail("no parent"))
        self.assertEqual(before, entry)

    def test_the_banner_of_a_demo_comes_from_its_full_game_when_it_has_none(self):
        entry = self.entry(10, parent=11)
        parent_common = {"library_assets_full": {"library_hero": {"image": {"english": "h/library_hero.jpg"}}}}
        has = {refresh.image(11, "h/library_hero.jpg"), refresh.image(10, "portrait.png")}
        refresh.mend_artwork([entry], exists=lambda url: url in has, parents=lambda ids: {11: {"common": parent_common}})
        self.assertEqual(refresh.image(11, "h/library_hero.jpg"), entry["banner"])


class ScanTimeoutTest(unittest.TestCase):
    """A read of Steam that takes too long is tried again, then counted as lost, and never stops the run."""

    def _scan(self, answers, ids, lost_ids=None):
        from gevent import Timeout

        class Fake:
            connected = True
            calls = 0

            def anonymous_login(self):
                return 1

            def get_product_info(self, apps, timeout):
                Fake.calls += 1
                answer = answers(Fake.calls, apps)
                if answer is Timeout:
                    raise Timeout(timeout)
                return answer

            def logout(self):
                pass

        original = refresh.SteamClient
        refresh.SteamClient = Fake
        try:
            return refresh.scan(ids, lost_ids), Fake.calls
        finally:
            refresh.SteamClient = original

    def test_a_read_that_times_out_once_is_tried_again(self):
        from gevent import Timeout
        (found, lost, _), calls = self._scan(lambda n, apps: Timeout if n == 1 else {"apps": {}}, [10, 20])
        self.assertEqual(0, lost)
        self.assertEqual(2, calls)

    def test_a_read_that_always_times_out_is_counted_as_lost_and_the_run_goes_on(self):
        from gevent import Timeout
        ids = list(range(1, refresh.CHUNK * 2 + 1))
        # The first chunk never answers; the second does.
        (found, lost, _), _ = self._scan(lambda n, apps: Timeout if apps[0] == 1 else {"apps": {}}, ids)
        self.assertEqual(refresh.CHUNK, lost)

    def test_the_ids_that_could_not_be_read_are_named_so_that_an_app_among_them_is_not_taken_for_gone(self):
        from gevent import Timeout
        ids = list(range(1, refresh.CHUNK * 2 + 1))
        lost_ids = set()
        (_, lost, _), _ = self._scan(lambda n, apps: Timeout if apps[0] == 1 else {"apps": {}}, ids, lost_ids)
        self.assertEqual(set(range(1, refresh.CHUNK + 1)), lost_ids)
        self.assertEqual(lost, len(lost_ids))
