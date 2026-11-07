// Vercel build for the demo's pages (Root Directory: web).
//   VITE_API_BASE=https://<cloud-run-url> node build.js
// Output dist/: the public pages with their shared pieces (static/partials/) filled in,
// the app at /app, every file they load under /static, and static/config.js with the API base.
// Cloud Run serves the API only; nothing here calls it except app.html.
const fs = require("fs");
const path = require("path");

const apiBase = (process.env.VITE_API_BASE || "http://localhost:8080").trim().replace(/\/+$/, "");
try { new URL(apiBase); } catch { console.error(`VITE_API_BASE is not a valid URL: ${apiBase}`); process.exit(1); }

const src = path.join(__dirname, "static");
const out = path.join(__dirname, "dist");
// Public pages and the path each one's header tab marks as current (web/pages.py in the full app).
const PAGES = { landing: null, uploads: "/uploads", connectors: "/connectors", faq: "/faq" };
const COPY = ["anchor.css", "site.css", "site.js", "answer.js", "cta.css", "cta.js", "samples.json", "samples", "demos"];

fs.rmSync(out, { recursive: true, force: true });
fs.mkdirSync(path.join(out, "static"), { recursive: true });

for (const [name, current] of Object.entries(PAGES)) {
  let html = fs.readFileSync(path.join(src, `${name}.html`), "utf8");
  for (const part of ["head", "nav", "footer"]) {
    html = html.replace(`<!--#${part}-->`, fs.readFileSync(path.join(src, "partials", `${part}.html`), "utf8"));
  }
  if (current) html = html.replace(`class="plain" href="${current}"`, `class="plain" href="${current}" aria-current="page"`);
  fs.writeFileSync(path.join(out, name === "landing" ? "index.html" : `${name}.html`), html);
}
fs.copyFileSync(path.join(src, "app.html"), path.join(out, "app.html"));
for (const item of COPY) fs.cpSync(path.join(src, item), path.join(out, "static", item), { recursive: true });
fs.writeFileSync(path.join(out, "static", "config.js"), `window.VITE_API_BASE = ${JSON.stringify(apiBase)};\n`);
console.log(`Built dist/ with API base ${apiBase}`);
