# Architecture — Awesome-Cause/wp-update-guard

Reviewed source: `744a9a6e60260179b945f75f89d589d6eecbea1d` (clean `main`). Profile: `standard`. Full tree. No compatible prior `coverage-ledger.json` or `findings.json`.

## Product, principals, authority, resources

WP Update Guard is an installable agent skill plus three local CLIs. An operator (human or Cursor agent) updates WordPress plugins and themes on a staging copy, screenshots configured key pages with Playwright, compares PNGs, then either promotes the same WP-CLI updates to live or rolls staging back. Protected resources: the live WordPress tree, staging WordPress tree, operator workstation (SSH keys, filesystem), and any client sites listed in one config.

Principals and designed authority:

- **OS invoker** running `guard.py` has the full CLI, including `promote`, with no authentication (`skills/wp-update-guard/scripts/guard.py:545-581`).
- **Config author / committed `wp-update-guard.json`** chooses SSH destinations, URLs, page paths, and `output_dir`. Docs allow committing the file (`skills/wp-update-guard/references/config.md:61`).
- **Agent following `SKILL.md`** is policy-bound only; the CLI does not enforce the runbook.
- **Staging HTTP origin** is fetched anonymously by Playwright (`skills/wp-update-guard/scripts/capture-pages.mjs:95-98`).
- **Remote SSH user + WP-CLI** can update/install plugins, themes, and optional core on the configured path (`skills/wp-update-guard/scripts/guard.py:179-205`).

The strongest coded live-site control is `cmd_promote` requiring phase `pass_promote` (`guard.py:457-458`). Rollback must not touch live (`guard.py:451-452`). There is no integrity check on `state.json`.

## Comparable baseline

Source-grounded comparables are WP-CLI over SSH, Playwright screenshots, and pixel compare—not a hosted visual-regression product. The intended trade-off is: operator-trusted config and SSH keys; staging is updated first; live is updated only after a Pass. A committed or shared config is still treated as operator input.

## Stack and local limits

Python 3 stdlib (`guard.py`, `compare-pages.py`) and Node ESM (`capture-pages.mjs`). No `package.json`, lockfile, CI, Dockerfile, or tests beyond `compare-pages.py --self-test`. Playwright and Chromium are optional runtime deps; WP-CLI and SSH live on remote hosts. Offline-capable checks: `init`, `--self-test`, directory compare, and `--dry-run` command shaping. Full snapshot/update/promote needs network, SSH keys, and remote WordPress—out of this run’s execution policy.

This environment cannot enforce an empty allowlisted environment, no-external-network namespace, read-only target mounts, or race-safe scratch promotion. Target-controlled execution that needs those controls is blocked; parent-side dummy-data dry-run and stdlib function tests remain available.

No Cloudflare Durable Objects, Agents SDK, Workflows, or self-rescheduling alarms appear in source.

## Entry surfaces and source-to-sink paths

1. **CLI / config discovery** — argv, `--config`, or first `wp-update-guard.json` walking cwd to `/` (`guard.py:61-74`) → site selection, state, all sinks.
2. **Config fields** — `ssh` → `ssh -o BatchMode=yes <ssh> <remote>` (`guard.py:189`); `pages[].path` → Playwright URL (`capture-pages.mjs:89-92`); `output_dir` → artifact root (`guard.py:126-127`); `wp_path` is quoted and character-checked (`guard.py:184-188`).
3. **Playwright** — `page.goto` on constructed URLs; writes `{id}.png` and `capture.json`.
4. **SSH / WP-CLI** — inventory JSON stored in `state.json` (`guard.py:208-214,344`); updates; rollback `plugin/theme install NAME --version=VER --force` (`guard.py:427`); live `plugin/theme update --all` (`guard.py:462-467`).
5. **PNG compare** — custom 8-bit RGB/RGBA decoder (`compare-pages.py:34-98`) → Pass/fail → phase `pass_promote` or `fail_rollback` (`guard.py:410-415`). Compare does not read HTTP status from `capture.json`.
6. **Agent runbook** — instructs `npx playwright install`, `run`, `promote`.

## Trust boundaries and strongest source-visible control

| Boundary | Strongest source-visible control |
| --- | --- |
| Config SSH string → local ssh argv | None beyond non-empty string (`guard.py:182-183`). Docs say `user@host` only. |
| `wp_path` → remote `wp --path` | Absolute path, reject `..` / `;|&` whitespace, `shlex.quote` (`guard.py:184-188`). |
| Inventory name/version → rollback | `shlex.quote`; skip must-use / missing fields (`guard.py:419-428`). |
| Page path → browser navigation | URL scheme on `staging.url` only; paths unchecked (`guard.py:110-112,99-101`). |
| Capture `--ids` → PNG path | Config page ids match `SITE_ID_RE`; standalone `--ids` unchecked. |
| Compare result → live update | Phase must be `pass_promote` (`guard.py:457-458`). Pixel threshold only; HTTP 200 is skill text, not code. |
| Staging vs live | Different config objects; no check they are distinct hosts. |
| Agent vs CLI | Skill hard rules (`SKILL.md:29-36`); not enforced in Python. |

## Starting paths

`skills/wp-update-guard/scripts/guard.py`, `capture-pages.mjs`, `compare-pages.py`, `SKILL.md`, `references/config.md`, `references/wp-cli.md`, `examples/wp-update-guard.json`, `.gitignore`, `README.md`.

## Prior coverage

No prior ledger. Nothing is excluded as same-source confirmed. All units are `prior_status: none`.

## Companion selection

Selected because reconnaissance found those boundaries:

- `AI-AND-LLM.md` — skill runbook drives SSH and live mutation from untrusted config/page/inventory content.
- `SUPPLY-CHAIN-AND-RELEASE.md` — plugin/theme/core updates and promote-by-`update --all`.
- `RESOURCE-EXHAUSTION-AND-AVAILABILITY.md` — unbounded PNG decode and remote JSON size.
- `DATA-ISOLATION-AND-LIFECYCLE.md` — multi-site state, inventory restore, rollback vs current policy.
- `MEMORY-SAFETY-AND-BINARY.md` — custom PNG parser.
- `CLIENT-SIDE.md` — Playwright navigation and untrusted staging HTML.

Excluded: `WEB-PROTOCOL-AND-AUTH.md` (no HTTP server, session, or identity protocol), `CLOUD-AND-DEPLOYMENT.md` (no IaC, Workers, DO, Workflows, or alarms), `PROTOCOLS-RPC-AND-MESSAGING.md` (SSH used as a fixed argv helper, no custom RPC/broker), `DESKTOP-MOBILE-AND-LOCAL-IPC.md` (no deep links, webview bridge, or local IPC server).
