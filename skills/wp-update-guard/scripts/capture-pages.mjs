#!/usr/bin/env node
/**
 * Capture key pages of a site as PNGs with Playwright.
 *
 *   node scripts/capture-pages.mjs --base-url https://staging.example.com \
 *     --pages /,/shop/,/contact/ --out ./before
 */
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

function usage() {
  console.error(
    "Usage: node capture-pages.mjs --base-url URL --pages /,/shop/ --out DIR [--width 1280] [--height 800]"
  );
}

function argValue(args, name, fallback) {
  const index = args.indexOf(name);
  if (index === -1) return fallback;
  const value = args[index + 1];
  if (!value || value.startsWith("--")) {
    throw new Error(`${name} needs a value`);
  }
  return value;
}

function pageId(path) {
  if (path === "/" || path === "") return "home";
  return path.replace(/^\/+|\/+$/g, "").replace(/[^\w.-]+/g, "-") || "home";
}

async function loadPlaywright() {
  try {
    return await import("playwright");
  } catch {
    const here = dirname(fileURLToPath(import.meta.url));
    const hint = join(here, "..", "node_modules", "playwright");
    try {
      return await import(hint);
    } catch {
      console.error(
        "Playwright is not installed. Run: npx --yes playwright install chromium"
      );
      process.exit(2);
    }
  }
}

async function main() {
  const args = process.argv.slice(2);
  if (args.includes("--help") || args.includes("-h")) {
    usage();
    process.exit(0);
  }

  const baseUrl = argValue(args, "--base-url");
  const pagesRaw = argValue(args, "--pages");
  const outDir = argValue(args, "--out");
  const width = Number(argValue(args, "--width", "1280"));
  const height = Number(argValue(args, "--height", "800"));
  const idsRaw = argValue(args, "--ids", "");

  if (!baseUrl || !pagesRaw || !outDir) {
    usage();
    process.exit(1);
  }
  if (!Number.isFinite(width) || !Number.isFinite(height) || width < 320 || height < 320) {
    throw new Error("viewport width and height must be numbers of at least 320");
  }

  const origin = new URL(baseUrl).origin;
  const paths = pagesRaw.split(",").map((item) => item.trim()).filter(Boolean);
  const ids = idsRaw
    ? idsRaw.split(",").map((item) => item.trim())
    : paths.map(pageId);
  if (ids.length !== paths.length) {
    throw new Error("--ids count must match --pages count");
  }

  const { chromium } = await loadPlaywright();
  await mkdir(outDir, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width, height } });
  const results = [];

  try {
    for (let i = 0; i < paths.length; i += 1) {
      const path = paths[i].startsWith("/") ? paths[i] : `/${paths[i]}`;
      const id = ids[i];
      const url = new URL(path, origin).href;
      let status = 0;
      try {
        const response = await page.goto(url, {
          waitUntil: "networkidle",
          timeout: 45000,
        });
        status = response ? response.status() : 0;
      } catch (error) {
        results.push({
          id,
          path,
          url,
          status: 0,
          ok: false,
          error: error instanceof Error ? error.message : String(error),
        });
        continue;
      }
      const file = join(outDir, `${id}.png`);
      await page.screenshot({ path: file, fullPage: true });
      results.push({
        id,
        path,
        url,
        status,
        ok: status >= 200 && status < 400,
        file,
      });
    }
  } finally {
    await browser.close();
  }

  const report = {
    baseUrl: origin,
    out: outDir,
    pages: results,
    ok: results.length > 0 && results.every((item) => item.ok),
  };
  await writeFile(join(outDir, "capture.json"), `${JSON.stringify(report, null, 2)}\n`);
  console.log(JSON.stringify(report, null, 2));
  process.exit(report.ok ? 0 : 1);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
