/* Create accounts on the server, in a batch.
 *
 *   node add-users.mjs https://rtm-internal-api.<you>.workers.dev Orn Croc Move
 *   node add-users.mjs https://rtm-internal-api.<you>.workers.dev --from-bundle
 *
 * Signs in as the admin and creates the accounts. Named on the command line,
 * each gets a freshly generated password, printed once - hand each person
 * their line.
 *
 * --from-bundle instead reads the account list the tool itself carries, out of
 * the manifest in ../index.html, and recreates every one of them under the
 * password it already had. Nobody has to learn a new one and no password is
 * typed, printed or written to a file on the way.
 *
 * Worth knowing which you are choosing: those built-in passwords are in this
 * repository, in a file anybody can read. Carrying them over keeps the tool
 * working exactly as the team remembers it and keeps that property too.
 *
 * A name that already exists is reported and skipped, so re-running to add one
 * more person is safe.
 */

import { createInterface } from "node:readline";
import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const [, , rawBase, ...names] = process.argv;
const base = (rawBase || "").replace(/\/+$/, "");
if (!base.startsWith("http") || !names.length) {
  console.error("usage: node add-users.mjs https://your-worker-url Name [Name ...]");
  process.exit(1);
}

/* The five accounts the tool ships with, read out of its own bundle: base64
 * inside the manifest, gzip inside that, and the literal is plain JSON once
 * the trailing comma is gone. */
function fromBundle() {
  const file = join(dirname(fileURLToPath(import.meta.url)), "..", "index.html");
  const html = readFileSync(file, "utf8");
  const open = html.indexOf('<script type="__bundler/manifest">');
  if (open < 0) throw new Error("no bundler manifest in " + file);
  const manifest = JSON.parse(
    html.slice(html.indexOf(">", open) + 1, html.indexOf("</script>", open)));
  for (const entry of Object.values(manifest)) {
    if (!/javascript|json|plain/.test(entry.mime)) continue;
    let text = Buffer.from(entry.data, "base64");
    text = (entry.compressed ? gunzipSync(text) : text).toString("utf8");
    const m = text.match(/const AUTH_USERS = (\{[\s\S]*?\n\});/);
    if (m) return Object.entries(JSON.parse(m[1].replace(/,(\s*\})/g, "$1")));
  }
  throw new Error("no AUTH_USERS in the bundle");
}

const useBundle = names.length === 1 && names[0] === "--from-bundle";
const bad = useBundle ? [] : names.filter(n => !/^[A-Za-z0-9_-]+$/.test(n));
if (bad.length) {
  console.error("these names are not allowed (letters, digits, _ and - only): " + bad.join(", "));
  process.exit(1);
}

/* Readable at a glance and still random: no l/I/1 or O/0 to misread down the
 * phone, and 10 characters out of a 30 letter alphabet is far past guessing. */
function makePassword(len = 10) {
  const alphabet = "abcdefghijkmnpqrstuvwxyz23456789";
  const bytes = new Uint8Array(len);
  crypto.getRandomValues(bytes);
  return [...bytes].map(b => alphabet[b % alphabet.length]).join("");
}

async function credentials() {
  if (process.env.RTM_ADMIN_PASS) {
    return { user: process.env.RTM_ADMIN_USER || "Meta", pass: process.env.RTM_ADMIN_PASS };
  }
  if (!process.stdin.isTTY) {
    const lines = readFileSync(0, "utf8").split(/\r?\n/);
    return { user: (lines[0] || "").trim() || "Meta", pass: (lines[1] || "").trim() };
  }
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  const ask = q => new Promise(resolve => rl.question(q, resolve));
  const user = (await ask('admin username (just press Enter for "Meta"): ')).trim() || "Meta";
  process.stdout.write("password: ");
  const echo = rl._writeToOutput;
  rl._writeToOutput = () => {};
  const pass = (await ask("")).trim();
  rl._writeToOutput = echo;
  process.stdout.write("\n");
  rl.close();
  return { user, pass };
}

const { user, pass } = await credentials();
if (!pass) {
  console.error("no password given");
  process.exit(1);
}

const login = await fetch(base + "/auth/login", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ user, pass }),
});
if (!login.ok) {
  console.error("sign in failed: HTTP " + login.status,
                await login.text().catch(() => ""));
  process.exit(1);
}
const { token } = await login.json();

const wanted = useBundle ? fromBundle() : names.map(n => [n, makePassword()]);
if (useBundle) {
  console.log("read " + wanted.length + " accounts from the bundle: " +
              wanted.map(([n]) => n).join(", "));
}

const made = [];
for (const [name, password] of wanted) {
  const r = await fetch(base + "/admin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
    body: JSON.stringify({ user: name, pass: password }),
  });
  if (r.ok) {
    made.push([name, useBundle ? null : password]);
  } else {
    const d = await r.json().catch(() => ({}));
    console.log(`${name}: skipped - ${d.error || "HTTP " + r.status}`);
  }
}

if (!made.length) {
  console.log("nothing to create");
} else if (useBundle) {
  console.log("created, each with the password it already had: " +
              made.map(([n]) => n).join(", "));
} else {
  console.log("\ncreated - give each person their line, it is not shown again:\n");
  const pad = Math.max(...made.map(([n]) => n.length));
  for (const [name, password] of made) {
    console.log("  " + name.padEnd(pad) + "   " + password);
  }
  console.log("");
}
