# Findings detail — confirmed medium / high / critical

Derived from Phase 5–verified `findings.json` records. Dummy principals and dummy hosts only. No live SSH, HTTP, npm, or Playwright.

## high — Config ssh field is passed to OpenSSH without destination validation or `--`

**Fingerprint:** `guard.py:ssh_wp:unvalidated-ssh-destination-argv`

### Trace

1. `skills/wp-update-guard/scripts/guard.py:68` `find_config` — `--config` or the first `wp-update-guard.json` walking from cwd toward `/` supplies `staging.ssh` and `live.ssh`.
2. `skills/wp-update-guard/scripts/guard.py:110` `validate_site` — target objects are accepted when `url` is http(s). `ssh` and `wp_path` are not validated at load time.
3. `skills/wp-update-guard/scripts/guard.py:189` `ssh_wp` — argv is `[ssh, -o, BatchMode=yes, <ssh>, <remote>]` with no `--` before the config-controlled destination; `subprocess.run` uses that list.

### Evidence

- `guard.py:182` — strongest `ssh` control is a non-empty string.
- `guard.py:186` — sibling `wp_path` rejects `..` and `;|&` whitespace and is later `shlex.quote`d; that control is not applied to `ssh`.
- `references/config.md:52` — documented contract is `user@host` passed to `ssh`; extra flags belong in `~/.ssh/config`.
- `references/config.md:61` — the JSON config may be committed if it has no secrets.

### Dummy principal and resource

- **Principal:** author of a committed or planted `wp-update-guard.json` who should only choose an SSH destination.
- **Resource:** operator workstation that holds SSH keys (dummy argv / `ssh -G` only; no keys used).

### Bounded reproduction

Native interface: library import of `ssh_wp` plus local `ssh -G` with no destination connection.

1. Import `skills/wp-update-guard/scripts/guard.py`.
2. Call `validate_site` on a dummy site whose `staging.ssh` is `-oProxyCommand=true` and whose `url` is `https://staging.example.com`. Observe acceptance.
3. Call `ssh_wp({'ssh': '-oProxyCommand=true', 'wp_path': '/var/www/staging'}, 'plugin list --format=json', dry_run=True)`. Record the returned command string and the argv list `[ssh, -o, BatchMode=yes, -oProxyCommand=true, wp --path=/var/www/staging ...]`.
4. Without connecting to a host, run `ssh -G -o BatchMode=yes -oProxyCommand=true` and observe usage (no destination).
5. Without connecting to a host, run `ssh -G -o BatchMode=yes -oProxyCommand=true dummyhost` and read the dumped `proxycommand` and `hostname` lines.

### Observed result and invariant

`validate_site` accepted a leading-dash `ssh`. `ssh_wp` dry-run returned `ssh -o BatchMode=yes -oProxyCommand=true 'wp --path=/var/www/staging plugin list --format=json'`. `ssh -G -o BatchMode=yes -oProxyCommand=true` printed usage and exited 255 (token consumed as an option, no destination left). `ssh -G -o BatchMode=yes -oProxyCommand=true dummyhost` dumped `hostname dummyhost` and `proxycommand true` (OpenSSH_9.6p1).

**Invariant broken:** `sites[].*.ssh` is only a `user@host` destination; OpenSSH must not treat the field as an option.

### Conditions and containment

- Config contains a leading-dash `ssh` value such as `-oProxyCommand=true`.
- Operator or agent runs a command that calls `ssh_wp` (update-staging, promote, rollback, or a non-dry-run snapshot inventory).
- Local OpenSSH treats a leading-dash argv token after `-o BatchMode=yes` as an option unless `--` is present.
- Containment: none beyond a non-empty string. Extra SSH settings are documented as belonging in `~/.ssh/config`.

### Remediation and regression

Reject values that are not `user@host` (at least reject a leading dash) and pass `--` immediately before the destination.

Regression: dummy `ssh="-oProxyCommand=true"` must raise at load/validate time; if a destination is accepted, `ssh_wp` argv must contain `--` immediately before it, and `ssh -G` on that argv must not set `proxycommand`.

---

## high — Promote Pass ignores capture HTTP status and configured page set

**Fingerprint:** `wp-update-guard.guard.pass-predicate-omits-http-and-page-set`

### Trace

1. `skills/wp-update-guard/scripts/capture-pages.mjs:118` `main` — each successful `goto` stores `status` and `ok`; a screenshot is written even when status is not 200.
2. `skills/wp-update-guard/scripts/guard.py:251` `capture_pages` — return code 1 from a failed `capture.ok` report is accepted and the JSON is returned unchecked.
3. `skills/wp-update-guard/scripts/compare-pages.py:171` `compare_dirs` — Pass is computed from globbed PNG stems, not from `capture.json` or the config page list.
4. `skills/wp-update-guard/scripts/guard.py:410` `cmd_compare` — `report.pass` alone sets decision Pass and phase `pass_promote`.
5. `skills/wp-update-guard/scripts/guard.py:505` `cmd_run` — after compare sets `pass_promote`, run immediately applies live `plugin/theme update --all`.

### Evidence

- `SKILL.md:134` — documented Pass requires HTTP 200 captured during snapshot, not only a pixel fraction.
- `SKILL.md:124` — an error status or a page that will not load is a failed page and must not promote.
- `guard.py:251` — `capture_pages` allows subprocess return code 1 and never reads `report.ok`.
- `compare-pages.py:178` — overall pass is `bool(pages)` and all PNG rows pass; a configured id absent from both folders is not a failure.
- `guard.py:410` — `cmd_compare` does not load before/after `capture.json` or `site.pages`.

### Dummy principal and resource

- **Principal:** staging HTTP origin that serves the same error/login body, plus the compare glob.
- **Resource:** dummy live WordPress path (`/var/www/live` on `deploy@live.example.net`) as shaped by `cmd_promote(dry_run=True)`.

### Bounded reproduction

Native interface: planted dummy PNGs and `capture.json`; library calls. Do not run `capture-pages.mjs` or SSH.

1. Import `compare-pages.py` and `guard.py`. In a temp `output_dir/runs/testrun/example-site`, write identical `home.png` files under `before/` and `after/` plus `capture.json` pages with `status: 500` and `ok: false`. Omit `shop.png`.
2. Call `compare_dirs(before, after, 0.02)`. Observe `pass` true and only id `home`, with `shop` absent and not failed.
3. Call `cmd_compare` with phase `screenshot_after`, that `run_id`, and `site.pages` `[home, shop]`. Observe decision Pass and phase `pass_promote`.
4. Call `cmd_promote(..., dry_run=True)` from that row. Observe live `plugin update --all` and `theme update --all` command strings.

### Observed result and invariant

`compare_dirs` returned `pass` true, decision Pass, `page_count` 1, `failed` []. `cmd_compare` set `row.phase` to `pass_promote` and `row.decision` to Pass; `shop` was not in `page_results`. `cmd_promote` dry-run emitted `ssh -o BatchMode=yes deploy@live.example.net 'wp --path=/var/www/live plugin update --all'` and the matching theme command. Empty PNG dirs instead returned `pass` false.

**Invariant broken:** Pass, and therefore live promote, must require every configured key page captured with HTTP 200, matching size, and a pixel fraction under threshold.

### Conditions and containment

- Anonymous staging can return the same non-200 or login/error body before and after; Playwright still screenshots on a successful `goto`.
- At least one matching before/after PNG pair exists so `compare_dirs` sees a non-empty page list; omitted configured ids are then ignored.
- Operator or agent runs `compare` or `run`, which auto-promotes once phase is `pass_promote`.
- Containment: empty PNG dirs fail closed; dual timeout of every page fails closed.

### Remediation and regression

Fail closed on `capture.ok` and on the configured page set. `capture_pages` must treat exit 1 or `report.ok` false as an error. `compare_dirs` must take expected page ids and fail any id missing from before or after. `cmd_compare` must require each configured id to have capture status 200 on both sides before copying `report.pass` into `pass_promote`.

Regression: identical dummy PNGs plus `status: 500` `capture.json` and a missing configured id must not set `pass_promote` or emit live `update --all`.

---

## high — Promote re-resolves latest channel updates instead of staging-tested versions

**Fingerprint:** `wp-update-guard.guard.promote-unpinned-update-all`

### Trace

1. `skills/wp-update-guard/scripts/guard.py:344` `cmd_snapshot` — the only persisted inventory is taken before staging updates and is the old rollback set.
2. `skills/wp-update-guard/scripts/guard.py:361` `cmd_update_staging` — staging applies unpinned `plugin update --all`, `theme update --all`, and optional `core update`.
3. `skills/wp-update-guard/scripts/guard.py:462` `cmd_promote` — live repeats those same unpinned `update --all` commands and never reads inventory versions.

### Evidence

- `guard.py:427` — rollback pins name and version from inventory; promote has no equivalent `install --version` path.
- `guard.py:476` — promote summary claims the same updates are on the live site after an unbound `update --all`.
- `references/wp-cli.md:51` — live promotion is documented as `plugin/theme update --all`.
- `SKILL.md:156` — runbook tells the user the same updates go to live after every key page checks out.

### Dummy principal and resource

- **Principal:** WordPress.org / live update channel between staging test and promote (timing / third-party resolution).
- **Resource:** dummy live WordPress tree shaped by `cmd_promote(dry_run=True)`.

### Bounded reproduction

Native interface: library dry-run of `cmd_promote` / `restore_items`. No live SSH.

1. Import `guard.py`. Build a dummy site with `live.ssh=deploy@live.example.net` and `live.wp_path=/var/www/live`, plus a row with `phase=pass_promote` and inventory plugins/themes that include `akismet@5.3.0`, `hello-dolly@1.7.2`, and `twentytwentyfour@1.0`.
2. Call `cmd_promote(site, row, {core_update: false}, dry_run=True)`. Expect live-only `plugin update --all` and `theme update --all`, with no `--version=` and no inventory names.
3. Repeat with `core_update` true. Expect an additional live `wp core update` and still no inventory pin.
4. Call `restore_items` / `cmd_rollback` on the same inventory with `dry_run=True`. Expect staging `plugin/theme install NAME --version=VER --force` and `live_changed` false.
5. Replace inventory names with `SHOULD-NOT-APPEAR` and call `cmd_promote` again. The promote JSON must omit that name and still emit `update --all`.

### Observed result and invariant

Dummy dry-run: `cmd_promote` from `pass_promote` printed `ssh -o BatchMode=yes deploy@live.example.net 'wp --path=/var/www/live plugin update --all'` and the matching theme command; with `core_update`, also `core update`. Inventory names and `--version=` were absent even when inventory held `akismet@5.3.0` and when it was replaced with `SHOULD-NOT-APPEAR`. The summary was “Every key page passed. The same updates are on the live site.” `restore_items` on the same inventory produced `plugin install akismet --version=5.3.0 --force` and equivalent theme pins against staging only.

**Invariant broken:** after a Pass, live should receive the plugin/theme/core identities that existed on staging after the passing check, not a second unbound channel resolution.

### Conditions and containment

- OS invoker already has the promote CLI and live SSH; no extra auth once phase is `pass_promote`.
- Promote requires `live.ssh` and `live.wp_path`; core update only when `config.core_update` is true.
- A newer channel release, or a live installed set that differs from staging, can make `update --all` resolve a different name or version.
- Rollback is staging-only; a later failed live SSH command stays on live.
- Containment: none on the promote command list; inventory is unused.

### Remediation and regression

After the staging update (or the after snapshot), persist a post-update inventory. Promote by installing those exact names and versions on live (`install NAME --version=VER --force`). Do not re-run `plugin/theme/core update --all`. If a later live SSH command fails, stop without marking Live and provide a live rollback using the same pins.

Regression: dummy inventory `akismet@5.3.0` must appear as live `install akismet --version=5.3.0 --force` (or `update NAME --version=VER`); `update --all` must not appear; `SHOULD-NOT-APPEAR` in the pre-update inventory must not be the promote pin.

---

## medium — After-snapshot accepts `update_staging` phase and can Pass without updating staging

**Fingerprint:** `wp-update-guard.guard.snapshot-after-skips-staging-update`

### Trace

1. `skills/wp-update-guard/scripts/guard.py:333` `cmd_snapshot` — after screenshots are accepted when phase is `update_staging`, `screenshot_after`, or `compare`.
2. `skills/wp-update-guard/scripts/guard.py:345` `cmd_snapshot` — a real before-snapshot sets phase to `update_staging` without recording that updates ran.
3. `skills/wp-update-guard/scripts/guard.py:348` `cmd_snapshot` — a successful after-snapshot moves phase to `compare` even when `updated` is still empty.
4. `skills/wp-update-guard/scripts/guard.py:457` `cmd_promote` — promote checks only `pass_promote` and does not require `row.updated` or a staging update log.

### Evidence

- `guard.py:333` — the after-snapshot guard lists `update_staging`, the pre-update phase, despite the error claiming screenshots come after the staging update.
- `guard.py:410` — `cmd_compare` sets `pass_promote` from `compare.json` pass with no check that staging commands ran.
- `SKILL.md:33` — the runbook forbids skipping steps and lists snapshot before, update staging, screenshot after, compare, then promote or rollback.

### Dummy principal and resource

- **Principal:** operator/agent using documented single-phase commands.
- **Resource:** dummy live WordPress path shaped by `cmd_promote(dry_run=True)` while `row.updated` is empty.

### Bounded reproduction

Native interface: library phase-walk with stubbed `capture_pages` / `inventory`. No SSH or Playwright.

1. Import `guard.py` and call the phase commands with a dummy site/row.
2. Call `cmd_snapshot(when=after)` from `idle` and confirm it raises “after screenshots come after the staging update”.
3. Call `cmd_snapshot(when=before)`. Observe `phase=update_staging` and `updated=[]`.
4. Skip `cmd_update_staging`. Call `cmd_snapshot(when=after)`. Observe it is accepted and phase becomes `compare` with `updated` still `[]`.
5. Write identical dummy PNGs into the run `before/` and `after/` folders and call `cmd_compare(dry_run=False)`.
6. Call `cmd_promote(dry_run=True)` and read the shaped live SSH commands.

### Observed result and invariant

After-snapshot is allowed from `update_staging`, `screenshot_after`, and `compare`, and denied from `idle`. Before-snapshot left `phase=update_staging` and `updated=[]`. Skipping `update-staging`, after-snapshot set `phase=compare` with `updated=[]`. Compare on identical dummy PNGs set decision Pass and `phase=pass_promote` with `updated=[]`. `cmd_promote(dry_run=True)` emitted live `plugin update --all` and `theme update --all`. `cmd_run` does not skip.

**Invariant broken:** after screenshots, compare, and promote must run only after staging plugins/themes were updated in this run.

### Conditions and containment

- Operator or agent uses documented single-phase commands and omits `update-staging`. `cmd_run` from `update_staging` still calls `cmd_update_staging`.
- A real before-snapshot has left `phase=update_staging` and `updated=[]`. Identical before/after PNGs then satisfy compare.
- OS invoker already has the local CLI and live SSH credentials configured for promote.
- Containment: `cmd_run` still updates staging; the hole is the single-phase path.

### Remediation and regression

Treat `update_staging` as pre-update only. After-snapshot must require `screenshot_after` or `compare`. Compare and promote must also require a non-empty staging update log.

Regression: after a dummy before-snapshot, `cmd_snapshot(when=after)` must raise; `cmd_compare` / `cmd_promote` with `updated=[]` must refuse.

---

## medium — Rollback refuses persisted `screenshot_after` after a fatal after-snapshot

**Fingerprint:** `wp-update-guard.guard.rollback-refuses-screenshot-after`

### Trace

1. `skills/wp-update-guard/scripts/guard.py:374` `cmd_update_staging` — a successful staging plugin/theme/core update persists phase `screenshot_after` before any after-screenshot exists.
2. `skills/wp-update-guard/scripts/guard.py:251` `capture_pages` — after-snapshot raises `GuardError` when `capture-pages.mjs` exits with a code other than 0 or 1.
3. `skills/wp-update-guard/scripts/guard.py:260` `capture_pages` — exit 1 with no `capture.json` and non-JSON stdout also raises (uncaught fullPage screenshot-throw path).
4. `skills/wp-update-guard/scripts/guard.py:575` `main` — `state.json` is written only after a successful command; a `GuardError` leaves the last `screenshot_after` row in place.
5. `skills/wp-update-guard/scripts/guard.py:434` `cmd_rollback` — restore runs only when phase is `fail_rollback` or `compare`.

### Evidence

- `guard.py:434` — rollback allowlist is `fail_rollback` and `compare`; `screenshot_after` is omitted.
- `guard.py:374` — update-staging writes phase `screenshot_after` immediately after the remote updates return.
- `capture-pages.mjs:45` — missing Playwright exits 2, which `capture_pages` treats as fatal rather than a failed page set.
- `capture-pages.mjs:112` — fullPage screenshot can throw before `capture.json` is written.
- `guard.py:498` — `cmd_run` from `screenshot_after` only retries after-screenshots and never calls `cmd_rollback`.
- `SKILL.md:170` — runbook tells the user a broken page rolls back on its own and that live is unchanged.
- `SKILL.md:69` — documented `screenshot_after` means after screenshots are saved; the CLI uses that name for the pre-screenshot post-update state.

### Dummy principal and resource

- **Principal:** after-capture fatal (Playwright missing, screenshot throw) after staging was already mutated.
- **Resource:** dummy staging WordPress tree that should be restored from inventory.

### Bounded reproduction

Native interface: dummy row library calls. No Playwright or SSH.

1. Import `guard.py`. Build a dummy site and a row with `phase=screenshot_after` plus a small plugin/theme inventory.
2. Call `cmd_rollback(site, row, dry_run=False)`. Observe `GuardError` “rollback is for a failed compare” and unchanged phase.
3. Call `cmd_run` from the same phase with `capture_pages` stubbed to raise `GuardError`. Observe the raise, phase still `screenshot_after`, and no restore.
4. Call `cmd_compare` against a before dir that has a dummy PNG and an empty after dir. Observe phase `fail_rollback` because the after screenshot is missing.
5. Call `cmd_rollback` again with `dry_run=True` or stubbed `ssh_wp`. Observe `restore_items` emit `plugin/theme install --version --force` for the inventory, `live_changed` false.

### Observed result and invariant

`cmd_rollback(phase=screenshot_after, dry_run=False)` raised “rollback is for a failed compare” and left phase `screenshot_after`. The same refuse fired for `update_staging` and `idle`. `cmd_run` from `screenshot_after` with a fatal after-capture raised without calling restore. `cmd_compare` from `screenshot_after` with a missing after PNG set phase `fail_rollback` and decision Rolled back; a subsequent rollback then emitted inventory install commands against staging only. `cmd_promote` from `screenshot_after` remained blocked.

**Invariant broken:** once staging packages have been mutated, rollback and run must still restore the recorded inventory if after-capture fatals or the operator asks to roll back.

### Conditions and containment

- A completed `update-staging` has already persisted phase `screenshot_after` and a pre-update inventory.
- After-snapshot fatals: Playwright missing (exit 2), uncaught screenshot throw with no `capture.json`, or any Node exit other than 0 or 1.
- Operator or agent follows rollback or run and does not take the undocumented compare-then-rollback escape hatch.
- Containment: live promote stays gated on `pass_promote`. Compare-then-rollback still exists but is not taken by `run`, `rollback`, or the runbook.

### Remediation and regression

Treat `screenshot_after` and `update_staging` as rollbackable once staging has been mutated. On a fatal after-capture, persist `fail_rollback` (or call restore immediately). Make `cmd_run` restore after that fatal instead of only retrying screenshots.

Regression: dummy `phase=screenshot_after` with inventory must allow `cmd_rollback` to emit staging `install NAME --version=VER --force`; a stubbed fatal after-capture in `cmd_run` must persist a rollbackable phase.
