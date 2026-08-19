/* Copy the notes saved from the old VPS into a running server.
 *
 *   node import-notes.mjs https://rtm-internal-api.<you>.workers.dev
 *
 * Asks for the admin password on the terminal rather than taking it as an
 * argument, so it does not end up in the shell history. Reads backup/*.json,
 * which is what the old server held the day the move was made.
 *
 * Safe to run twice: each store is written whole, so a second run puts the
 * same thing back. Not safe to run *after* people have started writing notes
 * on the new server - it would overwrite them with the old ones.
 */

import { readFileSync, readdirSync } from "node:fs";
import { createInterface } from "node:readline";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const BACKUP = join(HERE, "backup");
const STORES = ["item", "mob", "skill"];

const base = (process.argv[2] || "").replace(/\/+$/, "");
if (!base.startsWith("http")) {
  console.error("usage: node import-notes.mjs https://your-worker-url");
  process.exit(1);
}

/* Two ways in, because a prompt that only works on a terminal is a prompt
 * that fails silently in a script. Piped stdin is read whole - readline hits
 * end-of-input and closes before a second question can be asked, and the
 * second question then simply never answers. */
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
  const user = (await ask("admin user [Meta]: ")).trim() || "Meta";
  process.stdout.write("password: ");
  const echo = rl._writeToOutput;
  rl._writeToOutput = () => {};              // do not print the password
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
console.log("signed in as " + user);

let missing = 0;
for (const store of STORES) {
  const file = join(BACKUP, `notes-${store}.json`);
  let body;
  try {
    body = JSON.parse(readFileSync(file, "utf8"));
  } catch (e) {
    console.warn(`skipping ${store}: no readable ${file}`);
    missing++;
    continue;
  }
  const r = await fetch(`${base}/notes/${store}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
    body: JSON.stringify(body),
  });
  console.log(`${store}: ${Object.keys(body).length} entries -> HTTP ${r.status}`);
  if (!r.ok) process.exitCode = 1;
}
if (missing === STORES.length) {
  console.error("nothing was imported: " + BACKUP + " holds " +
                readdirSync(BACKUP).length + " files");
  process.exit(1);
}
