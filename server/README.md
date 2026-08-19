# The tool's backend

A Cloudflare Worker with one Durable Object. It replaces the VPS the tool used
to talk to, route for route, so the only thing that changed in the tool itself
is the address at the top of `index.html`.

Why it moved: the old server named a single origin in its CORS allowlist and
was not ours to change, so signing in failed from every address the tool was
hosted at afterwards - and failed as *"Invalid username or password"*, because
a blocked response and a rejected password arrive at the same `catch`. The
accounts and everyone's balance notes also lived on somebody else's machine.

## What it serves

| Route | Auth | Does |
| --- | --- | --- |
| `POST /auth/login` | — | `{user, pass}` in, `{token, user}` out |
| `GET /notes/:store` | — | the notes for `item`, `mob` or `skill` |
| `PUT /notes/:store` | signed in | replaces that store, tells every open page |
| `POST /notes/reset` | admin | empties all three |
| `GET /admin/users` | admin | `{users: [name, ...]}` |
| `POST /admin/users` | admin | `{user, pass}` |
| `PUT /admin/users/:name` | admin | `{pass}`, and signs that person out |
| `DELETE /admin/users/:name` | admin | removes them |
| `GET /events` | — | SSE: `hello` once, then `notes` on every write |

Admin is the account named `Meta`, because the tool's own user-management
screen only shows itself to that name. Passwords are stored as PBKDF2-SHA256
over a per-account salt, 100,000 iterations - the file never holds a password
and neither does this repository.

## Deploying it

From this folder:

```bash
npm install
npx wrangler login
npx wrangler deploy
npx wrangler secret put ADMIN_PASS
```

`wrangler login` opens a browser once. `deploy` prints the address it published
to, something like `https://rtm-internal-api.<your-subdomain>.workers.dev`.

`ADMIN_PASS` is the password for `Meta`. The account is created the first time
somebody signs in, and **setting the secret again changes the password** -
running `secret put` is how it is rotated, which signs `Meta` out everywhere
and leaves every other account and all the notes untouched. Until the secret is
set, a sign in answers *"server has no admin account"* rather than pretending
the password was wrong.

Everybody else is created from inside the tool, by `Meta`, and their passwords
are changed there too.

Then carry over the notes the old VPS held, saved in `backup/`:

```bash
node import-notes.mjs https://rtm-internal-api.<your-subdomain>.workers.dev
```

It asks for the admin password rather than taking it on the command line.
Run it **before** anybody starts writing notes on the new server - it replaces
each store whole, so a later run would put the old notes back over the new
ones.

Last, point the tool at it: one line at the top of `../index.html`.

```js
window.RTM_API_BASE = "https://rtm-internal-api.<your-subdomain>.workers.dev";
```

## Who may talk to it

`ALLOWED_ORIGINS` in `wrangler.toml`, comma separated. This is the setting the
old server got wrong, so it is worth getting right: add the address the tool is
hosted at, deploy again, done. A browser from an address not on the list is
told exactly that, in words, instead of being handed a reply it will silently
throw away.

`*` allows any origin. The notes are readable without signing in either way, so
this is not the thing keeping strangers out - it only decides which pages the
browser will let talk to the server.

## Working on it

```bash
npx wrangler dev
```

Runs the whole thing locally, Durable Object included, against a local
database - no Cloudflare account touched. It reads `ADMIN_PASS` from
`.dev.vars`, which is not committed. Point a local copy of the tool at
`http://127.0.0.1:8787`.

## What it costs

Nothing, on the free plan, at this size. Durable Objects there are limited to
100,000 requests and 13,000 GB-s of duration a day, and an object counts as
running for as long as an SSE stream is open. One object serves everybody, so
the cost is *hours anybody has the tool open*, not people: a tab left open
around the clock would spend about 11,000 GB-s of the 13,000. Closing tabs at
the end of the day keeps it comfortable, and going over means requests fail
until 00:00 UTC rather than a bill.

The notes are three JSON documents of a few kilobytes. Storage is not a
concern; the daily write limit of 100,000 rows is roughly 100,000 edits.
