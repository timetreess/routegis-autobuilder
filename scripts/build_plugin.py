# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
# How to run: uv run scripts/build_plugin.py

from __future__ import annotations

import configparser
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from email.parser import Parser
from pathlib import Path
from typing import Final
from xml.etree import ElementTree

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
PLUGIN_DIRECTORY_NAME: Final = "routegis_autobuilder"
ARCHIVE_TIMESTAMP: Final = (1980, 1, 1, 0, 0, 0)
BINARY_SUFFIXES: Final = {".dll", ".dylib", ".exe", ".pyd", ".so"}
MINIMUM_PYTHON_VERSION: Final = "3.9"
QGIS_PROVIDED_DISTRIBUTIONS: Final = {"numpy"}
INTENTIONALLY_OMITTED_DEPENDENCIES: Final = {
    "httpx": {"anyio"},
}


class ReleasePackageError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    name: str
    description: str
    version: str
    qgis_minimum_version: str
    qgis_maximum_version: str
    author: str
    about: str
    tags: str
    homepage: str
    repository: str
    tracker: str
    icon: str
    experimental: str
    deprecated: str
    server: str


def _read_metadata(path: Path) -> PluginMetadata:
    parser = configparser.ConfigParser(interpolation=None)
    if not parser.read(path, encoding="utf-8"):
        raise ReleasePackageError(f"Plugin metadata not found: {path}")
    general = parser["general"]
    required = (
        "name",
        "description",
        "version",
        "qgisMinimumVersion",
        "qgisMaximumVersion",
        "author",
        "email",
        "about",
        "homepage",
        "repository",
        "tracker",
        "icon",
    )
    missing = [key for key in required if not general.get(key, "").strip()]
    if missing:
        raise ReleasePackageError(f"Plugin metadata fields are empty: {', '.join(missing)}")
    return PluginMetadata(
        name=general["name"],
        description=general["description"],
        version=general["version"],
        qgis_minimum_version=general["qgisMinimumVersion"],
        qgis_maximum_version=general["qgisMaximumVersion"],
        author=general["author"],
        about=general["about"],
        tags=general.get("tags", ""),
        homepage=general["homepage"],
        repository=general["repository"],
        tracker=general["tracker"],
        icon=general["icon"],
        experimental=general.get("experimental", "False"),
        deprecated=general.get("deprecated", "False"),
        server=general.get("server", "False"),
    )


def _version_in(path: Path, pattern: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise ReleasePackageError(f"Version not found: {path}")
    return match.group(1)


def _assert_versions(metadata: PluginMetadata) -> None:
    versions = {
        metadata.version,
        _version_in(PROJECT_ROOT / "pyproject.toml", r'^version = "([^"]+)"$'),
        _version_in(PROJECT_ROOT / "src" / "routegis" / "__init__.py", r'^__version__ = "([^"]+)"$'),
        _version_in(PROJECT_ROOT / "tests" / "manual" / "qgis_smoke.py", r'^EXPECTED_VERSION = "([^"]+)"$'),
    }
    if len(versions) != 1:
        raise ReleasePackageError(f"Release versions differ: {sorted(versions)}")
    project_python = _version_in(
        PROJECT_ROOT / "pyproject.toml",
        r'^requires-python = ">=([^"]+)"$',
    )
    if project_python != MINIMUM_PYTHON_VERSION:
        raise ReleasePackageError(
            "Minimum Python versions differ: "
            f"{project_python} != {MINIMUM_PYTHON_VERSION}"
        )


def _copy_release_sources(destination: Path) -> None:
    plugin_source = PROJECT_ROOT / "plugin"
    for name in (
        "__init__.py",
        "metadata.txt",
        "routegis_plugin.py",
        "wizard.py",
        "build_task.py",
        ".bandit",
        ".secrets.baseline",
        ".env.example",
        "icon.png",
    ):
        source = plugin_source / name
        if not source.is_file():
            raise ReleasePackageError(f"Required plugin file not found: {source}")
        _ = shutil.copy2(source, destination / name)
    for name in ("LICENSE", "README.md", "CHANGELOG.md"):
        _ = shutil.copy2(PROJECT_ROOT / name, destination / name)
    _ = shutil.copytree(
        PROJECT_ROOT / "src" / "routegis",
        destination / "routegis",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def _install_vendored_dependencies(destination: Path) -> None:
    command = (
        "uv",
        "pip",
        "install",
        "--python",
        sys.executable,
        "--target",
        str(destination),
        "--python-version",
        MINIMUM_PYTHON_VERSION,
        "--no-compile-bytecode",
        "--no-deps",
        "-r",
        str(PROJECT_ROOT / "plugin-requirements.txt"),
    )
    _ = subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    for path in sorted(destination.rglob("*"), reverse=True):
        if path.is_file() and path.suffix.lower() in BINARY_SUFFIXES:
            path.unlink()
        elif path.is_dir() and path.name in {"__pycache__", "bin"}:
            shutil.rmtree(path)


def _normalize_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).casefold()


def _assert_dependency_closure(destination: Path) -> None:
    metadata_by_name = {}
    for path in destination.glob("*.dist-info/METADATA"):
        metadata = Parser().parsestr(path.read_text(encoding="utf-8"))
        name = metadata.get("Name", "").strip()
        if name:
            metadata_by_name[_normalize_distribution_name(name)] = metadata

    available = set(metadata_by_name) | {
        _normalize_distribution_name(name)
        for name in QGIS_PROVIDED_DISTRIBUTIONS
    }
    missing: dict[str, set[str]] = {}
    for distribution_name, metadata in metadata_by_name.items():
        for requirement in metadata.get_all("Requires-Dist", []):
            requirement_text, _, marker = requirement.partition(";")
            if re.search(r"\bextra\s*==", marker):
                continue
            match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement_text)
            if match is None:
                raise ReleasePackageError(
                    f"Cannot parse dependency for {distribution_name}: {requirement}"
                )
            required_name = _normalize_distribution_name(match.group(1))
            if required_name in INTENTIONALLY_OMITTED_DEPENDENCIES.get(
                distribution_name,
                set(),
            ):
                # RouteGIS uses only HTTPX's synchronous client. AnyIO 4.14.2,
                # the first release with the required security fixes, no longer
                # supports the Python 3.9 runtime bundled with QGIS 3.34.3.
                continue
            if required_name not in available:
                missing.setdefault(distribution_name, set()).add(required_name)
    if missing:
        details = "; ".join(
            f"{name}: {', '.join(sorted(requirements))}"
            for name, requirements in sorted(missing.items())
        )
        raise ReleasePackageError(f"Vendored dependency closure is incomplete: {details}")


def _write_archive(source: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative = Path(PLUGIN_DIRECTORY_NAME) / path.relative_to(source)
            info = zipfile.ZipInfo(relative.as_posix(), ARCHIVE_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            output.writestr(info, path.read_bytes(), compresslevel=9)


def _write_repository_xml(metadata: PluginMetadata, archive: Path, output: Path) -> None:
    tag = f"v{metadata.version}"
    root = ElementTree.Element("plugins", generator_name="RouteGIS release builder", generator_version="1")
    plugin = ElementTree.SubElement(root, "pyqgis_plugin", name=metadata.name, version=metadata.version)
    values = (
        ("about", metadata.about),
        ("author_name", metadata.author),
        ("create_date", "2026-09-22"),
        ("deprecated", metadata.deprecated),
        ("description", metadata.description),
        ("download_url", f"https://github.com/timetreess/routegis-autobuilder/releases/download/{tag}/{archive.name}"),
        ("experimental", metadata.experimental),
        ("file_name", archive.name),
        ("homepage", metadata.homepage),
        ("icon", f"https://raw.githubusercontent.com/timetreess/routegis-autobuilder/{tag}/plugin/{metadata.icon}"),
        ("repository", metadata.repository),
        ("qgis_maximum_version", metadata.qgis_maximum_version),
        ("qgis_minimum_version", metadata.qgis_minimum_version),
        ("server", metadata.server),
        ("tags", metadata.tags),
        ("tracker", metadata.tracker),
        ("update_date", datetime.now(timezone.utc).date().isoformat()),
        ("uploaded_by", metadata.author),
        ("version", metadata.version),
    )
    for name, value in values:
        ElementTree.SubElement(plugin, name).text = value
    ElementTree.indent(root, space="    ")
    ElementTree.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)


def main() -> int:
    metadata = _read_metadata(PROJECT_ROOT / "plugin" / "metadata.txt")
    _assert_versions(metadata)
    dist = PROJECT_ROOT / "dist"
    dist.mkdir(exist_ok=True)
    archive = dist / f"{PLUGIN_DIRECTORY_NAME}.{metadata.version}.zip"
    repository_xml = dist / "plugins.xml"
    with tempfile.TemporaryDirectory(prefix="routegis-release-") as temporary:
        staged_plugin = Path(temporary) / PLUGIN_DIRECTORY_NAME
        staged_plugin.mkdir()
        _copy_release_sources(staged_plugin)
        _install_vendored_dependencies(staged_plugin / "_vendor")
        _assert_dependency_closure(staged_plugin / "_vendor")
        _write_archive(staged_plugin, archive)
    _write_repository_xml(metadata, archive, repository_xml)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    _ = (dist / f"{archive.name}.sha256").write_text(
        f"{digest}  {archive.name}{os.linesep}",
        encoding="ascii",
    )
    print(f"Built {archive} ({archive.stat().st_size} bytes)")
    print(f"SHA256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
