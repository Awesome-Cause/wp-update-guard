#!/usr/bin/env python3
"""WP Update Guard — staging first, check the pages, go live only if it passes."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SITE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SSH_DEST_RE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9][A-Za-z0-9.-]*$")
ROLLBACKABLE_PHASES = frozenset({"fail_rollback", "compare", "screenshot_after", "update_staging"})
HERE = Path(__file__).resolve().parent
COMPARE_SCRIPT = HERE / "compare-pages.py"
CAPTURE_SCRIPT = HERE / "capture-pages.mjs"

STARTER_CONFIG = {
    "version": 1,
    "output_dir": ".wp-update-guard",
    "diff_threshold": 0.02,
    "core_update": False,
    "viewport": {"width": 1280, "height": 800},
    "sites": [
        {
            "id": "example-site",
            "name": "Example site",
            "pages": [
                {"id": "home", "path": "/"},
                {"id": "contact", "path": "/contact/"},
            ],
            "staging": {
                "url": "https://staging.example.com",
                "ssh": "deploy@staging.example.net",
                "wp_path": "/var/www/staging",
            },
            "live": {
                "url": "https://www.example.com",
                "ssh": "deploy@live.example.net",
                "wp_path": "/var/www/live",
            },
        }
    ],
}


class GuardError(Exception):
    pass


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def find_config(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise GuardError(f"config not found: {path}")
        return path
    here = Path.cwd().resolve()
    for folder in [here, *here.parents]:
        candidate = folder / "wp-update-guard.json"
        if candidate.is_file():
            return candidate
    raise GuardError(
        "No wp-update-guard.json found. Run `python3 scripts/guard.py init` first."
    )


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GuardError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GuardError(f"{path} must contain a JSON object")
    return data


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def validate_site(site: dict[str, Any]) -> None:
    site_id = site.get("id")
    if not isinstance(site_id, str) or not SITE_ID_RE.fullmatch(site_id):
        raise GuardError("each site id must be lowercase letters, numbers, and hyphens")
    pages = site.get("pages")
    if not isinstance(pages, list) or not pages:
        raise GuardError(f"{site_id}: add at least one key page")
    for page in pages:
        if not isinstance(page, dict) or "id" not in page or "path" not in page:
            raise GuardError(f"{site_id}: each page needs id and path")
        if not SITE_ID_RE.fullmatch(str(page["id"])):
            raise GuardError(f"{site_id}: page id {page['id']!r} is invalid")
    for side in ("staging", "live"):
        target = site.get(side)
        if not isinstance(target, dict):
            if side == "live":
                continue
            raise GuardError(f"{site_id}: missing {side}")
        url = target.get("url")
        if not isinstance(url, str) or urlparse(url).scheme not in ("http", "https"):
            raise GuardError(f"{site_id}: {side}.url must be an http(s) URL")
        if target.get("ssh") is not None:
            try:
                validate_ssh_destination(target.get("ssh"))
            except GuardError as exc:
                raise GuardError(f"{site_id}: {side}.ssh {exc}") from exc


def load_bundle(config_path: Path) -> tuple[dict[str, Any], Path, Path]:
    config = load_json(config_path)
    if config.get("version") != 1:
        raise GuardError("wp-update-guard.json version must be 1")
    sites = config.get("sites")
    if not isinstance(sites, list) or not sites:
        raise GuardError("config needs a non-empty sites list")
    for site in sites:
        if isinstance(site, dict):
            validate_site(site)
    root = config_path.parent
    output_dir = root / str(config.get("output_dir") or ".wp-update-guard")
    state_path = output_dir / "state.json"
    if state_path.is_file():
        state = load_json(state_path)
    else:
        state = {"sites": {}}
    state.setdefault("sites", {})
    return config, output_dir, state_path


def get_site(config: dict[str, Any], site_id: str) -> dict[str, Any]:
    for site in config["sites"]:
        if site.get("id") == site_id:
            return site
    known = ", ".join(site.get("id", "?") for site in config["sites"])
    raise GuardError(f"unknown site {site_id!r}. Known: {known}")


def site_state(state: dict[str, Any], site_id: str) -> dict[str, Any]:
    sites = state.setdefault("sites", {})
    current = sites.setdefault(
        site_id,
        {
            "phase": "idle",
            "run_id": None,
            "decision": None,
            "inventory": None,
            "page_results": [],
            "updated": [],
            "error": None,
        },
    )
    return current


def threshold_for(config: dict[str, Any], site: dict[str, Any]) -> float:
    value = site.get("diff_threshold", config.get("diff_threshold", 0.02))
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GuardError("diff_threshold must be a number") from exc
    if not 0 <= number <= 1:
        raise GuardError("diff_threshold must be between 0 and 1")
    return number


def run_dir(output_dir: Path, row: dict[str, Any], site_id: str) -> Path:
    run_id = row.get("run_id")
    if not run_id:
        raise GuardError("no run in progress — take a before snapshot first")
    return output_dir / "runs" / str(run_id) / site_id


def validate_ssh_destination(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GuardError("must be user@host")
    dest = value.strip()
    if dest.startswith("-") or not SSH_DEST_RE.fullmatch(dest):
        raise GuardError("must be user@host (set extra options in ~/.ssh/config)")
    return dest


def ssh_argv(target: dict[str, Any], command: str) -> list[str]:
    dest = validate_ssh_destination(target.get("ssh"))
    wp_path = target.get("wp_path")
    if not isinstance(wp_path, str) or not wp_path.startswith("/"):
        raise GuardError("wp_path must be an absolute path")
    if ".." in wp_path or any(ch in wp_path for ch in " \t\n;|&"):
        raise GuardError("wp_path looks unsafe")
    remote = f"wp --path={shlex.quote(wp_path)} {command}"
    return ["ssh", "-o", "BatchMode=yes", "--", dest, remote]


def ssh_wp(target: dict[str, Any], command: str, *, dry_run: bool) -> str:
    argv = ssh_argv(target, command)
    if dry_run:
        return " ".join(shlex.quote(part) for part in argv)
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GuardError(f"ssh failed: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "ssh/wp failed").strip()
        raise GuardError(detail[:800])
    return completed.stdout


def inventory(target: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return {"dry_run": True}
    plugins = json.loads(ssh_wp(target, "plugin list --format=json", dry_run=False) or "[]")
    themes = json.loads(ssh_wp(target, "theme list --format=json", dry_run=False) or "[]")
    core = ssh_wp(target, "core version", dry_run=False).strip()
    return {"core": core, "plugins": plugins, "themes": themes}


def capture_pages(
    site: dict[str, Any],
    dest: Path,
    config: dict[str, Any],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    staging_url = site["staging"]["url"]
    pages = site["pages"]
    viewport = config.get("viewport") or {}
    width = int(viewport.get("width") or 1280)
    height = int(viewport.get("height") or 800)
    paths = ",".join(str(page["path"]) for page in pages)
    ids = ",".join(str(page["id"]) for page in pages)
    argv = [
        "node",
        str(CAPTURE_SCRIPT),
        "--base-url",
        staging_url,
        "--pages",
        paths,
        "--ids",
        ids,
        "--out",
        str(dest),
        "--width",
        str(width),
        "--height",
        str(height),
    ]
    if dry_run:
        return {"dry_run": True, "command": argv, "out": str(dest)}
    dest.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(argv, check=False, capture_output=True, text=True)
    if completed.returncode not in (0, 1):
        detail = (completed.stderr or completed.stdout or "capture failed").strip()
        raise GuardError(detail[:800])
    report_path = dest / "capture.json"
    if report_path.is_file():
        report = load_json(report_path)
    else:
        try:
            report = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise GuardError("screenshot capture did not return JSON") from exc
    require_healthy_capture(report, site)
    if completed.returncode != 0:
        raise GuardError("screenshot capture failed")
    return report


def require_healthy_capture(report: dict[str, Any], site: dict[str, Any]) -> None:
    if not isinstance(report, dict) or report.get("dry_run"):
        return
    pages_by_id: dict[str, dict[str, Any]] = {}
    for page in report.get("pages") or []:
        if isinstance(page, dict) and page.get("id"):
            pages_by_id[str(page["id"])] = page
    missing: list[str] = []
    bad_status: list[str] = []
    for page in site["pages"]:
        page_id = str(page["id"])
        captured = pages_by_id.get(page_id)
        if captured is None:
            missing.append(page_id)
            continue
        if captured.get("status") != 200:
            bad_status.append(page_id)
    if report.get("ok") is False or missing or bad_status:
        parts = []
        if missing:
            parts.append("skipped: " + ", ".join(missing))
        if bad_status:
            parts.append("not HTTP 200: " + ", ".join(bad_status))
        raise GuardError(
            "page capture failed (" + "; ".join(parts or ["capture.ok is false"]) + ")"
        )


def cmd_init(args: argparse.Namespace) -> int:
    dest = Path(args.config).expanduser().resolve() if args.config else Path.cwd() / "wp-update-guard.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not args.force:
        raise GuardError(f"{dest} already exists (pass --force to replace)")
    save_json(dest, STARTER_CONFIG)
    gitignore = dest.parent / ".gitignore"
    marker = ".wp-update-guard/"
    if gitignore.is_file():
        text = gitignore.read_text(encoding="utf-8")
        if marker not in text.splitlines():
            gitignore.write_text(text.rstrip() + "\n" + marker + "\n", encoding="utf-8")
    else:
        gitignore.write_text(marker + "\n", encoding="utf-8")
    emit({"ok": True, "config": str(dest), "next": "Fill in staging and live SSH, then run status."})
    return 0


def cmd_list(config: dict[str, Any], state: dict[str, Any]) -> int:
    rows = []
    for site in config["sites"]:
        row = site_state(state, site["id"])
        rows.append(
            {
                "id": site["id"],
                "name": site.get("name") or site["id"],
                "phase": row.get("phase"),
                "decision": row.get("decision"),
            }
        )
    emit({"sites": rows})
    return 0


def cmd_status(site: dict[str, Any], row: dict[str, Any], config: dict[str, Any]) -> int:
    emit(
        {
            "id": site["id"],
            "name": site.get("name") or site["id"],
            "phase": row.get("phase"),
            "run_id": row.get("run_id"),
            "decision": row.get("decision"),
            "error": row.get("error"),
            "pages": [page["id"] for page in site["pages"]],
            "threshold": threshold_for(config, site),
            "staging_url": site["staging"]["url"],
            "live_url": (site.get("live") or {}).get("url"),
        }
    )
    return 0


def cmd_snapshot(
    site: dict[str, Any],
    row: dict[str, Any],
    config: dict[str, Any],
    output_dir: Path,
    when: str,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    if when not in ("before", "after"):
        raise GuardError("--when must be before or after")
    if when == "before":
        row["run_id"] = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        row["decision"] = None
        row["error"] = None
        row["page_results"] = []
        row["updated"] = []
        row["phase"] = "snapshot_before"
    elif not dry_run and row.get("phase") not in ("screenshot_after", "compare"):
        raise GuardError("after screenshots come after the staging update")
    dest_root = (
        output_dir / "runs" / "dry-run" / site["id"]
        if dry_run and not row.get("run_id")
        else run_dir(output_dir, row, site["id"])
    )
    dest = dest_root / when
    try:
        capture = capture_pages(site, dest, config, dry_run=dry_run)
    except GuardError:
        if when == "after" and not dry_run:
            row["phase"] = "fail_rollback"
            row["decision"] = "Rolled back"
        raise
    if when == "before":
        if not dry_run:
            row["inventory"] = inventory(site["staging"], dry_run=False)
            row["phase"] = "update_staging"
        return {"when": when, "capture": capture, "phase": row["phase"]}
    if not dry_run:
        row["phase"] = "compare"
    return {"when": when, "capture": capture, "phase": row["phase"]}


def cmd_update_staging(
    site: dict[str, Any],
    row: dict[str, Any],
    config: dict[str, Any],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    if not dry_run and row.get("phase") not in ("update_staging", "snapshot_before"):
        raise GuardError("update staging only after the before snapshot")
    commands = ["plugin update --all", "theme update --all"]
    if config.get("core_update"):
        commands.append("core update")
    logs = []
    for command in commands:
        logs.append(
            {
                "command": command,
                "output": ssh_wp(site["staging"], command, dry_run=dry_run),
            }
        )
    if not dry_run:
        row["updated"] = commands
        row["tested"] = inventory(site["staging"], dry_run=False)
        row["phase"] = "screenshot_after"
    return {"updated": logs, "phase": row["phase"]}


def cmd_compare(
    site: dict[str, Any],
    row: dict[str, Any],
    config: dict[str, Any],
    output_dir: Path,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    if not dry_run and row.get("phase") not in ("compare", "screenshot_after"):
        raise GuardError("compare only after after-screenshots")
    if not row.get("updated"):
        raise GuardError("compare only after staging has been updated")
    dest = run_dir(output_dir, row, site["id"])
    report_path = dest / "compare.json"
    argv = [
        sys.executable,
        str(COMPARE_SCRIPT),
        "--before",
        str(dest / "before"),
        "--after",
        str(dest / "after"),
        "--threshold",
        str(threshold_for(config, site)),
        "--out",
        str(report_path),
    ]
    if dry_run:
        return {"dry_run": True, "command": argv}
    completed = subprocess.run(argv, check=False, capture_output=True, text=True)
    if not report_path.is_file():
        detail = (completed.stderr or completed.stdout or "compare failed").strip()
        raise GuardError(detail[:800])
    report = load_json(report_path)
    row["page_results"] = report.get("pages") or []
    healthy = True
    try:
        require_healthy_capture(_load_capture_report(dest / "before"), site)
        require_healthy_capture(_load_capture_report(dest / "after"), site)
        result_ids = {page.get("id") for page in row["page_results"]}
        skipped = [str(page["id"]) for page in site["pages"] if page["id"] not in result_ids]
        if skipped:
            raise GuardError("configured pages were skipped: " + ", ".join(skipped))
    except GuardError as exc:
        healthy = False
        row["error"] = str(exc)
    if report.get("pass") and healthy:
        row["decision"] = "Pass"
        row["phase"] = "pass_promote"
    else:
        row["decision"] = "Rolled back"
        row["phase"] = "fail_rollback"
    return {"report": report, "phase": row["phase"], "decision": row["decision"]}


def _load_capture_report(folder: Path) -> dict[str, Any]:
    path = folder / "capture.json"
    if not path.is_file():
        raise GuardError(f"missing capture report in {folder.name}")
    return load_json(path)


def version_pin_commands(items: list[dict[str, Any]], kind: str) -> list[str]:
    commands = []
    for item in items:
        name = item.get("name")
        version = item.get("version")
        status = item.get("status")
        if not name or not version or status == "must-use":
            continue
        commands.append(
            f"{kind} install {shlex.quote(str(name))} --version={shlex.quote(str(version))} --force"
        )
    return commands


def restore_items(target: dict[str, Any], items: list[dict[str, Any]], kind: str, *, dry_run: bool) -> list[str]:
    restored = []
    for item in items:
        name = item.get("name")
        version = item.get("version")
        status = item.get("status")
        if not name or not version or status == "must-use":
            continue
        command = f"{kind} install {shlex.quote(str(name))} --version={shlex.quote(str(version))} --force"
        ssh_wp(target, command, dry_run=dry_run)
        restored.append(f"{name}@{version}")
    return restored


def cmd_rollback(site: dict[str, Any], row: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    if not dry_run and row.get("phase") not in ROLLBACKABLE_PHASES:
        raise GuardError("rollback is for a failed compare")
    inventory_data = row.get("inventory") or {}
    plugins = inventory_data.get("plugins") or []
    themes = inventory_data.get("themes") or []
    restored = {
        "plugins": restore_items(site["staging"], plugins, "plugin", dry_run=dry_run),
        "themes": restore_items(site["staging"], themes, "theme", dry_run=dry_run),
    }
    if not dry_run:
        row["decision"] = "Rolled back"
        row["phase"] = "done"
        row["error"] = None
    return {
        "restored": restored,
        "phase": row["phase"],
        "decision": "Rolled back",
        "live_changed": False,
        "summary": "A page did not match after the update. Staging was rolled back. The live site was not changed.",
    }


def cmd_promote(site: dict[str, Any], row: dict[str, Any], config: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    if not dry_run and row.get("phase") != "pass_promote":
        raise GuardError("promote only after every key page passed")
    if not row.get("updated"):
        raise GuardError("promote only after staging has been updated")
    tested = row.get("tested")
    if not isinstance(tested, dict):
        raise GuardError("promote needs the tested staging versions")
    live = site.get("live")
    if not isinstance(live, dict) or not live.get("ssh") or not live.get("wp_path"):
        raise GuardError("live ssh and wp_path are required to go live")
    commands = version_pin_commands(tested.get("plugins") or [], "plugin")
    commands.extend(version_pin_commands(tested.get("themes") or [], "theme"))
    if config.get("core_update"):
        core = tested.get("core")
        if not core:
            raise GuardError("promote needs the tested core version")
        commands.append(f"core update --version={shlex.quote(str(core))}")
    if not commands:
        raise GuardError("promote needs the tested staging versions")
    logs = []
    for command in commands:
        logs.append({"command": command, "output": ssh_wp(live, command, dry_run=dry_run)})
    if not dry_run:
        row["decision"] = "Live"
        row["phase"] = "done"
        row["error"] = None
    return {
        "updated": logs,
        "phase": row["phase"],
        "decision": "Live",
        "summary": "Every key page passed. The same updates are on the live site.",
    }


def cmd_run(
    site: dict[str, Any],
    row: dict[str, Any],
    config: dict[str, Any],
    output_dir: Path,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    steps = []
    phase = row.get("phase") or "idle"
    if phase in ("idle", "done", "snapshot_before"):
        steps.append(cmd_snapshot(site, row, config, output_dir, "before", dry_run=dry_run))
        phase = row.get("phase")
    if dry_run:
        return {"dry_run": True, "steps": steps, "phase": phase}
    if phase == "update_staging":
        steps.append(cmd_update_staging(site, row, config, dry_run=False))
        phase = row.get("phase")
    if phase == "screenshot_after":
        try:
            steps.append(cmd_snapshot(site, row, config, output_dir, "after", dry_run=False))
        except GuardError as exc:
            row["error"] = str(exc)
            row["phase"] = "fail_rollback"
            row["decision"] = "Rolled back"
            steps.append(cmd_rollback(site, row, dry_run=False))
            return {
                "phase": row.get("phase"),
                "decision": row.get("decision"),
                "steps": [step.get("summary") or step.get("phase") for step in steps],
                "summary": (steps[-1] or {}).get("summary") if steps else None,
            }
        phase = row.get("phase")
    if phase == "compare":
        steps.append(cmd_compare(site, row, config, output_dir, dry_run=False))
        phase = row.get("phase")
    if phase == "pass_promote":
        steps.append(cmd_promote(site, row, config, dry_run=False))
    elif phase == "fail_rollback":
        steps.append(cmd_rollback(site, row, dry_run=False))
    return {
        "phase": row.get("phase"),
        "decision": row.get("decision"),
        "steps": [step.get("summary") or step.get("phase") for step in steps],
        "summary": (steps[-1] or {}).get("summary") if steps else None,
    }


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="Path to wp-update-guard.json")
    common.add_argument("--dry-run", action="store_true", help="Print commands without running them")
    parser = argparse.ArgumentParser(
        description="Update WordPress on a staging copy first, check key pages, then go live only if they pass."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", parents=[common], help="Write a starter wp-update-guard.json")
    init.add_argument("--force", action="store_true")
    sub.add_parser("list", parents=[common], help="List sites and their current phase")
    status = sub.add_parser("status", parents=[common], help="Show one site")
    status.add_argument("--site", required=True)
    snap = sub.add_parser("snapshot", parents=[common], help="Screenshot key pages on staging")
    snap.add_argument("--site", required=True)
    snap.add_argument("--when", required=True, choices=("before", "after"))
    update = sub.add_parser("update-staging", parents=[common], help="Update plugins and themes on staging")
    update.add_argument("--site", required=True)
    compare = sub.add_parser("compare", parents=[common], help="Compare before and after screenshots")
    compare.add_argument("--site", required=True)
    promote = sub.add_parser("promote", parents=[common], help="Apply the same updates on live after a Pass")
    promote.add_argument("--site", required=True)
    rollback = sub.add_parser("rollback", parents=[common], help="Restore staging versions; leave live alone")
    rollback.add_argument("--site", required=True)
    run = sub.add_parser("run", parents=[common], help="Walk remaining phases for one site")
    run.add_argument("--site", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    state: dict[str, Any] | None = None
    state_path: Path | None = None
    try:
        if args.command == "init":
            return cmd_init(args)
        config_path = find_config(args.config)
        config, output_dir, state_path = load_bundle(config_path)
        state = load_json(state_path) if state_path.is_file() else {"sites": {}}
        if args.command == "list":
            return cmd_list(config, state)
        site = get_site(config, args.site)
        row = site_state(state, site["id"])
        result: dict[str, Any]
        if args.command == "status":
            return cmd_status(site, row, config)
        if args.command == "snapshot":
            result = cmd_snapshot(site, row, config, output_dir, args.when, dry_run=args.dry_run)
        elif args.command == "update-staging":
            result = cmd_update_staging(site, row, config, dry_run=args.dry_run)
        elif args.command == "compare":
            result = cmd_compare(site, row, config, output_dir, dry_run=args.dry_run)
        elif args.command == "promote":
            result = cmd_promote(site, row, config, dry_run=args.dry_run)
        elif args.command == "rollback":
            result = cmd_rollback(site, row, dry_run=args.dry_run)
        elif args.command == "run":
            result = cmd_run(site, row, config, output_dir, dry_run=args.dry_run)
        else:
            raise GuardError(f"unknown command {args.command}")
        if not args.dry_run:
            save_json(state_path, state)
        emit({"ok": True, **result})
        return 0
    except GuardError as exc:
        if state is not None and state_path is not None and not getattr(args, "dry_run", False):
            save_json(state_path, state)
        emit({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
