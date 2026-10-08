---
name: wp-update-guard
description: "Safely update WordPress plugins and themes. Use when the user asks to update WordPress, run plugin or theme updates, check a site after updates, take before-and-after screenshots of key pages, roll back a failed WordPress update, or look after several WordPress sites. Follows a fixed order: update a staging copy first, screenshot key pages, compare them, roll back on its own if a page breaks, and only then update the live site. Also use for WP Update Guard, staging WordPress updates, and wp-update-guard.json."
license: MIT
compatibility: Requires Python 3, Node 22+ for page screenshots, and SSH with WP-CLI on each WordPress host.
---

# WP Update Guard

Update WordPress on a staging copy first. Check the key pages. Go live only if they pass.

Install with `npx skills add danielhayessmith/wp-update-guard`.

This skill is the agent-facing runbook. Talk to the user in plain language: "It updates a copy of your site first. If a page breaks, it rolls back." Do not say CI, regression, diff, pipeline, or similar jargon to the user.

Script paths below are relative to this skill directory.

## When to use

Use this skill when the user wants to:

- Update WordPress plugins and themes without touching the live site first
- Check key pages with before-and-after screenshots
- Roll back a staging site after a bad update
- Run the same steps across several sites (their own and their clients’)

Do not use this skill for general WordPress development, theme coding, or content edits.

## Hard rules

1. **Never update live until staging passes.** Promote is blocked until every key page has a Pass.
2. **One site at a time.** Finish a site (Pass and live, or Rolled back) before starting the next.
3. **Do not skip steps.** The order is fixed: snapshot before → update staging → screenshot after → compare → promote or roll back.
4. **If unsure, stop.** Missing SSH, missing staging URL, or a page that will not load is a stop, not a guess.
5. **Secrets stay off screen.** Do not print passwords, keys, or `.env` values. SSH config may point at keys; do not cat them.
6. **User-facing status words:** Live, Checking, Pass, Rolled back. Use those exact words in summaries.

## Config

Look for `wp-update-guard.json` starting at the current working directory and walking up. If none exists, create one with the user:

```bash
python3 scripts/guard.py init
```

That writes `wp-update-guard.json` and adds `.wp-update-guard/` to `.gitignore`. Run state and screenshots are written into `.wp-update-guard/` later. Pass `--force` to replace an existing config. The schema is in [references/config.md](references/config.md).

Each site needs:

- A staging URL and a live URL
- SSH + a WordPress path for staging (and live, when it is time to promote)
- A short list of key pages (`/`, `/shop/`, `/contact/`, `/checkout/` are typical)

WP-CLI over SSH is the default way to update. Details are in [references/wp-cli.md](references/wp-cli.md).

## State machine

Per site, stored in `.wp-update-guard/state.json`:

```
idle → snapshot_before → update_staging → screenshot_after → compare
    → pass_promote → done
    → fail_rollback → done
```

- `idle` — no run in progress
- `snapshot_before` — before screenshots of staging (and plugin/theme list) are saved
- `update_staging` — plugins/themes updated on staging only
- `screenshot_after` — after screenshots of staging are saved
- `compare` — each key page is checked
- `pass_promote` — every page passed; live may be updated
- `fail_rollback` — a page failed; staging is rolled back; live is not touched
- `done` — run finished (Live or Rolled back)

Resume from the current phase. Do not restart a finished `done` run unless the user asks for a new update.

```bash
python3 scripts/guard.py status --site SITE_ID
python3 scripts/guard.py run --site SITE_ID
```

`run` walks the remaining phases. Use the single-phase commands when a step needs a human look.

## Workflow

### 1. Snapshot before

Save the current staging look **before** any update:

```bash
python3 scripts/guard.py snapshot --site SITE_ID --when before
```

This captures each key page on the staging URL into `.wp-update-guard/runs/<run-id>/<site>/before/`. It also records the plugin and theme versions from staging WP-CLI.

If Playwright is missing, install the browser once, then retry:

```bash
npx --yes playwright install chromium
```

Or capture with:

```bash
node scripts/capture-pages.mjs --base-url STAGING_URL --pages /,/shop/ --out BEFORE_DIR
```

### 2. Update staging

```bash
python3 scripts/guard.py update-staging --site SITE_ID
```

This runs WP-CLI on the **staging** path only (`plugin update --all`, `theme update --all`, optional `core update` if the config says so). Live SSH is not used here.

Tell the user: "Plugins and themes update on a private copy of your site. Your live site isn’t touched."

### 3. Screenshot after

```bash
python3 scripts/guard.py snapshot --site SITE_ID --when after
```

Same pages, same viewport, after the staging update. If a page returns an error status or will not load, treat that page as failed. Do not promote.

### 4. Compare

```bash
python3 scripts/guard.py compare --site SITE_ID
```

This calls `scripts/compare-pages.py` on the before/after folders. A page **Passes** when:

- HTTP status is 200 (captured during snapshot)
- Pixel difference is below the site’s `diff_threshold` (default `0.02`, meaning 2% of pixels)
- The after screenshot is the same size as before

A page fails otherwise. You must also **look at** the before and after images. A numeric pass with a visually broken header, overlay, or missing shop grid is a fail. The number does not override what you see.

Write results into state. Summarise for the user with Pass / Rolled back, never color or numbers alone.

Standalone compare:

```bash
python3 scripts/compare-pages.py --before BEFORE_DIR --after AFTER_DIR --threshold 0.02 --out report.json
```

### 5a. Pass — go live

Only if every key page passed:

```bash
python3 scripts/guard.py promote --site SITE_ID
```

Applies the same plugin/theme updates on the live WordPress path, then marks the site Live.

Tell the user: "When every page checks out, the same updates go to your live site."

### 5b. Fail — roll back

If any page failed:

```bash
python3 scripts/guard.py rollback --site SITE_ID
```

Restores staging plugins/themes to the versions recorded in the before snapshot. **Does not change live.** Marks the site Rolled back.

Tell the user: "If a page breaks, it rolls back on its own. Nothing changes on the live site."

## Many sites

List sites from config, then run each one in turn:

```bash
python3 scripts/guard.py list
python3 scripts/guard.py run --site SITE_ID
```

Give the user a short table when you finish: site name, what was checked, Live or Rolled back. One Guard can look after every site they run — their own and their clients’. Each site gets its own staging copy, its own checks, and its own clear result.

## Scripts

| Script | What it does |
| --- | --- |
| `scripts/guard.py` | Config, state, SSH WP-CLI, and the phase commands |
| `scripts/compare-pages.py` | Before/after PNG compare → Pass or fail per page |
| `scripts/capture-pages.mjs` | Playwright screenshots of the key pages |

`guard.py` shells out to the other two. You can run them directly when debugging a single step.

## What to say when you are done

Keep it short:

- **Live:** "Every key page passed. The same updates are on the live site."
- **Rolled back:** "A page did not match after the update. Staging was rolled back. The live site was not changed."
- **Stopped:** Say exactly what was missing (staging URL, SSH, a page that would not load) and wait.
