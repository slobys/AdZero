"""Native iStoreOS Buildroot APK evidence is a candidate, never an SDK release."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import inspect_istoreos_candidate as candidate
from build_ipk import version
from build_sdk import PACKAGE
from probe_istoreos import METADATA_SHA256, SOURCE_COMMIT


class IstoreNativeCandidateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / "vendor-source"
        self.source.mkdir()
        names = (
            "packages", "luci", "routing", "telephony", "video", "store",
            "third", "third_party", "diskman", "oaf", "linkease_nas",
            "linkease_nas_luci", "jjm2473_apps"
        )
        self.feeds = {name: f"{index + 1:040x}"
                      for index, name in enumerate(names)}
        feed_lines = [
            f"src-git {name} https://github.com/istoreos/{name}.git^{commit}"
            for name, commit in self.feeds.items()
        ]
        self.feed_file = self.source / "feeds.conf"
        self.feed_file.write_text("\n".join(feed_lines) + "\n")
        for name in names:
            (self.source / "feeds" / name).mkdir(parents=True)
        checksum = hashlib.sha256(self.feed_file.read_bytes()).hexdigest()
        patch = mock.patch.dict(candidate.METADATA_SHA256,
                                {"feeds.buildinfo": checksum})
        patch.start()
        self.addCleanup(patch.stop)
        shutil.copytree(PACKAGE, self.source / "package/luci-app-adzero")
        self.evidence = self.source / "adzero-source-provenance.json"
        self.evidence.write_text(json.dumps({
            "firmware": "istoreos", "source_commit": SOURCE_COMMIT,
            "metadata_sha256": METADATA_SHA256, "sdk_build": "not_run",
            "publishable": False,
        }))
        self.config = self.source / ".config"
        self.config.write_text(
            'CONFIG_VERSION_DIST="iStoreOS"\n'
            'CONFIG_TARGET_BOARD="x86"\n'
            'CONFIG_TARGET_SUBTARGET="64"\n'
            'CONFIG_TARGET_ARCH_PACKAGES="x86_64"\n'
            'CONFIG_USE_APK=y\n'
            'CONFIG_PACKAGE_luci-app-adzero=m\n'
        )
        self.apktool = self.source / "staging_dir/host/bin/apk"
        self.apktool.parent.mkdir(parents=True)
        self.apktool.write_text("#!/bin/sh\nexit 0\n")
        self.apktool.chmod(0o755)
        self.binary = self.source / "bin/packages/x86_64/vendor" / (
            f"luci-app-adzero-{version()}.apk"
        )
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b"mock-native-apk-bytes-not-suitable-for-installation")
        self.inspected = []

    def run_git(self, argv, text):
        self.assertEqual(argv[:2], ["git", "-C"])
        self.assertEqual(argv[-2:], ["rev-parse", "HEAD"])
        if argv[2] == str(self.source):
            return SOURCE_COMMIT + "\n"
        folder = Path(argv[2])
        self.assertEqual(folder.parent, self.source / "feeds")
        return self.feeds[folder.name] + "\n"

    def inspect(self, binary, apk_tool):
        self.inspected.append((binary, apk_tool))
        return {"runtime_source_and_modes": "passed",
                "luci_delivered_ui_regressions": "passed"}

    def validate(self):
        return candidate.validate(self.source, run=self.run_git,
                                  package_inspector=self.inspect)

    def test_true_source_build_remains_not_publishable(self):
        result = self.validate()
        self.assertFalse(result["publishable"])
        self.assertNotEqual(result["sdk_build"], "passed")
        self.assertEqual(result["revision_declared"], "2026102312")
        self.assertEqual(result["release_declared"], "25.12.6")
        self.assertEqual(result["sha256"], hashlib.sha256(
            self.binary.read_bytes()).hexdigest())
        self.assertEqual(self.inspected, [(self.binary, self.apktool)])
        self.assertEqual(result["vendor_feeds_checked"]["feed_count"], 13)
        self.assertEqual(result["vendor_feeds_checked"]["feeds_sha256"],
                         hashlib.sha256(self.feed_file.read_bytes()).hexdigest())

    def test_export_keeps_candidate_outside_vendor_tree(self):
        output = self.base / "evidence"
        result = candidate.export(self.source, output, run=self.run_git,
                                  package_inspector=self.inspect)
        self.assertEqual((output / result["package"]).read_bytes(),
                         self.binary.read_bytes())
        report = json.loads((output / "build-manifest.json").read_text())
        self.assertFalse(report["publishable"])
        self.assertNotEqual(report["sdk_build"], "passed")
        with self.assertRaisesRegex(ValueError, "already exists"):
            candidate.export(self.source, output, run=self.run_git,
                             package_inspector=self.inspect)

    def test_wrong_vendor_identity_or_arch_is_rejected(self):
        initial = self.config.read_text()
        for old, replacement in (
            ('"iStoreOS"', '"OpenWrt"'),
            ('"x86_64"', '"aarch64"'),
            ('CONFIG_USE_APK=y', 'CONFIG_USE_APK=n'),
        ):
            with self.subTest(replacement=replacement):
                self.config.write_text(initial.replace(old, replacement))
                with self.assertRaisesRegex(ValueError, "configuration"):
                    self.validate()
        self.config.write_text(initial)

    def test_no_unverified_binary_or_source_can_be_approved(self):
        self.binary.unlink()
        with self.assertRaisesRegex(ValueError, "Expected exactly one"):
            self.validate()
        self.binary.write_bytes(b"fixture")
        target = self.source / "package/luci-app-adzero/Makefile"
        target.write_text(target.read_text() + "\n# tampered\n")
        with self.assertRaisesRegex(ValueError, "source differs"):
            self.validate()

    def test_forged_provenance_or_git_revision_refused(self):
        meta = json.loads(self.evidence.read_text())
        meta["sdk_build"] = "passed"
        self.evidence.write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.validate()
        meta["sdk_build"] = "not_run"
        self.evidence.write_text(json.dumps(meta))
        def wrong_git(*_args, **_kwargs):
            return "0" * 40
        with self.assertRaisesRegex(ValueError, "Git source HEAD"):
            candidate.validate(self.source, run=wrong_git,
                               package_inspector=self.inspect)

    def test_feeds_are_not_trusted_if_manifest_or_checkout_drifted(self):
        original = self.feed_file.read_bytes()
        self.feed_file.write_bytes(original + b"# tampered\n")
        with self.assertRaisesRegex(ValueError, "feed manifest differs"):
            self.validate()
        self.feed_file.write_bytes(original)
        first = "packages"
        original_ref = self.feeds[first]
        self.feeds[first] = "0" * 40
        with self.assertRaisesRegex(ValueError, "feed revision drift"):
            self.validate()
        self.feeds[first] = original_ref

    def test_missing_or_symlinked_vendor_feed_is_rejected(self):
        name = "packages"
        folder = self.source / "feeds" / name
        folder.rmdir()
        with self.assertRaisesRegex(ValueError, "Missing pinned vendor feed"):
            self.validate()
        folder.symlink_to(self.source / "feeds" / "luci", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Missing pinned vendor feed"):
            self.validate()

    def test_symlinked_candidate_output_never_redirects_export(self):
        alias = self.base / "alias"
        target = self.base / "untrusted-path"
        alias.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "must not be symlinked"):
            candidate.export(self.source, alias, run=self.run_git,
                             package_inspector=self.inspect)
        self.assertFalse(target.exists())

    def test_output_must_not_be_in_vendor_source(self):
        with self.assertRaisesRegex(ValueError, "outside the Buildroot"):
            candidate.export(self.source, self.source / "evidence",
                             run=self.run_git, package_inspector=self.inspect)
        self.assertFalse((self.source / "evidence").exists())


if __name__ == "__main__":
    unittest.main()
