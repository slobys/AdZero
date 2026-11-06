"""Offline provenance guards for archived OpenWrt native CI fixtures.

These tests do not download packages or modify a router.
"""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
PREFIX="https://archive.openwrt.org/releases/24.10.8/packages/x86_64/"
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_timeout

class ArchivePinTests(unittest.TestCase):
    def test_official_openwrt_ci_archive_pins_and_current_consumers(self):
        report=json.loads((ROOT/"docs/evidence/ci-openwrt-24108-official-archive-pins.json").read_text(encoding="utf-8"))
        self.assertEqual(report["immutable_release_archive"],PREFIX)
        by_name={item["name"]:item for item in report["packages"]}
        self.assertEqual(len(by_name),7)
        for name,row in by_name.items():
            self.assertIn(row["feed"],("base","packages"))
            self.assertRegex(row["sha256"],r"^[0-9a-f]{64}$")
            self.assertTrue(name.endswith("_x86_64.ipk"))
        socket=(ROOT/"scripts/prepare_dns_socket.py").read_text(encoding="utf-8")
        self.assertIn(by_name["ucode-mod-socket_2025.07.18~3f64c808-r1_x86_64.ipk"]["sha256"],socket)
        self.assertIn(PREFIX+"base/",socket)
        timeout = report["timeout_executable_pin"]
        self.assertEqual(prepare_timeout.EXECUTABLE_SHA256, timeout["executable_sha256"])
        self.assertEqual(prepare_timeout.EXECUTABLE, timeout["executable_path"])
        self.assertEqual(prepare_timeout.URL, PREFIX + "packages/coreutils-timeout_9.7-r1_x86_64.ipk")
        for filename in ("check_passwall_runtime.py","check_dns_pool_runtime.py"):
            script=(ROOT/"scripts"/filename).read_text(encoding="utf-8")
            self.assertIn("prepare_timeout(ROOT)", script)
            self.assertIn("install_timeout(ROOT,base)", script)
        for filename in ("check_openclash_runtime.py","check_observe_runtime.py"):
            script=(ROOT/"scripts"/filename).read_text(encoding="utf-8")
            self.assertIn(PREFIX,script)
        self.assertEqual(report["physical_device_acceptance"],"not_run")

    def test_timeout_fixture_ignores_repack_metadata_but_rejects_changed_executable(self):
        def archive(name, data, timestamp):
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w:gz") as tar:
                member = tarfile.TarInfo("./" + name)
                member.size = len(data)
                member.mtime = timestamp
                tar.addfile(member, io.BytesIO(data))
            return output.getvalue()

        executable = b"pinned executable fixture"
        digest = hashlib.sha256(executable).hexdigest()
        with tempfile.TemporaryDirectory() as temp, patch.object(prepare_timeout, "EXECUTABLE_SHA256", digest):
            root = Path(temp)
            cache = root / "artifacts/runtime/passwall-extras" / prepare_timeout.NAME
            cache.parent.mkdir(parents=True)
            for timestamp, data in ((1, executable), (2, executable), (3, b"modified")):
                payload = archive(prepare_timeout.EXECUTABLE, data, timestamp)
                cache.write_bytes(archive("data.tar.gz", payload, timestamp))
                if data == executable:
                    self.assertEqual(prepare_timeout.prepare(root), executable)
                else:
                    with self.assertRaisesRegex(ValueError, "executable checksum mismatch"):
                        prepare_timeout.prepare(root)

if __name__=="__main__":
    unittest.main()
