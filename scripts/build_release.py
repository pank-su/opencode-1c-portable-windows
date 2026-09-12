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
import subprocess
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
BUNDLED_SKILLS_SOURCE = {
    "name": "cc-1c-skills",
    "repository": "https://github.com/Nikolay-Shirokov/cc-1c-skills",
    "branch": "port-opencode",
    "commit": "05b3b3a58700f337a8b4e5c9e0e23f9bc0d8fdd0",
    "generated_from_commit": "6169a7ff939aa3c73f7d9799acf693fa3bfd33ed",
    "path": ".opencode/skills",
    "license": "MIT",
    "license_sha256": "e1d8517691002bad67293250bd76338b34e4891698707d9fcbdc488f6e51bd79",
    "manifest_sha256": "2e0995f8de6822ccf48574c2f13eadea9cd8204dbded7ea07d78d8d7a5d2ca7a",
    "skill_count": 79,
    "file_count": 341,
}
BASE_PORTABLE_SOURCE_FILES = {
    "README.md",
    "check.cmd",
    "opencode.cmd",
    "setup-key.cmd",
    "setup-key.ps1",
    "userdata/.config/opencode/PORTABLE_1C_SKILLS.md",
    "userdata/.config/opencode/opencode.json",
    "userdata/.config/opencode/third-party/cc-1c-skills/LICENSE",
    "userdata/.config/opencode/third-party/cc-1c-skills/MANIFEST.sha256",
    "userdata/.config/opencode/third-party/cc-1c-skills/SOURCE.json",
}


def _is_reparse_stat(file_stat: os.stat_result) -> bool:
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(file_stat.st_mode) or bool(
        getattr(file_stat, "st_file_attributes", 0) & reparse_point
    )


def _windows_file_link_count(path: Path) -> int:
    """Return the native NTFS link count when Python reports it as unavailable."""
    if os.name != "nt":
        raise OSError("native Windows link count is only available on Windows")

    import ctypes
    from ctypes import wintypes

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [wintypes.HANDLE, ctypes.POINTER(ByHandleFileInformation)]
    get_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    file_share_read_write_delete = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    file_flag_open_reparse_point = 0x00200000
    handle = create_file(
        os.path.abspath(path),
        0,
        file_share_read_write_delete,
        None,
        open_existing,
        file_flag_open_reparse_point,
        None,
    )
    invalid_handle_value = wintypes.HANDLE(-1).value
    if handle == invalid_handle_value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        information = ByHandleFileInformation()
        if not get_information(handle, ctypes.byref(information)):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(information.nNumberOfLinks)
    finally:
        close_handle(handle)


def _has_multiple_hardlinks(
    path: Path, file_stat: os.stat_result, *, platform_name: str = os.name
) -> bool:
    link_count = getattr(file_stat, "st_nlink", 0)
    if link_count > 1:
        return True
    if link_count == 1:
        return False
    if platform_name == "nt":
        return _windows_file_link_count(path) > 1
    return False


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
                    if _has_multiple_hardlinks(path, entry_stat):
                        raise ValueError(f"hardlink is forbidden: {path}")
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


def _read_bundled_skills_manifest(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError("bundled 1C skills manifest is missing") from error
    entries: dict[str, str] = {}
    ordered_paths: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise ValueError(f"invalid bundled skills manifest line {line_number}")
        digest, relative = match.groups()
        pure = PurePosixPath(relative)
        if (
            "\\" in relative
            or pure.is_absolute()
            or ".." in pure.parts
            or relative in entries
        ):
            raise ValueError(f"unsafe or duplicate bundled skills path: {relative}")
        entries[relative] = digest
        ordered_paths.append(relative)
    if ordered_paths != sorted(ordered_paths):
        raise ValueError("bundled skills manifest paths must be sorted")
    return entries


def verify_bundled_skills(repository_root: Path) -> dict[str, str]:
    opencode_root = (
        repository_root
        / "portable"
        / "userdata"
        / ".config"
        / "opencode"
    )
    skills_root = opencode_root / "skills"
    provenance_root = opencode_root / "third-party" / "cc-1c-skills"
    source_path = provenance_root / "SOURCE.json"
    manifest_path = provenance_root / "MANIFEST.sha256"
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("bundled 1C skills source metadata is missing or invalid") from error
    if source != BUNDLED_SKILLS_SOURCE:
        raise ValueError("bundled 1C skills source metadata does not match the pinned upstream")
    verify_sha256(manifest_path, source["manifest_sha256"])
    verify_sha256(provenance_root / "LICENSE", source["license_sha256"])
    manifest = _read_bundled_skills_manifest(manifest_path)
    if len(manifest) != source["file_count"]:
        raise ValueError("bundled 1C skills manifest file count is incorrect")

    actual = {
        path.relative_to(skills_root).as_posix()
        for path in _regular_files_beneath(skills_root)
    }
    expected = set(manifest)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise ValueError(
            "bundled 1C skills tree differs from the pinned upstream; "
            f"missing={missing}; unexpected={unexpected}"
        )
    for relative, digest in manifest.items():
        verify_sha256(skills_root / relative, digest)

    skill_files = {
        path
        for path in manifest
        if len(PurePosixPath(path).parts) == 2
        and PurePosixPath(path).name == "SKILL.md"
    }
    if len(skill_files) != source["skill_count"]:
        raise ValueError("bundled 1C skill count is incorrect")
    for forbidden in ("1c-development", "1c-bsl-code-generation"):
        if (skills_root / forbidden).exists():
            raise ValueError(f"obsolete 1C skill must not be bundled: {forbidden}")
    return manifest


def verify_portable_source_tree(repository_root: Path) -> None:
    source_root = repository_root / "portable"
    manifest = verify_bundled_skills(repository_root)
    expected = BASE_PORTABLE_SOURCE_FILES | {
        f"userdata/.config/opencode/skills/{path}" for path in manifest
    }
    actual = {
        path.relative_to(source_root).as_posix()
        for path in _regular_files_beneath(source_root)
    }
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise ValueError(
            "portable source tree differs from the release allowlist; "
            f"missing={missing}; unexpected={unexpected}"
        )


def export_repository_index(
    repository_root: Path,
    destination_root: Path,
    extra_paths: tuple[str, ...] = (),
) -> Path:
    """Export release inputs from an immutable snapshot of the Git index."""
    repository_root = repository_root.resolve(strict=True)
    selected_roots = ("portable", *extra_paths)
    for relative in selected_roots:
        pure = PurePosixPath(relative)
        if (
            "\\" in relative
            or pure.is_absolute()
            or ".." in pure.parts
            or not pure.parts
        ):
            raise ValueError(f"unsafe indexed release path: {relative}")

    if destination_root.exists():
        shutil.rmtree(destination_root)
    destination_root.mkdir(parents=True)

    git_index_result = subprocess.run(
        ["git", "rev-parse", "--git-path", "index"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    git_index = Path(git_index_result.stdout.strip())
    if not git_index.is_absolute():
        git_index = repository_root / git_index

    with tempfile.TemporaryDirectory(
        prefix="opencode-1c-index-",
        dir=destination_root.parent,
    ) as temporary:
        index_snapshot = Path(temporary) / "index"
        shutil.copyfile(git_index, index_snapshot)
        git_environment = os.environ.copy()
        git_environment["GIT_INDEX_FILE"] = str(index_snapshot)
        listed = subprocess.run(
            ["git", "ls-files", "--stage", "-z"],
            cwd=repository_root,
            env=git_environment,
            check=True,
            capture_output=True,
        ).stdout

        selected: list[bytes] = []
        selected_names: set[str] = set()
        for record in listed.split(b"\0"):
            if not record:
                continue
            metadata, path_bytes = record.split(b"\t", 1)
            mode, _object_id, stage = metadata.split(b" ", 2)
            relative = os.fsdecode(path_bytes)
            pure = PurePosixPath(relative)
            include = relative == "portable" or relative.startswith("portable/")
            include = include or relative in extra_paths
            if not include:
                continue
            if (
                "\\" in relative
                or pure.is_absolute()
                or ".." in pure.parts
                or stage != b"0"
                or mode not in {b"100644", b"100755"}
            ):
                raise ValueError(
                    f"unsupported Git mode or path for release input: {relative}"
                )
            if relative in selected_names:
                raise ValueError(f"duplicate indexed release path: {relative}")
            selected.append(path_bytes)
            selected_names.add(relative)

        missing_roots = [
            relative
            for relative in selected_roots
            if relative != "portable"
            and relative not in selected_names
        ]
        if not any(name.startswith("portable/") for name in selected_names):
            missing_roots.append("portable")
        if missing_roots:
            raise ValueError(
                "release inputs are missing from the Git index: "
                + ", ".join(missing_roots)
            )

        checkout_input = b"\0".join(selected) + b"\0"
        prefix = destination_root.resolve().as_posix() + "/"
        subprocess.run(
            [
                "git",
                "checkout-index",
                "--force",
                f"--prefix={prefix}",
                "-z",
                "--stdin",
            ],
            cwd=repository_root,
            env=git_environment,
            input=checkout_input,
            check=True,
        )

    _regular_files_beneath(destination_root / "portable")
    return destination_root


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
    manifest_source = args.manifest
    if not manifest_source.is_absolute():
        manifest_source = repository_root / manifest_source
    try:
        manifest_relative = manifest_source.resolve(strict=False).relative_to(repository_root)
    except ValueError as error:
        raise ValueError("manifest must be a tracked file inside the repository") from error

    with tempfile.TemporaryDirectory(prefix="opencode-1c-build-") as temporary:
        temporary_root = Path(temporary)
        indexed_repository = export_repository_index(
            repository_root,
            temporary_root / "indexed-repository",
            extra_paths=(manifest_relative.as_posix(),),
        )
        manifest = load_manifest(indexed_repository / manifest_relative)
        verify_portable_source_tree(indexed_repository)
        asset_url = (
            "https://github.com/anomalyco/opencode/releases/download/"
            f"v{manifest['opencode_version']}/{manifest['asset_name']}"
        )
        asset = args.asset or temporary_root / manifest["asset_name"]
        if args.asset is None:
            print(f"Downloading {asset_url}")
            download_asset(asset_url, asset)
        verify_sha256(asset, manifest["asset_sha256"])
        trusted_binary_sha256 = hashlib.sha256(_read_opencode_binary(asset)).hexdigest()
        stage = temporary_root / "stage"
        stage_portable(indexed_repository, asset, stage, manifest)
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
