# WP Update Guard

Installable agent skill. It updates WordPress plugins and themes on a staging copy first, screenshots your key pages, rolls back if a page breaks, and only then updates the live site.

```bash
npx skills add danielhayessmith/wp-update-guard
```

That copies `skills/wp-update-guard/` into the agent you pick. Then:

```bash
python3 scripts/guard.py init
python3 scripts/compare-pages.py --self-test
```

Fill in staging and live SSH in `wp-update-guard.json`. One site at a time: snapshot before, update staging, screenshot after, compare, then promote or roll back.

User-facing status words: **Live**, **Checking**, **Pass**, **Rolled back**.

Do not put passwords in the config. Use SSH keys.

The marketing site is a separate repo: [wp-update-guard-site](https://github.com/danielhayessmith/wp-update-guard-site).
