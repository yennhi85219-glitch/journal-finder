import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";

const root = path.resolve(new URL(".", import.meta.url).pathname, "..");
const dataDir = path.join(root, "data");
const worklistPath = path.join(dataDir, "codex_worklist.json");
const outputPath = path.join(dataDir, "codex_harvest.json");

const worklist = JSON.parse(fs.readFileSync(worklistPath, "utf8"));
const harvest = fs.existsSync(outputPath)
  ? JSON.parse(fs.readFileSync(outputPath, "utf8"))
  : {};

function save() {
  const tmp = outputPath.replace(/\.json$/, ".tmp");
  fs.writeFileSync(tmp, JSON.stringify(harvest, null, 2), "utf8");
  fs.renameSync(tmp, outputPath);
}

function journalCode(url) {
  if (!url) return null;
  let m = url.match(/\/toc\/([^/]+)/);
  if (m) return m[1];
  m = url.match(/[?&]journalCode=([^&]+)/);
  if (m) return m[1];
  return null;
}

const CODE_OVERRIDES = {
  "1090-3127": "ipec20",
  "1362-5187": "iejc20",
};

function clean(text) {
  return (text || "")
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

function extractScope(text) {
  const lower = text.toLowerCase();
  let starts = [...text.matchAll(/(?:^|\n)(aims and scope|aim and scope)\s*\n/gi)].map((m) => ({
    pos: m.index + m[0].toLowerCase().indexOf(m[1].toLowerCase()),
    phrase: m[1],
  }));
  if (!starts.length) {
    for (const phrase of ["aims and scope", "aim and scope"]) {
      let pos = lower.indexOf(phrase);
      while (pos >= 0) {
        starts.push({ pos, phrase });
        pos = lower.indexOf(phrase, pos + phrase.length);
      }
    }
  }
  if (!starts.length) return "";
  starts.sort((a, b) => a.pos - b.pos);
  const chosen = starts[starts.length - 1];
  const after = text.slice(chosen.pos + chosen.phrase.length).trim();
  const stops = [
    "Journal metrics",
    "Editorial board",
    "Abstracting and indexing",
    "Open access",
    "Publication details",
  ];
  let end = after.length;
  for (const stop of stops) {
    const idx = after.indexOf(stop);
    if (idx > 80 && idx < end) end = idx;
  }
  let scope = clean(after.slice(0, end));
  scope = scope
    .split("\n")
    .filter((line) => !/(impact factor|citescore|downloads|submission to acceptance|journal metrics)/i.test(line))
    .join("\n");
  return clean(scope).slice(0, 6000);
}

function extractApc(text) {
  const contextRe = /(article publishing charge|article publication charge|apc|publication fee|publishing fee)/ig;
  const moneyRe = /(?:(?:US|U\.S\.)?\s?\$|USD\s?\$?|US dollars?\s?)\s?([1-9]\d{2,4}(?:,\d{3})?)/ig;
  for (const match of text.matchAll(contextRe)) {
    const window = text.slice(Math.max(0, match.index - 250), match.index + 450);
    for (const money of window.matchAll(moneyRe)) {
      const value = Number(money[1].replace(/,/g, ""));
      if (value >= 100 && value <= 15000) return value;
    }
  }
  return null;
}

const targets = worklist
  .filter((x) => x.source_scope === "scie_env_health")
  .slice(0, 300)
  .filter((x) => (x.publisher || "").toLowerCase().includes("taylor") || (x.homepage_url || "").includes("tandfonline.com"));

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({
  userAgent:
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
});

let added = 0;
let processed = 0;
for (const [i, item] of targets.entries()) {
  const issn = item.issn_l;
  if (!issn) continue;
  const code = CODE_OVERRIDES[issn] || journalCode(item.homepage_url);
  if (!code) continue;
  const url = `https://www.tandfonline.com/action/journalInformation?journalCode=${code}`;
  console.log(`[${i + 1}/${targets.length}] ${issn} ${item.name}`);
  try {
    const resp = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
    await page.waitForTimeout(2500);
    const text = await page.locator("body").innerText({ timeout: 10000 });
    const scope = extractScope(text);
    const apc = extractApc(text);
    processed += 1;
    if (scope || apc) {
      const rec = {
        aims_scope: scope || "",
        source_url: page.url(),
        confidence: scope.length >= 300 ? "high" : "medium",
        last_checked: new Date().toISOString().slice(0, 10),
      };
      if (apc) rec.apc_usd = apc;
      harvest[issn] = rec;
      added += 1;
      console.log(`  ok scope=${scope.length} apc=${apc || ""}`);
    } else {
      console.log(`  no usable data status=${resp && resp.status()}`);
    }
  } catch (err) {
    processed += 1;
    console.log(`  error ${String(err.message || err).slice(0, 160)}`);
  }
  if (processed % 10 === 0) save();
  await page.waitForTimeout(1100);
}

await browser.close();
save();
console.log(JSON.stringify({ targets: targets.length, processed, added, total_harvest: Object.keys(harvest).length, output: outputPath }, null, 2));
