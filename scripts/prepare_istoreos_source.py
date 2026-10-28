#!/usr/bin/env python3
"""Prepare a pinned iStoreOS vendor-source workspace for later SDK building.

This only clones the exact vendor source and pins its published build feeds.
It deliberately does not compile, publish, modify router state, or assert SDK
compatibility. No OpenWrt binary SDK is substituted for this vendor source.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess

from probe_istoreos import (
    ORIGIN, SOURCE_REPO, SOURCE_COMMIT, FIRMWARE_VERSION,
    METADATA_SHA256, read_remote, validate_metadata,
)
import hashlib

FEED_LINE = re.compile(
    r"^src-git ([a-z0-9_]+) (https://github\.com/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+\.git)\^([a-f0-9]{40})$"
)
REQUIRED_FEEDS = frozenset({"packages", "luci", "routing", "telephony"})


def parse_pinned_feeds(raw):
    if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != METADATA_SHA256["feeds.buildinfo"]:
        raise ValueError("iStoreOS vendor feeds have changed; manual pin review required")
    seen = set()
    lines = []
    for line in raw.decode("utf-8").splitlines():
        match = FEED_LINE.fullmatch(line.strip())
        if not match:
            raise ValueError("Unexpected vendor feed definition; refusing to execute it")
        name, repo, commit = match.groups()
        if name in seen:
            raise ValueError("Duplicate vendor feed")
        seen.add(name)
        lines.append(f"src-git {name} {repo}^{commit}")
    if not REQUIRED_FEEDS.issubset(seen):
        raise ValueError("Missing core vendor feeds")
    return "\n".join(lines) + "\n"


def prepare(destination, *, fetch=read_remote, run=subprocess.run, check_output=subprocess.check_output):
    evidence = validate_metadata(fetch)
    destination = Path(destination).resolve()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Vendor-source destination exists; refusing to overwrite other work")
    if not destination.parent.is_dir():
        raise ValueError("Create the parent directory first; refusing implicit source tree deletion")
    config = fetch(ORIGIN + "config.buildinfo")
    feeds = fetch(ORIGIN + "feeds.buildinfo")
    if hashlib.sha256(config).hexdigest() != METADATA_SHA256["config.buildinfo"]:
        raise ValueError("Vendor config digest changed")
    pinned_feeds = parse_pinned_feeds(feeds)
    # A vendor branch can move after a firmware is published. Fetch and
    # checkout the exact commit recorded in its buildinfo, rather than
    # assuming the branch still points at that historical revision.
    run(["git", "clone", "--filter=blob:none", "--no-checkout", "--depth", "1",
         "--branch", "istoreos-25.12", "--", SOURCE_REPO + ".git", str(destination)], check=True)
    run(["git", "-C", str(destination), "fetch", "--depth", "1", "origin",
         SOURCE_COMMIT], check=True)
    run(["git", "-C", str(destination), "checkout", "--detach", SOURCE_COMMIT], check=True)
    current = check_output(["git", "-C", str(destination), "rev-parse", "HEAD"], text=True).strip()
    if current != SOURCE_COMMIT:
        raise ValueError("Pinned iStoreOS source differs from official firmware build; no build allowed")
    if not (destination / "rules.mk").is_file() or not (destination / "scripts/feeds").is_file():
        raise ValueError("Cloned tree is not the expected OpenWrt Buildroot")
    (destination / ".config").write_bytes(config)
    (destination / "feeds.conf").write_text(pinned_feeds)
    result = {**evidence, "sdk_build": "not_run",
              "vendor_source_workspace": str(destination),
              "vendor_feed_count": len(pinned_feeds.splitlines()),
              "next_step": "Run vendor Buildroot toolchain and SDK build using locked feeds, then validate ABI"}
    (destination / "adzero-source-provenance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory", type=Path, required=True,
                   help="New, nonexisting directory for vendor source checkout")
    args = p.parse_args()
    try:
        print(json.dumps(prepare(args.directory), indent=2, ensure_ascii=False))
    except (ValueError, OSError, subprocess.CalledProcessError) as err:
        p.exit(1, f"iStoreOS source preparation refused: {err}\n")
