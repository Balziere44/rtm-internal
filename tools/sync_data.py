# -*- coding: utf-8 -*-
"""Bring the tool level with the public database.

    python tools/sync_data.py

The public database is built from the game client's own GRFs plus the
emulator's tables, and it is kept current. The tool is not: it holds what was
typed into it, which is most of the game and then a gap that widens with every
client update. Somebody asked why Bakefuda was missing, and the answer was that
418 items were.

So this reads `../rtm-database/assets/data/db-items.json` and `db-mobs.json` -
the finished tables the public site serves - and:

  * adds every item the tool does not have, with its drops, its category and
    its tooltip;
  * fills in the numbers on the ones it does have: level, weight, attack,
    magic attack, defence, magic defence, slots, refineability, equip slot and
    job list;
  * does the same for monsters: experience, job experience, attack, defence
    and magic defence;
  * adds the cells that print all of it, since the tool never had anywhere to
    show a weight.

Where the client tooltip and the emulator disagree about a number, the public
database already prefers the tooltip - what the game does today, as the GRFs
describe it - and taking its finished rows means taking that decision with
them rather than making a different one here.

**Names are left alone.** The client truncates them, so the tool calls a thing
`AcidusSOrb` where the game calls it `Acidus Scale Orb`, and the public side
carries a table of 824 such corrections. Renaming here would be an
improvement, except that every balance note is filed under the name it was
written against, and 824 renames would detach every one of them. That wants to
be one deliberate migration - notes and all - not a side effect of a sync.

Nothing flows the other way. Items, monsters, drops and skills are typed in
this tool and read *from* here by the public side; this only brings back what
the public side learned from the client and the emulator.

Re-runnable, and meant to be re-run: `index.html` is a build artifact, so a
rebuilt tool arrives with all of this gone. It is idempotent, so running it
when nothing needs doing changes no bytes.
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
PUBLIC = os.path.join(ROOT, os.pardir, "rtm-database")
ITEMS_JSON = os.path.join(PUBLIC, "assets", "data", "db-items.json")
MOBS_JSON = os.path.join(PUBLIC, "assets", "data", "db-mobs.json")
RENAMES = os.path.join(PUBLIC, "tools", "data", "itemnames.json")

MARK = "/* emulator numbers - tools/inject_stats.py */"


# ── the bundle ────────────────────────────────────────────────────────────

def manifest_span(html):
    start = html.index('<script type="__bundler/manifest">')
    return html.index(">", start) + 1, html.index("</script>", start)


def resources(html):
    a, b = manifest_span(html)
    for uuid, entry in json.loads(html[a:b]).items():
        if not entry["mime"].endswith(("javascript", "json", "plain")):
            continue
        raw = base64.b64decode(entry["data"])
        if entry.get("compressed"):
            raw = gzip.decompress(raw)
        yield uuid, entry, raw.decode("utf-8")


def repack(html, uuid, text):
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


def load(path, what):
    if not os.path.exists(path):
        raise SystemExit("no %s - the public database has to sit beside this "
                         "repository, and %s has to have been built"
                         % (os.path.normpath(path), what))
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ── the public tables ─────────────────────────────────────────────────────

def unpack(payload):
    """The published payload is columnar to keep it small. Undo that."""
    cols = payload["cols"]
    return [dict(zip(cols, row)) for row in payload["rows"]]


def item_sources(row, tables):
    """[id, name, level, zone, pct, mvp] back into what the tool reads.

    Race and element are null because the published row does not carry them
    per source; the tool prints them only when it has them, and inventing a
    value would be worse than the blank it already handles.
    """
    out = []
    for s in row.get("src") or ():
        out.append({
            "mob_id": s[0],
            "mob_name": tables["mobs"][s[1]] if s[1] < len(tables["mobs"]) else "",
            "mob_level": s[2],
            "mob_zone": tables["zones"][s[3]] if s[3] < len(tables["zones"]) else "",
            "mob_race": None,
            "mob_element": None,
            "mob_is_mvp": bool(s[5]),
            "drop_pct": s[4],
        })
    return out


def numbers(item, row, tables):
    """The fields the tool never had. Public wins: it read the client."""
    for key in ("id", "lv", "weight", "atk", "matk", "def", "mdef", "slots"):
        if row.get(key) is not None:
            item[key] = row[key]
    item["refine"] = bool(row.get("refine"))
    locs = [tables["locs"][i] for i in (row.get("loc") or ())
            if i < len(tables["locs"])]
    if locs:
        item["loc"] = locs
    jobs = row.get("jobs")
    if isinstance(jobs, int) and jobs < len(tables["jobs"]) and tables["jobs"][jobs]:
        item["jobs"] = tables["jobs"][jobs]


def merge_items(items, public, tables, renames):
    known = {}
    for it in items:
        known.setdefault(renames.get(it["name"], it["name"]), it)

    filled = added = 0
    for row in public:
        it = known.get(row["name"])
        if it is None:
            cats = tables["cats"]
            new = {
                "name": row["name"],
                "category": cats[row["cat"]] if row["cat"] < len(cats) else "",
                "description": row.get("desc") or "",
                "sources": item_sources(row, tables),
            }
            numbers(new, row, tables)
            items.append(new)
            added += 1
            continue
        numbers(it, row, tables)
        # An item the tool holds with no drops, that the public side knows the
        # drops for, is worth completing. One that already has them keeps them:
        # the curated list is the better of the two and it is the tool's own.
        if not it.get("sources") and (row.get("src") or ()):
            it["sources"] = item_sources(row, tables)
        filled += 1
    items.sort(key=lambda i: i["name"])
    return filled, added


def merge_mobs(mobs, public):
    by = {m["name"]: m for m in public}
    hit = 0
    for mob in mobs:
        row = by.get(mob["name"])
        if not row:
            continue
        hit += 1
        for key in ("exp", "jexp", "atk", "def", "mdef"):
            if row.get(key) is not None:
                mob[key] = row[key]
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
  // A list when it came from the emulator export, one joined string when it
  // came from the published table. Both arrive here.
  if (item.jobs && item.jobs.length)
    tags.push(Array.isArray(item.jobs) ? item.jobs.join(", ") : item.jobs);
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
    items_pub = load(ITEMS_JSON, "db-items.json")
    mobs_pub = load(MOBS_JSON, "db-mobs.json")
    renames = load(RENAMES, "itemnames.json")

    public_items = unpack(items_pub)
    public_mobs = unpack(mobs_pub)

    html = io.open(TOOL, encoding="utf-8").read()
    before = len(html)

    for uuid, entry, text in list(resources(html)):
        out = text

        mobs = literal(text, "MOBS")
        if mobs is not None:
            hit = merge_mobs(mobs, public_mobs)
            print("monsters: %d of %d given the emulator's numbers"
                  % (hit, len(mobs)))
            out = relit("MOBS", mobs)

        items = literal(text, "ITEMS")
        if items is not None:
            was = len(items)
            filled, added = merge_items(items, public_items, items_pub, renames)
            print("items:    %d updated, %d added - %d in the tool, was %d"
                  % (filled, added, len(items), was))
            out = relit("ITEMS", items)

        if "mob.size))" in out and "React.createElement(ElemBadge" in out:
            out, done = patch_ui(out, "mob")
            if done:
                print("monster card: added EXP, ATK, DEF and MDEF")

        if out != text:
            html = repack(html, uuid, out)

    a = html.index('<script type="__bundler/template">')
    b = html.index("</script>", a)
    template = json.loads(html[html.index(">", a) + 1:b])
    patched, done = patch_ui(template, "item")
    if done:
        print("item card: added the numbers row")
    if SPRITE_OLD in patched:
        patched = patched.replace(SPRITE_OLD, SPRITE_NEW)
        print("sprites: now read from this repository")
    if patched != template:
        # Closing tags inside the template have to stay escaped or the
        # browser's parser ends the script on the first one and the page is
        # blank. json.dumps will not do it: "/" needs no escape in JSON,
        # which is true and beside the point.
        body = json.dumps(patched, ensure_ascii=False).replace("</", "<\\/")
        html = html[:html.index(">", a) + 1] + body + html[b:]

    io.open(TOOL, "w", encoding="utf-8", newline="").write(html)
    print("index.html: %d kb -> %d kb" % (before // 1024, len(html) // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
