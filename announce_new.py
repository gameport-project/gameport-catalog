"""Announces on Discord the apps whose Android version just became installable: the games, demos, betas and applications that were not installable in the
catalog of the day before and are now. One framed block with a title and a line for each app: its name as a link to Steam, its kind (game, demo, beta, app), VR
or flat, its price, and its cover on the right. Discord shows nine apps to a message; only the first message notifies, the following ones are posted silently,
so a long list is still one notification.

    python3 announce_new.py PREVIOUS.json                   shows the messages, and sends nothing
    DISCORD_NEW_GAMES_WEBHOOK=<url> python3 announce_new.py PREVIOUS.json     sends them
    DISCORD_NEW_GAMES_WEBHOOK=<url> python3 announce_new.py --test 3          a trial: sends the three newest apps of the catalog, marked "Test", mentioning nobody

PREVIOUS.json is the catalog as it was before the run (the daily workflow keeps a copy of it). CURRENT.json (an argument after it) is v1/catalog.json by default.
DISCORD_NEW_GAMES_ROLE_ID, when set, is the role the first message mentions: its members are notified, the others see the channel as usual.
DISCORD_PRICE_COUNTRY is the country whose prices are shown (fr, in euros, by default); an app with no price there shows none.
The webhook is a secret of the repository, never written in a file. Without a previous catalog nothing is announced: a first run would announce everything.
"""
import io
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

SITE = "https://gameport-project.github.io"
STEAM = "https://store.steampowered.com/app"
MAX_ANNOUNCED = 30
BLURPLE = 0x5865F2
KINDS = {"game": "Game", "demo": "Demo", "beta": "Beta", "application": "App"}
SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "CAD": "CA$", "AUD": "A$", "CHF": "CHF "}
# Discord's flag for a message that does not notify anyone.
SILENT = 1 << 12
# Discord's flag for a message laid out with components (a container, texts, sections with a picture) instead of a content and embeds.
COMPONENTS_V2 = 1 << 15
# A message holds 40 components in all, nested ones counted: the container, its title, its footer, and for each app a section, its text and its picture, with a line
# between two apps. Nine apps make 38.
APPS_PER_MESSAGE = 9
# The side of the square a cover is put in. Discord shows it small and cuts what it is given to a square: a vertical cover would lose its top and its bottom.
THUMBNAIL = 220


def read(path):
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file).get("apps", [])
    except (OSError, ValueError):
        return []


def read_prices(country, folder="v1/prices/now"):
    """What each app costs now in a country: (the symbol of its currency, {appid: (final cents, initial cents)}). Nothing when the file cannot be read."""
    try:
        with open(f"{folder}/{country}.json", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        return "", {}
    currency = data.get("currency", "")
    prices = {int(appid): (line[0], line[1]) for appid, line in (data.get("prices") or {}).items() if isinstance(line, list) and len(line) >= 2}
    return SYMBOLS.get(currency, f"{currency} "), prices


def money(cents, symbol):
    return f"{symbol}{cents / 100:.2f}"


def price_text(price, symbol):
    """"Free", the price, or the price with the old one struck out and the share off; None when the app has no price."""
    if price is None:
        return None
    final, initial = price
    if final == 0:
        return "Free"
    if initial and initial > final:
        return f"{money(final, symbol)} ~~{money(initial, symbol)}~~ (-{round((initial - final) * 100 / initial)}%)"
    return money(final, symbol)


def new_apps(previous, current):
    """The apps that are installable now and were not before (new to the catalog, or whose build was published since), the newest app first."""
    if not previous:
        return []
    before = {app["appid"]: app for app in previous}
    fresh = [app for app in current if app.get("installable") and not (before.get(app["appid"]) or {}).get("installable")]
    return sorted(fresh, key=lambda app: -app["appid"])


def newest(apps, count):
    """The `count` newest installable apps of the catalog, by the day they arrived and then by app id: what a trial announces."""
    ready = [app for app in apps if app.get("installable")]
    return sorted(ready, key=lambda app: (app.get("androidSince") or "", app["appid"]), reverse=True)[:count]


def line(app, price=None, symbol=""):
    """The text of an app: its name as a link, then what it is, where it is played and what it costs."""
    kind = KINDS.get(app.get("type"), "App")
    where = "VR" if app.get("vr") else "Flat"
    name = app["name"].replace("[", "(").replace("]", ")")[:120]
    link = app.get("store") or f"{STEAM}/{app['appid']}"
    facts = " · ".join(part for part in (kind, where, price_text(price, symbol)) if part)
    return f"**[{name}]({link})**\n{facts}"


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "gameport-catalog"})
    with urllib.request.urlopen(request, timeout=30) as answer:
        return answer.read()


def thumbnail(data):
    """The whole cover, in the middle of a square, on a blurred and darkened copy of itself that fills the square: the PNG as bytes."""
    from PIL import Image, ImageFilter

    cover = Image.open(io.BytesIO(data)).convert("RGB")
    fill = max(THUMBNAIL / cover.width, THUMBNAIL / cover.height)
    back = cover.resize((math.ceil(cover.width * fill), math.ceil(cover.height * fill)))
    left, top = (back.width - THUMBNAIL) // 2, (back.height - THUMBNAIL) // 2
    back = back.crop((left, top, left + THUMBNAIL, top + THUMBNAIL)).filter(ImageFilter.GaussianBlur(14))
    back = Image.eval(back, lambda value: int(value * 0.55))
    fit = min(THUMBNAIL / cover.width, THUMBNAIL / cover.height)
    front = cover.resize((max(1, round(cover.width * fit)), max(1, round(cover.height * fit))))
    back.paste(front, ((THUMBNAIL - front.width) // 2, (THUMBNAIL - front.height) // 2))
    out = io.BytesIO()
    back.save(out, "PNG", optimize=True)
    return out.getvalue()


def thumbnails(apps, get=fetch):
    """The square pictures of the apps: {appid: PNG bytes}. An app whose pictures cannot be read has none, and Discord is given the address of its cover instead."""
    made = {}
    for app in apps[:MAX_ANNOUNCED]:
        for url in (app.get("cover"), app.get("header"), app.get("capsule"), app.get("banner")):
            if not url:
                continue
            try:
                made[app["appid"]] = thumbnail(get(url))
                break
            except Exception:
                continue
    return made


def picture_of(app):
    return app.get("cover") or app.get("header") or app.get("capsule") or app.get("banner")


def messages(apps, role_id=None, test=False, prices=None, symbol="", thumbs=None):
    """The messages to send, in order, each a webhook body: one framed block, a title, and a line for each app with its text on the left and its cover on the right,
    a thin line between two apps. The first message notifies; the others are silent. More than MAX_ANNOUNCED are cut."""
    prices = prices or {}
    shown = apps[:MAX_ANNOUNCED]
    hidden = len(apps) - len(shown)
    chunks = [shown[i:i + APPS_PER_MESSAGE] for i in range(0, len(shown), APPS_PER_MESSAGE)]
    out = []
    for number, chunk in enumerate(chunks):
        inside = []
        if number == 0:
            noun = "version" if len(apps) == 1 else "versions"
            inside.append({"type": 10, "content": ("**Test** " if test else "") + (f"<@&{role_id}> " if role_id else "") + f"**{len(apps)} new Android {noun} on Steam**"})
            inside.append({"type": 14, "divider": True, "spacing": 1})
        for position, app in enumerate(chunk):
            if position:
                inside.append({"type": 14, "divider": True, "spacing": 1})
            text = {"type": 10, "content": line(app, prices.get(app["appid"]), symbol)}
            picture = f"attachment://thumb-{app['appid']}.png" if app["appid"] in (thumbs or {}) else picture_of(app)
            # A section needs a picture; an app without any is only its text.
            inside.append({"type": 9, "components": [text], "accessory": {"type": 11, "media": {"url": picture}, "description": app["name"][:100]}} if picture else text)
        if number == len(chunks) - 1:
            more = f"and {hidden} more, " if hidden else ""
            inside.append({"type": 14, "divider": False, "spacing": 1})
            inside.append({"type": 10, "content": f"-# {more}all of them on {SITE}"})
        out.append({
            "username": "GamePort",
            "flags": COMPONENTS_V2 | (0 if number == 0 else SILENT),
            "components": [{"type": 17, "accent_color": BLURPLE, "components": inside}],
            "allowed_mentions": {"parse": [], "roles": [role_id] if role_id and number == 0 else []},
        })
    return out


def files_of(body, thumbs):
    """The pictures a message uses, by the name it gives them: {name: PNG bytes}."""
    used = {}
    for item in body["components"][0]["components"]:
        url = (item.get("accessory") or {}).get("media", {}).get("url", "")
        if url.startswith("attachment://"):
            name = url[len("attachment://"):]
            used[name] = thumbs[int(name[len("thumb-"):-len(".png")])]
    return used


def multipart(body, files):
    """The body and the content type of a webhook call that carries pictures: the message as JSON, then each file."""
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n{json.dumps(body)}\r\n'.encode()]
    for index, (name, data) in enumerate(files.items()):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files[{index}]"; filename="{name}"\r\nContent-Type: image/png\r\n\r\n'.encode())
        parts += [data, b"\r\n"]
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def send(url, body, files=None):
    url += ("&" if "?" in url else "?") + "with_components=true"
    if files:
        # Discord matches each picture to its place in the message by the order of the attachments.
        body = {**body, "attachments": [{"id": index, "filename": name} for index, name in enumerate(files)]}
        data, content_type = multipart(body, files)
    else:
        data, content_type = json.dumps(body).encode(), "application/json"
    request = urllib.request.Request(url, data=data, headers={"Content-Type": content_type, "User-Agent": "gameport-catalog"})
    with urllib.request.urlopen(request, timeout=30) as answer:
        return answer.status


def main():
    argv = sys.argv[1:]
    test = "--test" in argv
    if test:
        # A trial: the newest apps of the catalog stand for the new ones, and nobody is mentioned.
        position = argv.index("--test") + 1
        count = int(argv[position]) if position < len(argv) and argv[position].isdigit() else 3
        apps = newest(read("v1/catalog.json"), count)
        role = None
    else:
        args = [a for a in argv if not a.startswith("--")]
        if not args:
            sys.exit(__doc__)
        apps = new_apps(read(args[0]), read(args[1] if len(args) > 1 else "v1/catalog.json"))
        role = os.environ.get("DISCORD_NEW_GAMES_ROLE_ID") or None
    if not apps:
        print("no new Android version to announce")
        return
    symbol, prices = read_prices(os.environ.get("DISCORD_PRICE_COUNTRY") or "fr")
    thumbs = thumbnails(apps)
    bodies = messages(apps, role, test=test, prices=prices, symbol=symbol, thumbs=thumbs)
    for number, body in enumerate(bodies, 1):
        print(f"--- message {number}/{len(bodies)}{' (silent)' if body['flags'] & SILENT else ''}")
        for item in body["components"][0]["components"]:
            if item["type"] == 10:
                print(item["content"])
            elif item["type"] == 9:
                print("  " + item["components"][0]["content"].replace("\n", " · "))
    url = os.environ.get("DISCORD_NEW_GAMES_WEBHOOK")
    if not url:
        print("\nNothing was sent: DISCORD_NEW_GAMES_WEBHOOK is not set.")
        return
    for number, body in enumerate(bodies, 1):
        try:
            print(f"message {number}/{len(bodies)}: {send(url, body, files_of(body, thumbs))}")
        except urllib.error.HTTPError as error:
            sys.exit(f"Discord answered {error.code}")
        time.sleep(1)


if __name__ == "__main__":
    main()
