#!/usr/bin/env python3
"""Rebuilds v1/catalog.json: the Steam apps that have an Android build.

    STEAM_API_KEY=... python3 refresh.py                  lists the apps with Steam's own API, reads them, writes v1/catalog.json
    python3 refresh.py --ids applist.json --out x.json    reads the apps of a list already fetched

Steam's API lists every game and program but cannot filter on the platform, so each app is read with an anonymous Steam session (no account, no
licence: the product info of public apps is public) and kept when it declares an Android depot:

  ready       a depot tagged Android has a build on the public branch, and one of them is the base game: it can be installed
  unreleased  an Android depot is declared but nothing is published on the public branch yet, or it only holds add-ons: a future Android version

The file is left alone when the read looks wrong (too many apps lost, or far fewer found than before).

Nothing is ever dropped from the catalog. An app that Steam no longer lists with an Android build (a demo taken down, say) stays in the file as it was, with its name, its
pictures, its text and its media, marked `"delisted": "<the day it was first missing>"` and not installable; an app whose read failed this time (a chunk that timed out) stays
exactly as it was, without the mark. One that comes back is read again and loses the mark.

Then, for these apps only, Steam's store API gives the texts: a short description in every language Steam has, and the names of the tags (Action,
Adventure, Puzzle...). They go to v1/text/<language>.json, one file for each language, so the front reads only its own. A language that cannot be read
this time keeps the file it had.
"""
from gevent import monkey

monkey.patch_all()

import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from gevent import Timeout
from gevent.pool import Pool
from steam.client import SteamClient

IMAGES = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps"
STORE = "https://store.steampowered.com/app"
LISTED = {"game", "demo", "beta", "application"}
CHUNK = 200
PARALLEL = 4
READ_TIMEOUT = 90         # seconds to wait for one chunk of ids
MAX_LOST_SHARE = 0.005   # a read that loses more of the ids is not written
MIN_KEPT_SHARE = 0.5     # nor one that finds less than this share of what the file held
VR_TAG = 21978           # store tag "VR"; category_53 / category_54 are Valve's "VR Supported" / "VR Only"
# Steam's names for its languages, and the codes the front uses. The same codes name the files of v1/text/.
LANGUAGES = {
    "arabic": "ar", "bulgarian": "bg", "schinese": "zh-CN", "tchinese": "zh-TW", "czech": "cs", "danish": "da", "dutch": "nl", "english": "en",
    "finnish": "fi", "french": "fr", "german": "de", "greek": "el", "hungarian": "hu", "indonesian": "id", "italian": "it", "japanese": "ja",
    "koreana": "ko", "norwegian": "no", "polish": "pl", "portuguese": "pt", "brazilian": "pt-BR", "romanian": "ro", "russian": "ru", "latam": "es-419",
    "spanish": "es", "swedish": "sv", "thai": "th", "turkish": "tr", "ukrainian": "uk", "vietnamese": "vi",
}
# How Valve rates a game for its devices. 3 (verified) is certain: it is what the store filter frame_compatibility=3 lists. 1 and 2 follow the scale of the Steam
# Deck, whose results say so (a game whose test says SteamOS does not support VR is 1).
VERDICTS = {1: "unsupported", 2: "playable", 3: "verified"}
TAGS_KEPT = 8            # the heaviest tags of a game, which Steam lists first
STORE_API = "https://api.steampowered.com"
BATCH = 100              # apps for each call of the store API
PAUSE = 4                # seconds between two calls: Steam answers 429 to a faster pace
# The store categories that say how a game is played: single-player, multi-player and co-op (local, online, shared screen).
MODES = {"single": (2,), "multi": (1,), "coop": (9, 38, 39)}


def find(data, name, depth=3):
    """The first value called `name` in the first levels of the app's data, wherever Valve keeps it."""
    if not isinstance(data, dict) or depth < 0:
        return None
    for key, value in data.items():
        if key == name and isinstance(value, str):
            return value
        found = find(value, name, depth - 1)
        if found:
            return found
    return None


def number(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def day(unix_time):
    """A Unix time as a date, or None when Steam gives none."""
    return datetime.datetime.fromtimestamp(unix_time, datetime.timezone.utc).strftime("%Y-%m-%d") if unix_time and unix_time > 0 else None


def track(entries, previous, today):
    """Gives each installable app the day this catalog first saw it installable, which is the day its Android version arrived (the release date of the game
    says nothing of it). `previous` is the apps of the last file by app id, or None when there is no file: nothing is known of the past then, so no app has a
    date. An app that was already installable keeps the date it had, even when empty; one that was absent or only announced gets today's."""
    for entry in entries:
        old = (previous or {}).get(entry["appid"])
        if entry["android"] != "ready" or previous is None:
            entry["androidSince"] = None
        elif old is not None and old.get("android") == "ready":
            entry["androidSince"] = old.get("androidSince")
        else:
            entry["androidSince"] = today
    return entries


def image(appid, name):
    return f"{IMAGES}/{appid}/{name}" if name else None


def asset_name(common, key):
    """The file name Steam publishes for one of the library pictures of an app (`library_capsule`, `library_hero`...), or None when it publishes none."""
    return (((common.get("library_assets_full") or {}).get(key) or {}).get("image") or {}).get("english") or None


def keep_removed(found, previous, lost_ids, today):
    """The entries of the last file whose app was not found this time, to be kept in the file. An app that Steam no longer lists with an Android build is kept as it was, not
    installable and marked `delisted` with the day it was first missing (the day it already had when it was missing before); an app whose read failed (`lost_ids`) is
    kept exactly as it was, as nothing says it is gone. Nothing leaves the catalog."""
    here = {entry["appid"] for entry in found}
    kept = []
    for appid, old in sorted((previous or {}).items()):
        if appid in here:
            continue
        entry = dict(old)
        if appid not in lost_ids:
            entry["installable"] = False
            entry["delisted"] = old.get("delisted") or today
        kept.append(entry)
    return kept


def test_channels(data, android):
    """The channels (branches of Steam) other than the normal one that hold an Android build of the base game and ask no password, with the day of their last update: a test
    version of the game that anyone can pick on Steam. The depots of an add-on are not the game, and a channel that asks a password cannot be fetched."""
    branches = (data.get("depots") or {}).get("branches") or {}
    names = {branch for d in android if number(d.get("dlcappid")) <= 0 for branch, manifest in (d.get("manifests") or {}).items() if branch != "public" and isinstance(manifest, dict)}
    channels = []
    for name in sorted(names):
        info = branches.get(name) if isinstance(branches.get(name), dict) else {}
        if number(info.get("pwdrequired"), 0):
            continue
        channels.append({"name": name, "updated": day(number(info.get("timeupdated"), 0))})
    return channels


def classify(appid, data):
    """The catalog entry of an app, or None when it declares no Android depot (or is not a game, a demo, a beta or an application)."""
    common = data.get("common") or {}
    kind = str(common.get("type", "")).lower()
    name = str(common.get("name", "")).strip()
    if kind not in LISTED or not name:
        return None
    depots = {k: v for k, v in (data.get("depots") or {}).items() if str(k).isdigit() and isinstance(v, dict)}
    android = [d for d in depots.values() if "android" in [s.strip() for s in str((d.get("config") or {}).get("oslist", "")).split(",")]]
    if not android:
        return None
    published = [d for d in android if isinstance((d.get("manifests") or {}).get("public"), dict)]
    base = [d for d in published if number(d.get("dlcappid")) <= 0]
    ready = bool(base)
    channels = test_channels(data, android)
    asset = lambda key: asset_name(common, key)
    header = (common.get("header_image") or {}).get("english") or asset("library_header")
    categories = common.get("category") or {}
    tags = [number(t, -1) for t in (common.get("store_tags") or {}).values()]
    released = number(common.get("steam_release_date"), 0)
    # "VR Only" and "VR Supported" are Valve's own flags; the VR tag alone says the game has VR in it.
    vr_mode = "only" if "category_54" in categories else "supported" if "category_53" in categories or VR_TAG in tags else "none"
    associations = [a for a in (common.get("associations") or {}).values() if isinstance(a, dict)]
    people = lambda role: [a["name"] for a in associations if a.get("type") == role and a.get("name")]
    spoken = lambda flag: [LANGUAGES.get(name, name) for name, v in (common.get("supported_languages") or {}).items() if isinstance(v, dict) and v.get(flag) == "true"]
    tag_ids = [number(v, -1) for _, v in sorted((common.get("store_tags") or {}).items(), key=lambda kv: number(kv[0]))][:TAGS_KEPT]
    modes = [m for m, ids in MODES.items() if any(f"category_{i}" in categories for i in ids)]
    verdict = lambda key: VERDICTS.get(number((common.get(key) or {}).get("category"), 0)) if isinstance(common.get(key), dict) else None
    return {
        "appid": appid,
        "name": name,
        "type": kind,
        "parent": (number(common.get("parent"), 0) or None) if kind != "game" else None,
        "vr": vr_mode != "none",
        "vrMode": vr_mode,
        "android": "ready" if ready else "unreleased",
        "installable": ready,
        # The test channels that hold an Android build, and whether they are all there is: nothing on the normal channel, so it is not "ready", but it can be installed from a channel.
        "channels": channels,
        "channelOnly": bool(channels) and not ready,
        "runtime": find(data, "recommended_runtime"),
        "release": day(released),
        "updated": day(number(((data.get("depots") or {}).get("branches") or {}).get("public", {}).get("timeupdated"), 0) if isinstance((((data.get("depots") or {}).get("branches") or {}).get("public")), dict) else 0),
        "androidSince": None,
        "developer": people("developer"),
        "publisher": people("publisher"),
        "reviewPercent": number(common.get("review_percentage"), 0) or None,
        "metacritic": number(common.get("metacritic_score"), 0) or None,
        "modes": modes,
        "controller": common.get("controller_support") or None,
        "languages": spoken("supported"),
        "audio": spoken("full_audio"),
        "subtitles": spoken("subtitles"),
        "tags": tag_ids,
        "reviewCount": None,
        "platforms": [o for o in str(common.get("oslist", "")).split(",") if o],
        "frame": verdict("steam_frame_compatibility"),
        "deck": verdict("steam_deck_compatibility"),
        "store": f"{STORE}/{appid}",
        "cover": image(appid, asset("library_capsule") or "library_600x900.jpg"),
        "banner": image(appid, asset("library_hero") or "library_hero.jpg"),
        "header": image(appid, header or "header.jpg"),
        "capsule": image(appid, ((common.get("small_capsule") or {}).get("english")) or None),
        "installBytes": sum(number(d["manifests"]["public"].get("size")) for d in base),
        "downloadBytes": sum(number(d["manifests"]["public"].get("download")) for d in base),
    }


def render(entries):
    """The whole file, sorted by app id so that the same data always gives the same file: one app a line, so a day's changes show line by line."""
    entries = sorted(entries, key=lambda e: e["appid"])
    live = [e for e in entries if not e.get("delisted")]
    counts = {s: sum(1 for e in live if e["android"] == s) for s in ("ready", "unreleased")}
    gone = len(entries) - len(live)
    head = json.dumps({"v": 1, "count": len(entries), **counts, **({"delisted": gone} if gone else {})}, separators=(",", ":"))[:-1]
    return head + ',"apps":[\n' + ",\n".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in entries) + "\n]}\n"


def get_json(url):
    """The answer of Steam's API; waits and tries again when Steam says the pace is too fast."""
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as answer:
                return json.load(answer)
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == 3:
                raise
            time.sleep(30 * (attempt + 1))


# What the English reading also asks for, in the same calls: the pictures and the trailers of the store page (see media_entry). They do not depend on the language.
MEDIA_REQUEST = {"include_screenshots": True, "include_trailers": True}


def store_items(key, ids, language, extra=None):
    """The store's view of the apps in one language: their texts, reviews and release, by app id. Steam answers in English for a text it has no translation of.
    `extra` adds to what is asked of each app, in the same calls."""
    items = {}
    for i in range(0, len(ids), BATCH):
        request = {
            "ids": [{"appid": a} for a in ids[i:i + BATCH]],
            "context": {"language": language, "country_code": "US"},
            "data_request": {"include_basic_info": True, "include_reviews": True, **(extra or {})},
        }
        body = get_json(f"{STORE_API}/IStoreBrowseService/GetItems/v1?key={key}&{urllib.parse.urlencode({'input_json': json.dumps(request)})}")
        for item in body["response"].get("store_items", []):
            if item.get("success") == 1:
                items[item["appid"]] = item
        time.sleep(PAUSE)
    return items


def tag_names(key, language):
    """The names of all the store's tags in one language, by tag id."""
    body = get_json(f"{STORE_API}/IStoreService/GetTagList/v1?key={key}&language={language}")
    time.sleep(PAUSE)
    return {t["tagid"]: t["name"] for t in body["response"].get("tags", [])}


def summary_of(item):
    return ((item or {}).get("basic_info") or {}).get("short_description") or None


def language_texts(apps, items, names, code, kept=(), old=None):
    """What the front needs in one language: the name of each tag the catalog uses, and the short description of each app. The apps Steam no longer lists (`kept`) are not
    asked: their tags still count, and their description is the one the file already had (`old`, its summaries)."""
    used = sorted({t for a in list(apps) + list(kept) for t in a["tags"]})
    summaries = {str(a["appid"]): summary_of(items.get(a["appid"])) for a in apps if summary_of(items.get(a["appid"]))}
    for a in kept:
        before = (old or {}).get(str(a["appid"]))
        if before:
            summaries[str(a["appid"])] = before
    return {
        "lang": code,
        "tags": {str(t): names[t] for t in used if t in names},
        "summaries": dict(sorted(summaries.items(), key=lambda kv: int(kv[0]))),
    }


def render_texts(texts):
    """One tag or one app a line, as in the catalog."""
    def block(table):
        return "{\n" + ",\n".join(f"{json.dumps(k)}:{json.dumps(v, ensure_ascii=False)}" for k, v in table.items()) + "\n}"
    return f'{{"v":1,"lang":{json.dumps(texts["lang"])},"tags":{block(texts["tags"])},"summaries":{block(texts["summaries"])}}}\n'


def read_text(path):
    with open(path, encoding="utf-8") as file:
        return file.read()


def write_if_changed(path, text):
    if os.path.exists(path) and read_text(path) == text:
        return False
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path + ".tmp", "w") as out:
        out.write(text)
    os.replace(path + ".tmp", path)
    return True


MAX_TRAILERS = 3
# Newer apps keep a screenshot in a folder of its own (`steam/apps/<id>/<hash>/ss_<hash>.jpg`), older ones directly under the app (`steam/apps/<id>/ss_<hash>.jpg`):
# the path after the app is kept, without the extension, and the front adds the size.
SCREENSHOT = re.compile(r"^steam/apps/\d+/((?:[0-9a-f]{40}/)?ss_[0-9a-f]{40})\.jpg")


def media_entry(item):
    """The pictures and trailers of one app's store page, as short as a file can hold them, or None when it has none. The addresses are built by the front from
    these: a screenshot is its path under the app without the extension, a trailer the paths of its adaptive stream (HLS) and of its short loop (MP4) on the video CDN, and of its thumbnail on the
    image CDN. Only what is for all ages is kept."""
    shots = []
    for shot in ((item.get("screenshots") or {}).get("all_ages_screenshots") or []):
        found = SCREENSHOT.match(shot.get("filename") or "")
        if found and found.group(1) not in shots:
            shots.append(found.group(1))
    trailers = []
    for trailer in ((item.get("trailers") or {}).get("highlights") or []):
        if len(trailers) >= MAX_TRAILERS:
            break
        if trailer.get("all_ages") is False:
            continue
        hls = next((t["cdn_path"] for t in trailer.get("adaptive_trailers") or [] if t.get("encoding") == "hls_h264" and t.get("cdn_path")), None)
        loop = next((m["filename"] for m in trailer.get("microtrailer") or [] if m.get("type") == "video/mp4" and m.get("filename")), None)
        if not hls and not loop:
            continue
        entry = {"n": trailer.get("trailer_name") or "", "p": trailer.get("screenshot_medium") or ""}
        if hls:
            entry["h"] = hls
        if loop:
            entry["m"] = loop
        trailers.append(entry)
    if not shots and not trailers:
        return None
    entry = {}
    if shots:
        entry["s"] = shots
    if trailers:
        entry["t"] = trailers
    return entry


def render_media(entries):
    """v1/media.json: one app a line, the apps in order of their id, so a change of one shows as a change of one line."""
    lines = [f'"{appid}":' + json.dumps(entry, separators=(",", ":"), ensure_ascii=False) for appid, entry in sorted(entries.items())]
    return '{"v":1,"count":%d,"apps":{\n%s\n}}\n' % (len(lines), ",\n".join(lines))


def read_old(path, key):
    """What a file of the catalog held under `key` (its summaries, its apps), or nothing when it cannot be read."""
    try:
        return json.loads(read_text(path)).get(key) or {}
    except (OSError, ValueError):
        return {}


def write_media(apps, english, path, kept=()):
    """Writes v1/media.json from the English reading. The apps Steam no longer lists (`kept`) keep the pictures the file had. The file is left as it was when far fewer apps
    have media than before (a reading that went wrong)."""
    entries = {}
    for app in apps:
        entry = media_entry(english.get(app["appid"]) or {})
        if entry:
            entries[app["appid"]] = entry
    old = read_old(path, "apps")
    for app in kept:
        if str(app["appid"]) in old:
            entries[app["appid"]] = old[str(app["appid"])]
    before = 0
    if os.path.exists(path):
        match = re.search(r'"count":(\d+)', read_text(path)[:100])
        before = int(match.group(1)) if match else 0
    if before and len(entries) < before * MIN_KEPT_SHARE:
        print(f"media: only {len(entries)} apps against {before} in the file: the file is left as it was")
        return
    print(f"{path} {'written' if write_if_changed(path, render_media(entries)) else 'unchanged'} ({len(entries)} apps)")


def add_store_facts(apps, english):
    """Facts that do not depend on the language, from the store's view of each app."""
    for app in apps:
        summary = (((english.get(app["appid"]) or {}).get("reviews") or {}).get("summary_filtered") or {})
        app["reviewCount"] = number(summary.get("review_count"), 0) or None


def write_texts(key, apps, folder, english, kept=()):
    """The text files of every language. A language that cannot be read keeps the file it had. The apps Steam no longer lists (`kept`) are not asked: they keep their texts."""
    ids = sorted(a["appid"] for a in apps)
    written, kept = 0, []
    for steam_name, code in LANGUAGES.items():
        try:
            items = english if steam_name == "english" else store_items(key, ids, steam_name)
            path = os.path.join(folder, f"{code}.json")
            texts = language_texts(apps, items, tag_names(key, steam_name), code, kept, read_old(path, "summaries"))
            written += write_if_changed(path, render_texts(texts))
        except Exception as error:
            kept.append(code)
            print(f"v1/text/{code}.json kept as it was: {type(error).__name__} {error}", flush=True)
    print(f"texts: {len(LANGUAGES) - len(kept)} languages read, {written} files changed" + (f", kept: {', '.join(kept)}" if kept else ""))


def app_list(key):
    """Every game and program Steam lists, by its official API (the key is only sent to Steam and never printed)."""
    ids, last = [], 0
    while True:
        url = f"https://api.steampowered.com/IStoreService/GetAppList/v1/?key={key}&include_games=true&include_software=true&max_results=50000&last_appid={last}"
        with urllib.request.urlopen(url, timeout=60) as answer:
            body = json.load(answer)["response"]
        batch = [a["appid"] for a in body.get("apps", [])]
        ids += batch
        print(f"list: {len(ids)} ids", flush=True)
        if not body.get("have_more_results") or not batch:
            return sorted(set(ids))
        last = batch[-1]


DEMOS_PAGE = 1000


def demo_ids():
    """Every demo the store lists. Steam's list of apps (app_list) leaves them out, and a demo can have an Android build of its own. The store's own query answers
    without a key, a page at a time: the ids only, nothing else is asked. Sort 2 is by app id: the other orders repeat some demos from one page to the next and skip others."""
    ids, start = [], 0
    while True:
        payload = {"query": {"start": start, "count": DEMOS_PAGE, "sort": 2, "filters": {"type_filters": {"include_demos": True, "include_games": False}}},
                   "context": {"language": "english", "country_code": "US", "steam_realm": 1}, "data_request": {}}
        url = "https://api.steampowered.com/IStoreQueryService/Query/v1/?input_json=" + urllib.parse.quote(json.dumps(payload, separators=(",", ":")))
        body = get_json(url)["response"]
        batch = [item["appid"] for item in body.get("ids", [])]
        ids += batch
        total = (body.get("metadata") or {}).get("total_matching_records", 0)
        print(f"demos: {len(ids)} of {total}", flush=True)
        if not batch or len(ids) >= total:
            return sorted(set(ids))
        start += len(batch)
        time.sleep(PAUSE)


# The pictures an app gets by default when Steam names none: the file is only a guess, and a recent app or a demo often has no such file.
GUESSED = {"cover": "library_600x900.jpg", "banner": "library_hero.jpg"}
ARTWORK_KEYS = {"cover": "library_capsule", "banner": "library_hero"}


def picture_exists(url):
    """True when Steam has a file at that address."""
    try:
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "gameport-catalog"})
        with urllib.request.urlopen(request, timeout=20) as answer:
            return answer.status == 200
    except Exception:
        return False


def read_parents(ids):
    """The data of the apps `ids` (the full games of the demos), read the way the scan reads them."""
    client = SteamClient()
    client.anonymous_login()
    try:
        return {int(appid): data for appid, data in (client.get_product_info(apps=list(ids), timeout=90).get("apps") or {}).items()}
    finally:
        client.logout()


# The other file names Steam uses for the same picture, on the apps that predate the hashed names.
OLD_NAMES = {"cover": ["portrait.png"], "banner": []}


def mend_artwork(entries, exists=picture_exists, parents=read_parents):
    """Finds a real file for the cover or the banner of an app whose own is only a guess that has no file: first the other name Steam gave the same picture on the
    older apps (portrait.png), then, for a demo or any app that has a parent, the picture of its full game. An app that has neither keeps what it has: nothing
    better is known. Only the guessed addresses are checked; the ones Steam named are trusted. Returns how many pictures were replaced."""
    missing = []
    for entry in entries:
        for field, default in GUESSED.items():
            url = entry.get(field)
            if url and url == image(entry["appid"], default) and not exists(url):
                missing.append((entry, field))
    mended = 0
    left = []
    for entry, field in missing:
        found = next((u for u in (image(entry["appid"], name) for name in OLD_NAMES[field]) if exists(u)), None)
        if found:
            entry[field] = found
            mended += 1
        elif entry.get("parent"):
            left.append((entry, field))
    if not left:
        return mended
    data = parents(sorted({entry["parent"] for entry, _ in left}))
    for entry, field in left:
        common = (data.get(entry["parent"]) or {}).get("common") or {}
        names = [asset_name(common, ARTWORK_KEYS[field]), GUESSED[field]] + OLD_NAMES[field]
        url = next((u for u in (image(entry["parent"], n) for n in names if n) if exists(u)), None)
        if url:
            entry[field] = url
            mended += 1
    return mended


def read_with_tokens(client, apps):
    """Reads again the apps that Steam answered with a stub, as the player's own client does: first it asks for the access token of each, then for the product info with it. Returns
    the answer's apps and how many tokens Steam gave. An app whose token is 0 is public: a stub for it is not a matter of permission."""
    tokens = (client.get_access_tokens(app_ids=list(apps)) or {}).get("apps") or {}
    if not tokens:
        return {}, 0
    answer = client.get_product_info(apps=[{"appid": appid, "access_token": token} for appid, token in tokens.items()], timeout=READ_TIMEOUT)
    return (answer.get("apps") or {}), sum(1 for token in tokens.values() if token)


def scan(ids, lost_ids=None, canary=()):
    """The entries of the apps with an Android build among `ids`, the number of ids that could not be read, and the count of each app type seen. The ids that could not be read
    are added to `lost_ids` when a set is given: an app among them is not gone, it was not read. `canary` is the apps the catalog already holds: how many of them Steam answers
    for with their data is written first, to tell a session that sees nothing from a catalog that really lost its apps."""
    client = SteamClient()
    result = client.anonymous_login()
    print(f"anonymous logon: {result}, server {getattr(client, 'current_server_addr', '?')}", flush=True)
    if canary:
        named = 0
        for start in range(0, len(canary), CHUNK):
            try:
                answer = client.get_product_info(apps=list(canary[start:start + CHUNK]), timeout=READ_TIMEOUT)
            except (Exception, Timeout) as error:
                print(f"canary read failed: {type(error).__name__}", flush=True)
                continue
            named += sum(1 for data in (answer.get("apps") or {}).values() if (data.get("common") or {}).get("name"))
        print(f"canary: {named} of the {len(canary)} apps the catalog holds are answered with their data", flush=True)
    found, lost, types, done = [], [0], {}, [0]
    # What Steam answered since the last line: the entries, those with a name, the stubs (no permission), the ids it does not know, and what a read with a token gave back.
    window = {"entries": 0, "named": 0, "stubs": 0, "unknown": 0, "tokens_given": 0, "recovered": 0}
    chunks = [ids[i:i + CHUNK] for i in range(0, len(ids), CHUNK)]

    def take(apps):
        for appid, data in apps.items():
            kind = str((data.get("common") or {}).get("type", "?")).lower()
            types[kind] = types.get(kind, 0) + 1
            entry = classify(int(appid), data)
            if entry:
                found.append(entry)

    def read(chunk):
        for attempt in range(3):
            try:
                answer = client.get_product_info(apps=chunk, timeout=READ_TIMEOUT)
                apps = answer.get("apps") or {}
                stubs = [appid for appid, data in apps.items() if data.get("_missing_token")]
                window["entries"] += len(apps)
                window["named"] += sum(1 for data in apps.values() if (data.get("common") or {}).get("name"))
                window["stubs"] += len(stubs)
                window["unknown"] += len(answer.get("unknown_apps") or [])
                if stubs:
                    again, given = read_with_tokens(client, stubs)
                    window["tokens_given"] += given
                    window["recovered"] += sum(1 for data in again.values() if (data.get("common") or {}).get("name"))
                    apps = {**apps, **{appid: data for appid, data in again.items() if (data.get("common") or {}).get("name")}}
                take(apps)
                break
            # gevent's Timeout is not an Exception: without naming it here a read that took too long stopped the whole run, after more than twenty minutes of work.
            except (Exception, Timeout) as error:
                if attempt == 2:
                    lost[0] += len(chunk)
                    if lost_ids is not None:
                        lost_ids.update(chunk)
                    print(f"lost {len(chunk)} ids from {chunk[0]}: {type(error).__name__}", flush=True)
                elif not client.connected:
                    client.anonymous_login()
        done[0] += 1
        if done[0] % 100 == 0:
            print(f"{done[0]}/{len(chunks)} chunks, {len(found)} apps with an Android depot; Steam answered {window['entries']} entries, {window['named']} with a name, "
                  f"{window['stubs']} stubs, {window['unknown']} unknown ids; tokens: {window['tokens_given']} given, {window['recovered']} apps recovered", flush=True)
            for key in window:
                window[key] = 0

    Pool(PARALLEL).map(read, chunks)
    client.logout()
    return found, lost[0], types


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids", help="a list of app ids already fetched (the answer of Steam's GetAppList)")
    parser.add_argument("--out", default="v1/catalog.json")
    parser.add_argument("--texts", default="v1/text", help="the folder of the text files")
    parser.add_argument("--media", default="v1/media.json", help="the file of the pictures and trailers of the store pages")
    parser.add_argument("--no-texts", action="store_true", help="only the catalog, not the texts")
    parser.add_argument("--no-demos", action="store_true", help="do not ask the store for the list of demos (Steam's list of apps does not hold them)")
    args = parser.parse_args()
    key = os.environ.get("STEAM_API_KEY")
    if args.ids:
        ids = sorted({int(i) for i in re.findall(r'"appid"\s*:\s*(\d+)', read_text(args.ids))})
    elif key:
        ids = app_list(key)
    else:
        sys.exit("STEAM_API_KEY is not set, and no --ids file was given")
    if not args.no_demos:
        # A list that cannot be read stops the run: the file stays as it was, rather than losing every demo for a day.
        ids = sorted(set(ids) | set(demo_ids()))
    print(f"{len(ids)} ids to read", flush=True)
    lost_ids = set()
    # The apps the catalog already holds (those Steam still lists): the first to be read, to see whether this session gets their data.
    known = []
    if os.path.exists(args.out):
        known = sorted(e["appid"] for e in json.loads(read_text(args.out)).get("apps", []) if not e.get("delisted"))
    found, lost, types = scan(ids, lost_ids, known)
    print(f"pictures found again for the apps whose own cover or banner has no file: {mend_artwork(found)}", flush=True)
    ready = sum(1 for e in found if e["android"] == "ready")
    print(f"{len(found)} apps with an Android depot ({ready} ready, {len(found) - ready} unreleased, {sum(1 for e in found if e['vr'])} VR), {lost} ids lost")
    print("app types seen:", dict(sorted(types.items(), key=lambda kv: -kv[1])[:8]))

    if lost > len(ids) * MAX_LOST_SHARE:
        sys.exit(f"too many ids lost ({lost}): the file is left as it was")
    previous = None
    if os.path.exists(args.out):
        previous = {e["appid"]: e for e in json.loads(read_text(args.out)).get("apps", [])}
    # What the file held that Steam still lists: the apps that were kept as gone do not count, so they never hide a read that went wrong.
    before = sum(1 for e in (previous or {}).values() if not e.get("delisted"))
    if before and len(found) < before * MIN_KEPT_SHARE:
        sys.exit(f"only {len(found)} apps against {before} in the file: the file is left as it was")
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    track(found, previous, today)
    kept = keep_removed(found, previous, lost_ids, today)
    gone = [e["appid"] for e in kept if e.get("delisted") == today]
    if kept:
        print(f"{len(kept)} apps kept that were not found this time ({len(gone)} newly marked delisted, {len(lost_ids & {e['appid'] for e in kept})} only not read)")
    english = {}
    if key and not args.no_texts:
        english = store_items(key, sorted(a["appid"] for a in found), "english", MEDIA_REQUEST)
        add_store_facts(found, english)
    print(f"{args.out} {'written' if write_if_changed(args.out, render(found + kept)) else 'unchanged'}")
    if key and not args.no_texts:
        write_texts(key, found, args.texts, english, kept)
        write_media(found, english, args.media, kept)
    elif not args.no_texts:
        print("no STEAM_API_KEY: the texts are not read")


if __name__ == "__main__":
    main()
