---
name: cafe-food-label
description: Make Phomemo food labels for packaged lunch Louie brought home from the Snowflake Bon Appétit cafe, e.g. "umami combo, grill roast beef + fries".
---

# cafe-food-label

Louie labels the cafe food he brings home on his Phomemo M110. You look up today's lunch, write the label
text, and hand back print links. Load `phomymo-link` too; its scripts do the rendering.

## Input

`,` separates labels (one per bag). `+` joins items into one label, even across stations
(`umami fried rice + grill fries`). If a `+` looks like a typo for `,`, such as `global combo + umami combo`,
confirm with him before making anything.

- `<station> combo`: everything that station served today, in menu order. Being exact doesn't matter;
  include alternates (both Vegan and regular Lemongrass Chicken).
- `<station> <item>`: just that item, matched loosely.
- e.g. `umami combo, grill roast beef + fries, hearth squash pizza` gives three labels.

If he gives just a station name: Global, Gold Coast and Umami are usually combos; Pure, The Hearth and
Gold Coast Grill are usually pick-one. Ask only if you can't tell.

## Steps

Run the scripts in one bash call each, finding the skill with
`D="$(ls -1dt /mnt/skills/*/cafe-food-label ~/.claude/skills/synced/*/cafe-food-label 2>/dev/null | head -1)"`.

1. `python3 $D/menu.py` prints today's lunch, one `@Station: item | item` line per station.
2. Write each label's text from the full menu names, joining items with `, `.
3. Get `/Phomymo Templates/48x40 Food Label (Menu).json` from his main Dropbox (connector
   `download_link`, then `curl`). Use it for every label, even single items.
4. `python3 $D/labels.py template.json OUT_DIR "text 1" "text 2" ...` renders every label. For
   each one it picks the largest font from 28 down to 20 (the floor for legibility on his printer) that fits.
   It prints `FITS <size> <text>` or `OVERFLOW 20 <text>`, and writes `OUT_DIR/labels.html` (every
   label's preview and print button) and `OUT_DIR/tile.png` (all previews, for you to check).
5. For an `OVERFLOW`, shorten that label's text however you judge best and run it again with every label.

## Reply

Publish `OUT_DIR/labels.html` with the **Artifact** tool, as phomymo-link says; never `SendUserFile`. The
links exist only in files: never paste, retype or rebuild one, since a single wrong character breaks the
label. In chat, each label's text, plus a note on anything you shortened or guessed.
