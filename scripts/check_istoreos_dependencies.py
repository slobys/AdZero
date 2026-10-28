#!/usr/bin/env python3
"""Read-only iStoreOS 25.12.6 native APK dependency closure against its real image.

This *does not* install packages or prove runtime DNS/ABI compatibility.
Only the original vendor-source artifacts may satisfy packages absent from the
exact official x86/64 firmware image. All inputs are SHA-256/provenance checked.
"""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

from inspect_istoreos_candidate import validate as validate_candidate
from build_ipk import version as adzero_version

IMAGE_RAW_SHA256 = "833e203e667fb49b27dd69ce8a8a8176a563d5f7154ba95ac219bed0af5da3c9"
IMAGE_OFFSET = 263168 * 512
SAFE_NAME = re.compile(r"[a-z0-9][a-z0-9_.+-]*\Z")


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as reader:
        for block in iter(lambda: reader.read(2 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def original_installed_db(image, *, run=subprocess.check_output):
    image = Path(image)
    if image.is_symlink() or not image.is_file():
        raise ValueError("Official iStoreOS image missing or symlinked")
    if file_sha256(image) != IMAGE_RAW_SHA256:
        raise ValueError("iStoreOS raw image SHA-256 differs from pinned official firmware")
    data = run(["unsquashfs", "-offset", str(IMAGE_OFFSET), "-cat",
                str(image.resolve()), "lib/apk/db/installed"])
    if not isinstance(data, bytes) or not 128 <= len(data) <= 8 * 1024 * 1024:
        raise ValueError("Missing or invalid official APK installed database")
    installed = {line[2:] for line in data.decode("utf-8").splitlines()
                 if line.startswith("P:")}
    if len(installed) < 250 or "luci-base" not in installed or "rpcd" not in installed:
        raise ValueError("APK database does not describe the pinned iStoreOS firmware")
    return installed


def native_apk_info(apk_path, apk_tool, *, run=subprocess.check_output):
    raw = run([str(apk_tool), "adbdump", "--format", "json", str(apk_path)])
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("info"), dict):
        raise ValueError("Invalid native APK metadata")
    return data["info"]


def closure(source, image, *, inspect=validate_candidate, installed_reader=original_installed_db,
            info_reader=native_apk_info):
    source = Path(source)
    if source.is_symlink() or not source.is_dir():
        raise ValueError("Pinned iStoreOS Buildroot source missing or symlinked")
    source = source.resolve()
    base = inspect(source)
    if base.get("publishable") is not False or base.get("sdk_build") == "passed":
        raise ValueError("Candidate inspection unexpectedly authorized a release")
    apk_tool = source / "staging_dir/host/bin/apk"
    vendor_dir = source / "bin/packages/x86_64"
    if vendor_dir.is_symlink() or not vendor_dir.is_dir():
        raise ValueError("Native iStoreOS build package directory missing or symlinked")
    binaries = [path for path in vendor_dir.glob(
        f"*/{base['package']}"
    ) if path.is_file() and not path.is_symlink()]
    if len(binaries) != 1:
        raise ValueError("Native AdZero candidate APK missing or ambiguous")
    binary = binaries[0]
    installed = installed_reader(image)
    if not isinstance(installed, set):
        raise ValueError("Invalid original firmware installed package set")
    meta = info_reader(binary, apk_tool)
    if (meta.get("name") != "luci-app-adzero"
            or meta.get("arch") != "noarch"
            or meta.get("version") != adzero_version()):
        raise ValueError("Native AdZero package identity, version or architecture differs")
    native_sha256 = file_sha256(binary)
    if base.get("sha256") != native_sha256:
        raise ValueError("Native AdZero APK SHA-256 differs from the inspected vendor build")
    dependencies = meta.get("depends")
    if not isinstance(dependencies, list) or not dependencies:
        raise ValueError("Native AdZero package has no declared runtime dependencies")
    # The original iStoreOS image contains the full tcpdump. Requiring the
    # alternate mini package would silently remove the user's existing tool.
    # Packet observation is optional; never treat this as a core dependency.
    if "tcpdump" in installed and "tcpdump-mini" in dependencies:
        raise ValueError("Mandatory tcpdump-mini would replace the original full tcpdump")
    queue = list(dependencies)
    required, selected = set(), {}
    while queue:
        name = queue.pop(0)
        if not isinstance(name, str) or not SAFE_NAME.fullmatch(name):
            raise ValueError(f"Unresolved or versioned APK dependency: {name!r}")
        if name in required:
            continue
        required.add(name)
        if name in installed:
            continue
        matches = [p for p in vendor_dir.glob(f"*/{name}-*.apk")
                   if p.is_file() and not p.is_symlink() and not p.parent.is_symlink()]
        valid = []
        for path in matches:
            entry = info_reader(path, apk_tool)
            if entry.get("name") == name:
                valid.append((path, entry))
        if len(valid) != 1:
            raise ValueError(f"Missing or ambiguous original-vendor APK dependency: {name}")
        path, entry = valid[0]
        if entry.get("arch") not in ("noarch", "x86_64"):
            raise ValueError(f"Vendor dependency {name} has wrong architecture")
        version = entry.get("version")
        if not isinstance(version, str) or not version or path.name != f"{name}-{version}.apk":
            raise ValueError(f"Vendor dependency {name} has an unverified version or filename")
        deps = entry.get("depends")
        if not isinstance(deps, list):
            raise ValueError(f"Vendor dependency {name} has no dependency metadata")
        queue.extend(deps)
        selected[name] = {
            "file": path.name, "version": entry.get("version"),
            "arch": entry["arch"], "sha256": file_sha256(path),
        }
    return {
        "firmware": "istoreos", "release": "25.12.6",
        "revision": "2026102312",
        "image_raw_sha256": IMAGE_RAW_SHA256,
        "apk": binary.name,
        "apk_sha256": native_sha256,
        "source_sha256": base.get("source_sha256"),
        "vendor_source_commit": base.get("vendor_source_commit"),
        "vendor_feeds_checked": base.get("vendor_feeds_checked"),
        "direct_dependencies": dependencies,
        "installed_packages_examined": len(installed),
        "missing_from_original_firmware": selected,
        "unresolved_dependencies": [],
        "closure": "passed_vendor_builds_only",
        "device_acceptance": "not_run",
        "sdk_build": "native_vendor_source_candidate_not_sdk_verified",
        "publishable": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True,
                        help="Verified uncompressed iStoreOS x86/64 original image")
    parser.add_argument("--output", type=Path,
                        help="Write a new JSON evidence file (never overwrite)")
    args = parser.parse_args()
    try:
        report = closure(args.source, args.image)
        encoded = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            if args.output.is_symlink() or args.output.exists():
                raise ValueError("Report exists or is symlinked; refusing overwrite")
            args.output.write_text(encoded, encoding="utf-8", newline="\n")
        print(encoded, end="")
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"iStoreOS dependency verification refused: {error}\n")
