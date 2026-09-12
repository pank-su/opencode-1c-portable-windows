#!/usr/bin/env python3
"""Build the public, secret-free Windows portable release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import urllib.request
import zipfile


PACKAGE_ROOT = "OpenCode-1C-Portable-Windows-x64"
REQUIRED_MANIFEST_FIELDS = {
    "opencode_version",
    "asset_name",
    "asset_sha256",
    "model",
}
SEMVER_CORE = r"(?:0|[1-9][0-9]*)"
SEMVER_PRERELEASE_IDENTIFIER = (
    r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
)
SEMVER_RE = re.compile(
    rf"{SEMVER_CORE}\.{SEMVER_CORE}\.{SEMVER_CORE}"
    rf"(?:-{SEMVER_PRERELEASE_IDENTIFIER}"
    rf"(?:\.{SEMVER_PRERELEASE_IDENTIFIER})*)?"
)
SECRET_PATTERNS = (
    re.compile(rb"sk-(?:ant-)?[A-Za-z0-9_-]{20,}"),
    re.compile(rb"(?:ghp|gho|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(rb"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"xox[baprs]-[0-9A-Za-z-]{20,}"),
)
TRUSTED_BINARY = PurePosixPath("bin/opencode.exe")
FORBIDDEN_NAMES = {"auth.json", ".env", "credentials.json"}
FORBIDDEN_SUFFIXES = {".pem", ".p12", ".pfx", ".jks", ".key"}
BUNDLED_SKILL_SOURCE = {
    "name": "1c-bsl-code-generation",
    "repository": "https://github.com/SteelMorgan/cursor-anthropic-skills",
    "commit": "4df7122c0960d54fe1b9a7e535cc92c315cee653",
    "path": "custom-skills/1C_BSL_SKILL.md",
    "sha256": "f2f9d035cdd619e6475a3595f8e9b0af6cb77312216cdd22f20155fb83123124",
    "license": "MIT",
    "license_sha256": "59d246c7c36696458513387f2161fa1b912e31a52be98d4e658e84abd089918a",
}
PORTABLE_SOURCE_FILES = {
    "README.md",
    "check.cmd",
    "opencode.cmd",
    "setup-key.cmd",
    "setup-key.ps1",
    "userdata/.config/opencode/opencode.json",
    "userdata/.config/opencode/skills/1c-bsl-code-generation/LICENSE",
    "userdata/.config/opencode/skills/1c-bsl-code-generation/SKILL.md",
    "userdata/.config/opencode/skills/1c-bsl-code-generation/SOURCE.json",
}


def _is_reparse_stat(file_stat: os.stat_result) -> bool:
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(file_stat.st_mode) or bool(
        getattr(file_stat, "st_file_attributes", 0) & reparse_point
    )


def _regular_files_beneath(root: Path) -> list[Path]:
    root_stat = os.stat(root, follow_symlinks=False)
    if _is_reparse_stat(root_stat):
        raise ValueError(f"reparse point is forbidden: {root}")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError(f"packaging root is not a directory: {root}")
    resolved_root = root.resolve(strict=True)
    files: list[Path] = []

    def visit(directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                entry_stat = entry.stat(follow_symlinks=False)
                if _is_reparse_stat(entry_stat):
                    raise ValueError(f"symlink or reparse point is forbidden: {path}")
                try:
                    path.resolve(strict=True).relative_to(resolved_root)
                except ValueError as error:
                    raise ValueError(f"packaged path escapes root: {path}") from error
                if stat.S_ISDIR(entry_stat.st_mode):
                    visit(path)
                elif stat.S_ISREG(entry_stat.st_mode):
                    files.append(path)
                else:
                    raise ValueError(f"non-regular packaged member: {path}")

    visit(root)
    return files


def _contains_secret(data: bytes) -> bool:
    return any(pattern.search(data) for pattern in SECRET_PATTERNS)


def _forbidden_file(path: Path) -> bool:
    name = path.name.lower()
    return (
        name in FORBIDDEN_NAMES
        or name.startswith(".env.")
        or path.suffix.lower() in FORBIDDEN_SUFFIXES
    )


def load_manifest(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = REQUIRED_MANIFEST_FIELDS - data.keys()
    if missing:
        raise ValueError(f"manifest is missing: {', '.join(sorted(missing))}")
    digest = data["asset_sha256"].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("asset_sha256 must contain 64 lowercase hex characters")
    if not SEMVER_RE.fullmatch(data["opencode_version"]):
        raise ValueError("opencode_version must be semantic x.y.z")
    if data["asset_name"] != "opencode-windows-x64.zip":
        raise ValueError("only the official Windows x64 asset is supported")
    if not data["model"].startswith("opencode-go/"):
        raise ValueError("model must use the opencode-go provider")
    return data


def verify_sha256(path: Path, expected: str) -> None:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual.lower() != expected.lower():
        raise ValueError(f"SHA-256 mismatch for {path.name}: {actual}")


def verify_bundled_skill(repository_root: Path) -> None:
    skills_root = (
        repository_root
        / "portable"
        / "userdata"
        / ".config"
        / "opencode"
        / "skills"
    )
    skill_root = skills_root / BUNDLED_SKILL_SOURCE["name"]
    source_path = skill_root / "SOURCE.json"
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("bundled 1C skill source metadata is missing or invalid") from error
    if source != BUNDLED_SKILL_SOURCE:
        raise ValueError("bundled 1C skill source metadata does not match the pinned upstream")
    if (skills_root / "1c-development").exists():
        raise ValueError("the custom 1c-development skill must not be bundled")
    verify_sha256(skill_root / "SKILL.md", source["sha256"])
    verify_sha256(skill_root / "LICENSE", source["license_sha256"])


def verify_portable_source_tree(repository_root: Path) -> None:
    source_root = repository_root / "portable"
    actual = {
        path.relative_to(source_root).as_posix()
        for path in _regular_files_beneath(source_root)
    }
    if actual != PORTABLE_SOURCE_FILES:
        missing = sorted(PORTABLE_SOURCE_FILES - actual)
        unexpected = sorted(actual - PORTABLE_SOURCE_FILES)
        raise ValueError(
            "portable source tree differs from the release allowlist; "
            f"missing={missing}; unexpected={unexpected}"
        )


def download_asset(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "opencode-1c-portable-builder"})
    with urllib.request.urlopen(request, timeout=120) as response:
        destination.write_bytes(response.read())


def _read_opencode_binary(asset: Path) -> bytes:
    with zipfile.ZipFile(asset) as archive:
        candidates = []
        for name in archive.namelist():
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts:
                raise ValueError(f"unsafe path in upstream archive: {name}")
            if pure.name.lower() == "opencode.exe" and not name.endswith("/"):
                candidates.append(name)
        if len(candidates) != 1:
            raise ValueError("upstream archive must contain exactly one opencode.exe")
        binary = archive.read(candidates[0])
    if not binary.startswith(b"MZ"):
        raise ValueError("upstream opencode.exe is not a PE executable")
    return binary


def _extract_opencode(asset: Path, destination: Path) -> None:
    binary = _read_opencode_binary(asset)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(binary)


def scan_for_secrets(package: Path) -> None:
    for path in _regular_files_beneath(package):
        relative = path.relative_to(package)
        if _forbidden_file(path):
            raise ValueError(f"secret-bearing file is forbidden: {path.relative_to(package)}")
        if relative == TRUSTED_BINARY:
            continue
        if _contains_secret(path.read_bytes()):
            raise ValueError(f"possible API key found in {relative}")


def scan_archive_for_secrets(
    archive_path: Path,
    trusted_binary_sha256: str,
) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", trusted_binary_sha256):
        raise ValueError("trusted_binary_sha256 must contain 64 lowercase hex characters")
    trusted_binary_count = 0
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            pure = PurePosixPath(info.filename)
            if (
                "\\" in info.filename
                or info.is_dir()
                or pure.is_absolute()
                or ".." in pure.parts
                or len(pure.parts) < 2
                or pure.parts[0] != PACKAGE_ROOT
            ):
                raise ValueError(f"unsafe packaged archive member: {info.filename}")
            relative = PurePosixPath(*pure.parts[1:])
            if _forbidden_file(Path(pure.name)):
                raise ValueError(f"archive contains secret-bearing file: {info.filename}")
            if relative == TRUSTED_BINARY:
                trusted_binary_count += 1
                actual = hashlib.sha256(archive.read(info)).hexdigest()
                if actual != trusted_binary_sha256:
                    raise ValueError(
                        "trusted binary SHA-256 mismatch in release archive: "
                        f"{actual}"
                    )
                continue
            if _contains_secret(archive.read(info)):
                raise ValueError(f"archive contains possible API key: {info.filename}")
    if trusted_binary_count != 1:
        raise ValueError("release archive must contain exactly one trusted binary")


def stage_portable(
    repository_root: Path,
    upstream_asset: Path,
    stage_root: Path,
    manifest: dict[str, str],
) -> Path:
    package = stage_root / PACKAGE_ROOT
    if stage_root.exists():
        shutil.rmtree(stage_root)
    source = repository_root / "portable"
    _regular_files_beneath(source)
    shutil.copytree(source, package)
    _regular_files_beneath(package)
    for batch_path in package.rglob("*.cmd"):
        content = batch_path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        batch_path.write_bytes(content.replace(b"\n", b"\r\n"))

    config_path = package / "userdata" / ".config" / "opencode" / "opencode.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["model"] = manifest["model"]
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    _extract_opencode(upstream_asset, package / "bin" / "opencode.exe")
    (package / "VERSION.txt").write_text(
        f"Portable release: generated\nOpenCode: {manifest['opencode_version']}\n"
        f"Default model: {manifest['model']}\n",
        encoding="utf-8",
    )
    scan_for_secrets(package)
    return package


def make_archive(
    stage_root: Path,
    dist_root: Path,
    release_version: str,
    *,
    trusted_binary_sha256: str | None = None,
) -> tuple[Path, Path]:
    if not SEMVER_RE.fullmatch(release_version):
        raise ValueError("release_version must be x.y.z or x.y.z-suffix")
    dist_root.mkdir(parents=True, exist_ok=True)
    archive_path = dist_root / f"OpenCode-1C-Portable-Windows-x64-v{release_version}.zip"
    archive_path.unlink(missing_ok=True)

    files = sorted(_regular_files_beneath(stage_root))
    trusted_binary = stage_root / PACKAGE_ROOT / TRUSTED_BINARY
    if trusted_binary_sha256 is None:
        raise ValueError("trusted_binary_sha256 is required")
    if not trusted_binary.is_file():
        raise ValueError("release package is missing the trusted binary")
    actual_binary_sha256 = hashlib.sha256(trusted_binary.read_bytes()).hexdigest()
    if actual_binary_sha256 != trusted_binary_sha256:
        raise ValueError(
            "trusted binary SHA-256 mismatch before archiving: "
            f"{actual_binary_sha256}"
        )
    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for path in files:
            relative = path.relative_to(stage_root).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)

    with zipfile.ZipFile(archive_path) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise ValueError(f"archive CRC failed for {bad_member}")
    scan_archive_for_secrets(archive_path, trusted_binary_sha256)

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum_path = archive_path.with_suffix(".zip.sha256")
    checksum_path.write_text(f"{digest}  {archive_path.name}\n", encoding="ascii")
    return archive_path, checksum_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("opencode-version.json"))
    parser.add_argument("--asset", type=Path)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--release-version", required=True)
    args = parser.parse_args()

    repository_root = Path(__file__).resolve().parents[1]
    manifest = load_manifest(args.manifest)
    verify_portable_source_tree(repository_root)
    verify_bundled_skill(repository_root)
    asset_url = (
        "https://github.com/anomalyco/opencode/releases/download/"
        f"v{manifest['opencode_version']}/{manifest['asset_name']}"
    )

    with tempfile.TemporaryDirectory(prefix="opencode-1c-build-") as temporary:
        temporary_root = Path(temporary)
        asset = args.asset or temporary_root / manifest["asset_name"]
        if args.asset is None:
            print(f"Downloading {asset_url}")
            download_asset(asset_url, asset)
        verify_sha256(asset, manifest["asset_sha256"])
        trusted_binary_sha256 = hashlib.sha256(_read_opencode_binary(asset)).hexdigest()
        stage = temporary_root / "stage"
        stage_portable(repository_root, asset, stage, manifest)
        archive, checksum = make_archive(
            stage,
            args.dist,
            args.release_version,
            trusted_binary_sha256=trusted_binary_sha256,
        )

    print(archive)
    print(checksum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
