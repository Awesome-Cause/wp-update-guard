# WP-CLI over SSH

Updates run on the server with WP-CLI. The Guard never updates live until staging pages pass.

## How commands are built

`guard.py` runs:

```bash
ssh -o BatchMode=yes SSH_TARGET "wp --path=WP_PATH COMMAND"
```

`SSH_TARGET` is `staging.ssh` or `live.ssh` from the config (`user@host`). Extra SSH options belong in `~/.ssh/config` (IdentityFile, ProxyJump, Port).

`BatchMode=yes` means no password prompt. If SSH needs a password, stop and tell the user to set up a key.

## Inventory (before snapshot)

Record versions so rollback can put them back:

```bash
wp --path=WP_PATH plugin list --format=json
wp --path=WP_PATH theme list --format=json
wp --path=WP_PATH core version
```

Save the JSON onto the run in `state.json`.

## Staging update

On the **staging** path only:

```bash
wp --path=STAGING_PATH plugin update --all
wp --path=STAGING_PATH theme update --all
```

If `core_update` is true:

```bash
wp --path=STAGING_PATH core update
```

Do not pass `--dry-run` for the real run. A dry run is fine if the user only asked what would change.

## Promote (live)

Same plugin/theme (and optional core) updates, on the **live** path, and only after every key page passed:

```bash
wp --path=LIVE_PATH plugin update --all
wp --path=LIVE_PATH theme update --all
```

If the live host is a copy-from-staging setup instead of WP-CLI updates, stop. This skill promotes by applying the same WP-CLI updates, not by rsyncing uploads or databases. Say that plainly and wait for the user.

## Rollback (staging)

For each plugin/theme that changed, restore the version recorded in the before snapshot:

```bash
wp --path=STAGING_PATH plugin install PLUGIN --version=OLD_VERSION --force
wp --path=STAGING_PATH theme install THEME --version=OLD_VERSION --force
```

If a version cannot be reinstalled, leave staging as-is, mark the run stopped, and tell the user the live site was not changed.

Never run rollback against the live path.

## Safety checks

- Confirm `wp --info` works on the host before updating.
- Confirm the `--path` is staging when the phase is `update_staging` or `fail_rollback`.
- If WP-CLI says it is already at latest, that is still a valid run: screenshots and compare still happen.
- Database search-replace, cache flush, or object-cache restarts are out of scope unless the user asks after a Pass.

## Talking to the user

Say "a private copy of your site", not "staging environment". Say "rolled back", not "reverted the deploy".
