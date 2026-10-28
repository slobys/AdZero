"""Pinned iStoreOS source preparation: no real clone, SDK build, or router writes."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_istoreos_source as source
import probe_istoreos as probe


class IstoreSourcePreparationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.files = {
            "commit.buildinfo": (probe.SOURCE_COMMIT[:10] + "\n").encode(),
            "config.buildinfo": b'CONFIG_VERSION_DIST="iStoreOS"\nCONFIG_TARGET_x86_64=y\n',
            "feeds.buildinfo": (
                b'src-git packages https://github.com/jjm2473/packages.git^' + b'a'*40 + b'\n'
                b'src-git luci https://github.com/jjm2473/luci.git^' + b'b'*40 + b'\n'
                b'src-git routing https://github.com/openwrt/routing.git^' + b'c'*40 + b'\n'
                b'src-git telephony https://github.com/openwrt/telephony.git^' + b'd'*40 + b'\n'
            ),
            "feeds.conf": b'src-include defaults feeds.conf.default\n',
            "version.latest.v2": f'[{probe.IMAGE_FILENAME}]({probe.IMAGE_FILENAME})\n'.encode(),
        }
        self.pins = {k: hashlib.sha256(data).hexdigest() for k, data in self.files.items()}
        self.directory = self.root / "istore-src"
        def downloader(url):
            return self.files[url.removeprefix(probe.ORIGIN)]
        self.fetch = downloader

    def test_preparation_pins_full_commit_source_and_feeds(self):
        git_commands = []
        def fake_git(argv, check):
            self.assertTrue(check)
            git_commands.append(argv)
            if "checkout" in argv:
                (self.directory / "scripts").mkdir(parents=True)
                (self.directory / "rules.mk").write_text("rules")
                (self.directory / "scripts/feeds").write_text("fixture")
        with patch.dict(probe.METADATA_SHA256, self.pins, clear=True):
            report = source.prepare(self.directory, fetch=self.fetch,
                       run=fake_git,
                       check_output=lambda *a, **kw: probe.SOURCE_COMMIT)
        self.assertEqual(len(git_commands), 3)
        self.assertEqual(git_commands[0][:2], ["git", "clone"])
        self.assertIn("--no-checkout", git_commands[0])
        self.assertEqual(git_commands[0][-2], probe.SOURCE_REPO + ".git")
        self.assertEqual(git_commands[1][-4:], ["--depth", "1", "origin", probe.SOURCE_COMMIT])
        self.assertEqual(git_commands[2][-3:], ["checkout", "--detach", probe.SOURCE_COMMIT])
        self.assertEqual(report["sdk_build"], "not_run")
        self.assertFalse(report["publishable"])
        self.assertEqual(report["source_commit"], probe.SOURCE_COMMIT)
        self.assertEqual(report["vendor_feed_count"], 4)
        self.assertEqual((self.directory / ".config").read_bytes(),
                         self.files["config.buildinfo"])
        self.assertIn("^" + "a"*40, (self.directory / "feeds.conf").read_text())
        self.assertTrue((self.directory / "adzero-source-provenance.json").is_file())

    def test_mismatched_source_commit_refuses_to_export_sdk_manifest(self):
        def fake_clone(*args, **kwargs):
            (self.directory / "scripts").mkdir(parents=True, exist_ok=True)
        with patch.dict(probe.METADATA_SHA256, self.pins, clear=True):
            with self.assertRaisesRegex(ValueError, "Pinned iStoreOS source differs"):
                source.prepare(self.directory, fetch=self.fetch, run=fake_clone,
                               check_output=lambda *_args, **_kw: "0"*40)
        self.assertFalse((self.directory / "adzero-source-provenance.json").exists())

    def test_unavailable_pinned_commit_never_writes_source_evidence(self):
        import subprocess
        calls = []
        def unavailable_fetch(argv, check):
            calls.append(argv)
            if "fetch" in argv:
                raise subprocess.CalledProcessError(128, argv)
        with patch.dict(probe.METADATA_SHA256, self.pins, clear=True):
            with self.assertRaises(subprocess.CalledProcessError):
                source.prepare(self.directory, fetch=self.fetch,
                               run=unavailable_fetch)
        self.assertEqual(len(calls), 2)
        self.assertFalse((self.directory / "adzero-source-provenance.json").exists())

    def test_existing_directory_is_not_overwritten(self):
        self.directory.mkdir()
        (self.directory / "keep").write_text("preserve")
        with patch.dict(probe.METADATA_SHA256, self.pins, clear=True):
            with self.assertRaisesRegex(ValueError, "destination exists"):
                source.prepare(self.directory, fetch=self.fetch)
        self.assertEqual((self.directory / "keep").read_text(), "preserve")

    def test_rejects_untrusted_or_duplicate_feed_urls(self):
        good = self.files["feeds.buildinfo"]
        for bad in (good.replace(b"https://github.com/", b"https://evil.example/"),
                    good + good.splitlines(keepends=True)[0]):
            with self.subTest(bad_size=len(bad)):
                with patch.dict(probe.METADATA_SHA256, {
                        **self.pins, "feeds.buildinfo": hashlib.sha256(bad).hexdigest()
                }, clear=True):
                    with self.assertRaises(ValueError):
                        source.parse_pinned_feeds(bad)


if __name__ == "__main__":
    unittest.main()
