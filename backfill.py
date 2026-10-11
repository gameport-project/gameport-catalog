"""Fills the history of the prices (v1/prices) with the past: two years of history from IsThereAnyDeal, once.

The API allows 1000 requests in a window of 5 minutes and asks not to run at the limit: this keeps to 400 in any window, a request a second at most, waits as long as
Retry-After says when it answers 429, and stops (to be started again, it keeps what it did) on any other refusal, since going on after a refusal is what gets banned.
Not part of the daily work. Needs ITAD_API_KEY (an API key of an app made at https://isthereanydeal.com/apps/), never written to a file. The lines it adds
stop where the lines read from Steam begin, which are never touched but for the day of the first one, moved back when it carries on the last price of the past.
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

import prices

API = "https://api.isthereanydeal.com"
STEAM_SHOP = 61
YEARS = 2
PAUSE = 1.0
LOOKUP_BATCH = 100


class Throttle:
    """At most [calls] requests in any window of [window] seconds: waits when the window is full."""

    def __init__(self, calls, window, clock=time.monotonic, sleep=time.sleep):
        self.calls, self.window, self.clock, self.sleep = calls, window, clock, sleep
        self.times = deque()

    def wait(self):
        now = self.clock()
        while self.times and now - self.times[0] >= self.window:
            self.times.popleft()
        if len(self.times) >= self.calls:
            self.sleep(self.window - (now - self.times[0]))
            now = self.clock()
            while self.times and now - self.times[0] >= self.window:
                self.times.popleft()
        self.times.append(self.clock())


THROTTLE = Throttle(calls=400, window=300)


def call(url, body=None):
    """The answer of the API, as JSON. Waits as long as Retry-After says when the pace is too fast; any other refusal is raised, not retried."""
    for attempt in range(6):
        THROTTLE.wait()
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", "User-Agent": "gameport-catalog"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as answer:
                return json.load(answer)
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == 5:
                raise
            retry = error.headers.get("Retry-After")
            time.sleep(int(retry) + 1 if retry and retry.isdigit() else 60 * (attempt + 1))


def lookup(key, appids):
    """The id IsThereAnyDeal gives each Steam app, by app id (the apps it does not know are left out)."""
    found = {}
    for i in range(0, len(appids), LOOKUP_BATCH):
        chunk = appids[i:i + LOOKUP_BATCH]
        answer = call(f"{API}/lookup/id/shop/{STEAM_SHOP}/v1?key={key}", [f"app/{a}" for a in chunk])
        for a in chunk:
            if answer.get(f"app/{a}"):
                found[a] = answer[f"app/{a}"]
        time.sleep(PAUSE)
    return found


def history(key, game_id, country, currency, since):
    """The lines [day, price, price before discount] of the past of a game in a country, oldest first, one for each change."""
    query = urllib.parse.urlencode({"key": key, "id": game_id, "country": country.upper(), "shops": STEAM_SHOP, "since": since})
    return lines_of(call(f"{API}/games/history/v2?{query}"), currency)


def lines_of(entries, currency):
    """What the API answers (newest first) as lines, oldest first: a price in another currency is ignored, a day that changed twice keeps its last price, and a price that did not change adds nothing."""
    lines = []
    for entry in sorted(entries, key=lambda e: e["timestamp"]):
        deal = entry.get("deal") or {}
        price, regular = deal.get("price") or {}, deal.get("regular") or {}
        if price.get("currency") != currency or price.get("amountInt") is None:
            continue
        day = datetime.datetime.fromisoformat(entry["timestamp"]).astimezone(datetime.timezone.utc).date().isoformat()
        line = [day, int(price["amountInt"]), int(regular.get("amountInt", price["amountInt"]))]
        if lines and lines[-1][0] == day:
            lines[-1] = line
        else:
            lines.append(line)
    out = []
    for line in lines:
        if not out or out[-1][1:3] != line[1:3]:
            out.append(line)
    return out


def merge(ours, past):
    """The past put before our own lines: only what is older than our first line. When the last of the past is the price our first line carries on, that one line is kept, with the earlier day."""
    if not ours:
        return past
    older = [line for line in past if line[0] < ours[0][0]]
    if older and ours[0][1] is not None and older[-1][1:3] == ours[0][1:3]:
        first = [older.pop()[0]] + ours[0][1:]
        return older + [first] + ours[1:]
    return older + ours


MARKS = "prices-backfilled.json"


def pending(ids, marks, countries):
    """The (app, country) pairs whose past is not read yet, in the order of the apps then of the countries."""
    return [(appid, country) for appid in ids for country in countries if appid not in set(marks.get(country, ()))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="at most this many (app, country) pairs in this run: a long backlog is read over several runs")
    args = parser.parse_args()
    key = os.environ.get("ITAD_API_KEY")
    if not key:
        sys.exit("ITAD_API_KEY is not set")
    ids = [a["appid"] for a in json.load(open("v1/catalog.json", encoding="utf-8"))["apps"]]
    marks = json.load(open(MARKS)) if os.path.exists(MARKS) else {}
    todo = pending(ids, marks, prices.COUNTRIES)
    if args.limit:
        todo = todo[:args.limit]
    if not todo:
        print("every past is already read")
        return
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=365 * YEARS)).strftime("%Y-%m-%dT00:00:00Z")
    games = lookup(key, sorted({appid for appid, _ in todo}))
    print(f"{len(todo)} pairs to read, {len(games)} apps known to IsThereAnyDeal", flush=True)
    book = prices.load()
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    added = 0

    def save():
        prices.write_store(book, today)
        with open(MARKS, "w") as out:
            json.dump({country: sorted(done) for country, done in sorted(marks.items())}, out, separators=(",", ":"))
            out.write("\n")

    # An app IsThereAnyDeal does not know yet is not marked: it is asked again at the next run.
    for n, (appid, country) in enumerate([pair for pair in todo if pair[0] in games], 1):
        past = history(key, games[appid], country, prices.COUNTRIES[country], since)
        ours = book.get(str(appid), {}).get(country, [])
        merged = merge(ours, past)
        if merged != ours:
            book.setdefault(str(appid), {})[country] = merged
            added += len(merged) - len(ours)
        marks.setdefault(country, []).append(appid)
        if n % 20 == 0:
            save()
            print(f"{n} pairs read, {added} lines added", flush=True)
    save()
    print(f"history written, {added} lines added", flush=True)


if __name__ == "__main__":
    main()
