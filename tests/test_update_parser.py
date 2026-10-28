"""Exercise the exact OpenWrt ucode release parser, not a mocked shell response.

Only runs when ADZERO_UCODE is provided by the pinned official OpenWrt rootfs
check. Uses temporary private files and never contacts GitHub or a router.
"""
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PARSER = ROOT / "luci-app-adzero/root/usr/libexec/adzero/update-check.uc"
COMMAND = os.environ.get("ADZERO_UCODE", "")


@unittest.skipUnless(COMMAND, "Official OpenWrt Ucode runtime not configured")
class OfficialReleaseParserTest(unittest.TestCase):
    def setUp(self):
        directory = Path("/tmp/adzero-update")
        if directory.is_symlink():
            self.skipTest("Update directory is a symlink")
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.stat().st_uid != os.getuid():
            self.skipTest("Update directory is owned by a different uid")
        if stat.S_IMODE(directory.stat().st_mode) & 0o077:
            self.skipTest("Update directory is not private")
        fd, name = tempfile.mkstemp(prefix="releases.", dir=directory)
        self.file = Path(name)
        os.fchmod(fd, 0o600)
        os.close(fd)
        self.addCleanup(self.file.unlink, missing_ok=True)
        self.ucode = shlex.split(COMMAND)

    def parse(self, data):
        self.file.write_text(json.dumps(data))
        return subprocess.run(
            self.ucode + [str(PARSER), str(self.file)],
            text=True, capture_output=True, timeout=15
        )

    def test_selects_latest_published_numeric_version_with_official_digest(self):
        digest = "1" * 64
        cases = [
            {"tag_name": "v0.21.4", "draft": False,
             "assets": [{"name": "install.sh", "digest": "sha256:" + "0" * 64}]},
            {"tag_name": "v0.21.5", "draft": False, "prerelease": True,
             "assets": [{"name": "install.sh", "digest": "sha256:" + digest}]},
            {"tag_name": "v0.21.9", "draft": True,
             "assets": [{"name": "install.sh", "digest": "sha256:" + "9" * 64}]},
            {"tag_name": "v0.21.8", "draft": False,
             "assets": [{"name": "install.sh", "digest": "sha256:INVALID"}]},
            {"tag_name": "v0.21.7;touch", "draft": False,
             "assets": [{"name": "install.sh", "digest": "sha256:" + "7" * 64}]},
        ]
        result = self.parse(cases)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"v0.21.5 {digest}")

    def test_refuses_nonprivate_and_untrusted_release_files(self):
        self.file.chmod(0o644)
        result = self.parse([{"tag_name": "v0.21.5", "assets": []}])
        self.assertNotEqual(result.returncode, 0)
        self.file.chmod(0o600)
        unsafe = self.file.parent.parent / self.file.name
        result = subprocess.run(self.ucode + [str(PARSER), str(unsafe)],
                                text=True, capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)

    def test_release_without_a_verified_installer_is_never_selected(self):
        response = self.parse([
            {"tag_name": "v0.21.7", "draft": False,
             "assets": [{"name": "install.sh", "digest": None}]}
        ])
        self.assertNotEqual(response.returncode, 0)


if __name__ == "__main__":
    unittest.main()
