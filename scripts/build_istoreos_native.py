#!/usr/bin/env python3
"""Build an iStoreOS 25.12.6 candidate APK from exact pinned vendor sources.

Linux/WSL maintainer command. Only touches the explicitly supplied Buildroot
source tree and a NEW output directory; no router, firmware flash, release tag
or package installation. Never treats a compiled APK as device-accepted.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from build_sdk import PACKAGE, prepare as prepare_package
from inspect_istoreos_candidate import (
    export as export_candidate, verify_pinned_vendor_feeds,
)
from probe_istoreos import SOURCE_COMMIT, METADATA_SHA256


def build(source, output, jobs=4, *, run=subprocess.run,
          read_git=subprocess.check_output, install_source=prepare_package,
          export=export_candidate):
    if os.name != "posix":
        raise ValueError("Use Linux/WSL for the exact iStoreOS Buildroot")
    if not isinstance(jobs, int) or not 1 <= jobs <= 12:
        raise ValueError("Parallel jobs must be an integer between 1 and 12")
    source_input, output_input = Path(source), Path(output)
    if source_input.is_symlink() or output_input.is_symlink():
        raise ValueError("Vendor build paths must not be symlinks")
    source = source_input.resolve()
    output = output_input.resolve()
    if not source.is_dir():
        raise ValueError("Pinned iStoreOS source tree is missing or symlinked")
    if output == source or output.is_relative_to(source):
        raise ValueError("Export candidate APK outside the source tree")
    if output.exists():
        raise ValueError("Candidate output already exists; refusing overwrite")
    if not output.parent.is_dir():
        raise ValueError("Candidate output parent does not exist")
    meta_file = source / "adzero-source-provenance.json"
    if not meta_file.is_file() or meta_file.is_symlink():
        raise ValueError("Missing pinned official source provenance")
    evidence = json.loads(meta_file.read_text(encoding="utf-8"))
    if (evidence.get("source_commit") != SOURCE_COMMIT or
            evidence.get("metadata_sha256") != METADATA_SHA256 or
            evidence.get("firmware") != "istoreos" or
            evidence.get("publishable") is not False):
        raise ValueError("Vendor provenance differs from official iStoreOS firmware")
    head = read_git(["git", "-C", str(source), "rev-parse", "HEAD"],
                    text=True).strip()
    if head != SOURCE_COMMIT:
        raise ValueError("Vendor Git checkout differs from exact official firmware")
    verify_pinned_vendor_feeds(source, read_git)
    install_source(source, PACKAGE)
    config = source / ".config"
    data = config.read_text(encoding="utf-8")
    lines = data.splitlines()
    configured = (lines.count("CONFIG_PACKAGE_luci-app-adzero=m") == 1 and
                  not any(
                      line.startswith("CONFIG_PACKAGE_luci-app-adzero=") and
                      line != "CONFIG_PACKAGE_luci-app-adzero=m"
                      or line == "# CONFIG_PACKAGE_luci-app-adzero is not set"
                      for line in lines
                  ))
    if not configured:
        data = re.sub(r"(?m)^(?:CONFIG_PACKAGE_luci-app-adzero=.*|"
                      r"# CONFIG_PACKAGE_luci-app-adzero is not set)\n?", "", data)
        config.write_text(
            data.rstrip("\n") + "\nCONFIG_PACKAGE_luci-app-adzero=m\n",
            encoding="utf-8", newline="\n"
        )
    # Rewriting an already-correct Buildroot .config can invalidate an entire
    # native kernel build; no config mutation or defconfig when unchanged.
    steps = (
        (() if configured else ("defconfig",)) +
        ("tools/install", "toolchain/install",
         "target/linux/compile", "package/feeds/luci/luci-base/host/compile",
         "package/luci-app-adzero/compile")
    )
    for step in steps:
        run(["make", f"-j{jobs}", step], cwd=source, check=True)
    # Even if the vendor build exits 0, its binary must pass original-source,
    # feed-SHA, package/architecture, native APK and LuCI content inspection.
    result = export(source, output, run=read_git)
    if result.get("publishable") is not False or result.get("sdk_build") == "passed":
        raise ValueError("Native APK candidates cannot authorize an SDK release")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.source, args.output, args.jobs),
                         indent=2, ensure_ascii=False))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"iStoreOS candidate build refused: {error}\n")
