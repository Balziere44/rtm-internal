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



# ── deleting your own notes from the Noted tab ────────────────────────────
#
# The Noted tab is where you look at everything you have written, and it was
# the one place you could not act on any of it: taking a note back meant
# finding the monster again in a list of seven hundred. Everything needed to
# fix that was already in the bundle - toggleFlagVote, userVoted,
# getCurrentUser - and the tab just had no controls wired to it.
#
# Only your own. Somebody else's comment is theirs, and a vote count that any
# reader can edit is not a vote count.

NOTED_MARK = "/* delete your own - tools/sync_data.py */"

NOTED_HELPER = """  const [section, setSection] = React.useState("skills");

  """ + NOTED_MARK + """
  const NOTE_STORES = {mob:"rtm_balance_notes_v1", skill:"rtm_skills_notes_v1", item:"rtm_items_notes_v1"};
  function editNote(store, id, change){
    const key = NOTE_STORES[store];
    if(!key) return;
    let all = {};
    try { all = JSON.parse(localStorage.getItem(key)||"{}") || {}; } catch(e) {}
    const next = change(all[id] || {flags:{}, comments:[]});
    const copy = Object.assign({}, all);
    // An entry with nothing left in it is not an empty note, it is not a
    // note. Leaving the key behind keeps the card on screen with nothing on
    // it and keeps it counted in the tab's total.
    if(!next || (!flagKeys(next.flags).length && !(next.comments||[]).length)) delete copy[id];
    else copy[id] = next;
    // Straight to localStorage: the sync layer intercepts the write and
    // pushes it, so the other people looking at this see it go.
    localStorage.setItem(key, JSON.stringify(copy));
    const setter = {mob:setMobNotes, skill:setSkillNotes, item:setItemNotes}[store];
    if(setter) setter(copy);
  }
"""

NOTED_CARD = """  function Card({border, label, name, meta, flags, comments, badge, store, noteId}){
    """ + NOTED_MARK + """
    const me = getCurrentUser();
    const mineFlags = flagKeys(flags).filter(f=>userVoted(flags, f, me));
    const canEdit = !!me && !!store && noteId !== undefined && noteId !== null;
    const xStyle = {
      background:"transparent",border:"none",color:"#8a6e72",cursor:"pointer",
      fontFamily:"var(--font-mono)",fontSize:"11px",lineHeight:1,padding:"0 2px"
    };
    return React.createElement("div",{style:{
      background:"#2a1a1f",border:`1px solid ${border}`,
      borderRadius:"4px",padding:"12px 14px",display:"flex",flexDirection:"column",gap:4
    }},
      React.createElement("div",{style:{fontFamily:"var(--font-heading)",fontSize:"9px",letterSpacing:"0.16em",textTransform:"uppercase",color:"var(--fg-faint)"}},label),
      React.createElement("div",{style:{display:"flex",alignItems:"center",gap:6}},
        (function(){const __k=flagKeys(flags); return __k.length>0;})() && React.createElement("span",{style:{
          width:8,height:8,borderRadius:"50%",background:FLAG_COLORS[flagKeys(flags)[0]]||"#888",
          flexShrink:0,boxShadow:`0 0 4px ${FLAG_COLORS[flagKeys(flags)[0]]||"#888"}`
        }}),
        React.createElement("span",{style:{fontFamily:"var(--font-heading)",fontSize:"13px",fontWeight:700,color:"var(--fg-primary)"}},name),
        badge
      ),
      meta && React.createElement("div",{style:{fontFamily:"var(--font-mono)",fontSize:"10px",color:"var(--fg-faint)"}},meta),
      React.createElement(FlagPills,{flags}),

      canEdit && mineFlags.length > 0 && React.createElement("div",{style:{display:"flex",gap:4,flexWrap:"wrap",marginTop:2,alignItems:"center"}},
        React.createElement("span",{style:{fontFamily:"var(--font-mono)",fontSize:"9px",color:"#60464a"}},"your votes:"),
        mineFlags.map(f=>React.createElement("button",{
          key:f, title:"Remove your "+(FLAG_LABELS[f]||f)+" vote",
          onClick:()=>editNote(store, noteId, n=>toggleFlagVote(n, f, me)),
          style:Object.assign({}, xStyle, {
            border:"1px solid oklch(40% 0.06 355/0.4)", borderRadius:"2px",
            padding:"1px 6px", fontSize:"9px", letterSpacing:"0.08em",
            textTransform:"uppercase", fontFamily:"var(--font-heading)"
          })
        }, "\\u00d7 "+(FLAG_LABELS[f]||f)))
      ),

      (comments && comments.length > 0) && React.createElement("div",{style:{display:"flex",flexDirection:"column",gap:3,marginTop:4}},
        comments.map((c,i)=>React.createElement("div",{key:i,style:{
          background:"#1e0f13",borderRadius:"2px",padding:"5px 8px",
          border:"1px solid oklch(40% 0.06 355/0.3)"
        }},
          React.createElement("div",{style:{display:"flex",alignItems:"center",gap:6}},
            React.createElement("div",{style:{flex:1,fontFamily:"var(--font-mono)",fontSize:"9px",color:"#60464a",marginBottom:2}},
              "@"+c.author+" \\u00b7 "+(new Date(c.ts).toLocaleDateString())),
            canEdit && me && c.author === me && React.createElement("button",{
              title:"Delete this note of yours",
              onClick:()=>{
                if(!confirm("Delete your note on "+name+"?")) return;
                editNote(store, noteId, n=>Object.assign({}, n, {
                  comments:(n.comments||[]).filter((x,j)=>j!==i)
                }));
              },
              style:xStyle
            }, "\\u00d7")
          ),
          React.createElement("div",{style:{fontFamily:"var(--font-body)",fontSize:"12px",color:"#cebcbe",fontStyle:"italic"}},c.text)
        ))
      )
    );
  }"""

# The three lists, each of which has to say which store its cards belong to.
NOTED_SITES = [
    ("notedSkills.map(({ck,cd,s,n})=>React.createElement(Card,{\n              key:ck+s.id,",
     "notedSkills.map(({ck,cd,s,n})=>React.createElement(Card,{\n              key:ck+s.id, store:\"skill\", noteId:s.id,"),
    ("notedItems.map(({it,n})=>React.createElement(Card,{\n              key:it.name,",
     "notedItems.map(({it,n})=>React.createElement(Card,{\n              key:it.name, store:\"item\", noteId:it.name,"),
    ("notedMobs.map(({m,n})=>React.createElement(Card,{\n              key:m.id,",
     "notedMobs.map(({m,n})=>React.createElement(Card,{\n              key:m.id, store:\"mob\", noteId:m.id,"),
]

CARD_START = "  function Card({border, label, name, meta, flags, comments, badge}){"
CARD_END = "React.createElement(Comments,{comments})\n    );\n  }"


def patch_noted(text):
    """Put a delete control on your own notes, in the tab that lists them."""
    if NOTED_MARK in text:
        return text, False
    if CARD_START not in text or NOTED_HELPER.split("\n")[0] not in text:
        raise SystemExit("the Noted tab moved - the tool was rebuilt differently "
                         "and tools/sync_data.py needs looking at")
    a = text.index(CARD_START)
    b = text.index(CARD_END, a) + len(CARD_END)
    text = text[:a] + NOTED_CARD + text[b:]
    text = text.replace(NOTED_HELPER.split("\n")[0], NOTED_HELPER.rstrip("\n"), 1)
    for old, new in NOTED_SITES:
        if old not in text:
            raise SystemExit("a Noted list moved - tools/sync_data.py needs "
                             "looking at")
        text = text.replace(old, new, 1)
    return text, True


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
    patched, done = patch_noted(patched)
    if done:
        print("noted tab: your own notes and votes can be deleted there")
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
