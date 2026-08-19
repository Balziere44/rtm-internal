/* RtM Internal — the tool's backend.
 *
 * A Cloudflare Worker in front of one Durable Object. The tool speaks an API
 * that already existed on a VPS; this reimplements it, route for route, so
 * nothing in the bundle had to change except the address it points at.
 *
 *   POST   /auth/login          {user, pass}  ->  {token, user}
 *   GET    /admin/users                       ->  {users: [name, ...]}
 *   POST   /admin/users         {user, pass}
 *   PUT    /admin/users/:name   {pass}
 *   DELETE /admin/users/:name
 *   GET    /notes/:store                      ->  the notes object
 *   PUT    /notes/:store        the notes object
 *   POST   /notes/reset
 *   GET    /events                            ->  SSE: hello, notes
 *
 * Everything lives in one Durable Object rather than KV. Notes are edited by
 * several people at once and KV is eventually consistent - a note written on
 * one machine can be missing from the next read for up to a minute, which for
 * a shared notes tool means silently losing somebody's work. A Durable Object
 * is a single serialised place, so a read after a write sees the write, and it
 * is also the only thing here that can hold the open SSE streams a broadcast
 * needs.
 *
 * The reason the tool moved off the old VPS: that server named one origin in
 * its CORS allowlist and would not answer any other, so signing in failed from
 * every address the tool was later hosted at. ALLOWED_ORIGINS is that setting,
 * in wrangler.toml where it can be changed without a deploy of anything else.
 */

const ADMIN = "Meta";          // the tool's own UI shows user management to this name only
const STORES = ["item", "mob", "skill"];
const SESSION_DAYS = 30;
const KEEPALIVE_MS = 25000;    // under the 30s most proxies idle out at
const ORIGIN_HEADER = "X-RTM-Origin";  // the Worker vouches for the origin to the object
// The admin password travels the same way, per request, rather than being read
// from the object's own env. An object captures env once, when it is created,
// so a rotated secret would not reach a long-lived one - and whether it ever
// gets recreated is a scheduling detail, not something to hang a password on.
const ADMIN_HEADER = "X-RTM-Admin-Pass";

/* ── CORS ──────────────────────────────────────────────────────────────── */

function allowed(origin, env) {
  if (!origin) return null;
  const list = (env.ALLOWED_ORIGINS || "").split(",")
    .map(s => s.trim()).filter(Boolean);
  if (list.includes("*")) return origin;
  return list.includes(origin) ? origin : null;
}

function cors(origin) {
  const h = {
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin",
  };
  // The header the old server left out. Without it a browser discards the
  // response and the fetch rejects, which upstream looks identical to a wrong
  // password. Everything else here was already correct and still useless.
  if (origin) h["Access-Control-Allow-Origin"] = origin;
  return h;
}

function json(body, status, origin) {
  return new Response(JSON.stringify(body), {
    status: status || 200,
    headers: { "Content-Type": "application/json", ...cors(origin) },
  });
}

/* ── passwords ─────────────────────────────────────────────────────────── */

const enc = new TextEncoder();

function hex(buf) {
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
}

async function derive(pass, salt) {
  const key = await crypto.subtle.importKey("raw", enc.encode(pass), "PBKDF2",
                                            false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt: enc.encode(salt), iterations: 100000 },
    key, 256);
  return hex(bits);
}

async function sha256(text) {
  return hex(await crypto.subtle.digest("SHA-256", enc.encode(text)));
}

function token() {
  return hex(crypto.getRandomValues(new Uint8Array(32)));
}

// Comparison that does not finish early on the first wrong character.
function same(a, b) {
  if (a.length !== b.length) return false;
  let bad = 0;
  for (let i = 0; i < a.length; i++) bad |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return bad === 0;
}

/* ── the Durable Object ────────────────────────────────────────────────── */

export class Hub {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.sql = ctx.storage.sql;
    this.clients = new Set();

    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS users (
        name TEXT PRIMARY KEY, salt TEXT NOT NULL, hash TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY, user TEXT NOT NULL, expires INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS notes (
        store TEXT PRIMARY KEY, body TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
      );
    `);
  }

  /* The admin password is the ADMIN_PASS secret, and stays it. Setting the
   * secret again is how that password is rotated - which is the first thing
   * anybody reaches for, and in the first version of this it did nothing at
   * all once the account existed, silently, with no way to tell from outside.
   *
   * A fingerprint of the secret is kept so the common case is one hash rather
   * than a rewrite on every request. Rotating signs the admin out everywhere,
   * which is the point of rotating.
   *
   * The password never comes from a committed file. That is how the previous
   * five ended up public. */
  async bootstrap() {
    const pass = this.adminPass;
    if (!pass) {
      return this.sql.exec("SELECT COUNT(*) AS n FROM users").one().n > 0;
    }
    const print = await sha256(pass);
    const seen = this.sql.exec(
      "SELECT value FROM meta WHERE key = 'admin_pass'").toArray();
    if (!seen.length || seen[0].value !== print) {
      await this.addUser(ADMIN, pass);
      this.sql.exec("DELETE FROM sessions WHERE user = ?", ADMIN);
      this.sql.exec(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('admin_pass', ?)", print);
    }
    return true;
  }

  async addUser(name, pass) {
    const salt = hex(crypto.getRandomValues(new Uint8Array(16)));
    const hash = await derive(pass, salt);
    this.sql.exec("INSERT OR REPLACE INTO users (name, salt, hash) VALUES (?, ?, ?)",
                  name, salt, hash);
  }

  async check(name, pass) {
    const rows = this.sql.exec(
      "SELECT name, salt, hash FROM users WHERE name = ? COLLATE NOCASE", name).toArray();
    if (!rows.length) return null;
    const row = rows[0];
    return same(await derive(pass, row.salt), row.hash) ? row.name : null;
  }

  whoami(req) {
    const auth = req.headers.get("Authorization") || "";
    const tok = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
    if (!tok) return null;
    this.sql.exec("DELETE FROM sessions WHERE expires < ?", Date.now());
    const rows = this.sql.exec("SELECT user FROM sessions WHERE token = ?", tok).toArray();
    return rows.length ? rows[0].user : null;
  }

  /* Tell every open page that a store changed. The event carries only the
   * store's name: the client re-reads it, which is one more request but keeps
   * a page that reconnects mid-write from applying half an update. */
  broadcast(store) {
    const line = enc.encode(`event: notes\ndata: ${JSON.stringify({ store })}\n\n`);
    for (const w of [...this.clients]) {
      w.write(line).catch(() => this.clients.delete(w));
    }
  }

  async fetch(req) {
    const url = new URL(req.url);
    const origin = req.headers.get(ORIGIN_HEADER) || null;
    this.adminPass = req.headers.get(ADMIN_HEADER) || "";
    const path = url.pathname;
    const send = (b, s) => json(b, s, origin);

    /* ── SSE ── */
    if (path === "/events" && req.method === "GET") {
      const { readable, writable } = new TransformStream();
      const w = writable.getWriter();
      this.clients.add(w);
      w.write(enc.encode("event: hello\ndata: {}\n\n")).catch(() => {});

      // A stream nobody writes to is closed by whatever sits in the middle.
      // The comment is ignored by EventSource and keeps the pipe warm.
      const beat = setInterval(() => {
        w.write(enc.encode(": keepalive\n\n")).catch(() => {
          clearInterval(beat);
          this.clients.delete(w);
        });
      }, KEEPALIVE_MS);
      req.signal?.addEventListener("abort", () => {
        clearInterval(beat);
        this.clients.delete(w);
        w.close().catch(() => {});
      });

      return new Response(readable, {
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          "Connection": "keep-alive",
          ...cors(origin),
        },
      });
    }

    /* ── login ── */
    if (path === "/auth/login" && req.method === "POST") {
      if (!(await this.bootstrap())) {
        return send({ error: "server has no admin account: set the ADMIN_PASS secret" }, 503);
      }
      let body;
      try { body = await req.json(); } catch (e) { return send({ error: "bad request" }, 400); }
      const name = await this.check((body.user || "").trim(), (body.pass || "").trim());
      if (!name) return send({ error: "invalid username or password" }, 401);
      const tok = token();
      this.sql.exec("INSERT INTO sessions (token, user, expires) VALUES (?, ?, ?)",
                    tok, name, Date.now() + SESSION_DAYS * 86400000);
      return send({ token: tok, user: name });
    }

    /* ── notes ── */
    if (path === "/notes/reset" && req.method === "POST") {
      if (this.whoami(req) !== ADMIN) return send({ error: "forbidden" }, 403);
      this.sql.exec("DELETE FROM notes");
      for (const s of STORES) this.broadcast(s);
      return send({ ok: true });
    }

    const note = path.match(/^\/notes\/([a-z]+)$/);
    if (note && STORES.includes(note[1])) {
      const store = note[1];
      if (req.method === "GET") {
        const rows = this.sql.exec("SELECT body FROM notes WHERE store = ?", store).toArray();
        return new Response(rows.length ? rows[0].body : "{}", {
          headers: { "Content-Type": "application/json", ...cors(origin) },
        });
      }
      if (req.method === "PUT") {
        // Reads are open on purpose - the tool pulls before anybody signs in -
        // but a write says who made it, so it needs a session.
        if (!this.whoami(req)) return send({ error: "not signed in" }, 401);
        let body;
        try { body = await req.json(); } catch (e) { return send({ error: "bad request" }, 400); }
        if (body === null || typeof body !== "object" || Array.isArray(body)) {
          return send({ error: "expected an object" }, 400);
        }
        this.sql.exec("INSERT OR REPLACE INTO notes (store, body) VALUES (?, ?)",
                      store, JSON.stringify(body));
        this.broadcast(store);
        return send({ ok: true });
      }
    }

    /* ── users ── */
    if (path.startsWith("/admin/users")) {
      await this.bootstrap();
      if (this.whoami(req) !== ADMIN) return send({ error: "forbidden" }, 403);

      if (path === "/admin/users" && req.method === "GET") {
        const rows = this.sql.exec("SELECT name FROM users ORDER BY name").toArray();
        return send({ users: rows.map(r => r.name) });
      }

      if (path === "/admin/users" && req.method === "POST") {
        let body;
        try { body = await req.json(); } catch (e) { return send({ error: "bad request" }, 400); }
        const name = (body.user || "").trim();
        const pass = (body.pass || "").trim();
        if (!/^[A-Za-z0-9_-]+$/.test(name)) return send({ error: "username must be alphanumeric (or _-)" }, 400);
        if (pass.length < 3) return send({ error: "password must be at least 3 characters" }, 400);
        const taken = this.sql.exec(
          "SELECT name FROM users WHERE name = ? COLLATE NOCASE", name).toArray();
        if (taken.length) return send({ error: "that name is taken" }, 409);
        await this.addUser(name, pass);
        return send({ ok: true });
      }

      const one = path.match(/^\/admin\/users\/(.+)$/);
      if (one) {
        const name = decodeURIComponent(one[1]);
        const rows = this.sql.exec(
          "SELECT name FROM users WHERE name = ? COLLATE NOCASE", name).toArray();
        if (!rows.length) return send({ error: "no such user" }, 404);
        const real = rows[0].name;

        if (req.method === "PUT") {
          let body;
          try { body = await req.json(); } catch (e) { return send({ error: "bad request" }, 400); }
          const pass = (body.pass || "").trim();
          if (pass.length < 3) return send({ error: "password must be at least 3 characters" }, 400);
          await this.addUser(real, pass);
          // Every session that password opened stops here, this one included.
          this.sql.exec("DELETE FROM sessions WHERE user = ?", real);
          return send({ ok: true });
        }

        if (req.method === "DELETE") {
          if (real === ADMIN) return send({ error: "the admin account cannot be deleted" }, 400);
          this.sql.exec("DELETE FROM users WHERE name = ?", real);
          this.sql.exec("DELETE FROM sessions WHERE user = ?", real);
          return send({ ok: true });
        }
      }
    }

    return send({ error: "not found" }, 404);
  }
}

/* ── the Worker ────────────────────────────────────────────────────────── */

export default {
  async fetch(req, env) {
    const origin = allowed(req.headers.get("Origin"), env);

    if (req.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors(origin) });
    }

    // A browser that is not on the list gets told so in words, rather than
    // being handed a reply it will throw away and report as a bad password.
    if (req.headers.get("Origin") && !origin) {
      return json({ error: "this origin is not in ALLOWED_ORIGINS" }, 403, null);
    }

    // One object holds everything: the notes are one small shared document and
    // the SSE clients all have to be reachable from wherever a write lands.
    const id = env.HUB.idFromName("hub");

    const headers = new Headers(req.headers);
    headers.delete(ORIGIN_HEADER);            // never let a client set either
    headers.delete(ADMIN_HEADER);
    if (origin) headers.set(ORIGIN_HEADER, origin);
    if (env.ADMIN_PASS) headers.set(ADMIN_HEADER, env.ADMIN_PASS);

    // Read the body here rather than handing the object a stream. A route
    // that answers before touching the body - an unauthorised write, say -
    // leaves that stream open, and the runtime kills the request mid-flight
    // with "can't read from request stream after response has been sent". The
    // bodies here are a few kilobytes of notes, so buffering costs nothing.
    const init = { method: req.method, headers };
    if (req.method !== "GET" && req.method !== "HEAD") {
      init.body = await req.arrayBuffer();
    }
    return env.HUB.get(id).fetch(new Request(req.url, init));
  },
};
