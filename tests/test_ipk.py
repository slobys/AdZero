#!/usr/bin/env python3
"""Structural and reproducibility tests for SDK-independent IPK preview."""
import io
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_ipk  # noqa: E402


def parse_ar(content):
    if not content.startswith(b"!<arch>\n"):
        raise ValueError("Missing ar magic")
    result = {}
    pos = 8
    while pos < len(content):
        hdr = content[pos:pos + 60]
        if len(hdr) != 60 or hdr[-2:] != b"\x60\n":
            raise ValueError("Malformed ar header")
        name = hdr[0:16].decode("ascii").strip().rstrip("/")
        length = int(hdr[48:58].decode("ascii").strip())
        pos += 60
        data = content[pos:pos + length]
        if len(data) != length:
            raise ValueError("Truncated ar member")
        pos += length + length % 2
        if name in result:
            raise ValueError("Duplicate ar member")
        result[name] = data
    return result


def list_tar(content):
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        return {member.name.lstrip("./"): (member, archive.extractfile(member).read())
                for member in archive.getmembers() if member.isfile()}


class IpkPackagingTest(unittest.TestCase):
    def test_ar_structure_and_control(self):
        package, version = build_ipk.build_bytes()
        self.assertEqual(version, "0.21.5-r1")
        members = parse_ar(package)
        self.assertEqual(set(members), {"debian-binary", "control.tar.gz", "data.tar.gz"})
        self.assertEqual(members["debian-binary"], b"2.0\n")
        control = list_tar(members["control.tar.gz"])
        self.assertEqual(set(control), {"control", "conffiles", "postinst", "prerm"})
        body = control["control"][1].decode()
        self.assertIn("Package: luci-app-adzero\n", body)
        self.assertIn("Version: 0.21.5-r1\n", body)
        self.assertIn("Architecture: all\n", body)
        self.assertIn("Depends: luci-base, adblock-fast, curl, rpcd-mod-file, rpcd-mod-ucode, ca-bundle, ucode, ucode-mod-fs, ucode-mod-uci, ucode-mod-ubus, ucode-mod-uloop, ucode-mod-socket, tcpdump, conntrack, coreutils-timeout\n", body)
        self.assertEqual(control["conffiles"][1], b"/etc/config/adzero\n")
        self.assertEqual(control["postinst"][0].mode & 0o777, 0o755)
        self.assertIn(b"IPKG_INSTROOT", control["postinst"][1])
        self.assertEqual(control["prerm"][0].mode & 0o777, 0o755)
        self.assertIn(b"/usr/libexec/adzero/ctl disable", control["prerm"][1])
        self.assertIn(b"upgrade", control["prerm"][1])

    def test_exact_installed_paths_and_modes(self):
        members = parse_ar(build_ipk.build_bytes()[0])
        installed = list_tar(members["data.tar.gz"])
        self.assertEqual(set(installed), {
            "usr/libexec/adzero/dns-guard",
            "usr/libexec/adzero/dns-guard.uc",
            "usr/libexec/adzero/dns-guard-lib.uc",
            "usr/libexec/adzero/dns-pool.uc",
            "usr/libexec/adzero/dns-pool-lib.uc",
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
            "usr/libexec/adzero/rules-cache.uc",
            "usr/libexec/adzero/routing-lib.uc",
            "usr/libexec/adzero/routing-data.uc",
            "usr/libexec/adzero/routing-data",
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
            "usr/share/rpcd/ucode/adzero",
            "etc/config/adzero",
            "usr/libexec/adzero/ctl",
            "usr/libexec/adzero/probe",
            "usr/libexec/adzero/probe.uc",
            "usr/libexec/adzero/proxy-state.uc",
            "usr/libexec/adzero/rules.sh",
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
        })
        self.assertEqual(installed["usr/libexec/adzero/ctl"][0].mode & 0o777, 0o755)
        self.assertEqual(installed["usr/libexec/adzero/probe"][0].mode & 0o777, 0o755)
        for name, (member, data) in installed.items():
            if name not in {"usr/libexec/adzero/ctl", "usr/libexec/adzero/probe", "usr/libexec/adzero/adapter", "usr/libexec/adzero/backend", "usr/libexec/adzero/watch", "usr/libexec/adzero/observe", "usr/libexec/adzero/dns-handoff", "usr/libexec/adzero/dns-enhance", "usr/libexec/adzero/dns-native", "usr/libexec/adzero/policy", "usr/libexec/adzero/sources", "usr/libexec/adzero/job", "usr/libexec/adzero/update", "usr/libexec/adzero/routing-data", "usr/libexec/adzero/cache-curl/curl", "etc/init.d/adzero", "etc/init.d/adzero-dns", "usr/libexec/adzero/dns-guard"}:
                self.assertEqual(member.mode & 0o777, 0o644)
            self.assertTrue(data)

    def test_reproducible_byte_for_byte(self):
        first, _ = build_ipk.build_bytes()
        second, _ = build_ipk.build_bytes()
        self.assertEqual(first, second)

    def test_cli_writes_only_requested_output(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "my-build.ipk"
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/build_ipk.py"),
                 "--output", str(destination)],
                text=True, capture_output=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(destination.is_file())
            self.assertEqual(destination.read_bytes(), build_ipk.build_bytes()[0])
            self.assertIn("UNSIGNED EXPERIMENTAL OPKG IPK", result.stdout)

    def test_rejects_unexpected_payload_file(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as directory:
            bad_root = Path(directory)
            unexpected = bad_root / "root" / "etc" / "adzero"
            unexpected.parent.mkdir(parents=True)
            unexpected.write_text("surprise")
            with mock.patch.object(build_ipk, "PKG", bad_root):
                with self.assertRaisesRegex(ValueError, "Unexpected package payload"):
                    build_ipk.payload()


if __name__ == "__main__":
    unittest.main()
