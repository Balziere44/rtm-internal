# -*- coding: utf-8 -*-
"""Carry the emulator's numbers from the public database into this tool.

    python tools/inject_stats.py

The tool knows what an item is and where a monster drops it, because that is
what the team typed into it. What it has never known is what the emulator
actually gives out: experience, attack, defence, the weight of a shield, the
level you have to be to wear it. Those live in the public database, which
reads them out of a rAthena checkout, and they are exactly the numbers a
balance pass argues about - so a tool for arguing about balance should show
them.

Nothing flows the other way. Items, monsters, drops and skills come *from*
here; this only adds fields the public side owns. If a number disagrees, the
emulator is right about what the game does and this file does not try to
reconcile it.

Reads ../rtm-database/tools/data/stats.json, which the public database keeps
committed, so this needs no network and no emulator checkout of its own.

Re-runnable, and meant to be re-run: `index.html` is a build artifact, so a
rebuilt tool arrives with all of this gone. Running it again puts it back. It
is also idempotent - running it twice changes nothing the second time.
"""

import base64
import gzip
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(ROOT, "index.html")
STATS = os.path.join(ROOT, os.pardir, "rtm-database", "tools", "data", "stats.json")

MARK = "/* emulator numbers - tools/inject_stats.py */"


# ── the bundle ────────────────────────────────────────────────────────────

def manifest_span(html):
    start = html.index('<script type="__bundler/manifest">')
    return html.index(">", start) + 1, html.index("</script>", start)


def resources(html):
    """Every text resource in the manifest, decompressed, with its uuid."""
    a, b = manifest_span(html)
    for uuid, entry in json.loads(html[a:b]).items():
        if not entry["mime"].endswith(("javascript", "json", "plain")):
            continue
        raw = base64.b64decode(entry["data"])
        if entry.get("compressed"):
            raw = gzip.decompress(raw)
        yield uuid, entry, raw.decode("utf-8")


def repack(html, uuid, text):
    """Put one resource back, leaving every other byte of the file alone."""
    data = base64.b64encode(gzip.compress(text.encode("utf-8"))).decode("ascii")
    pat = re.compile(r'("%s": \{[^{}]*?"data": ")[^"]*(")' % uuid)
    if not pat.search(html):
        raise SystemExit("could not find the manifest entry for " + uuid)
    return pat.sub(lambda m: m.group(1) + data + m.group(2), html, count=1)


def literal(text, name):
    head = "const %s=" % name
    if not text.startswith(head):
        return None
    return json.loads(text[len(head):].strip().rstrip(";"))


def relit(name, value):
    return "const %s=%s;\n" % (name, json.dumps(value, ensure_ascii=False,
                                                separators=(",", ":")))


# ── the numbers ───────────────────────────────────────────────────────────

def by_name(rows):
    """First row per name. The emulator lists a handful of names twice, on
    separate ids; the tool has no id to tell them apart with, so the choice is
    arbitrary and being consistent about it is the whole of the fix."""
    out = {}
    for row in rows:
        out.setdefault(row["name"], row)
    return out


def merge_mobs(mobs, stats):
    hit = 0
    for mob in mobs:
        s = stats.get(mob["name"])
        if not s:
            continue
        hit += 1
        for key in ("exp", "jexp", "atk", "def", "mdef"):
            if s.get(key) is not None:
                mob[key] = s[key]
    return hit


def merge_items(items, stats):
    hit = 0
    for item in items:
        s = stats.get(item["name"])
        if not s:
            continue
        hit += 1
        for key in ("id", "atk", "matk", "def", "lv", "slots"):
            if s.get(key) is not None:
                item[key] = s[key]
        # The emulator carries weight in tenths. The public site divides, so
        # this one does too: two databases disagreeing by a factor of ten
        # about how heavy a shield is would be nobody's idea of a good time.
        if s.get("weight") is not None:
            item["weight"] = s["weight"] // 10
        if s.get("refine"):
            item["refine"] = True
        if s.get("loc"):
            item["loc"] = s["loc"]
        if s.get("jobs"):
            item["jobs"] = s["jobs"]
    return hit


# ── the two places the numbers have to appear ─────────────────────────────

MOB_ANCHOR = ('React.createElement("span",null,React.createElement("span",'
              '{style:{color:"#60464a",fontSize:"9px",textTransform:"uppercase",'
              'letterSpacing:"0.1em",display:"block"}},"Size"),'
              'React.createElement("span",{style:{color:"#cebcbe"}},mob.size))')

MOB_CELLS = MOB_ANCHOR + """,
          """ + MARK + """
          ...[["EXP",mob.exp],["JEXP",mob.jexp],["ATK",mob.atk],["DEF",mob.def],["MDEF",mob.mdef]]
            .filter(([,v])=>typeof v==="number")
            .map(([label,v])=>React.createElement("span",{key:label},
              React.createElement("span",{style:{color:"#60464a",fontSize:"9px",textTransform:"uppercase",letterSpacing:"0.1em",display:"block"}},label),
              React.createElement("span",{style:{color:"#cebcbe"}},v.toLocaleString())))"""

ITEM_COMPONENT = MARK + """
function ItemStats({item}){
  const cells = [["ATK",item.atk],["MATK",item.matk],["DEF",item.def],
                 ["LV",item.lv],["SLOTS",item.slots],["WEIGHT",item.weight]]
    .filter(([,v])=>typeof v==="number" && v>0);
  const tags = [];
  if (item.loc && item.loc.length) tags.push(item.loc.join(" / "));
  if (item.refine) tags.push("Refineable");
  if (item.jobs && item.jobs.length) tags.push(item.jobs.join(", "));
  // A consumable has none of these, and a row of zeroes would say less than
  // no row at all.
  if (!cells.length && !tags.length) return null;
  return React.createElement("div",{style:{display:"flex",gap:12,flexWrap:"wrap",
      alignItems:"baseline",fontFamily:"var(--font-mono)",fontSize:"11px",marginBottom:8}},
    ...cells.map(([l,v])=>React.createElement("span",{key:l},
      React.createElement("span",{style:{color:"#60464a",fontSize:"9px",textTransform:"uppercase",letterSpacing:"0.1em",display:"block"}},l),
      React.createElement("span",{style:{color:"#cebcbe"}},v.toLocaleString()))),
    tags.length ? React.createElement("span",{key:"tags",style:{color:"#8a6e72",fontSize:"10px",flexBasis:"100%"}},tags.join(" · ")) : null
  );
}

function ItemCard("""

ITEM_ANCHOR = "\n    // Description\n    item.description &&"
ITEM_ROW = ("\n    " + MARK + "\n    React.createElement(ItemStats,{item}),"
            + ITEM_ANCHOR)


def patch_ui(text, kind):
    """Add the cells that print the numbers. Idempotent by marker."""
    if MARK in text:
        return text, False
    if kind == "mob":
        if MOB_ANCHOR not in text:
            raise SystemExit("the monster stat row moved - the tool was rebuilt "
                             "differently and this script needs looking at")
        return text.replace(MOB_ANCHOR, MOB_CELLS, 1), True
    if "function ItemCard(" not in text or ITEM_ANCHOR not in text:
        raise SystemExit("the item card moved - the tool was rebuilt differently "
                         "and this script needs looking at")
    text = text.replace("function ItemCard(", ITEM_COMPONENT, 1)
    return text.replace(ITEM_ANCHOR, ITEM_ROW, 1), True


# The sprites are in this repository, beside index.html. Pointing at somebody
# else's deployment for them means the pictures go the day that account tidies
# up, and it is the last thread still tying this tool to the old home.
SPRITE_OLD = '"https://crosscutunion99-ops.github.io/RTMInternalE/sprites/"'
SPRITE_NEW = '"sprites/"'


def main():
    if not os.path.exists(STATS):
        print("no %s - the public database has to sit beside this repository"
              % os.path.normpath(STATS), file=sys.stderr)
        return 1

    with io.open(STATS, encoding="utf-8") as fh:
        stats = json.load(fh)
    mob_stats = by_name(stats["mobs"])
    item_stats = by_name(stats["items"])

    html = io.open(TOOL, encoding="utf-8").read()
    before = len(html)
    touched = []

    for uuid, entry, text in list(resources(html)):
        out = text

        mobs = literal(text, "MOBS")
        if mobs is not None:
            hit = merge_mobs(mobs, mob_stats)
            print("monsters: %d of %d matched the emulator" % (hit, len(mobs)))
            out = relit("MOBS", mobs)

        items = literal(text, "ITEMS")
        if items is not None:
            hit = merge_items(items, item_stats)
            print("items:    %d of %d matched the emulator" % (hit, len(items)))
            out = relit("ITEMS", items)

        if "mob.size))" in out and "React.createElement(ElemBadge" in out:
            out, done = patch_ui(out, "mob")
            if done:
                print("monster card: added EXP, ATK, DEF and MDEF")

        if out != text:
            html = repack(html, uuid, out)
            touched.append(uuid)

    # The item card lives in the page template rather than in a resource.
    a = html.index('<script type="__bundler/template">')
    b = html.index("</script>", a)
    template = json.loads(html[html.index(">", a) + 1:b])
    patched, done = patch_ui(template, "item")
    if done:
        print("item card: added ATK, DEF, level, slots, weight and equip slot")
    if SPRITE_OLD in patched:
        patched = patched.replace(SPRITE_OLD, SPRITE_NEW)
        print("sprites: now read from this repository, not the old account's")
    if patched != template:
        # The template is a whole HTML document living inside a <script>, so
        # every closing tag in it has to stay escaped or the browser's parser
        # ends the script on the first one and the page is a blank screen.
        # json.dumps will not do it: "/" needs no escape in JSON, which is
        # true and beside the point.
        body = json.dumps(patched, ensure_ascii=False).replace("</", "<\\/")
        html = html[:html.index(">", a) + 1] + body + html[b:]

    io.open(TOOL, "w", encoding="utf-8", newline="").write(html)
    print("index.html: %d kb -> %d kb" % (before // 1024, len(html) // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
