"""Native iStoreOS Buildroot build orchestration: mocked commands, no real build."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_istoreos_native as builder
from probe_istoreos import METADATA_SHA256, SOURCE_COMMIT


class IstoreBuildCommandTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "adzero-source-provenance.json").write_text(json.dumps({
            "source_commit": SOURCE_COMMIT, "metadata_sha256": METADATA_SHA256,
            "firmware": "istoreos", "publishable": False,
        }))
        self.config = self.source / ".config"
        self.config.write_text(
            'CONFIG_VERSION_DIST="iStoreOS"\n'
            'CONFIG_PACKAGE_luci-app-adzero=y\n'
            '# CONFIG_PACKAGE_luci-app-adzero is not set\n'
        )
        self.output = self.root / "candidate"
        self.events = []
        self.report = {"publishable": False,
                       "sdk_build": "native_vendor_source_candidate_not_sdk_verified"}
        self.feed_patch = mock.patch.object(builder, "verify_pinned_vendor_feeds",
                                           side_effect=lambda *_: self.events.append("feeds"))
        self.feed_patch.start()
        self.addCleanup(self.feed_patch.stop)

    def execute(self, argv, *, cwd, check):
        self.assertTrue(check)
        self.assertEqual(cwd, self.source)
        self.events.append(tuple(argv))

    def read_git(self, argv, text):
        self.assertEqual(argv[:2], ["git", "-C"])
        self.assertTrue(text)
        return SOURCE_COMMIT

    def package_prepare(self, source, package):
        self.assertEqual(source, self.source)
        self.assertEqual(package, builder.PACKAGE)
        self.events.append("package-sources")

    def export(self, source, output, run):
        self.assertEqual(source, self.source)
        self.assertEqual(output, self.output)
        self.assertEqual(run, self.read_git)
        self.events.append("inspect")
        return self.report

    def build(self, **kwargs):
        return builder.build(
            self.source, self.output, run=self.execute,
            read_git=self.read_git, install_source=self.package_prepare,
            export=self.export, **kwargs
        )

    def test_compiles_all_vendor_steps_and_never_publishes(self):
        result = self.build(jobs=6)
        self.assertFalse(result["publishable"])
        self.assertEqual(self.events[0:2], ["feeds", "package-sources"])
        stages = [event for event in self.events if isinstance(event, tuple)]
        self.assertEqual(stages, [
            ("make", "-j6", "defconfig"),
            ("make", "-j6", "tools/install"),
            ("make", "-j6", "toolchain/install"),
            ("make", "-j6", "target/linux/compile"),
            ("make", "-j6", "package/feeds/luci/luci-base/host/compile"),
            ("make", "-j6", "package/luci-app-adzero/compile"),
        ])
        self.assertEqual(self.events[-1], "inspect")
        self.assertEqual(
            self.config.read_text().count("CONFIG_PACKAGE_luci-app-adzero=m"), 1
        )
        self.assertNotIn("CONFIG_PACKAGE_luci-app-adzero=y", self.config.read_text())
        self.assertNotIn("# CONFIG_PACKAGE_luci-app-adzero is not set",
                         self.config.read_text())
        self.assertFalse(self.output.exists())

    def test_bad_jobs_or_destination_never_triggers_compile(self):
        for jobs in (0, 13, "8", -1):
            with self.subTest(jobs=jobs):
                with self.assertRaisesRegex(ValueError, "Parallel jobs"):
                    self.build(jobs=jobs)
                self.assertFalse(self.events)
        self.output.mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.build()
        self.assertFalse(self.events)

    def test_dangling_symlink_destination_is_rejected_before_build(self):
        alias = self.root / "alias"
        target = self.root / "missing-out"
        alias.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "must not be symlinks"):
            builder.build(self.source, alias, run=self.execute,
                          read_git=self.read_git,
                          install_source=self.package_prepare,
                          export=self.export)
        self.assertFalse(target.exists())
        self.assertFalse(self.events)

    def test_changed_vendor_identity_and_git_commit_rejected(self):
        meta = json.loads((self.source / "adzero-source-provenance.json").read_text())
        meta["source_commit"] = "0" * 40
        (self.source / "adzero-source-provenance.json").write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError, "Vendor provenance differs"):
            self.build()
        self.assertFalse(self.events)
        meta["source_commit"] = SOURCE_COMMIT
        (self.source / "adzero-source-provenance.json").write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError, "Git checkout differs"):
            builder.build(self.source, self.output,
                          read_git=lambda *_args, **_kw: "0" * 40,
                          install_source=self.package_prepare)
        self.assertFalse(self.events)

    def test_feed_drift_cannot_start_compilation(self):
        with mock.patch.object(builder, "verify_pinned_vendor_feeds",
                               side_effect=ValueError("vendor feed drift")):
            with self.assertRaisesRegex(ValueError, "vendor feed drift"):
                self.build()
        self.assertFalse(self.events)

    def test_failed_make_or_forged_publishable_report_rejected(self):
        def failed_step(argv, *, cwd, check):
            if argv[-1] == "target/linux/compile":
                raise RuntimeError("vendor compilation failed")
        with self.assertRaisesRegex(RuntimeError, "vendor compilation failed"):
            builder.build(self.source, self.output, run=failed_step,
                          read_git=self.read_git,
                          install_source=self.package_prepare, export=self.export)
        self.report = {"publishable": True, "sdk_build": "passed"}
        with self.assertRaisesRegex(ValueError, "cannot authorize"):
            self.build()


if __name__ == "__main__":
    unittest.main()
