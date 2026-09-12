from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "build_release.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_release", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load build_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = load_builder()

    def test_verify_sha256_rejects_tampered_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            asset = Path(tmp) / "asset.zip"
            asset.write_bytes(b"official")
            digest = hashlib.sha256(asset.read_bytes()).hexdigest()
            self.builder.verify_sha256(asset, digest)
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                self.builder.verify_sha256(asset, "0" * 64)

    def test_export_repository_index_ignores_dirty_and_hardlinked_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repository = tmp_path / "repo"
            repository.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
            portable = repository / "portable"
            portable.mkdir()
            outside = tmp_path / "outside.txt"
            outside.write_bytes(b"indexed-safe")
            linked = portable / "input.txt"
            try:
                os.link(outside, linked)
            except OSError:
                self.skipTest("hardlinks are unavailable on this platform")
            subprocess.run(["git", "add", "portable/input.txt"], cwd=repository, check=True)
            outside.write_bytes(b"dirty-worktree-secret")

            exported = self.builder.export_repository_index(
                repository,
                tmp_path / "exported",
            )

            self.assertEqual(
                (exported / "portable" / "input.txt").read_bytes(),
                b"indexed-safe",
            )

    def test_export_repository_index_rejects_symlink_git_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repository = tmp_path / "repo"
            repository.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
            portable = repository / "portable"
            portable.mkdir()
            target = tmp_path / "outside.txt"
            target.write_text("outside", encoding="utf-8")
            try:
                (portable / "linked.txt").symlink_to(target)
            except OSError:
                self.skipTest("symlinks are unavailable on this platform")
            subprocess.run(["git", "add", "portable/linked.txt"], cwd=repository, check=True)

            with self.assertRaisesRegex(ValueError, "Git mode"):
                self.builder.export_repository_index(
                    repository,
                    tmp_path / "exported",
                )

    def test_main_builds_only_from_exported_git_index(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        main_source = source[source.index("def main()") :]
        self.assertIn("indexed_repository = export_repository_index", main_source)
        self.assertIn("verify_portable_source_tree(indexed_repository)", main_source)
        self.assertIn("stage_portable(indexed_repository", main_source)

    def test_stage_portable_contains_launcher_skill_and_no_auth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            upstream = tmp_path / "upstream.zip"
            with zipfile.ZipFile(upstream, "w") as archive:
                archive.writestr("opencode.exe", b"MZ" + b"\0" * 128)

            stage = tmp_path / "stage"
            manifest = {
                "opencode_version": "1.18.30",
                "asset_name": "opencode-windows-x64.zip",
                "asset_sha256": hashlib.sha256(upstream.read_bytes()).hexdigest(),
                "model": "opencode-go/gpt-5.6-luna",
            }
            self.builder.stage_portable(ROOT, upstream, stage, manifest)

            package = stage / "OpenCode-1C-Portable-Windows-x64"
            self.assertTrue((package / "bin" / "opencode.exe").is_file())
            self.assertTrue((package / "opencode.cmd").is_file())
            self.assertTrue((package / "setup-key.cmd").is_file())
            skills_root = (
                package
                / "userdata"
                / ".config"
                / "opencode"
                / "skills"
            )
            self.assertEqual(len(list(skills_root.glob("*/SKILL.md"))), 79)
            self.assertTrue((skills_root / "epf-init" / "SKILL.md").is_file())
            self.assertTrue((skills_root / "web-test" / "SKILL.md").is_file())
            self.assertFalse(
                (
                    package
                    / "userdata"
                    / ".local"
                    / "share"
                    / "opencode"
                    / "auth.json"
                ).exists()
            )
            config = json.loads(
                (
                    package
                    / "userdata"
                    / ".config"
                    / "opencode"
                    / "opencode.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(config["model"], "opencode-go/gpt-5.6-luna")

            text_files = [
                path
                for path in package.rglob("*")
                if path.is_file() and path.suffix.lower() != ".exe"
            ]
            combined = b"\n".join(path.read_bytes() for path in text_files)
            self.assertNotRegex(combined, rb"sk-[A-Za-z0-9_-]{20,}")

    def test_bundled_1c_skills_are_exact_pinned_opencode_upstream(self) -> None:
        opencode_root = (
            ROOT
            / "portable"
            / "userdata"
            / ".config"
            / "opencode"
        )
        skills_root = opencode_root / "skills"
        provenance_root = opencode_root / "third-party" / "cc-1c-skills"
        source = json.loads((provenance_root / "SOURCE.json").read_text(encoding="utf-8"))
        self.assertEqual(source["repository"], "https://github.com/Nikolay-Shirokov/cc-1c-skills")
        self.assertEqual(source["branch"], "port-opencode")
        self.assertEqual(source["commit"], "05b3b3a58700f337a8b4e5c9e0e23f9bc0d8fdd0")
        self.assertEqual(source["generated_from_commit"], "6169a7ff939aa3c73f7d9799acf693fa3bfd33ed")
        self.assertEqual(source["path"], ".opencode/skills")
        self.assertEqual(source["license"], "MIT")
        self.assertEqual(source["skill_count"], 79)
        self.assertEqual(source["file_count"], 341)
        self.assertEqual(
            hashlib.sha256((provenance_root / "MANIFEST.sha256").read_bytes()).hexdigest(),
            "2e0995f8de6822ccf48574c2f13eadea9cd8204dbded7ea07d78d8d7a5d2ca7a",
        )
        self.assertEqual(
            hashlib.sha256((provenance_root / "LICENSE").read_bytes()).hexdigest(),
            "e1d8517691002bad67293250bd76338b34e4891698707d9fcbdc488f6e51bd79",
        )
        self.assertEqual(len(list(skills_root.glob("*/SKILL.md"))), 79)
        for required_skill in ("epf-init", "form-compile", "meta-compile", "web-test"):
            self.assertTrue((skills_root / required_skill / "SKILL.md").is_file())
        for forbidden_skill in ("1c-development", "1c-bsl-code-generation"):
            self.assertFalse((skills_root / forbidden_skill).exists())
        self.builder.verify_bundled_skills(ROOT)
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
        self.assertIn(
            "portable/userdata/.config/opencode/skills/** -text",
            attributes,
        )
        self.assertIn(
            "portable/userdata/.config/opencode/third-party/cc-1c-skills/LICENSE -text",
            attributes,
        )

    def test_portable_source_tree_contains_only_release_inputs(self) -> None:
        self.builder.verify_portable_source_tree(ROOT)

    def test_documentation_lists_ready_skill_suite_requirements(self) -> None:
        for path in (ROOT / "README.md", ROOT / "portable" / "README.md"):
            text = path.read_text(encoding="utf-8")
            for requirement in (
                "Nikolay-Shirokov/cc-1c-skills",
                "79",
                "PowerShell 5.1+",
                "1С:Предприятие 8.3",
                "Node.js 18+",
                "npx playwright install chromium",
                "ffmpeg",
                "lxml",
                "Pillow",
            ):
                self.assertIn(requirement, text, f"{requirement} is missing from {path}")

    def test_stage_portable_normalizes_batch_files_to_crlf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repository = tmp_path / "repo"
            portable = repository / "portable"
            config_path = portable / "userdata" / ".config" / "opencode" / "opencode.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text('{"model": "old"}\n', encoding="utf-8")
            (portable / "launcher.cmd").write_bytes(b"@echo off\necho ready\n")
            upstream = tmp_path / "upstream.zip"
            with zipfile.ZipFile(upstream, "w") as archive:
                archive.writestr("opencode.exe", b"MZ" + b"\0" * 128)
            manifest = {
                "opencode_version": "1.18.30",
                "asset_name": "opencode-windows-x64.zip",
                "asset_sha256": hashlib.sha256(upstream.read_bytes()).hexdigest(),
                "model": "opencode-go/gpt-5.6-luna",
            }

            package = self.builder.stage_portable(
                repository,
                upstream,
                tmp_path / "stage",
                manifest,
            )

            batch = (package / "launcher.cmd").read_bytes()
            self.assertIn(b"\r\n", batch)
            self.assertNotIn(b"\n", batch.replace(b"\r\n", b""))

    def test_launcher_routes_all_xdg_state_inside_userdata(self) -> None:
        launcher = (ROOT / "portable" / "opencode.cmd").read_text(encoding="utf-8")
        for variable in (
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_CACHE_HOME",
            "XDG_STATE_HOME",
            "OPENCODE_CONFIG_DIR",
            "OPENCODE_1C_SKILLS_DIR",
        ):
            self.assertIn(f'set "{variable}=', launcher)
        self.assertNotIn('set "HOME=', launcher)
        self.assertNotIn('set "USERPROFILE=', launcher)

    def test_portable_instructions_resolve_upstream_project_paths(self) -> None:
        config_root = ROOT / "portable" / "userdata" / ".config" / "opencode"
        config = json.loads((config_root / "opencode.json").read_text(encoding="utf-8"))
        self.assertIn("./PORTABLE_1C_SKILLS.md", config["instructions"])
        instructions = (config_root / "PORTABLE_1C_SKILLS.md").read_text(encoding="utf-8")
        self.assertIn(".opencode/skills/", instructions)
        self.assertIn("OPENCODE_1C_SKILLS_DIR", instructions)
        self.assertIn("npx playwright install chromium", instructions)
        self.assertIn("не изменяй", instructions.lower())

    def test_upstream_script_execution_requires_user_approval(self) -> None:
        config_path = ROOT / "portable" / "userdata" / ".config" / "opencode" / "opencode.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["permission"]["bash"]["*"], "ask")
        self.assertEqual(config["permission"]["bash"]["*web-publish*"], "deny")
        self.assertEqual(config["permission"]["skill"]["web-publish"], "deny")
        for path in (ROOT / "README.md", ROOT / "portable" / "README.md"):
            text = path.read_text(encoding="utf-8").lower()
            self.assertIn("подтверждение", text)
            self.assertIn("web-publish", text)
            self.assertIn("отключ", text)

    def test_windows_ci_parses_and_executes_upstream_powershell(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("./tests/windows-skills-smoke.ps1", workflow)
        self.assertIn("github.event_name == 'push'", workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        smoke_path = ROOT / "tests" / "windows-skills-smoke.ps1"
        smoke = smoke_path.read_text(encoding="utf-8")
        self.assertIn("Parser]::ParseFile", smoke)
        self.assertIn("ast.parse", smoke)
        self.assertIn("-ne 72", smoke)
        self.assertIn("epf-init", smoke)
        self.assertIn("OPENCODE_1C_SKILLS_DIR", smoke)
        self.assertIn("build_release.py", smoke)
        self.assertIn("debug skill", smoke)
        self.assertIn("$SkillJson -join", smoke)
        self.assertIn("-ne 79", smoke)

    def test_make_archive_has_one_root_and_checksum_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            stage = tmp_path / "stage"
            package = stage / "OpenCode-1C-Portable-Windows-x64"
            package.mkdir(parents=True)
            (package / "file.txt").write_text("payload", encoding="utf-8")
            binary = package / "bin" / "opencode.exe"
            binary.parent.mkdir()
            binary.write_bytes(b"MZ official upstream")
            binary_sha256 = hashlib.sha256(binary.read_bytes()).hexdigest()
            dist = tmp_path / "dist"

            archive_path, checksum_path = self.builder.make_archive(
                stage,
                dist,
                release_version="1.0.0",
                trusted_binary_sha256=binary_sha256,
            )

            self.assertTrue(archive_path.is_file())
            self.assertTrue(checksum_path.is_file())
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(
                    archive.namelist(),
                    [
                        "OpenCode-1C-Portable-Windows-x64/bin/opencode.exe",
                        "OpenCode-1C-Portable-Windows-x64/file.txt",
                    ],
                )
                self.assertIsNone(archive.testzip())
            expected = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            self.assertEqual(
                checksum_path.read_text(encoding="ascii"),
                f"{expected}  {archive_path.name}\n",
            )

    def test_stage_portable_rejects_source_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repository = tmp_path / "repo"
            portable = repository / "portable"
            portable.mkdir(parents=True)
            config_path = portable / "userdata" / ".config" / "opencode" / "opencode.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text('{"model": "old"}\n', encoding="utf-8")
            (portable / "real.txt").write_text("safe", encoding="utf-8")
            try:
                (portable / "linked.txt").symlink_to(portable / "real.txt")
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable on this platform")
            upstream = tmp_path / "upstream.zip"
            with zipfile.ZipFile(upstream, "w") as archive:
                archive.writestr("opencode.exe", b"MZ" + b"\0" * 128)
            manifest = {
                "opencode_version": "1.18.30",
                "asset_name": "opencode-windows-x64.zip",
                "asset_sha256": hashlib.sha256(upstream.read_bytes()).hexdigest(),
                "model": "opencode-go/gpt-5.6-luna",
            }

            with self.assertRaisesRegex(ValueError, "symlink|reparse"):
                self.builder.stage_portable(
                    repository, upstream, tmp_path / "stage", manifest
                )

    def test_hardlink_count_zero_is_treated_as_unknown_not_multiple(self) -> None:
        class FakeStat:
            st_nlink = 0

        self.assertFalse(self.builder._has_multiple_hardlinks(FakeStat()))
        FakeStat.st_nlink = 1
        self.assertFalse(self.builder._has_multiple_hardlinks(FakeStat()))
        FakeStat.st_nlink = 2
        self.assertTrue(self.builder._has_multiple_hardlinks(FakeStat()))

    def test_stage_portable_rejects_source_hardlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repository = tmp_path / "repo"
            portable = repository / "portable"
            portable.mkdir(parents=True)
            config_path = portable / "userdata" / ".config" / "opencode" / "opencode.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text('{"model": "old"}\n', encoding="utf-8")
            outside = tmp_path / "outside.txt"
            outside.write_text("external", encoding="utf-8")
            try:
                os.link(outside, portable / "linked.txt")
            except OSError:
                self.skipTest("hardlinks are unavailable on this platform")
            upstream = tmp_path / "upstream.zip"
            with zipfile.ZipFile(upstream, "w") as archive:
                archive.writestr("opencode.exe", b"MZ" + b"\0" * 128)
            manifest = {
                "opencode_version": "1.18.30",
                "asset_name": "opencode-windows-x64.zip",
                "asset_sha256": hashlib.sha256(upstream.read_bytes()).hexdigest(),
                "model": "opencode-go/gpt-5.6-luna",
            }

            with self.assertRaisesRegex(ValueError, "hardlink"):
                self.builder.stage_portable(
                    repository, upstream, tmp_path / "stage", manifest
                )

    def test_make_archive_rejects_packaged_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            stage = tmp_path / "stage"
            package = stage / "OpenCode-1C-Portable-Windows-x64"
            package.mkdir(parents=True)
            (package / "real.txt").write_text("safe", encoding="utf-8")
            try:
                (package / "linked.txt").symlink_to(package / "real.txt")
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable on this platform")

            with self.assertRaisesRegex(ValueError, "symlink|reparse"):
                self.builder.make_archive(stage, tmp_path / "dist", "1.0.0")

    def test_make_archive_rejects_binary_not_matching_upstream_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            stage = tmp_path / "stage"
            binary = stage / "OpenCode-1C-Portable-Windows-x64" / "bin" / "opencode.exe"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"MZ untrusted replacement")
            expected = hashlib.sha256(b"MZ official upstream").hexdigest()

            with self.assertRaisesRegex(ValueError, "trusted binary SHA-256 mismatch"):
                self.builder.make_archive(
                    stage,
                    tmp_path / "dist",
                    "1.0.0",
                    trusted_binary_sha256=expected,
                )

    def test_semver_rejects_empty_prerelease_components(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "release_version"):
                self.builder.make_archive(
                    Path(tmp) / "stage", Path(tmp) / "dist", "1.0.0-alpha..1"
                )

        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("[.-][0-9A-Za-z.-]+", workflow)

    def test_semver_rejects_numeric_prerelease_with_leading_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            package = tmp_path / "stage" / "OpenCode-1C-Portable-Windows-x64"
            package.mkdir(parents=True)
            (package / "file.txt").write_text("payload", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "release_version"):
                self.builder.make_archive(
                    tmp_path / "stage", tmp_path / "dist", "1.0.0-01"
                )

        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("SEMVER_RE.fullmatch", workflow)

    def test_secret_scan_rejects_additional_secret_files_and_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "package"
            package.mkdir()
            (package / ".env.production").write_text("MODEL=safe\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "secret-bearing file"):
                self.builder.scan_for_secrets(package)

        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "package"
            package.mkdir()
            (package / "notes.txt").write_text(
                "github token " + "ghp_" + ("1" * 40) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "possible API key"):
                self.builder.scan_for_secrets(package)

    def test_final_archive_scan_rejects_secret_and_trusts_pinned_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "release.zip"
            trusted_binary = b"MZ " + b"sk-" + (b"1" * 32)
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "OpenCode-1C-Portable-Windows-x64/bin/opencode.exe",
                    trusted_binary,
                )
                archive.writestr(
                    "OpenCode-1C-Portable-Windows-x64/notes.txt",
                    b"sk-" + (b"1" * 32),
                )
            with self.assertRaisesRegex(ValueError, "archive contains possible API key"):
                self.builder.scan_archive_for_secrets(
                    archive_path,
                    hashlib.sha256(trusted_binary).hexdigest(),
                )

            clean_archive = Path(tmp) / "trusted.zip"
            clean_binary = b"MZ " + b"sk-" + (b"2" * 32)
            with zipfile.ZipFile(clean_archive, "w") as archive:
                archive.writestr(
                    "OpenCode-1C-Portable-Windows-x64/bin/opencode.exe",
                    clean_binary,
                )
            self.builder.scan_archive_for_secrets(
                clean_archive,
                hashlib.sha256(clean_binary).hexdigest(),
            )

    def test_batch_scripts_quote_missing_binary_and_check_skips_launcher(self) -> None:
        launcher = (ROOT / "portable" / "opencode.cmd").read_text(encoding="utf-8")
        check = (ROOT / "portable" / "check.cmd").read_text(encoding="utf-8")
        for script in (launcher, check):
            self.assertIn("setlocal EnableExtensions DisableDelayedExpansion", script)
            self.assertIn('set "ROOT=%~dp0"', script)
            self.assertNotIn("for %%I in", script)
        self.assertIn('echo [ERROR] File not found: "%ROOT%bin\\opencode.exe"', launcher)
        self.assertNotIn("call \"%~dp0opencode.cmd\"", check)
        self.assertNotIn('call "%ROOT%setup-key.cmd"', launcher)
        self.assertIn('"%ROOT%bin\\opencode.exe" --version', check)

    def test_setup_key_script_contains_atomic_user_acl_hardening(self) -> None:
        script = (ROOT / "portable" / "setup-key.ps1").read_text(encoding="utf-8")
        for required in (
            "[IO.File]::Replace",
            "SetAccessRuleProtection($true, $false)",
            "ReparsePoint",
            "Ensure-DirectoryPathWithoutReparse",
            '@("userdata", ".local", "share", "opencode")',
            "Remove-Item",
            "NTFS",
        ):
            self.assertIn(required, script)


if __name__ == "__main__":
    unittest.main()
