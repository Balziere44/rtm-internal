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
localStorage and sync to `https://rtmrefuge.duckdns.org/notes/{item,mob,skill}`
over SSE, with a bearer token kept in `rtm_auth_token_v1`. Writes need the
token; reads do not. Notes are working commentary and never reach the public
site.

## Accounts

Signing in puts a name on a note and guards against editing by accident. It is
not security: the page is public and the account list travels inside it, so
anyone who opens the tool can read the list. Closing the tool off is the
hosting's job.

Two stores, tried in that order:

1. **The server**, `POST /auth/login` on the VPS, which returns the bearer
   token the notes sync needs. It answers only browsers whose origin its CORS
   allowlist names, and today that is the tool's old Vercel address and
   nothing else.
2. **The list in the file**, used when the server does not answer. Five
   accounts sit in the bundle, and `window.RTM_USERS` at the top of
   `index.html` adds to them.

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
stay in that browser's localStorage and are not shared. Shared notes need the
VPS to answer this origin - one line in its CORS allowlist - or a backend of
our own.

## How it deploys

Static, no build step. Cloudflare Pages: connect this repository, leave the
build command empty, set the output directory to `/`. `_headers` marks
everything `noindex` and stops browsers caching the HTML, so a reload always
shows the last commit; `robots.txt` refuses crawlers.

Neither of those is access control. Anyone with the URL can open the tool, and
the notes endpoint answers unauthenticated reads. If this has to be closed off,
put Cloudflare Access in front of the project - that is the only thing here
that actually gates it.

To read it locally, serve the folder rather than opening the file directly:

    python -m http.server 8788

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
