# Config

`wp-update-guard.json` lives in the project the agent is working in (or a parent folder). `python3 scripts/guard.py init` writes a starter file. Pass `--force` to replace one that already exists. `guard.py --dry-run` prints SSH/WP-CLI commands without running them.

## File

```json
{
  "version": 1,
  "output_dir": ".wp-update-guard",
  "diff_threshold": 0.02,
  "core_update": false,
  "viewport": { "width": 1280, "height": 800 },
  "sites": [
    {
      "id": "bakery-on-main",
      "name": "Bakery on Main",
      "pages": [
        { "id": "home", "path": "/" },
        { "id": "shop", "path": "/shop/" },
        { "id": "contact", "path": "/contact/" },
        { "id": "checkout", "path": "/checkout/" }
      ],
      "staging": {
        "url": "https://staging.bakery-on-main.com",
        "ssh": "deploy@staging.example.net",
        "wp_path": "/var/www/staging"
      },
      "live": {
        "url": "https://bakery-on-main.com",
        "ssh": "deploy@live.example.net",
        "wp_path": "/var/www/live"
      }
    }
  ]
}
```

## Fields

| Field | Required | Notes |
| --- | --- | --- |
| `version` | yes | Always `1` |
| `output_dir` | no | Default `.wp-update-guard`. Screenshots and `state.json` go here. |
| `diff_threshold` | no | Default `0.02`. Fraction of pixels that may differ before a page fails. |
| `core_update` | no | Default `false`. If `true`, staging (and later live) also runs `wp core update`. |
| `viewport` | no | Playwright window. Default 1280×800. |
| `sites[].id` | yes | Lowercase letters, numbers, hyphens. Used on the CLI. |
| `sites[].name` | no | Shown to the user. Falls back to `id`. |
| `sites[].pages` | yes | At least one key page. `id` is the screenshot filename; `path` is the URL path. |
| `sites[].staging.url` | yes | Origin for before/after screenshots. |
| `sites[].staging.ssh` | yes | `user@host` passed to `ssh`. Extra flags belong in `~/.ssh/config`. |
| `sites[].staging.wp_path` | yes | Absolute path that contains WordPress (`wp` runs with `--path`). |
| `sites[].live.*` | yes to promote | Same shape as staging. Promote refuses to run if live SSH or path is missing. |
| `sites[].diff_threshold` | no | Overrides the file-level threshold for that site. |

## Secrets

Do not put passwords in this file. Use SSH keys and `~/.ssh/config`. If a host needs a jump box, configure `ProxyJump` there, not here.

Add `.wp-update-guard/` to the project `.gitignore`. The JSON config may be committed if it has no secrets.

## Finding the file

`guard.py` walks from the current working directory toward `/` and uses the first `wp-update-guard.json` it finds. Pass `--config path` to override.
