#!/usr/bin/env python3
"""Validate a native iStoreOS Buildroot-produced AdZero APK as a *candidate*.

This consumes an already prepared/compiled, officially pinned vendor source
tree. It neither installs packages, modifies routers nor grants SDK approval.
The build's revision and package must be checked against device ABI before any
later release includes it.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from build_ipk import version
from build_sdk import PACKAGE, read_config, source_hash
from check_sdk_packages import inspect
from probe_istoreos import SOURCE_COMMIT, METADATA_SHA256
from prepare_istoreos_source import FEED_LINE

FIRMWARE_VERSION = "25.12.6"
FIRMWARE_REVISION = "2026102312"
FIRMWARE_IMAGE_SHA256 = "48791c15b9fa7c261c519825020eaaf7792f06afbc495dd314966ffb13691d32"



def verify_pinned_vendor_feeds(source, run):
    """Require the exact vendor-published feed manifest and all checked-out Git SHAs."""
    manifest = source / "feeds.conf"
    if (not manifest.is_file() or manifest.is_symlink()
            or manifest.stat().st_size > 64 * 1024):
        raise ValueError("Pinned iStoreOS vendor feeds.conf is missing, oversized or a symlink")
    raw = manifest.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != METADATA_SHA256["feeds.buildinfo"]:
        raise ValueError("Pinned vendor feed manifest differs from original firmware")
    feed_root = source / "feeds"
    if not feed_root.is_dir() or feed_root.is_symlink():
        raise ValueError("Pinned vendor feeds directory is missing or symlinked")
    seen = set()
    for line in raw.decode("utf-8").splitlines():
        # Pin both official feed identity and immutable source commit.
        match = FEED_LINE.fullmatch(line)
        if match is None:
            raise ValueError("Untrusted vendor feed declaration")
        name, _origin, commit = match.groups()
        if name in seen:
            raise ValueError("Duplicate vendor feed declaration")
        seen.add(name)
        feed_path = source / "feeds" / name
        if not feed_path.is_dir() or feed_path.is_symlink():
            raise ValueError(f"Missing pinned vendor feed: {name}")
        actual = run(["git", "-C", str(feed_path), "rev-parse", "HEAD"],
                     text=True).strip()
        if actual != commit:
            raise ValueError(f"Vendor feed revision drift: {name}")
    if len(seen) != 13:
        raise ValueError("Incomplete pinned iStoreOS vendor feed set")
    return {"feed_count": len(seen), "feeds_sha256": digest}

def validate(source, run=subprocess.check_output, package_inspector=inspect):
    requested_source = Path(source)
    if requested_source.is_symlink():
        raise ValueError("Pinned vendor source must not be a symlink")
    source = requested_source.resolve()
    evidence_path = source / "adzero-source-provenance.json"
    if not evidence_path.is_file() or evidence_path.is_symlink():
        raise ValueError("Original pinned iStoreOS source provenance is required")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if (evidence.get("firmware") != "istoreos" or
            evidence.get("source_commit") != SOURCE_COMMIT or
            evidence.get("metadata_sha256") != METADATA_SHA256 or
            evidence.get("sdk_build") != "not_run" or evidence.get("publishable") is not False):
        raise ValueError("Vendor source provenance differs from the pinned official build")
    actual_git_head = run(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if actual_git_head != SOURCE_COMMIT:
        raise ValueError("Git source HEAD differs from pinned iStoreOS firmware")
    pinned_feeds = verify_pinned_vendor_feeds(source, run)
    config = read_config(source)
    required = {
        "CONFIG_VERSION_DIST": "iStoreOS",
        "CONFIG_TARGET_BOARD": "x86",
        "CONFIG_TARGET_SUBTARGET": "64",
        "CONFIG_TARGET_ARCH_PACKAGES": "x86_64",
        "CONFIG_USE_APK": "y",
        "CONFIG_PACKAGE_luci-app-adzero": "m",
    }
    for key, expected in required.items():
        if config.get(key) != expected:
            raise ValueError(f"Wrong iStoreOS vendor build configuration: {key}")
    vendor_package = source / "package/luci-app-adzero"
    if not vendor_package.is_dir() or vendor_package.is_symlink():
        raise ValueError("AdZero package sources missing or symlinked in vendor Buildroot")
    local_sha = source_hash(PACKAGE)
    if source_hash(vendor_package) != local_sha:
        raise ValueError("Compiled iStoreOS source differs from current AdZero package")
    apk_tool = source / "staging_dir/host/bin/apk"
    if (apk_tool.is_symlink() or not apk_tool.is_file()
            or not apk_tool.stat().st_mode & 0o111):
        raise ValueError("The matching vendor APK extraction tool is missing")
    packages_root = source / "bin/packages/x86_64"
    if (not packages_root.is_dir() or packages_root.is_symlink()
            or packages_root.parent.is_symlink()):
        raise ValueError("Native vendor APK directory is missing or symlinked")
    packages = [
        path for path in packages_root.glob(
            f"*/luci-app-adzero-{version()}.apk"
        ) if path.is_file() and not path.is_symlink()
        and not path.parent.is_symlink()
    ]
    if len(packages) != 1:
        raise ValueError(f"Expected exactly one matching native iStoreOS APK, found {len(packages)}")
    binary = packages[0]
    details = package_inspector(binary, apk_tool=apk_tool)
    if details.get("runtime_source_and_modes") != "passed" or details.get("luci_delivered_ui_regressions") != "passed":
        raise ValueError("Native iStoreOS APK payload verification is incomplete")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    return {
        "firmware_declared": "istoreos",
        "release_declared": FIRMWARE_VERSION,
        "revision_declared": FIRMWARE_REVISION,
        "target_observed": "x86/64",
        "package_arch_observed": "x86_64",
        "package_format": "apk",
        "firmware_image_sha256_reference": FIRMWARE_IMAGE_SHA256,
        "vendor_source_commit": SOURCE_COMMIT,
        "vendor_metadata_sha256": METADATA_SHA256,
        "vendor_feeds_checked": pinned_feeds,
        "source_sha256": local_sha,
        "package": binary.name,
        "sha256": digest,
        "sdk_build": "native_vendor_source_candidate_not_sdk_verified",
        "device_acceptance": "not_run",
        "publishable": False,
        "payload_validation": details,
    }


def export(source, output, run=subprocess.check_output, package_inspector=inspect):
    report = validate(source, run=run, package_inspector=package_inspector)
    requested_source, requested_output = Path(source), Path(output)
    if requested_source.is_symlink() or requested_output.is_symlink():
        raise ValueError("Source or output must not be symlinked")
    source = requested_source.resolve()
    output = requested_output.resolve()
    if output == source or output.is_relative_to(source):
        raise ValueError("Export APK evidence outside the Buildroot source tree")
    if output.exists():
        raise ValueError("Candidate output already exists; refusing overwrite")
    output.mkdir(parents=True)
    source_file = next((source / "bin/packages/x86_64").glob(
        f"*/{report['package']}"
    ))
    artifact = output / report["package"]
    manifest = output / "build-manifest.json"
    try:
        shutil.copyfile(source_file, artifact)
        # Verify copied bytes, not just the input observed before exporting.
        # A changed source package must never receive a stale valid manifest.
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != report["sha256"]:
            raise ValueError("Exported iStoreOS APK differs from inspected candidate")
        manifest.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        )
    except (OSError, ValueError):
        artifact.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        output.rmdir()
        raise
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(export(args.source, args.output), indent=2, ensure_ascii=False))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"iStoreOS candidate not publishable: {error}\n")
