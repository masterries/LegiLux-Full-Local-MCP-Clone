#!/usr/bin/env node
// Headless-browser session against the lgx-streamlit UI (Playwright, Chromium): screenshots and/or a record of
// every request the page makes (PLAN §11.6 "browser HAR check"). Used by scripts/egress_check.sh and for the
// screenshots in docs/screenshots/ (ui/README.md).
//
//   NODE_PATH=$(npm root -g) node scripts/ui_browser.cjs --url http://127.0.0.1:8501 \
//     [--chat "question"] [--direct "art. L. 121-1 C. trav."] [--corpus] [--shots DIR] [--requests OUT.json]
//
// Needs the `playwright` npm package (any recent 1.x) and its Chromium (PLAYWRIGHT_BROWSERS_PATH, or
// --executable PATH); it never downloads a browser. Steps run in this order: chat, direct search, corpus.
// Exit code: 0 = every step finished; 1 = a step failed (the request record is still written).
"use strict";

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

function parseArgs(argv) {
  const out = { url: "http://127.0.0.1:8501", chatTimeoutS: 900, width: 1360, height: 1100, shotHeight: 1900, corpus: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => argv[++i];
    if (a === "--url") out.url = next();
    else if (a === "--chat") out.chat = next();
    else if (a === "--direct") out.direct = next();
    else if (a === "--corpus") out.corpus = true;
    else if (a === "--shots") out.shots = next();
    else if (a === "--requests") out.requests = next();
    else if (a === "--chat-timeout-s") out.chatTimeoutS = Number(next());
    else if (a === "--width") out.width = Number(next());
    else if (a === "--height") out.height = Number(next());
    else if (a === "--shot-height") out.shotHeight = Number(next());
    else if (a === "--executable") out.executable = next();
    else throw new Error(`unknown option ${a}`);
  }
  return out;
}

function log(msg) {
  process.stderr.write(`[ui_browser] ${msg}\n`);
}

async function waitIdle(page, timeoutMs) {
  // Streamlit shows a "running" status widget while a script run is in progress.
  await page.waitForTimeout(500);
  await page
    .locator('[data-testid="stStatusWidget"]')
    .waitFor({ state: "detached", timeout: timeoutMs })
    .catch(() => {});
  await page.waitForTimeout(500);
}

async function shot(page, opts, name, scrollTo) {
  // Streamlit scrolls inside its main container, so a "full page" screenshot is only the viewport: the viewport is
  // made taller for the shot, and `scrollTo` (a locator) is brought to the top first.
  if (!opts.shots) return;
  fs.mkdirSync(opts.shots, { recursive: true });
  const file = path.join(opts.shots, name);
  await page.setViewportSize({ width: opts.width, height: opts.shotHeight });
  await page.waitForTimeout(800);
  if (scrollTo) {
    await scrollTo.evaluate((el) => {
      el.scrollIntoView({ block: "start" });
      // keep the element clear of Streamlit's sticky top bar: scroll its scrolling ancestor back a little
      for (let p = el.parentElement; p; p = p.parentElement) {
        if (p.scrollHeight > p.clientHeight && getComputedStyle(p).overflowY.match(/auto|scroll/)) {
          p.scrollBy(0, -90);
          break;
        }
      }
    });
  }
  await page.waitForTimeout(800);
  await page.screenshot({ path: file, fullPage: false });
  await page.setViewportSize({ width: opts.width, height: opts.height });
  log(`screenshot ${file}`);
}

async function gotoPage(page, title) {
  await page.locator('[data-testid="stTopNavLink"], [data-testid="stNavLink"]').filter({ hasText: title }).first().click();
  await page.waitForTimeout(800);
  await waitIdle(page, 30000);
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const target = new URL(opts.url);
  const launch = { headless: true, args: ["--disable-background-networking", "--disable-component-update", "--no-first-run"] };
  if (opts.executable) launch.executablePath = opts.executable;
  const browser = await chromium.launch(launch);
  const context = await browser.newContext({ viewport: { width: opts.width, height: opts.height }, locale: "fr-FR" });
  const requests = [];
  context.on("request", (r) => requests.push({ url: r.url(), method: r.method(), type: r.resourceType() }));
  const page = await context.newPage();
  page.on("websocket", (ws) => requests.push({ url: ws.url(), method: "GET", type: "websocket" }));
  const steps = [];
  let ok = true;
  const step = async (name, fn) => {
    const t0 = Date.now();
    try {
      await fn();
      steps.push({ name, ok: true, seconds: (Date.now() - t0) / 1000 });
      log(`${name}: ok (${((Date.now() - t0) / 1000).toFixed(1)} s)`);
    } catch (e) {
      ok = false;
      steps.push({ name, ok: false, seconds: (Date.now() - t0) / 1000, error: String(e).slice(0, 500) });
      log(`${name}: FAILED ${e}`);
      await shot(page, opts, `failed-${name}.png`).catch(() => {});
    }
  };

  await step("load", async () => {
    await page.goto(opts.url, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.getByText("Serveur MCP").first().waitFor({ timeout: 60000 });
    await page.getByText(/prêt ·/).first().waitFor({ timeout: 60000 });
    await waitIdle(page, 30000);
  });

  if (opts.chat) {
    await step("chat", async () => {
      const input = page.locator('[data-testid="stChatInputTextArea"]');
      await input.waitFor({ timeout: 30000 });
      await input.fill(opts.chat);
      await input.press("Enter");
      await page.getByText(/Base légale — \d+ source/).first().waitFor({ timeout: opts.chatTimeoutS * 1000 });
      await waitIdle(page, 60000);
      await shot(page, opts, "chat.png", page.locator('[data-testid="stChatMessage"]').first());
    });
  }

  if (opts.direct) {
    await step("direct_search", async () => {
      await gotoPage(page, "Recherche directe");
      await page.getByText("Recherche directe (sans LLM)").first().waitFor({ timeout: 30000 });
      const query = page.locator('textarea[aria-label^="query"], input[aria-label^="query"]').first();
      await query.waitFor({ timeout: 30000 });
      await query.fill(opts.direct);
      await page.getByRole("button", { name: "Appeler l'outil" }).click();
      await page.getByText(/search_legislation · .* · isError=/).first().waitFor({ timeout: 60000 });
      await waitIdle(page, 30000);
      await shot(page, opts, "direct-search.png", page.locator("h2", { hasText: "Recherche directe" }).first());
    });
  }

  if (opts.corpus) {
    await step("corpus", async () => {
      await gotoPage(page, "Corpus");
      await page.getByText("Données au (T)").first().waitFor({ timeout: 30000 });
      await waitIdle(page, 30000);
      await shot(page, opts, "corpus.png", page.locator("h2", { hasText: "Corpus" }).first());
    });
  }

  await browser.close();
  const hosts = {};
  for (const r of requests) {
    let host = "?";
    try {
      host = new URL(r.url).host;
    } catch (e) {}
    hosts[host] = (hosts[host] || 0) + 1;
  }
  const foreign = requests.filter((r) => {
    try {
      const u = new URL(r.url);
      return !["data:", "blob:", "about:"].includes(u.protocol) && u.host !== target.host;
    } catch (e) {
      return true;
    }
  });
  const record = { target: target.origin, steps, requests_total: requests.length, hosts, foreign, requests };
  if (opts.requests) {
    fs.mkdirSync(path.dirname(opts.requests), { recursive: true });
    fs.writeFileSync(opts.requests, JSON.stringify(record, null, 2));
    log(`requests: ${requests.length} (${Object.keys(hosts).join(", ")}), foreign ${foreign.length} -> ${opts.requests}`);
  }
  process.exit(ok ? 0 : 1);
}

main().catch((e) => {
  log(`fatal: ${e}`);
  process.exit(1);
});
