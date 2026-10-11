"""Keeps the history of the prices of the apps of v1/catalog.json, in a few currencies.

Run every few hours: it reads what Steam sells each app for now, and adds a line to the history of an app only when the price changed. A change that could be a
glitch of the reading (the game no longer sold, a paid game suddenly at zero) is written only when a second reading, the next run, says the same.
The history starts the day this first ran: nothing is known of before. The format is described in README.md.
"""
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# The countries Steam is asked for, and the currency it answers in. Prices differ from a country to another, not only by the rate.
COUNTRIES = {
    "us": "USD", "gb": "GBP", "fr": "EUR", "ch": "CHF", "pl": "PLN", "br": "BRL", "jp": "JPY", "no": "NOK", "id": "IDR", "my": "MYR", "ph": "PHP",
    "sg": "SGD", "th": "THB", "vn": "VND", "kr": "KRW", "ua": "UAH", "mx": "MXN", "ca": "CAD", "au": "AUD", "nz": "NZD", "cn": "CNY", "in": "INR",
    "cl": "CLP", "pe": "PEN", "co": "COP", "za": "ZAR", "hk": "HKD", "tw": "TWD", "sa": "SAR", "ae": "AED", "il": "ILS", "kz": "KZT", "kw": "KWD",
    "qa": "QAR", "cr": "CRC", "uy": "UYU",
}
# One country for each currency Steam bills in (the euro zone has "fr"): a price is the same in every country that has the currency. Russia, Turkey and
# Argentina are left out: Steam sells nothing there, or in dollars, which "us" has.
# Where the files are: one folder for the version of the format. A format that is published is never changed in place: a new one gets a new folder (v2/) and the old one
# is kept up to date for as long as apps read it.
ROOT = "v1/prices"
PENDING = "prices-pending.json"
STORE_API = "https://api.steampowered.com"
BATCH = 100              # apps for each call of the store API
PAUSE = 4                # seconds between two calls: Steam answers 429 to a faster pace

# What a reading says of an app in a country: (final, initial, end of the discount or None), or one of these.
UNAVAILABLE = "unavailable"   # Steam answers for the app but sells it nowhere in this country: no price


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


def price_items(key, ids, country):
    """What Steam sells each app for in one country, by app id: the raw store items."""
    items = {}
    for i in range(0, len(ids), BATCH):
        request = {
            "ids": [{"appid": a} for a in ids[i:i + BATCH]],
            "context": {"language": "english", "country_code": country.upper()},
            "data_request": {},
        }
        body = get_json(f"{STORE_API}/IStoreBrowseService/GetItems/v1?key={key}&{urllib.parse.urlencode({'input_json': json.dumps(request)})}")
        for item in body["response"].get("store_items", []):
            if item.get("success") == 1:
                items[item["appid"]] = item
        time.sleep(PAUSE)
    return items


def reading_of(item):
    """What the item says: (final, initial, end) in cents and a Unix time, (0, 0, None) for a free app, UNAVAILABLE when it is not sold there, None when the answer has no word of it."""
    if not item:
        return None
    if item.get("is_free"):
        return (0, 0, None)
    option = item.get("best_purchase_option") or {}
    final = option.get("final_price_in_cents")
    if final is None:
        return UNAVAILABLE
    initial = option.get("original_price_in_cents")
    ends = [d.get("discount_end_date") for d in option.get("active_discounts") or [] if d.get("discount_end_date")]
    return (int(final), int(initial if initial is not None else final), min(ends) if ends else None)


def _last(lines):
    """The reading the last line stands for, or None when there is no line."""
    if not lines:
        return None
    line = lines[-1]
    if line[1] is None:
        return UNAVAILABLE
    return (line[1], line[2], line[3] if len(line) > 3 else None)


def _suspicious(before, now):
    """A change that a bad reading could make: the app no longer sold, or a paid app suddenly free."""
    if now == UNAVAILABLE:
        return before != UNAVAILABLE
    return before not in (None, UNAVAILABLE) and before[0] > 0 and now[0] == 0


def _line(day, reading):
    if reading == UNAVAILABLE:
        return [day, None, None]
    final, initial, end = reading
    return [day, final, initial] + ([end] if end else [])


def record(history, pending, appid, country, reading, today):
    """Updates the history of an app in a country with a reading. True when a line was added.

    The same price again adds nothing (the end of its discount may be refreshed). A change goes in at once, except a suspicious one, which waits for the next
    reading to say the same and then goes in with the day it was first seen. An app never sold in a country gets no line.
    """
    if reading is None:
        return False
    lines = history.setdefault(str(appid), {}).setdefault(country, [])
    key = f"{appid}/{country}"
    before = _last(lines)
    if before is None and reading == UNAVAILABLE:
        history[str(appid)].pop(country)
        if not history[str(appid)]:
            history.pop(str(appid))
        return False
    if before is not None and reading != UNAVAILABLE and before != UNAVAILABLE and reading[:2] == before[:2]:
        pending.pop(key, None)
        if reading[2] != before[2]:
            lines[-1] = _line(lines[-1][0], reading)
        return False
    if before == reading:
        pending.pop(key, None)
        return False
    if before is not None and _suspicious(before, reading):
        seen = pending.get(key)
        if seen is None or _last([seen]) != reading:
            pending[key] = _line(today, reading)
            return False
        lines.append(seen)
        pending.pop(key)
        return True
    pending.pop(key, None)
    lines.append(_line(today, reading))
    return True


def render_history(appid, by_country):
    """The file of one app: its lines in every country, oldest first."""
    return json.dumps({"v": 1, "appid": int(appid), "lines": by_country}, separators=(",", ":")) + "\n"


def render_now(country, history):
    """The file of one country: what each app costs there now (the last line of its history), by app id. An app no longer sold there is left out."""
    prices = {}
    for appid in sorted(history, key=int):
        lines = history[appid].get(country)
        if lines and lines[-1][1] is not None:
            prices[appid] = lines[-1][1:]
    return json.dumps({"v": 1, "country": country, "currency": COUNTRIES[country], "prices": prices}, separators=(",", ":")) + "\n"


def render_index(updated):
    """What an app reads first: the version of the format, the countries with their currency, and the day of the last change."""
    return json.dumps({"v": 1, "updated": updated, "currencies": COUNTRIES}, separators=(",", ":")) + "\n"


def render_pending(pending):
    return json.dumps(pending, sort_keys=True, separators=(",", ":")) + "\n"


def write_if_changed(path, text):
    if os.path.exists(path) and open(path, encoding="utf-8").read() == text:
        return False
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as out:
        out.write(text)
    os.replace(path + ".tmp", path)
    return True


def load(root=ROOT):
    """The history of every app, read from its file: {app id: {country: lines}}."""
    history = {}
    folder = os.path.join(root, "history")
    if not os.path.isdir(folder):
        return history
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as file:
                data = json.load(file)
            history[str(data["appid"])] = data["lines"]
        except (OSError, ValueError, KeyError):
            continue
    return history


def write_store(history, today, root=ROOT):
    """Writes what changed: the file of each app whose history changed, then each country's file of current prices, then the index when anything did. The names of the files that changed."""
    changed = []
    for appid in history:
        path = os.path.join(root, "history", f"{appid}.json")
        if write_if_changed(path, render_history(appid, history[appid])):
            changed.append(path)
    for country in COUNTRIES:
        path = os.path.join(root, "now", f"{country}.json")
        if write_if_changed(path, render_now(country, history)):
            changed.append(path)
    index = os.path.join(root, "index.json")
    previous = None
    try:
        previous = json.load(open(index, encoding="utf-8"))
    except (OSError, ValueError):
        pass
    # The day of the last change moves only when something changed: a run with nothing new leaves every file as it was.
    if changed or previous is None or previous.get("currencies") != COUNTRIES:
        if write_if_changed(index, render_index(today)):
            changed.append(index)
    return changed


def load_pending(path=PENDING):
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return {}


def main():
    key = os.environ.get("STEAM_API_KEY")
    if not key:
        sys.exit("STEAM_API_KEY is not set")
    ids = [a["appid"] for a in json.load(open("v1/catalog.json", encoding="utf-8"))["apps"]]
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    history = load()
    pending = load_pending()
    changed = 0
    for country in COUNTRIES:
        items = price_items(key, ids, country)
        for appid in ids:
            changed += record(history, pending, appid, country, reading_of(items.get(appid)), today)
        print(f"{country}: {len(items)} of {len(ids)} apps answered")
    files = write_store(history, today)
    write_if_changed(PENDING, render_pending(pending))
    print(f"{len(files)} files written, {changed} changes, {len(pending)} waiting for a second reading" if files else f"nothing changed, {len(pending)} waiting for a second reading")


if __name__ == "__main__":
    main()
