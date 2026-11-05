#!/usr/bin/env python3
"""Print today's lunch at the Snowflake cafe, one station per line: `@Station: item | item | ...`."""
import json, re, sys, urllib.request

URL = "https://snowflake.cafebonappetit.com/"
html = urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read().decode()
dec = json.JSONDecoder()

def at(prefix):
    return dec.raw_decode(html[html.index(prefix) + len(prefix):].lstrip())[0]

items = at("Bamco.menu_items =")
stations = {}
for key in re.findall(r"Bamco\.dayparts\['(\w+)'\]\s*=", html):
    dp = at(f"Bamco.dayparts['{key}'] =")
    if dp["label"] != "Lunch":
        continue
    for st in dp["stations"]:
        for iid in st["items"]:
            it = items.get(str(iid))
            if it:
                name = re.sub(r"<[^>]+>|@", "", it["station"]).strip()
                stations.setdefault(name, []).append(it["label"])

if not stations:
    sys.exit("No lunch on the menu today (weekend/holiday?) or the page format changed.")
for name, labels in stations.items():
    print(f"@{name}: " + " | ".join(labels))
