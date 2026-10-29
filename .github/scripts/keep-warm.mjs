/**
 * Menjaga app Streamlit Community Cloud tetap bangun.
 *
 * Streamlit menghitung "activity" dari sesi browser (JavaScript + WebSocket),
 * bukan dari HTTP GET biasa. Skrip ini membuka app dengan Chromium headless,
 * mengklik tombol bangun bila app sedang tidur, lalu menahan sesi beberapa detik
 * supaya kunjungan benar-benar tercatat.
 *
 * Env:
 *   APP_URL        URL app Streamlit (wajib)
 *   HOLD_SECONDS   Lama sesi ditahan setelah app sehat (default 60)
 */

import { chromium } from "playwright";

const APP_URL = (process.env.APP_URL ?? "").trim().replace(/\/+$/, "");
const HOLD_SECONDS = Number.parseInt(process.env.HOLD_SECONDS ?? "60", 10) || 0;
const HEALTH_PATHS = ["/~/+/_stcore/health", "/_stcore/health"];
const WAKE_BUTTON_NAME = /get this app back up/i;
const SCREENSHOT_PATH = "keep-warm-failure.png";
const HEALTH_TIMEOUT_MS = 180_000;

if (!APP_URL) {
  console.error("[keep-warm] APP_URL kosong. Set repository variable STREAMLIT_APP_URL.");
  process.exit(1);
}

const log = (...args) => console.log(`[keep-warm ${new Date().toISOString()}]`, ...args);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function healthStatus(request) {
  const probes = [];

  for (const path of HEALTH_PATHS) {
    try {
      const response = await request.get(`${APP_URL}${path}`, {
        timeout: 30_000,
        failOnStatusCode: false,
      });

      if (response.status() === 200) {
        return true;
      }

      probes.push(`${path}=${response.status()}`);
    } catch (error) {
      probes.push(`${path}=${error.message}`);
    }
  }

  log(`app belum sehat (${probes.join(", ")})`);
  return false;
}

async function waitForHealthy(request, timeoutMs) {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    if (await healthStatus(request)) {
      return true;
    }

    await sleep(5_000);
  }

  return false;
}

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1280, height: 900 },
  locale: "id-ID",
});
const page = await context.newPage();
let exitCode = 0;

try {
  log(`membuka ${APP_URL}`);
  await page.goto(APP_URL, { waitUntil: "domcontentloaded", timeout: 120_000 });

  const wakeButton = page.getByRole("button", { name: WAKE_BUTTON_NAME }).first();
  const sleeping = await wakeButton
    .waitFor({ state: "visible", timeout: 8_000 })
    .then(() => true)
    .catch(() => false);

  if (sleeping) {
    log("app sedang tidur, mengklik tombol bangunkan");
    await wakeButton.click({ timeout: 15_000 });
  } else {
    log("app tidak menampilkan halaman tidur (sudah bangun atau sedang cold start)");
  }

  if (!(await waitForHealthy(context.request, HEALTH_TIMEOUT_MS))) {
    log("GAGAL: health check tidak pernah 200 — app tidak siap.");
    exitCode = 1;
  } else {
    log(`app sehat, menahan sesi ${HOLD_SECONDS} detik agar aktivitas tercatat`);
    await sleep(HOLD_SECONDS * 1_000);
  }

  if (exitCode === 0 && !(await waitForHealthy(context.request, 30_000))) {
    log("GAGAL: app tidak sehat lagi setelah sesi ditahan.");
    exitCode = 1;
  }
} catch (error) {
  log(`GAGAL: ${error.message}`);
  exitCode = 1;
} finally {
  if (exitCode !== 0) {
    await page
      .screenshot({ path: SCREENSHOT_PATH, fullPage: true })
      .then(() => log(`screenshot disimpan: ${SCREENSHOT_PATH}`))
      .catch((error) => log(`screenshot gagal: ${error.message}`));
  }

  await context.close().catch(() => {});
  await browser.close().catch(() => {});
}

log(exitCode === 0 ? "selesai: app tetap terjaga" : "selesai dengan kegagalan");
process.exit(exitCode);
