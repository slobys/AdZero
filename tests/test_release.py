"""Release selection boundaries, with fake SDK output contents."""
import hashlib
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
import sys
import tempfile
import tarfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_release
import fetch_sdk


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "release"

    def manifest(self, target="rockchip/armv8", arch="aarch64_generic", release="25.12.5", fmt="apk"):
        slot = self.root / (release + '-' + target.replace('/', '-'))
        slot.mkdir(exist_ok=True)
        package = slot / ("luci-app-adzero-" + build_release.version() + "." + fmt)
        package.write_bytes(b"mock-SDK-output")
        report = {"sdk_build":"passed", "source_sha256":"current", "firmware_declared":"openwrt",
                  "target_observed":target, "package_arch_observed":arch, "release_declared":release,
                  "package_format":fmt, "package":package.name,
                  "sha256":hashlib.sha256(package.read_bytes()).hexdigest()}
        manifest = slot / "build-manifest.json"
        manifest.write_text(json.dumps(report))
        return manifest

    def test_verified_arm_build_gets_distinct_target_and_sdk_arch_row(self):
        with mock.patch.object(build_release, "source_hash", return_value="current"):
            build_release.bundle([self.manifest()], self.output)
        rows = (self.output / "releases.tsv").read_text()
        self.assertIn("rockchip-armv8-aarch64_generic.apk", rows)
        self.assertIn("rockchip/armv8\taarch64_generic\tapk", rows)
        release = build_release.version().rsplit('-r', 1)[0]
        self.assertTrue((self.root / f"adzero-{release}-install.tar.gz").exists())
        instructions = (self.output / 'INSTALL.txt').read_text()
        self.assertIn('sh install.sh --menu --local-dir "$(pwd)"', instructions)

    def test_windows_defaults_cannot_put_crlf_in_delivered_text_or_archive(self):
        original = Path.write_text
        def windows_write(path, text, *args, **kwargs):
            kwargs.setdefault('newline', '\r\n')
            return original(path, text, *args, **kwargs)
        with mock.patch.object(Path, 'write_text', new=windows_write), mock.patch.object(build_release, 'source_hash', return_value='current'):
            build_release.bundle([self.manifest()], self.output)
        for name in ('releases.tsv', 'build-provenance.json', 'install.sh', 'INSTALL.txt'):
            self.assertNotIn(b'\r', (self.output / name).read_bytes(), name)
        release = build_release.version().rsplit('-r', 1)[0]
        with tarfile.open(self.root / f'adzero-{release}-install.tar.gz') as archive:
            for member in archive.getmembers():
                if member.name.endswith(('.tsv', '.json', '.sh', '.txt')):
                    self.assertNotIn(b'\r', archive.extractfile(member).read(), member.name)

    def test_delivery_check_rejects_crlf_and_bom(self):
        with mock.patch.object(build_release, 'source_hash', return_value='current'):
            build_release.bundle([self.manifest()], self.output)
        path = self.output / 'releases.tsv'
        original = path.read_bytes()
        for raw in (original.replace(b'\n', b'\r\n'), b'\xef\xbb\xbf' + original):
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, 'use LF'):
                build_release.validate_release(self.output)

    def test_delivery_check_rejects_duplicate_and_changed_package(self):
        with mock.patch.object(build_release, 'source_hash', return_value='current'):
            build_release.bundle([self.manifest()], self.output)
        path = self.output / 'releases.tsv'
        original = path.read_bytes()
        path.write_bytes(original + original.splitlines(keepends=True)[1])
        with self.assertRaisesRegex(ValueError, 'duplicate identity'):
            build_release.validate_release(self.output)
        path.write_bytes(original)
        package = next(self.output.glob('*.apk'))
        package.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'checksum differs'):
            build_release.validate_release(self.output)

    @unittest.skipUnless(os.name == 'posix', 'installer lifecycle requires Linux/WSL')
    def test_actual_generated_catalog_and_archive_pass_installer_for_all_targets(self):
        from test_installer import InstallerTest
        manifests = [self.manifest('x86/64', 'x86_64', '24.10.8', 'ipk'),
                     self.manifest('x86/64', 'x86_64'), self.manifest()]
        with mock.patch.object(build_release, 'source_hash', return_value='current'):
            build_release.bundle(manifests, self.output)
        release = build_release.version().rsplit('-r', 1)[0]
        with tarfile.open(self.root / f'adzero-{release}-install.tar.gz') as archive:
            # Python 3.11 lacks tarfile.extractall(filter='data'). Validate
            # every member and copy only ordinary files into our test root.
            destination = self.root / 'unpacked'
            for member in archive.getmembers():
                relative = Path(member.name)
                self.assertFalse(relative.is_absolute())
                self.assertNotIn('..', relative.parts)
                self.assertTrue(member.isfile(), member.name)
                output = destination / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, output.open('wb') as target:
                    shutil.copyfileobj(source, target)
        delivered = self.root / 'unpacked' / f'adzero-{release}'
        for kind in ('ipk', 'apk-x86', 'apk-arm'):
            with self.subTest(target=kind):
                fixture = InstallerTest()
                fixture.setUp()
                try:
                    if kind != 'ipk':
                        fixture.arm_apk(arch='aarch64' if kind == 'apk-arm' else 'x86_64')
                        if kind == 'apk-x86':
                            fixture.write('etc/openwrt_release', "DISTRIB_ID='OpenWrt'\nDISTRIB_RELEASE='25.12.5'\nDISTRIB_TARGET='x86/64'\nDISTRIB_ARCH='x86_64'\n")
                    fixture.release = delivered
                    fixture.upgrade_fixture()
                    # Test the exact installer script included in the release,
                    # not merely the current source-tree script. Checking must
                    # leave the previously installed version fully untouched.
                    shell = shlex.split(os.environ.get('ADZERO_TEST_SHELL', 'sh'))
                    result = subprocess.run(
                        shell + [str(delivered / 'install.sh'), '--menu',
                                 '--local-dir', str(delivered)],
                        input='3\n0\n', env=fixture.env, text=True,
                        capture_output=True, timeout=20
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('通过 SHA-256 校验', result.stdout)
                    self.assertFalse(fixture.log.exists())
                    self.assertTrue((fixture.root / 'etc/adzero/adapter.json').is_file())
                    reply = fixture.run_installer('--upgrade', '--no-enable')
                    expected = 'rockchip-armv8-aarch64_generic.apk' if kind == 'apk-arm' else ('25.12.5-x86_64.apk' if kind == 'apk-x86' else '24.10.8-x86_64.ipk')
                    self.assertIn(expected, reply.stdout)
                    manager = 'opkg' if kind == 'ipk' else 'apk'
                    expected_ops=['opkg update','ctl disable','opkg install'] if manager=='opkg' else ['apk check-local','ctl disable','apk add-local']
                    self.assertEqual(fixture.log.read_text().splitlines(),expected_ops)
                finally:
                    fixture.doCleanups()

    @unittest.skipUnless(os.name == 'posix', 'actual package lifecycle uses POSIX shell')
    def test_five_exact_sdk_platforms_assemble_and_install_safely(self):
        """Real release selection requires both distinct vendor packages."""
        from check_release_delivery import check
        manifests = [
            self.manifest("x86/64", "x86_64", "24.10.8", "ipk"),
            self.manifest("x86/64", "x86_64", "25.12.5", "apk"),
            self.manifest("rockchip/armv8", "aarch64_generic", "25.12.5", "apk"),
        ]
        for ver, fmt in (("24.10.6", "ipk"), ("25.12.2", "apk")):
            item = self.manifest("x86/64", "x86_64", ver, fmt)
            record = json.loads(item.read_text())
            record["firmware_declared"] = "immortalwrt"
            item.write_text(json.dumps(record))
            manifests.append(item)
        with mock.patch.object(build_release, "source_hash", return_value="current"):
            build_release.bundle(manifests, self.output)
        report = check(self.output)
        self.assertEqual(report["checks"], 10)
        self.assertTrue(all(x["selection_checksum_backup_restore_install"] == "passed"
                            for x in report["cases"]))
        targets = {x["target"] for x in report["cases"]}
        self.assertIn("immortalwrt-24.10.6-x86/64-ipk", targets)
        self.assertIn("immortalwrt-25.12.2-x86/64-apk", targets)
        # Even with the new vendor rows, an unknown iStoreOS release cannot
        # silently reuse the same x86_64 binary package.
        self.assertNotIn("istoreos", (self.output / "releases.tsv").read_text())

    def test_trusted_matrix_contains_only_current_sdk_verified_rows(self):
        actual = build_release.verified_builds()
        self.assertEqual(actual, {
            ("openwrt", "24.10.8", "x86/64", "ipk"): "x86_64",
            ("openwrt", "25.12.5", "x86/64", "apk"): "x86_64",
            ("openwrt", "25.12.5", "rockchip/armv8", "apk"): "aarch64_generic",
            ("immortalwrt", "24.10.6", "x86/64", "ipk"): "x86_64",
            ("immortalwrt", "25.12.2", "x86/64", "apk"): "x86_64",
        })
        self.assertFalse(any(key[0] == "istoreos" for key in actual))

    def test_derivative_release_requires_explicit_sdk_verified_matrix_row(self):
        manifest = self.manifest()
        payload = json.loads(manifest.read_text())
        payload["firmware_declared"] = "immortalwrt"
        manifest.write_text(json.dumps(payload))
        with mock.patch.object(build_release, "source_hash", return_value="current"):
            with self.assertRaisesRegex(ValueError, "supported build matrix"):
                build_release.bundle([manifest], self.output)

            with mock.patch.dict(build_release.SUPPORTED_BUILDS, {
                ("immortalwrt", "25.12.5", "rockchip/armv8", "apk"): "aarch64_generic"
            }):
                build_release.bundle([manifest], self.output)
        catalog = (self.output / "releases.tsv").read_text()
        self.assertIn("\timmortalwrt\t25.12.5\trockchip/armv8\t", catalog)
        self.assertIn("adzero-immortalwrt-", catalog)
        # Without the trusted matrix entry the same catalog is not releasable.
        with self.assertRaisesRegex(ValueError, "unsupported"):
            build_release.validate_release(self.output)

    def test_invalid_and_duplicate_verified_matrix_entries_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matrix.json"
            data = {"schema_version": 1, "sdk_verified": [
                {"firmware": "istoreos", "release": "24.10.8",
                 "target": "x86/64", "format": "ipk",
                 "arch": "x86_64", "sdk_build": "pending"}
            ]}
            path.write_text(json.dumps(data))
            self.assertEqual(build_release.verified_builds(path), {})
            data["sdk_verified"][0]["sdk_build"] = "passed"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "pinned ten-digit"):
                build_release.verified_builds(path)
            data["sdk_verified"][0]["revision"] = "2026102312"
            path.write_text(json.dumps(data))
            self.assertEqual(build_release.verified_revisions(path), {
                ("istoreos", "24.10.8", "x86/64", "ipk"): "2026102312"
            })
            self.assertEqual(build_release.verified_builds(path), {
                ("istoreos", "24.10.8", "x86/64", "ipk"): "x86_64"
            })
            data["sdk_verified"].append(dict(data["sdk_verified"][0]))
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "Duplicate verified"):
                build_release.verified_builds(path)
            malicious = dict(data["sdk_verified"][0], firmware="../../outside")
            data["sdk_verified"] = [malicious]
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "Invalid verified SDK identity"):
                build_release.verified_builds(path)

    def test_istoreos_catalog_requires_verified_revision_and_matching_manifest(self):
        item = self.manifest("x86/64", "x86_64", "25.12.5", "apk")
        report = json.loads(item.read_text())
        report.update(firmware_declared="istoreos", release_declared="25.12.6",
                      revision_declared="2026102312")
        item.write_text(json.dumps(report))
        identity = ("istoreos", "25.12.6", "x86/64", "apk")
        with mock.patch.object(build_release, "source_hash", return_value="current"), \
             mock.patch.dict(build_release.SUPPORTED_BUILDS, {identity: "x86_64"}), \
             mock.patch.dict(build_release.SUPPORTED_REVISIONS, {identity: "2026102312"}):
            build_release.bundle([item], self.output)
            lines = (self.output / "releases.tsv").read_text().splitlines()
            row = next(line for line in lines if not line.startswith("#"))
            self.assertEqual(len(row.split("\t")), 8)
            self.assertTrue(row.endswith("\t2026102312"))
            build_release.validate_release(self.output)
            # Corrupt, missing or mismatched revisions can never publish.
            for corrupted in (row.rsplit("\t", 1)[0],
                              row.rsplit("\t", 1)[0] + "\t2026102313",
                              row.rsplit("\t", 1)[0] + "\tbad"):
                (self.output / "releases.tsv").write_text(lines[0] + "\n" + corrupted + "\n", newline="\n")
                with self.assertRaisesRegex(ValueError, "revision"):
                    build_release.validate_release(self.output)
            report["revision_declared"] = "2026102313"
            item.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, "Build revision"):
                build_release.bundle([item], self.root / "reject-mismatch")

    def test_x86_output_cannot_be_relabelled_as_arm(self):
        with mock.patch.object(build_release, "source_hash", return_value="current"):
            with self.assertRaisesRegex(ValueError, "supported build matrix"):
                build_release.bundle([self.manifest(arch="x86_64")], self.output)
        self.assertFalse((self.output / "releases.tsv").exists())

    def test_unknown_target_has_no_release_row(self):
        with mock.patch.object(build_release, "source_hash", return_value="current"):
            with self.assertRaisesRegex(ValueError, "supported build matrix"):
                build_release.bundle([self.manifest(target="mediatek/filogic")], self.output)

    def test_pinned_rockchip_sdk_and_unsupported_combinations(self):
        name, sha = fetch_sdk.sdk_pin("25.12.5", "rockchip/armv8")
        self.assertIn("rockchip-armv8", name)
        self.assertEqual(sha, "59194a023968398af64bfa7d8bc3eac322641f6dc9cdbade28a4d9dd41866eba")
        with self.assertRaisesRegex(ValueError, "No pinned SDK"):
            fetch_sdk.sdk_pin("24.10.8", "rockchip/armv8")
