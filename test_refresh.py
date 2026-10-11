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


if __name__ == "__main__":
    unittest.main()
