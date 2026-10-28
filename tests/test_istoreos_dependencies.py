"""Real iStoreOS image dependency-closure logic with isolated tiny fixtures.

No network, native install or firmware mutation. Package records and the
official raw-image hash are explicitly injected for deterministic tests.
"""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_istoreos_dependencies as closure


class OriginalFirmwareClosureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="adzero-istore-deps-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / "vendor"
        self.source.mkdir()
        self.folder = self.source / "bin/packages/x86_64/vendor"
        self.folder.mkdir(parents=True)
        self.adzero = self.folder / "luci-app-adzero-0.21.5-r1.apk"
        self.adzero.write_bytes(b"mock-original-source-package")
        self.apk_tool = self.source / "staging_dir/host/bin/apk"
        self.apk_tool.parent.mkdir(parents=True)
        self.apk_tool.write_text("not-executed")
        self.image = self.base / "image.img"
        self.image.write_bytes(b"fake-fixture-image")
        self.installed = {"libc", "luci-base", "rpcd", "curl"}
        self.meta = {
            "luci-app-adzero": {
                "name": "luci-app-adzero", "version": "0.21.5-r1",
                "arch": "noarch", "depends": ["libc", "curl", "adblock-fast", "conntrack"]
            },
            "adblock-fast": {
                "name": "adblock-fast", "version": "1.2.4-r4",
                "arch": "noarch", "depends": ["libc"]
            },
            "conntrack": {
                "name": "conntrack", "version": "1.4.8-r1",
                "arch": "x86_64", "depends": ["libc", "libnetfilter-cthelper0"]
            },
            "libnetfilter-cthelper0": {
                "name": "libnetfilter-cthelper0", "version": "1.0.0-r2",
                "arch": "x86_64", "depends": ["libc"]
            },
        }
        for name, item in self.meta.items():
            if name != "luci-app-adzero":
                (self.folder / f"{name}-{item['version']}.apk").write_bytes(
                    f"{name}:{item['version']}".encode()
                )

    def call(self):
        def inspected(source):
            self.assertEqual(source, self.source)
            return {
                "publishable": False,
                "sdk_build": "native_vendor_source_candidate_not_sdk_verified",
                "package": self.adzero.name,
                "sha256": (hashlib.sha256(self.adzero.read_bytes()).hexdigest()
                           if self.adzero.exists() else "0" * 64),
                "source_sha256": "a" * 64,
                "vendor_source_commit": "b" * 40,
                "vendor_feeds_checked": {"feed_count": 13},
            }
        def installed_reader(path):
            self.assertEqual(path, self.image)
            return self.installed
        def read_info(path, apk_tool):
            self.assertEqual(apk_tool, self.apk_tool)
            name = next(k for k in sorted(self.meta, key=len, reverse=True)
                        if path.name.startswith(f"{k}-"))
            return self.meta[name]
        return closure.closure(
            self.source, self.image, inspect=inspected,
            installed_reader=installed_reader, info_reader=read_info
        )

    def test_recursively_resolves_missing_vendor_packages_and_checksums(self):
        report = self.call()
        self.assertFalse(report["publishable"])
        self.assertEqual(report["revision"], "2026102312")
        self.assertEqual(report["apk_sha256"],
                         hashlib.sha256(self.adzero.read_bytes()).hexdigest())
        self.assertEqual(report["source_sha256"], "a" * 64)
        self.assertEqual(report["vendor_feeds_checked"], {"feed_count": 13})
        self.assertEqual(report["direct_dependencies"],
                         ["libc", "curl", "adblock-fast", "conntrack"])
        self.assertEqual(set(report["missing_from_original_firmware"]),
                         {"adblock-fast", "conntrack", "libnetfilter-cthelper0"})
        self.assertEqual(report["unresolved_dependencies"], [])
        self.assertEqual(report["closure"], "passed_vendor_builds_only")
        self.assertEqual(report["sdk_build"],
                         "native_vendor_source_candidate_not_sdk_verified")
        for name, evidence in report["missing_from_original_firmware"].items():
            path = self.folder / evidence["file"]
            self.assertEqual(evidence["sha256"],
                             hashlib.sha256(path.read_bytes()).hexdigest())

    def test_candidate_integrity_and_version_mismatch_fail_closed(self):
        original = self.meta["luci-app-adzero"]["version"]
        self.meta["luci-app-adzero"]["version"] = "0.21.4-r1"
        with self.assertRaisesRegex(ValueError, "identity, version"):
            self.call()
        self.meta["luci-app-adzero"]["version"] = original

        def forged(source):
            return {
                "publishable": False,
                "sdk_build": "native_vendor_source_candidate_not_sdk_verified",
                "package": self.adzero.name,
                "sha256": "0" * 64,
            }
        with self.assertRaisesRegex(ValueError, "APK SHA-256 differs"):
            closure.closure(
                self.source, self.image, inspect=forged,
                installed_reader=lambda _path: self.installed,
                info_reader=lambda p, _tool: self.meta[next(
                    name for name in sorted(self.meta, key=len, reverse=True)
                    if p.name.startswith(name + "-"))],
            )

    def test_original_full_tcpdump_cannot_be_replaced_by_mini_dependency(self):
        self.installed.add("tcpdump")
        self.meta["luci-app-adzero"]["depends"].append("tcpdump-mini")
        with self.assertRaisesRegex(ValueError, "replace the original full tcpdump"):
            self.call()

    def test_missing_transitive_package_fails_closed(self):
        (self.folder / "libnetfilter-cthelper0-1.0.0-r2.apk").unlink()
        with self.assertRaisesRegex(ValueError, "Missing or ambiguous"):
            self.call()

    def test_unknown_or_versioned_constraint_fails_closed(self):
        for bad in ("libc>=1.2", "../other", "so:foo", "domain/name", "", "luci-base < 1"):
            with self.subTest(bad=bad):
                self.meta["adblock-fast"]["depends"] = [bad]
                with self.assertRaisesRegex(ValueError, "Unresolved or versioned"):
                    self.call()

    def test_wrong_architecture_and_version_rejected(self):
        self.meta["conntrack"]["arch"] = "aarch64"
        with self.assertRaisesRegex(ValueError, "wrong architecture"):
            self.call()
        self.meta["conntrack"]["arch"] = "x86_64"
        self.meta["conntrack"]["version"] = "0.0.0"
        with self.assertRaisesRegex(ValueError, "unverified version or filename"):
            self.call()

    def test_symlinked_vendor_dependency_is_not_an_authorized_provider(self):
        package = self.folder / "conntrack-1.4.8-r1.apk"
        package.unlink()
        package.symlink_to(self.folder / "adblock-fast-1.2.4-r4.apk")
        with self.assertRaisesRegex(ValueError, "Missing or ambiguous"):
            self.call()

    def test_ambiguous_binaries_fail_closed(self):
        alternate = self.folder.parent / "duplicate"
        alternate.mkdir()
        path = alternate / "conntrack-1.4.8-r1.apk"
        path.write_bytes(b"same-name-duplicate")
        with self.assertRaisesRegex(ValueError, "Missing or ambiguous"):
            self.call()

    def test_missing_candidate_and_unexpected_approval_rejected(self):
        self.adzero.unlink()
        with self.assertRaisesRegex(ValueError, "candidate APK missing"):
            self.call()
        self.adzero.write_bytes(b"restore")
        with mock.patch.object(closure, "validate_candidate",
                               return_value={"publishable": True,
                                             "sdk_build": "passed",
                                             "package": self.adzero.name}):
            with self.assertRaisesRegex(ValueError, "authorized a release"):
                closure.closure(
                    self.source, self.image,
                    inspect=closure.validate_candidate,
                    installed_reader=lambda _path: self.installed,
                    info_reader=lambda _path, _tool: self.meta["luci-app-adzero"],
                )

    def test_mismatched_raw_vendor_image_digest_fails_closed(self):
        with mock.patch.object(closure, "IMAGE_RAW_SHA256", "0" * 64):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                closure.original_installed_db(self.image, run=lambda _: b"fixture")
        alias = self.base / "alias.img"
        alias.symlink_to(self.image)
        with self.assertRaisesRegex(ValueError, "symlinked"):
            closure.original_installed_db(alias, run=lambda _: b"fixture")

    def test_unexpected_original_rootfs_db_refused(self):
        with mock.patch.object(
            closure, "IMAGE_RAW_SHA256",
            hashlib.sha256(self.image.read_bytes()).hexdigest()
        ):
            with self.assertRaisesRegex(ValueError, "Missing or invalid"):
                closure.original_installed_db(self.image, run=lambda _: b"bad")
            with self.assertRaisesRegex(ValueError, "does not describe"):
                closure.original_installed_db(
                    self.image,
                    run=lambda _: ("P:other\n" * 256).encode()
                )


if __name__ == "__main__":
    unittest.main()
