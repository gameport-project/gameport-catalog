# gameport-catalog

[![Discord](https://img.shields.io/badge/Discord-Join%20the%20community-5865F2?logo=discord&logoColor=white)](https://discord.gg/yWXXAC2AKc)

The data behind GamePort's catalogs, as plain files. GamePort reads them when it starts; there is no server.

Everything an app reads is under `v1/`, the version of the format. A published format is never changed in place: a change gets a new folder (`v2/`), and `v1/` stays up to date for as long as apps read it. The files that are not under `v1/` are for the scripts.

| Path | What it is |
|---|---|
| `v1/catalog.json` | The Steam apps that have an Android build, one app a line. Does not depend on the language. |
| `v1/text/<language>.json` | The short descriptions and the tag names, in one language (30 files). |
| `v1/media.json` | The screenshots and trailers of the store page of each app, for the apps that have some. Does not depend on the language. |
| `v1/prices/` | The prices of those apps in every currency Steam bills in, and their history: see below. |
| `v1/vr-ports/` | The community VR ports: `index.json` and one folder for each port. The format GamePort's loader reads. |
| `ports_index.py` | Writes `v1/vr-ports/index.json` from the manifests: nobody edits it by hand. |
| `refresh.py` | Rebuilds `v1/catalog.json`, `v1/text/` and `v1/media.json` from Steam. |
| `prices.py` | Reads the prices and writes the files of `v1/prices/` that changed (`prices-pending.json` holds the changes waiting for a second reading). |
| `backfill.py` | Reads the past prices (two years) from IsThereAnyDeal, for each game and country that has none yet (`prices-backfilled.json` notes the ones done). |
| `.github/workflows/backfill.yml` | Runs `backfill.py` right after each refresh of the catalog, so a new game has its history within minutes; by hand, with a number of pairs, for a long backlog. |
| `.github/workflows/prices.yml` | Runs `prices.py` every six hours and commits what changed. |
| `.github/workflows/daily.yml` | Runs `refresh.py` once a day and commits what changed. |

## v1/catalog.json

```
{"v":1,"count":200,"ready":167,"unreleased":33,"apps":[
{"appid":846470,"name":"Moss VR", ... },
...
]}
```

Each app:

| Field | Meaning |
|---|---|
| `appid`, `name`, `type` | Steam app id, name, and `game`, `demo` or `beta`. |
| `parent` | For a demo or a beta, the app id of the full game. |
| `android` | `ready`: a build is published and can be installed. `unreleased`: an Android depot exists but nothing is published (a future version). |
| `installable` | `true` when `android` is `ready`, and the app is not `delisted`. |
| `androidSince` | The day this catalog first saw the app installable: when its Android version arrived (the release date says nothing of it). `null` for apps that were already there when tracking began, and for apps not installable. |
| `delisted` | Only on an app that Steam no longer lists with an Android build (a demo taken down, say): the day the catalog first missed it. It stays in the file as it was, with its name, its pictures, its text and its media, and is not installable. An app whose read failed for one run is kept without this mark, and one that comes back loses it. Nothing is ever dropped from the catalog. The head of the file then counts them apart (`"delisted":1`), and `ready` and `unreleased` only count the apps Steam still lists. |
| `vr`, `vrMode` | `vrMode` is `only` (Valve's "VR Only"), `supported` or `none`. `vr` is true unless `none`. |
| `runtime` | The layer Valve recommends for the Steam Frame; `lepton-stable` is the Android one. May be `null`. |
| `frame`, `deck` | Valve's rating for the Steam Frame and the Steam Deck: `verified`, `playable`, `unsupported`, or `null`. `verified` for the Frame is certain (it is what the store filter `frame_compatibility=3` lists); `playable` and `unsupported` follow the Deck's scale. |
| `release`, `updated` | Release date, and date of the last update of the public build (ISO dates). |
| `developer`, `publisher` | Lists of names. |
| `reviewPercent`, `reviewCount`, `metacritic` | Steam reviews and Metacritic score. |
| `modes` | `single`, `multi`, `coop`. |
| `controller` | `full`, `partial` or `null`. |
| `languages`, `audio`, `subtitles` | Language codes (see below) for the interface, the voices and the subtitles. |
| `platforms` | `windows`, `macos`, `linux`, `android`. |
| `tags` | The ids of the 8 heaviest store tags. Their names are in `v1/text/`. |
| `store`, `cover`, `banner`, `header`, `capsule` | Complete links: the store page and four images. |
| `installBytes`, `downloadBytes` | Size of the base game on the public branch. |

## v1/text/&lt;language&gt;.json

```
{"v":1,"lang":"fr","tags":{"19":"Action",...},"summaries":{"846470":"Moss™ est un jeu ..."}}
```

`tags` names the tag ids used by the catalog. `summaries` holds the short description of each app. Steam answers in English when a text has no translation, so every file is complete: the front never needs a fallback.

Language codes: `ar bg zh-CN zh-TW cs da nl en fi fr de el hu id it ja ko no pl pt pt-BR ro ru es-419 es sv th tr uk vi`.

## v1/media.json

```
{"v":1,"count":228,"apps":{
"2710650":{"s":["3e1b39a5927040c84c8bcb6d340c3ec7d902a102/ss_3e1b39a5927040c84c8bcb6d340c3ec7d902a102",...],"t":[{"n":"Quantum Void Trailer English","p":"257163632/313822af.../movie_600x337.jpg","h":"2710650/2018454788/77063d.../1783384772/hls_264_master.m3u8","m":"2710650/2018454788/77063d.../1783384772/microtrailer.mp4"}]},
...
}}
```

One app a line, in order of id. It comes from the same calls of the store API as the texts (the English reading also asks for the screenshots and the trailers), so it costs Steam no call more. Only what is for all ages is kept.

| Key | What it is |
|---|---|
| `s` | The screenshots, in the order of the store page, each as its path under the app without the extension: `<hash>/ss_<hash>` for a recent app, `ss_<hash>` for an older one. The pictures are at `https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/<appid>/<path>.600x338.jpg` (thumbnail) and `.1920x1080.jpg` (full size). |
| `t` | Up to three trailers: `n` the name, `p` the thumbnail (at `https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/<p>`), `h` the adaptive stream in HLS and `m` the short silent loop in MP4, both under `https://video.akamai.steamstatic.com/store_trailers/`. Either `h` or `m` may be missing. |

The file is left as it was when far fewer apps have media than before (a reading that went wrong).

## v1/prices/

The prices, cut in files so an app downloads only what it shows. The folder name is the version of the format: a published format is never changed in place. A change of format gets a new folder (`v2/prices/`), and the old one is kept up to date for as long as apps read it. An app that finds a file it does not understand shows no prices.

| Path | What it is | Size |
|---|---|---|
| `v1/prices/index.json` | The version, the countries with their currency, and the day of the last change. Read first. | under 1 KB |
| `v1/prices/now/<country>.json` | What each app costs now in one country, for the lists. | 5 to 8 KB |
| `v1/prices/history/<app id>.json` | The history of one app in every country, for its page. | 1.5 to 5 KB |

```
now/fr.json      {"v":1,"country":"fr","currency":"EUR","prices":{"846470":[1999,1999],"418650":[599,1499,1792688400]}}
history/418650   {"v":1,"appid":418650,"lines":{"fr":[["2025-01-10",1499,1499],["2026-10-01",599,1499,1792688400]],"us":[...]}}
```

A price is `[price, price before discount, end of the discount]`, in hundredths of the currency (the third is there only while a discount is on). A line of a history starts with the day it was first seen. `[day, null, null]` says the app is no longer sold in that country; such an app is not in `now/`. There is one country for each currency Steam bills in (the euro zone has `fr`): the countries are the keys of `currencies` in the index, and a price is the same in every country that has the currency. A line is added only when the price or the discount changed, so the last line of a history is what is true now. The history starts the day the script first ran for a country (two years back for `us`, `fr` and `gb`, from IsThereAnyDeal): nothing is known of before.

The script runs every six hours, so a sale of a few hours can still be missed. A change that a bad reading could make, the app no longer sold or a paid app suddenly free, is first parked in `prices-pending.json` and written only when the next reading says the same, with the day it was first seen.

## v1/vr-ports/

One folder for each port, `ports/<id>/port.json`, and the index the app reads first. To add or change a port, write its `port.json`: that is all. The index (the list and the fingerprint of each manifest, which the app checks before it trusts a manifest) is written by `ports_index.py`, which the workflow `ports.yml` runs on every change of a manifest; locally, `python3 ports_index.py`. Nothing is versioned by hand: the `revision` of the index is the one of the manifest, copied, and the app does not compare it, so it is not raised when a port changes. A port is updated when its fingerprint changes.

## Running it

```
pip install -r requirements.txt
python3 -m unittest
STEAM_API_KEY=... python3 refresh.py            # the whole catalog and all texts
python3 refresh.py --no-texts                   # the catalog only
STEAM_API_KEY=... python3 prices.py             # the prices, after the catalog
ITAD_API_KEY=... python3 backfill.py --limit 1500   # the past prices of what has none yet
```

`STEAM_API_KEY` is a Steam Web API key (https://steamcommunity.com/dev/apikey), kept as a repository secret for the workflows. `ITAD_API_KEY` is the API key of an app made at https://isthereanydeal.com/apps/, also a repository secret: it is only for the past prices of the games and countries that have none (the OAuth client of that app is not used). Each key is only sent to the service it belongs to and is never written to a file.

`SITE_DISPATCH_TOKEN` is a fine-grained personal access token that may only start workflows of the site's repository (`gameport-project.github.io`, permission Actions: read and write, nothing else). The daily workflow uses it to ask the site to rebuild right after the catalog changes. Without it the site still rebuilds on its own, every six hours.

How it works: Steam's API lists every game and program but cannot filter on the platform. Each app is read with an anonymous Steam session (no account, no licence: the product info of public apps is public) and kept when it declares an Android depot. The store API then gives the texts, by batches, with a pause between calls because Steam answers `429` to a faster pace. The files are left alone when a read looks wrong (too many ids lost, far fewer apps than before, a language that cannot be read).

## The announcement of new Android versions

After each daily run, `announce_new.py` posts on a Discord channel the apps (games, demos, betas, applications) whose Android version became installable in that run. It is a list of cards, one for each app: its name as a link to Steam, its kind (game, demo, beta, app), VR or flat, and its cover (on the right of the card, as Discord lays it out). Discord shows ten cards to a message, thirty at most are posted, and only the first message notifies: the following ones are posted silently, so a long list is still one notification. It compares the catalog with the copy the workflow kept before the run, so a first run, or a run with no copy, announces nothing.

To set it up, once:

1. In Discord, make the channel (for example "new-android-games"; a channel of type "Announcement" lets servers and members follow it). In its settings, Integrations, Webhooks, make a webhook and copy its address.
2. In the repository settings, Secrets and variables, Actions: add the secret `DISCORD_NEW_GAMES_WEBHOOK` with that address.
3. To notify people, make a role (for example "Android news") that members can take, copy its number (Discord developer mode, "Copy role ID") and add it as the variable `DISCORD_NEW_GAMES_ROLE_ID`. Without it, the message is posted without a mention; people still choose how the channel notifies them.

`python3 announce_new.py PREVIOUS.json` shows what would be posted and sends nothing.
