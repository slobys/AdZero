#!/usr/bin/env python3
"""Create a reproducible, unsigned, SDK-independent OpenWrt opkg IPK preview.

For release builds, use the OpenWrt SDK matching the actual firmware.
No network access, commands, signing, installation or device modifications.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
from pathlib import Path
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "luci-app-adzero"
NAME = "luci-app-adzero"

POSTINST = b"""#!/bin/sh
# Refresh the LuCI menu and rpcd ACLs only when installed on a live target.
[ -n "$IPKG_INSTROOT" ] && exit 0
rm -f /tmp/luci-indexcache.*
rm -rf /tmp/luci-modulecache/
/etc/init.d/rpcd reload >/dev/null 2>&1 || :
exit 0
"""


# For removal only. Upgrades retain the marker and backups until they are migrated.
PRERM = b"""#!/bin/sh
[ -n "$IPKG_INSTROOT" ] && exit 0
case "$1" in upgrade|failed-upgrade) exit 0 ;; esac
if [ -f /etc/adzero/managed ]; then
    /usr/libexec/adzero/ctl disable || {
        echo "AdZero: cannot safely restore the managed backend; aborting removal." >&2
        exit 1
    }
fi
exit 0
"""


def version() -> str:
    makefile = (PKG / "Makefile").read_text(encoding="utf-8")
    match_version = re.search(r"^PKG_VERSION:=(\d+\.\d+\.\d+)$", makefile, flags=re.M)
    match_release = re.search(r"^PKG_RELEASE:=(\d+)$", makefile, flags=re.M)
    if not match_version or not match_release:
        raise ValueError("Missing fixed PKG_VERSION/PKG_RELEASE in LuCI Makefile")
    if "+luci-base" not in makefile or "+adblock-fast" not in makefile or "+rpcd-mod-file" not in makefile or "+ca-bundle" not in makefile:
        raise ValueError("Expected LuCI and backend dependencies are not declared")
    return f"{match_version.group(1)}-r{match_release.group(1)}"


def payload() -> dict[str, tuple[bytes, int]]:
    output: dict[str, tuple[bytes, int]] = {}
    for subdir, prefix in (("root", ""), ("htdocs", "www/")):
        basedir = PKG / subdir
        for src in sorted(basedir.rglob("*")):
            if src.is_symlink():
                raise ValueError(f"Symlink not allowed in offline package: {src}")
            if not src.is_file():
                continue
            relative = src.relative_to(basedir)
            name = (Path(prefix) / relative).as_posix()
            if name in output:
                raise ValueError(f"Duplicate package path: {name}")
            mode = 0o755 if name in {"usr/libexec/adzero/ctl", "usr/libexec/adzero/probe", "usr/libexec/adzero/adapter", "usr/libexec/adzero/backend", "usr/libexec/adzero/watch", "usr/libexec/adzero/policy", "usr/libexec/adzero/sources", "usr/libexec/adzero/job", "usr/libexec/adzero/update", "usr/libexec/adzero/routing-data", "usr/libexec/adzero/cache-curl/curl", "usr/libexec/adzero/observe", "usr/libexec/adzero/dns-handoff", "usr/libexec/adzero/dns-enhance", "usr/libexec/adzero/dns-native", "usr/libexec/adzero/dns-guard", "etc/init.d/adzero-dns", "etc/init.d/adzero"} else 0o644
            output[name] = (src.read_bytes(), mode)

    expected = {
        "usr/libexec/adzero/dns-pool-lib.uc",
        "usr/libexec/adzero/dns-pool.uc",
        "usr/libexec/adzero/dns-guard-lib.uc",
        "usr/libexec/adzero/dns-guard.uc",
        "usr/libexec/adzero/dns-guard",
        "etc/init.d/adzero-dns",
        "usr/libexec/adzero/dns-handoff",
        "usr/libexec/adzero/dns-enhance",
        "usr/libexec/adzero/dns-enhance.uc",
        "usr/libexec/adzero/dns-enhance-lib.uc",
        "usr/libexec/adzero/dns-io.uc",
        "usr/libexec/adzero/dns-relay.uc",
        "usr/libexec/adzero/dns-native",
        "usr/libexec/adzero/dns-native.uc",
        "usr/libexec/adzero/dns-native-lib.uc",
        "usr/libexec/adzero/dns-alias-http.uc",
        "usr/libexec/adzero/dns-cleanup.uc",
        "usr/libexec/adzero/provenance.uc",
        "usr/libexec/adzero/dns-handoff.uc",
        "usr/libexec/adzero/dns-handoff-lib.uc",
        "usr/libexec/adzero/observe",
        "usr/libexec/adzero/observe.uc",
        "usr/libexec/adzero/observe-lib.uc",
        "usr/libexec/adzero/observe-state.uc",
        "usr/libexec/adzero/policy",
        "usr/libexec/adzero/policy.uc",
        "usr/libexec/adzero/policy-lib.uc",
        "usr/libexec/adzero/progress-lib.uc",
        "usr/libexec/adzero/sources",
        "usr/libexec/adzero/sources.uc",
        "usr/libexec/adzero/source-lib.uc",
        "usr/libexec/adzero/source-cache.uc",
        "usr/libexec/adzero/capacity-lib.uc",
        "usr/libexec/adzero/provider-store.uc",
        "usr/libexec/adzero/dns-pool-state.uc",
        "usr/libexec/adzero/cache-curl.uc",
        "usr/libexec/adzero/cache-curl/curl",
        "usr/libexec/adzero/job",
        "usr/libexec/adzero/update",
        "usr/libexec/adzero/update-check.uc",
        "usr/share/adzero/version",
        "usr/libexec/adzero/routing-data",
        "usr/libexec/adzero/routing-data.uc",
        "usr/libexec/adzero/routing-lib.uc",
        "usr/share/rpcd/ucode/adzero",
        "etc/config/adzero",
        "usr/libexec/adzero/ctl",
        "usr/libexec/adzero/probe",
        "usr/libexec/adzero/probe.uc",
        "usr/libexec/adzero/proxy-state.uc",
        "usr/libexec/adzero/rules.sh",
        "usr/libexec/adzero/rules-cache.uc",
        "usr/libexec/adzero/adapter",
        "usr/libexec/adzero/adapter.uc",
        "usr/libexec/adzero/adapter-lib.uc",
        "usr/libexec/adzero/yaml-json.rb",
        "usr/libexec/adzero/backend",
        "usr/libexec/adzero/backend.uc",
        "usr/libexec/adzero/watch",
        "etc/init.d/adzero",
        "usr/share/luci/menu.d/luci-app-adzero.json",
        "usr/share/rpcd/acl.d/luci-app-adzero.json",
        "www/luci-static/resources/view/adzero/overview.js",
        "www/luci-static/resources/view/adzero/overview.css",
        "www/luci-static/resources/view/adzero/logo.png",
        "usr/share/licenses/luci-app-adzero/Phosphor-Icons-LICENSE",
    }
    if set(output) != expected:
        raise ValueError(f"Unexpected package payload: missing={expected - set(output)}, extra={set(output) - expected}")
    if not output["usr/libexec/adzero/ctl"][0].startswith(b"#!/bin/sh\n"):
        raise ValueError("Backend entry point is not a POSIX shell script")
    return output


def gzip_tar(files: dict[str, tuple[bytes, int]]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for name, (content, mode) in sorted(files.items()):
            if not name or name.startswith("/") or ".." in Path(name).parts:
                raise ValueError("Unsafe tar path")
            info = tarfile.TarInfo("./" + name)
            info.size = len(content)
            info.mode = mode
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            tar.addfile(info, io.BytesIO(content))
    result = io.BytesIO()
    with gzip.GzipFile(fileobj=result, mode="wb", compresslevel=9, mtime=0, filename="") as gz:
        gz.write(raw.getvalue())
    return result.getvalue()


def ar_member(name: str, data: bytes) -> bytes:
    if len(name) > 15 or not name.isascii():
        raise ValueError("Invalid ar member name")
    hdr = (
        f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}"
    ).encode("ascii") + b"\x60\n"
    if len(hdr) != 60:
        raise AssertionError("Invalid ar header size")
    return hdr + data + (b"\n" if len(data) % 2 else b"")


def build_bytes() -> tuple[bytes, str]:
    ver = version()
    files = payload()
    control = (
        f"Package: {NAME}\n"
        f"Version: {ver}\n"
        "Architecture: all\n"
        "Section: luci\n"
        "Priority: optional\n"
        "Depends: luci-base, adblock-fast, curl, rpcd-mod-file, rpcd-mod-ucode, ca-bundle, ucode, ucode-mod-fs, ucode-mod-uci, ucode-mod-ubus, ucode-mod-uloop, ucode-mod-socket, tcpdump, conntrack, coreutils-timeout\n"
        "Maintainer: AdZero project\n"
        "Description: One-click safe DNS ad blocking on OpenWrt LuCI\n"
    ).encode("utf-8")
    control_files = {
        "control": (control, 0o644),
        "conffiles": (b"/etc/config/adzero\n", 0o644),
        "postinst": (POSTINST, 0o755),
        "prerm": (PRERM, 0o755),
    }
    ipk = (
        b"!<arch>\n"
        + ar_member("debian-binary", b"2.0\n")
        + ar_member("control.tar.gz", gzip_tar(control_files))
        + ar_member("data.tar.gz", gzip_tar(files))
    )
    return ipk, ver


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output path; default: dist/luci-app-adzero_VERSION_all.ipk",
    )
    args = parser.parse_args()
    ipk, ver = build_bytes()
    output = args.output or (ROOT / "dist" / f"{NAME}_{ver}_all.ipk")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Only write within requested output; no device-side effects.
    staging = output.with_suffix(output.suffix + ".tmp")
    try:
        staging.write_bytes(ipk)
        os.replace(staging, output)
    finally:
        staging.unlink(missing_ok=True)
    print(f"{output} ({len(ipk)} bytes) sha256={hashlib.sha256(ipk).hexdigest()}")
    print("UNSIGNED EXPERIMENTAL OPKG IPK; OpenWrt SDK and target device validation still required.")


if __name__ == "__main__":
    main()
