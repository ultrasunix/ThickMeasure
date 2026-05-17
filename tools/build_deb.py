#!/usr/bin/env python3
"""Build a self-contained ThickMeasureCN .deb package without dpkg-deb."""

from __future__ import annotations

import argparse
import io
import os
import tarfile
import time
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "thickmeasurecn"
VERSION = "0.1.0"

EXCLUDED_DIRS = {".git", "__pycache__", "dist", "build", "packaging", "release", "tools"}
EXCLUDED_SUFFIXES = {".pyc", ".swp"}


def should_include(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDED_DIRS for part in rel.parts):
        return False
    if path.suffix in EXCLUDED_SUFFIXES:
        return False
    return True


def add_text(tar: tarfile.TarFile, name: str, text: str, mode: int = 0o644) -> None:
    data = text.encode("utf-8")
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.mtime = int(time.time())
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    tar.addfile(info, io.BytesIO(data))


def make_tar_xz(files: list[tuple[Path, str, int]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:xz") as tar:
        added_dirs: set[str] = set()
        for source, dest, mode in files:
            for parent in reversed(PurePosixPath(dest).parents):
                parent_name = str(parent).strip(".")
                if not parent_name or parent_name in added_dirs:
                    continue
                info = tarfile.TarInfo(parent_name)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.mtime = int(time.time())
                info.uid = 0
                info.gid = 0
                info.uname = "root"
                info.gname = "root"
                tar.addfile(info)
                added_dirs.add(parent_name)

            info = tar.gettarinfo(str(source), arcname=dest)
            info.mode = mode
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            with source.open("rb") as handle:
                tar.addfile(info, handle)
    return buffer.getvalue()


def make_control_tar() -> bytes:
    buffer = io.BytesIO()
    control_dir = ROOT / "packaging" / "deb"
    with tarfile.open(fileobj=buffer, mode="w:xz") as tar:
        for name, mode in (("control", 0o644), ("postinst", 0o755), ("postrm", 0o755)):
            source = control_dir / name
            info = tar.gettarinfo(str(source), arcname=f"./{name}")
            info.mode = mode
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            with source.open("rb") as handle:
                tar.addfile(info, handle)
    return buffer.getvalue()


def collect_data_files() -> list[tuple[Path, str, int]]:
    files: list[tuple[Path, str, int]] = []
    app_prefix = "./opt/thickmeasurecn/ThickMeasure"

    for path in ROOT.rglob("*"):
        if path.is_dir() or not should_include(path):
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(".git/"):
            continue
        mode = 0o755 if path.suffix == ".sh" or rel.endswith("prog_ram.sh") or rel.endswith("prog_flash.sh") else 0o644
        files.append((path, f"{app_prefix}/{rel}", mode))

    return files


def ar_member(name: str, data: bytes, mode: int = 0o100644) -> bytes:
    if len(name) > 15:
        raise ValueError(f"ar member name too long: {name}")
    mode_field = f"{mode:o}".ljust(8)
    header = (
        f"{name + '/':<16}"
        f"{int(time.time()):<12}"
        f"{0:<6}"
        f"{0:<6}"
        f"{mode_field}"
        f"{len(data):<10}`\n"
    ).encode("ascii")
    padding = b"\n" if len(data) % 2 else b""
    return header + data + padding


def build(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    deb_path = output_dir / f"ThickMeasureCN_{VERSION}_all.deb"

    data_tar = make_tar_xz(collect_data_files())
    control_tar = make_control_tar()

    with deb_path.open("wb") as deb:
        deb.write(b"!<arch>\n")
        deb.write(ar_member("debian-binary", b"2.0\n"))
        deb.write(ar_member("control.tar.xz", control_tar))
        deb.write(ar_member("data.tar.xz", data_tar))

    return deb_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the ThickMeasureCN Debian package.")
    parser.add_argument("--output-dir", default=str(ROOT / "dist"), help="Directory for the generated .deb")
    args = parser.parse_args()

    package_path = build(Path(args.output_dir))
    print(package_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
