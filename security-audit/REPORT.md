# Security audit report — Awesome-Cause/wp-update-guard

## Run

- **Profile:** `standard` (full tree). Not a quick, scoped, or budget-limited pass.
- **Budget:** unset (`null`). Agents spent: 4 reconnaissance, 7 hunters (5 + 1 + 1), 3 coverage critics (wave-1, wave-2/3 post-wave, distinct final-clean), 8 Phase 3 verifiers, 8 Phase 5 record verifiers.
- **Source ref:** `744a9a6e60260179b945f75f89d589d6eecbea1d` on `main`, clean worktree.
- **Execution policy:** sandboxed source-and-local-only. No deployed endpoints, Cloudflare account, live WordPress host, or `wrangler`/`npm run deploy` were touched. Parent-approved dummy-data checks imported `guard.py` / `compare-pages.py` and used `ssh -G` with no network destination. Playwright, `npx`, and real SSH were not executed.
- **Prior runs:** none. No carried same-source confirmations or changed-source revalidations.
- **Deferred / out of scope:** none. Wave-1 critic reassigns of already-closed obvious-things, PNG-decompression, and feature-abuse units were rejected; concurrent-state and Playwright-install gaps were added as new units instead.
- **Banned Cloudflare cost primitives:** source contains no Durable Objects, Agents SDK, Workflows, or self-rescheduling alarms (covered under Obvious things).
- **Recorded browser walkthroughs:** skipped as requested.

`run_status` is `complete`. Every ledger candidate has an independent Phase 3 disposition and every retained record passed Phase 5 (`verified`).

## Security posture

WP Update Guard is a local CLI plus an agent runbook. The OS invoker and SSH keys are the real authority. The strongest coded live-site gate is `pass_promote`, but that gate is weaker than the skill text: it is a pixel glob, not HTTP 200 plus the configured page set, and single-phase commands can skip the staging update. A committed `wp-update-guard.json` can inject OpenSSH options through the unvalidated `ssh` field. Promote re-resolves `update --all` on live instead of pinning staging-tested versions. Rollback cannot run from the persisted post-update `screenshot_after` phase after a fatal after-capture. No secrets are hardcoded. Staging-to-agent prompt injection and unpinned `npx playwright install` remain owner-validation leads.

## Confirmed findings

| Severity | Title | Affected boundary | Observed result |
| --- | --- | --- | --- |
| high | Config ssh field is passed to OpenSSH without destination validation or `--` | Config `ssh` → local `ssh` argv | `ssh_wp` dry-run placed `-oProxyCommand=true` as an OpenSSH option; `ssh -G` consumed it |
| high | Promote Pass ignores capture HTTP status and configured page set | Capture/compare → `pass_promote` | Identical dummy PNGs plus `status: 500` `capture.json` set `pass_promote`; dry-run promote emitted live `update --all` |
| high | Promote re-resolves latest channel updates instead of staging-tested versions | `cmd_promote` vs inventory pins | Dry-run promote emitted live `plugin/theme update --all` and ignored inventory `--version=` |
| medium | After-snapshot accepts `update_staging` phase and can Pass without updating staging | After-snapshot phase allowlist | Dummy walk: skip `update-staging`, after-snapshot + identical PNGs → `pass_promote` with `updated=[]` |
| medium | Rollback refuses persisted `screenshot_after` after a fatal after-snapshot | `cmd_rollback` phase gate | `cmd_rollback(phase=screenshot_after)` raised `rollback is for a failed compare` |

### high — Config ssh field is passed to OpenSSH without destination validation or `--`

- **Where:** `skills/wp-update-guard/scripts/guard.py:68` → `:110` → `:189`
- **Lower-trust principal:** author of a committed or planted `wp-update-guard.json`
- **Reproduction (native CLI / library):** import `ssh_wp(..., dry_run=True)` with `ssh="-oProxyCommand=true"`; optionally `ssh -G -o BatchMode=yes -oProxyCommand=true` (no host connection)
- **Conditions:** config contains a leading-dash `ssh` value; operator/agent runs a command that calls `ssh_wp`
- **Result:** OpenSSH treats the field as an option (`ProxyCommand` / `LocalCommand` / `-F`)
- **Impact:** local command execution on the workstation that holds SSH keys
- **Priority:** high — config is documented as committable; sibling `wp_path` is already sanitized
- **Fix:** reject leading-dash / non-`user@host` values and insert `--` before the destination

### high — Promote Pass ignores capture HTTP status and configured page set

- **Where:** `capture-pages.mjs:118` → `guard.py:251` → `compare-pages.py:171` → `guard.py:410` / `:505`
- **Lower-trust principal:** staging HTTP origin (same error/login body) plus the compare glob
- **Reproduction:** plant identical PNGs and `capture.json` with `status: 500`; call `compare_dirs` and `cmd_compare`; `cmd_promote(dry_run=True)`
- **Conditions:** at least one matching PNG pair; operator/agent runs compare or `run`
- **Result:** `pass_promote` and live `update --all` without HTTP 200 or the full configured page set
- **Impact:** live WordPress updated when documented Pass checks never ran
- **Priority:** high — `cmd_run` auto-promotes
- **Fix:** fail closed on `capture.ok` and require every configured page id with HTTP 200

### high — Promote re-resolves latest channel updates instead of staging-tested versions

- **Where:** `guard.py:344` → `:361` → `:462`
- **Lower-trust principal:** WordPress.org / live update channel between staging test and promote
- **Reproduction:** `cmd_promote(..., dry_run=True)` from `pass_promote` with a dummy inventory; compare to `restore_items` pins
- **Conditions:** phase `pass_promote`; live SSH configured
- **Result:** live `plugin/theme update --all` (optional `core update`); inventory names unused
- **Impact:** live can install packages that were never visually checked; rollback is staging-only
- **Priority:** high — user-facing copy claims “the same updates”
- **Fix:** persist post-update inventory and promote with `install NAME --version=VER --force`

### medium — After-snapshot accepts `update_staging` phase and can Pass without updating staging

- **Where:** `guard.py:333` → `:345` → `:348` → `:457`
- **Lower-trust principal:** operator/agent using documented single-phase commands
- **Reproduction:** dummy `cmd_snapshot(before)` then `cmd_snapshot(after)` without `update-staging`; compare identical PNGs; `cmd_promote(dry_run=True)`
- **Conditions:** `cmd_run` does not skip; single-phase commands do
- **Result:** `pass_promote` with `updated=[]`; live `update --all` never applied on staging
- **Impact:** live updated without a staging mutation under test
- **Priority:** medium — requires skipping `update-staging`, but the error text and status labels invite it
- **Fix:** after-snapshot must require `screenshot_after` or `compare`; compare/promote must require a non-empty `updated` log

### medium — Rollback refuses persisted `screenshot_after` after a fatal after-snapshot

- **Where:** `guard.py:374` → `:251` / `:260` → `:575` → `:434`
- **Lower-trust principal:** after-capture fatal (Playwright missing, screenshot throw) after staging was already mutated
- **Reproduction:** dummy row `phase=screenshot_after`; `cmd_rollback(dry_run=False)` raises; `cmd_run` retries snapshot only
- **Conditions:** `update-staging` already saved `screenshot_after`; after-capture exits other than 0/1
- **Result:** coded restore refuses; staging stays on untested packages
- **Impact:** staging integrity; live remains gated on `pass_promote`
- **Priority:** medium — compare-then-rollback is a same-operator escape hatch the runbook does not take
- **Fix:** treat `screenshot_after` / `update_staging` as rollbackable; persist `fail_rollback` on fatal after-capture

## NEEDS VALIDATION

| Title | Trace | Blocker | Local next step | Owner-observed check |
| --- | --- | --- | --- | --- |
| Rollback installs from stored inventory names with only `shlex.quote` | `guard.py:211` → `:344` → `:427` | WP-CLI list/install interpretation of URL, path, or `--` tokens | Dummy `restore_items` command strings already observed; remaining need is an offline WP-CLI fixture | Owner: what `wp plugin list --format=json` emits and how `plugin install` treats those tokens |
| Skill runbook installs unpinned Playwright and Chromium | `SKILL.md:99` → `capture-pages.mjs:43` → `:84` | npm/npx and Playwright CDN integrity defaults | Confirm the command has no `@version` and uses `--yes`; do not fetch the registry | Owner: resolved version, integrity, chromium URL/checksum before agents run the command |
| Staging capture artifacts enter the agent tool loop with only prompt isolation | `capture-pages.mjs:95` → `:112` → `guard.py:341` → `SKILL.md:138` → `guard.py:456` | Model behavior is not locally observable | Dummy CLI JSON already shows capture/SSH text entering agent-visible output | Owner dummy staging page with instruction-like text; dry-run only; watch for unrequested promote/ssh |

These are source-grounded leads, not confirmed vulnerabilities, and have no severity.

## Hardening notes and positive patterns

**Hardening (not findings):** constrain `output_dir` under the config parent; cap PNG decode size and `fullPage` screenshot bytes; cap inventory JSON; `flock` `state.json` and reject duplicate site ids; gitignore the configured `output_dir`; redact URL userinfo and remote SSH streams in `emit`; refuse update when staging and live SSH/path are identical.

**Positive patterns:** `subprocess.run` uses an argv list, not `shell=True`. `wp_path` is absolute-checked, rejects `;|&` / `..`, and is `shlex.quote`d. `cmd_update_staging` and `cmd_rollback` always target `site["staging"]`. `cmd_promote` requires `pass_promote` and live SSH/path. Page ids in config match `SITE_ID_RE`. Compare fail-closes when `compare.json` is missing. Docs forbid passwords in config. No hardcoded secrets, debug HTTP endpoints, Durable Objects, Agents SDK, Workflows, or alarms.

## Coverage

Ledger: **27** units (21 wave 1, 5 wave 2, 1 wave 3). **18 covered**, **9 candidate**, **0 blocked**, **0 deferred**, **0 out_of_scope**.

Wave-1 post-wave critic accepted five missing units (inventory JSON size, fullPage capture size, unpinned Playwright install, staging `update --all` trust, concurrent `state.json`). Wave-2 hunter closed four as covered (self-process DoS / same-principal races / designed staging update) and one as candidate (Playwright pin). Wave-2 critic accepted the rollback-after-fatal-capture unit (wave 3, candidate). Distinct final-clean critic accepted no further units (`stop: true`).

Limits that remain: this host cannot enforce a full OS sandbox (empty allowlisted env, no-external-network namespace, read-only mounts, race-safe scratch promotion). WP-CLI, npm, and model-turn facts stay in `needs_validation`. A later run should re-check those three leads if WP-CLI or a sandboxed agent fixture becomes available.
