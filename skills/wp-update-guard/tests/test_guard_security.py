"""Regression tests for the five confirmed security-audit findings in guard.py.

Dummy hosts and planted files only. No live SSH, HTTP, npm, or Playwright.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load_script(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


guard = _load_script("guard.py", "wp_update_guard_guard")
compare_pages = _load_script("compare-pages.py", "wp_update_guard_compare_pages")


SITE = {
    "id": "example-site",
    "name": "Example site",
    "pages": [
        {"id": "home", "path": "/"},
        {"id": "shop", "path": "/shop/"},
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

CONFIG = {
    "version": 1,
    "diff_threshold": 0.02,
    "core_update": False,
    "viewport": {"width": 1280, "height": 800},
    "sites": [SITE],
}

BEFORE_INVENTORY = {
    "core": "6.4.0",
    "plugins": [
        {"name": "akismet", "version": "5.2.0", "status": "active"},
        {"name": "SHOULD-NOT-APPEAR", "version": "1.0", "status": "active"},
    ],
    "themes": [
        {"name": "twentytwentyfour", "version": "1.0", "status": "active"},
        {"name": "hello-dolly", "version": "1.6.0", "status": "inactive"},
    ],
}

TESTED_INVENTORY = {
    "core": "6.4.2",
    "plugins": [
        {"name": "akismet", "version": "5.3.0", "status": "active"},
        {"name": "hello-dolly", "version": "1.7.2", "status": "active"},
    ],
    "themes": [
        {"name": "twentytwentyfour", "version": "1.1", "status": "active"},
    ],
}

PIXELS = [(200, 40, 40)] * 16


def _write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    compare_pages.write_png_rgb(path, 4, 4, PIXELS)


def _write_capture(folder: Path, pages: list[dict]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    report = {
        "baseUrl": "https://staging.example.com",
        "out": str(folder),
        "pages": pages,
        "ok": bool(pages) and all(page.get("ok") for page in pages),
    }
    (folder / "capture.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def _plant_identical_pages(root: Path, page_ids: list[str], *, status: int = 200, omit: list[str] | None = None) -> None:
    omitted = set(omit or [])
    for when in ("before", "after"):
        folder = root / when
        captured = []
        for page_id in page_ids:
            if page_id in omitted:
                continue
            _write_png(folder / f"{page_id}.png")
            captured.append({"id": page_id, "status": status, "ok": status == 200, "path": "/"})
        _write_capture(folder, captured)


def _row(**overrides):
    row = {
        "phase": "idle",
        "run_id": "testrun",
        "decision": None,
        "inventory": None,
        "page_results": [],
        "updated": [],
        "error": None,
    }
    row.update(overrides)
    return row


class SshDestinationTests(unittest.TestCase):
    """HIGH: config ssh is injected into OpenSSH without validation or --."""

    def test_validate_site_rejects_leading_dash_ssh_option(self) -> None:
        site = json.loads(json.dumps(SITE))
        site["staging"]["ssh"] = "-oProxyCommand=true"
        with self.assertRaises(guard.GuardError) as ctx:
            guard.validate_site(site)
        self.assertIn("ssh", str(ctx.exception).lower())

    def test_ssh_wp_rejects_proxycommand_option(self) -> None:
        target = {"ssh": "-oProxyCommand=true", "wp_path": "/var/www/staging"}
        with self.assertRaises(guard.GuardError):
            guard.ssh_wp(target, "plugin list --format=json", dry_run=True)

    def test_ssh_argv_is_a_list_with_dashdash_before_destination(self) -> None:
        target = {"ssh": "deploy@staging.example.net", "wp_path": "/var/www/staging"}
        argv = guard.ssh_argv(target, "plugin list --format=json")
        self.assertIsInstance(argv, list)
        self.assertNotIn(True, [isinstance(part, str) and " " in part and part.startswith("ssh ") for part in argv[:3]])
        dest_index = argv.index("deploy@staging.example.net")
        self.assertEqual(argv[dest_index - 1], "--")
        self.assertEqual(argv[0], "ssh")
        self.assertFalse(any(part.startswith("-oProxy") for part in argv))

    def test_ssh_g_on_argv_does_not_set_proxycommand(self) -> None:
        target = {"ssh": "deploy@staging.example.net", "wp_path": "/var/www/staging"}
        argv = guard.ssh_argv(target, "plugin list --format=json")
        probe = ["ssh", "-G", *argv[1:-1]]
        completed = subprocess.run(probe, check=False, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        dumped = {line.split(" ", 1)[0]: line.split(" ", 1)[1] if " " in line else "" for line in completed.stdout.splitlines()}
        self.assertEqual(dumped.get("hostname"), "staging.example.net")
        self.assertNotEqual((dumped.get("proxycommand") or "").strip(), "true")

    def test_ssh_wp_dry_run_does_not_use_a_shell_string_as_argv(self) -> None:
        target = {"ssh": "deploy@staging.example.net", "wp_path": "/var/www/staging"}
        with patch.object(guard.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
            guard.ssh_wp(target, "plugin list --format=json", dry_run=False)
        passed = run.call_args.args[0]
        self.assertIsInstance(passed, list)
        self.assertEqual(passed[0], "ssh")
        self.assertFalse(run.call_args.kwargs.get("shell"))


class PageCheckPassTests(unittest.TestCase):
    """HIGH: Pass ignored HTTP status and skipped configured pages."""

    def test_compare_does_not_pass_on_http_500_or_skipped_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            dest = output_dir / "runs" / "testrun" / "example-site"
            _plant_identical_pages(dest, ["home", "shop"], status=500, omit=["shop"])
            row = _row(phase="screenshot_after", updated=["plugin update --all", "theme update --all"])
            result = guard.cmd_compare(SITE, row, CONFIG, output_dir, dry_run=False)
            self.assertNotEqual(row["phase"], "pass_promote")
            self.assertNotEqual(row["decision"], "Pass")
            self.assertEqual(row["phase"], "fail_rollback")
            self.assertNotEqual(result["decision"], "Pass")

    def test_compare_does_not_pass_when_configured_page_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            dest = output_dir / "runs" / "testrun" / "example-site"
            _plant_identical_pages(dest, ["home", "shop"], status=200, omit=["shop"])
            row = _row(phase="screenshot_after", updated=["plugin update --all"])
            guard.cmd_compare(SITE, row, CONFIG, output_dir, dry_run=False)
            self.assertEqual(row["phase"], "fail_rollback")
            self.assertNotEqual(row["decision"], "Pass")

    def test_run_does_not_promote_when_capture_is_an_error_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            dest = output_dir / "runs" / "testrun" / "example-site"
            _plant_identical_pages(dest, ["home", "shop"], status=500, omit=["shop"])
            row = _row(
                phase="compare",
                inventory=BEFORE_INVENTORY,
                updated=["plugin update --all", "theme update --all"],
                tested=TESTED_INVENTORY,
            )
            recorded: list[str] = []

            def fake_ssh(target, command, *, dry_run):
                recorded.append(f"{target.get('ssh')} {command}")
                return f"dry {command}"

            with patch.object(guard, "ssh_wp", side_effect=fake_ssh):
                result = guard.cmd_run(SITE, row, CONFIG, output_dir, dry_run=False)
            joined = " ".join(recorded)
            self.assertNotIn("update --all", joined)
            self.assertNotEqual(row.get("decision"), "Live")
            self.assertNotEqual(row.get("phase"), "pass_promote")
            self.assertNotIn("deploy@live.example.net", joined)
            self.assertEqual(result["decision"], "Rolled back")

    def test_capture_pages_treats_error_report_as_failure(self) -> None:
        report = {
            "ok": False,
            "pages": [{"id": "home", "status": 500, "ok": False}, {"id": "shop", "status": 200, "ok": True}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            (dest / "capture.json").write_text(json.dumps(report), encoding="utf-8")
            completed = subprocess.CompletedProcess(args=[], returncode=1, stdout=json.dumps(report), stderr="")
            with patch.object(guard.subprocess, "run", return_value=completed):
                with self.assertRaises(guard.GuardError):
                    guard.capture_pages(SITE, dest, CONFIG, dry_run=False)


class PromotePinnedVersionTests(unittest.TestCase):
    """HIGH: promote re-ran live update --all instead of tested versions."""

    def test_promote_installs_tested_versions_not_update_all(self) -> None:
        row = _row(
            phase="pass_promote",
            inventory=BEFORE_INVENTORY,
            updated=["plugin update --all", "theme update --all"],
            tested=TESTED_INVENTORY,
        )
        result = guard.cmd_promote(SITE, row, CONFIG, dry_run=True)
        outputs = " ".join(item.get("output") or item.get("command") or "" for item in result["updated"])
        commands = " ".join(item.get("command") or "" for item in result["updated"])
        blob = outputs + " " + commands
        self.assertIn("plugin install akismet --version=5.3.0 --force", blob)
        self.assertIn("plugin install hello-dolly --version=1.7.2 --force", blob)
        self.assertIn("theme install twentytwentyfour --version=1.1 --force", blob)
        self.assertNotIn("update --all", blob)
        self.assertNotIn("SHOULD-NOT-APPEAR", blob)
        self.assertIn("deploy@live.example.net", blob)
        self.assertIn("/var/www/live", blob)

    def test_promote_uses_tested_core_version_when_enabled(self) -> None:
        row = _row(
            phase="pass_promote",
            inventory=BEFORE_INVENTORY,
            updated=["plugin update --all", "theme update --all", "core update"],
            tested=TESTED_INVENTORY,
        )
        result = guard.cmd_promote(SITE, row, {**CONFIG, "core_update": True}, dry_run=True)
        blob = " ".join(item.get("output") or item.get("command") or "" for item in result["updated"])
        self.assertIn("core update --version=6.4.2", blob)
        self.assertNotIn("core update --all", blob)
        self.assertNotRegex(blob, r"(?<!version=)core update(?! --version)")

    def test_update_staging_persists_post_update_inventory_as_tested(self) -> None:
        row = _row(phase="update_staging", inventory=BEFORE_INVENTORY)
        with patch.object(guard, "ssh_wp", return_value="ok"), patch.object(
            guard, "inventory", return_value=TESTED_INVENTORY
        ) as inv:
            guard.cmd_update_staging(SITE, row, CONFIG, dry_run=False)
        inv.assert_called_once()
        self.assertEqual(row["tested"], TESTED_INVENTORY)
        self.assertEqual(row["inventory"], BEFORE_INVENTORY)
        self.assertEqual(row["phase"], "screenshot_after")


class AfterSnapshotRequiresStagingUpdateTests(unittest.TestCase):
    """MEDIUM: after-snapshot accepted update_staging and could Pass with updated=[]."""

    def test_after_snapshot_rejects_update_staging_phase(self) -> None:
        row = _row(phase="update_staging", updated=[], inventory=BEFORE_INVENTORY)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(guard.GuardError) as ctx:
                guard.cmd_snapshot(SITE, row, CONFIG, Path(tmp), "after", dry_run=False)
        self.assertIn("staging update", str(ctx.exception).lower())

    def test_compare_refuses_empty_updated_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            dest = output_dir / "runs" / "testrun" / "example-site"
            _plant_identical_pages(dest, ["home", "shop"], status=200)
            row = _row(phase="compare", updated=[])
            with self.assertRaises(guard.GuardError):
                guard.cmd_compare(SITE, row, CONFIG, output_dir, dry_run=False)
            self.assertNotEqual(row.get("phase"), "pass_promote")

    def test_promote_refuses_empty_updated_log(self) -> None:
        row = _row(phase="pass_promote", updated=[], inventory=BEFORE_INVENTORY, tested=TESTED_INVENTORY)
        with self.assertRaises(guard.GuardError):
            guard.cmd_promote(SITE, row, CONFIG, dry_run=True)


class RollbackAfterFatalCaptureTests(unittest.TestCase):
    """MEDIUM: rollback refused saved screenshot_after after a fatal after-capture."""

    def test_rollback_accepts_screenshot_after_and_restores_inventory(self) -> None:
        row = _row(phase="screenshot_after", inventory=BEFORE_INVENTORY, updated=["plugin update --all"])
        recorded: list[str] = []

        def fake_ssh(target, command, *, dry_run):
            recorded.append(f"{target.get('ssh')} {target.get('wp_path')} {command}")
            return f"ok {command}"

        with patch.object(guard, "ssh_wp", side_effect=fake_ssh):
            result = guard.cmd_rollback(SITE, row, dry_run=False)
        joined = " ".join(recorded)
        self.assertIn("plugin install akismet --version=5.2.0 --force", joined)
        self.assertIn("theme install twentytwentyfour --version=1.0 --force", joined)
        self.assertIn("deploy@staging.example.net", joined)
        self.assertNotIn("deploy@live.example.net", joined)
        self.assertFalse(result["live_changed"])
        self.assertEqual(row["decision"], "Rolled back")
        self.assertEqual(row["phase"], "done")

    def test_run_fatal_after_capture_persists_rollbackable_phase_and_restores(self) -> None:
        row = _row(
            phase="screenshot_after",
            inventory=BEFORE_INVENTORY,
            updated=["plugin update --all", "theme update --all"],
        )
        recorded: list[str] = []

        def fake_ssh(target, command, *, dry_run):
            recorded.append(command)
            return f"ok {command}"

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(guard, "capture_pages", side_effect=guard.GuardError("Playwright is not installed")):
                with patch.object(guard, "ssh_wp", side_effect=fake_ssh):
                    result = guard.cmd_run(SITE, row, CONFIG, Path(tmp), dry_run=False)
        self.assertIn(row["phase"], {"fail_rollback", "done"})
        self.assertEqual(result["decision"], "Rolled back")
        joined = " ".join(recorded)
        self.assertIn("plugin install akismet --version=5.2.0 --force", joined)
        self.assertNotIn("plugin update --all", joined)

    def test_rollback_still_refuses_idle(self) -> None:
        row = _row(phase="idle")
        with self.assertRaises(guard.GuardError):
            guard.cmd_rollback(SITE, row, dry_run=False)


class HealthyPathStillPasses(unittest.TestCase):
    def test_compare_passes_when_every_configured_page_is_http_200(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            dest = output_dir / "runs" / "testrun" / "example-site"
            _plant_identical_pages(dest, ["home", "shop"], status=200)
            row = _row(phase="screenshot_after", updated=["plugin update --all", "theme update --all"])
            result = guard.cmd_compare(SITE, row, CONFIG, output_dir, dry_run=False)
            self.assertEqual(row["phase"], "pass_promote")
            self.assertEqual(result["decision"], "Pass")


if __name__ == "__main__":
    unittest.main()
