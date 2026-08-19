/* Create accounts on the server, in a batch.
 *
 *   node add-users.mjs https://rtm-internal-api.<you>.workers.dev Orn Croc Move
 *
 * Signs in as the admin, creates each name with a freshly generated password,
 * and prints the passwords once. Nothing is written to a file: hand each
 * person their line, and they can change it later from the tool.
 *
 * New passwords rather than the old ones on purpose. The five accounts the
 * tool used to carry are in this repository's history in plain text, and this
 * whole move was about not having working passwords in a public file. Anybody
 * who wants their old one back can set it in the user management screen.
 *
 * A name that already exists is reported and skipped, so re-running to add one
 * more person is safe.
 */

import { createInterface } from "node:readline";
import { readFileSync } from "node:fs";

const [, , rawBase, ...names] = process.argv;
const base = (rawBase || "").replace(/\/+$/, "");
if (!base.startsWith("http") || !names.length) {
  console.error("usage: node add-users.mjs https://your-worker-url Name [Name ...]");
  process.exit(1);
}

const bad = names.filter(n => !/^[A-Za-z0-9_-]+$/.test(n));
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

const made = [];
for (const name of names) {
  const password = makePassword();
  const r = await fetch(base + "/admin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
    body: JSON.stringify({ user: name, pass: password }),
  });
  if (r.ok) {
    made.push([name, password]);
  } else {
    const d = await r.json().catch(() => ({}));
    console.log(`${name}: skipped - ${d.error || "HTTP " + r.status}`);
  }
}

if (made.length) {
  console.log("\ncreated - give each person their line, it is not shown again:\n");
  const pad = Math.max(...made.map(([n]) => n.length));
  for (const [name, password] of made) {
    console.log("  " + name.padEnd(pad) + "   " + password);
  }
  console.log("");
}
