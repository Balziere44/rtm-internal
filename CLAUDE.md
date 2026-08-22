# RtM Internal

The staff balance tool for Return to Morroc: Refuge. It is the **upstream** of
the public database: numbers are edited here first, and the public site is
generated from what this repository holds.

Not a public site. It carries unreleased content, internal names, and the
team's balance notes.

Home is `github.com/Balziere44/rtm-internal`. The history before the first
commit that added this file came from the tool's original repository, which is
no longer a remote here and is not pushed to: a rebuilt tool arrives as a new
`index.html` committed to this repository, from whoever rebuilt it.

## What is in here

| File | What it is |
| --- | --- |
| `index.html` | The whole tool. One file, ~2.4 MB. |
| `builder.html` | The character builder, same shape. |
| `sprites/` | Monster sprites by mob id, `.gif` / `.png` / `.webp`. |
| `server/` | The backend: accounts, shared notes, live sync. |
| `server/backup/` | The notes the old VPS held the day we moved off it. |
| `tools/` | Scripts that patch the bundle. See below. |

`index.html` is a bundle, not a page anybody hand-edits. It carries a
`<script type="__bundler/manifest">` holding every asset gzipped and base64'd
under a uuid, and a `<script type="__bundler/template">` holding the page that
those uuids get substituted into. The data lives in the manifest as four
JavaScript literals:

| Literal | Rows | What it holds |
| --- | --- | --- |
| `ITEMS` | ~2600 | Name, category, in-game description, every drop source. |
| `MOBS` | ~720 | Stats, race, element, zone, maps, card effect, drop list. |
| `CLASS_DATA_FULL` | 40 | Class descriptions and the skill list the site prints. |
| `CLASSES` | | The emulator's own skill table, with aegis names. |

To read them without a browser, decode the manifest: base64, then gzip, then
the resource is `const NAME=` followed by JSON.

## How it is fed

Croc edits the tool and commits the rebuilt `index.html`. Per-item and
per-monster balance notes are not in the file at all - they live in
localStorage and sync to `/notes/{item,mob,skill}` on the server in `server/`,
over SSE, with a bearer token kept in `rtm_auth_token_v1`. Writes need the
token; reads do not. Notes are working commentary and never reach the public
site.

Until 19 Aug 2026 that server was a VPS belonging to somebody else, at
`rtmrefuge.duckdns.org`. What it held on the day we left is in `server/backup/`.

## Accounts

Signing in puts a name on a note and guards against editing by accident. It is
not security: the page is public and the account list travels inside it, so
anyone who opens the tool can read the list. Closing the tool off is the
hosting's job.

Two stores, tried in that order:

1. **The server** at `window.RTM_API_BASE`, `POST /auth/login`, which returns
   the bearer token the notes sync needs. Ours is in `server/` - a Cloudflare
   Worker, see its README. Accounts are made from inside the tool, by `Meta`.
2. **The list in the file**, used when the server does not answer. Five
   accounts sit in the bundle, and `window.RTM_USERS` at the top of
   `index.html` adds to them. This is the way in when the server is down or
   has not been deployed yet; notes taken this way stay in that browser.

A browser blocked by CORS throws in exactly the same place a wrong password
does, so the first version of this reported "Invalid username or password"
whenever the tool was served from anywhere new - a correct password, a
reachable server, and no way to tell from the screen. The fall-through is what
that cost.

To add somebody, put a line in `window.RTM_USERS` and push:

    window.RTM_USERS = {
      "Nome": "senha",
    };

Signed in against the list rather than the server, there is no token, so notes
stay in that browser's localStorage and are not shared. Sharing needs the
server, which is why `server/` exists.

## How it deploys

Static, no build step. It is served from GitHub Pages, at
`balziere44.github.io/rtm-internal/`. `robots.txt` refuses crawlers. `_headers`
is a Cloudflare Pages file and does nothing where it is now; it stays because
moving to Cloudflare Pages is a matter of connecting the repository, with an
empty build command and `/` as the output directory.

Whichever of the two, neither is access control: anyone with the address opens
the tool, and `GET /notes/*` answers without a token. Closing it off means
Cloudflare Access in front of a Pages project, or a private repository on a
paid GitHub plan. The account list is a name tag, not a lock - it never was
one, and pretending otherwise is how the passwords ended up in a public file.

Whatever address it ends up at has to be in `ALLOWED_ORIGINS` in
`server/wrangler.toml`, or the browser will not let the page reach the server -
which is the exact failure this whole move was about.

To read it locally, serve the folder rather than opening the file directly:

    python -m http.server 8788

## Patching the bundle

`index.html` is a build artifact. Everything we add to it - the sign-in
fall-through, the emulator numbers, the items, the sprite path - is a patch
applied to somebody else's output, and **a rebuilt tool arrives with all of it
gone**. That is not a risk to be avoided, it is the normal case, so the patches
are scripts rather than edits:

    python tools/sync_data.py

Re-run it after every new `index.html`. It is idempotent, so running it when
nothing needs doing changes no bytes.

It reads the public database's finished tables - `db-items.json` and
`db-mobs.json`, built from the client's GRFs and the emulator - and brings the
tool level with them: adds the items it does not have, fills in level, weight,
attack, defence, slots, refineability, equip slot and job list on the ones it
does, gives monsters their experience and combat numbers, and adds the cells
that print all of it. It also repoints the sprites at this repository, which
the bundle otherwise loads from the old account's GitHub Pages.

Run on 19 Aug 2026: 719 monsters updated, 2,583 items updated, **418 added**.
Somebody asked why Bakefuda was missing; the answer was that 418 items were.

Where the client tooltip and the emulator disagree about a number, the public
side already prefers the tooltip - what the game does today - and taking its
finished rows takes that decision with them.

Nothing flows the other way. Items, monsters, drops and skills are typed here
and read *from* here.

### Deleting your own notes

The Noted tab lists everything anybody has written and used to be the one
place none of it could be acted on: taking a note back meant finding the
monster again in a list of seven hundred. Each card there now carries a small
cross on **your** comments and on **your** flag votes, and an entry with
nothing left in it disappears rather than staying as an empty card that still
counts.

Only your own. Somebody else's comment is theirs, and a vote count any reader
can edit is not a vote count. Signed out, no crosses appear at all.

The delete writes to localStorage, which is what the sync layer intercepts, so
it reaches everybody else the same way writing a note does.

### The names are still the client's

The client truncates item names, so the tool says `AcidusSOrb` where the game
says `Acidus Scale Orb`, and the public side keeps a table of 824 such
corrections. The sync deliberately does **not** apply them: every balance note
is filed under the name it was written against, so 824 renames would detach
824 notes' worth of work. Fixing it properly means renaming the items and
rewriting the note keys in the same pass, against the server, once.

## Handing changes to the public database

The public site lives in the `rtm-database` repository, deployed at
rtm-database.pages.dev. It reads this repository - it does not get typed into
by hand, and nothing is copied across by editing two files.

Keep the two checkouts side by side:

    CLAUDE LOCAL/
      rtm-internal/     <- this repository
      rtm-database/     <- the public site

Then, after a change lands here, one command over in `rtm-database` pulls it
across and rebuilds every page:

    python tools/fetch_encyclopedia.py
    python build.py

The fetcher prefers this checkout over the deployment, so a change is carried
across the moment it is saved here - no push, no deploy, no wait. It prints
which copy it read and when that copy was saved; if the date is old, this
repository needs a `git pull`.

What crosses over is item names, categories, descriptions and drop sources;
monster stats, zones and drop lists; class summaries and skill descriptions.
What does not: balance notes, the user list, skill scaling numbers, and
anything the public site's own checker forbids. That filtering is the
fetcher's job and belongs in `rtm-database`, not here.
