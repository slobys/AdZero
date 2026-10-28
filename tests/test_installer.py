"""Installer safety boundaries with fake package managers and an isolated root."""
import hashlib
import os
from pathlib import Path
import signal
import shlex
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="adzero-install-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "root"
        self.bin = self.base / "bin"
        self.release = self.base / "release"
        self.bin.mkdir()
        self.release.mkdir()
        self.log = self.base / "operations"
        self.env = {**os.environ, "PATH": str(self.bin) + ":" + os.environ["PATH"], "ADZERO_INSTALL_ROOT": str(self.root), "MOCK_INSTALL_LOG": str(self.log), "ADZERO_UPGRADE_WAIT_SECONDS": "3"}
        self.write("etc/openwrt_release", "DISTRIB_ID='OpenWrt'\nDISTRIB_RELEASE='24.10.8'\nDISTRIB_TARGET='x86/64'\n")
        self.write("etc/config/keep", "private=original\n")
        self.write("usr/lib/opkg/status", "fixture")
        self.executable(self.bin / "id", "#!/bin/sh\necho 0\n")
        self.executable(self.bin / "opkg", '#!/bin/sh\nif [ "$1" = print-architecture ]; then echo "arch x86_64 10"; else echo "opkg $1" >> "$MOCK_INSTALL_LOG"; [ "$1" != update ] || exit "${MOCK_UPDATE_FAIL:-0}"; fi\n')
        self.executable(self.root / "usr/libexec/adzero/ctl", '#!/bin/sh\necho "ctl $1" >> "$MOCK_INSTALL_LOG"\n')
        package = self.release / "adzero-test.ipk"
        package.write_bytes(b"not executed - package manager fixture")
        self.sum = hashlib.sha256(package.read_bytes()).hexdigest()
        self.row()

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target

    def executable(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o755)

    def row(self, sha=None, name="adzero-test.ipk"):
        (self.release / "releases.tsv").write_text(f"{sha or self.sum}\t{name}\topenwrt\t24.10.8\tx86/64\tx86_64\tipk\n")

    def run_installer(self, *extra, success=True):
        command = shlex.split(os.environ.get("ADZERO_TEST_SHELL", "sh"))
        result = subprocess.run(command + [str(ROOT / "scripts/install.sh"), "--local-dir", str(self.release), *extra], env=self.env, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def test_exact_matrix_checksum_backup_install_then_enable(self):
        self.run_installer()
        self.assertEqual(self.log.read_text().splitlines(), ["opkg update", "opkg install", "ctl enable"])
        self.assertTrue(list((self.root / "etc/adzero-backups").glob("*/config.tar.gz")))
        self.assertEqual((self.root / "etc/config/keep").read_text(), "private=original\n")

    def test_distribution_normalization_avoids_busybox_tr_class_bug(self):
        # Official OpenWrt 24.10.8 BusyBox tr maps "OpenWrt" to "OwenWrt"
        # when passed paired [:upper:] [:lower:] character classes. A local
        # shim intentionally reproduces that defect so ordinary sh CI also
        # verifies the portable ASCII translation boundary.
        self.executable(self.bin / "tr", """#!/bin/sh
if [ "$1" = '[:upper:]' ] && [ "$2" = '[:lower:]' ]; then
    sed 's/OpenWrt/OwenWrt/g'
else
    exec /usr/bin/tr "$@"
fi
""")
        self.run_installer("--no-enable")
        self.assertEqual(self.log.read_text().splitlines(), ["opkg update", "opkg install"])

    def test_unsupported_version_and_vendor_do_not_install(self):
        for value in ("DISTRIB_ID='ImmortalWrt'\nDISTRIB_RELEASE='24.10.8'\nDISTRIB_TARGET='x86/64'\n", "DISTRIB_ID='OpenWrt'\nDISTRIB_RELEASE='24.10.9'\nDISTRIB_TARGET='x86/64'\n"):
            self.write("etc/openwrt_release", value)
            self.run_installer(success=False)
            self.assertFalse(self.log.exists())

    def test_unsupported_derivative_reports_real_matrix_not_generic_sdk_error(self):
        for vendor in ("ImmortalWrt", "iStoreOS"):
            with self.subTest(vendor=vendor):
                self.write("etc/openwrt_release",
                           f"DISTRIB_ID='{vendor}'\nDISTRIB_RELEASE='24.10.8'\nDISTRIB_TARGET='x86/64'\n")
                result = self.run_installer(success=False)
                self.assertIn(vendor, result.stderr)
                self.assertIn("当前发行包支持", result.stderr)
                self.assertIn("openwrt 24.10.8", result.stderr)
                self.assertIn("target=x86/64", result.stderr)
                self.assertNotIn("此发行版尚未通过该固件 SDK 验证", result.stderr)
                self.assertFalse(self.log.exists())
                self.assertFalse((self.root / "etc/adzero-backups").exists())

    def test_verified_derivative_catalog_row_can_be_selected_without_name_block(self):
        # A local fixture models a future *separately verified* vendor SDK
        # artifact. The public release still has no such vendor rows.
        for vendor, canonical in (("ImmortalWrt", "immortalwrt"),
                                  ("iStoreOS", "istoreos")):
            with self.subTest(vendor=vendor):
                revision = "2026102312" if canonical == "istoreos" else ""
                self.write("etc/openwrt_release",
                           f"DISTRIB_ID='{vendor}'\nDISTRIB_RELEASE='24.10.8'\n"
                           "DISTRIB_TARGET='x86/64'\nDISTRIB_ARCH='x86_64'\n"
                           + (f"DISTRIB_REVISION='{revision}'\n" if revision else ""))
                (self.release / "releases.tsv").write_text(
                    f"{self.sum}\tadzero-test.ipk\t{canonical}\t24.10.8\tx86/64\tx86_64\tipk"
                    + (f"\t{revision}" if revision else "") + "\n")
                self.run_installer("--no-enable")
                self.assertEqual(self.log.read_text().splitlines(),
                                 ["opkg update", "opkg install"])
                self.log.unlink()

    def test_istoreos_revision_is_mandatory_and_must_match_exactly(self):
        name = "istoreos"
        expected_revision = "2026102312"
        self.write("etc/openwrt_release",
                   "DISTRIB_ID='iStoreOS'\nDISTRIB_RELEASE='25.12.6'\n"
                   "DISTRIB_TARGET='x86/64'\nDISTRIB_ARCH='x86_64'\n"
                   "DISTRIB_REVISION='2026102313'\n")
        row = f"{self.sum}\tadzero-test.ipk\t{name}\t25.12.6\tx86/64\tx86_64\tipk"
        (self.release / "releases.tsv").write_text(row + f"\t{expected_revision}\n")
        result = self.run_installer(success=False)
        self.assertIn("revision=2026102313", result.stderr)
        self.assertIn("revision=2026102312", result.stderr)
        self.assertFalse(self.log.exists())
        self.write("etc/openwrt_release",
                   "DISTRIB_ID='iStoreOS'\nDISTRIB_RELEASE='25.12.6'\n"
                   "DISTRIB_TARGET='x86/64'\nDISTRIB_ARCH='x86_64'\n"
                   "DISTRIB_REVISION='2026102312'\n")
        # A legacy 7-field catalog can never authorize a vendor-specific
        # iStoreOS firmware: a matching release number alone is insufficient.
        (self.release / "releases.tsv").write_text(row + "\n")
        self.run_installer(success=False)
        self.assertFalse(self.log.exists())
        (self.release / "releases.tsv").write_text(row + f"\t{expected_revision}\n")
        self.run_installer("--no-enable")
        self.assertEqual(self.log.read_text().splitlines(), ["opkg update", "opkg install"])

    def test_verified_future_target_requires_real_package_architecture(self):
        self.write("etc/openwrt_release",
                   "DISTRIB_ID='OpenWrt'\nDISTRIB_RELEASE='24.10.8'\n"
                   "DISTRIB_TARGET='mediatek/filogic'\nDISTRIB_ARCH='aarch64_cortex-a53'\n")
        (self.release / "releases.tsv").write_text(
            f"{self.sum}\tadzero-test.ipk\topenwrt\t24.10.8\t"
            "mediatek/filogic\taarch64_cortex-a53\tipk\n")
        self.executable(self.bin / "opkg",
                        '#!/bin/sh\nif [ "$1" = print-architecture ]; then echo "arch aarch64_cortex-a53 10"; '
                        'else echo "opkg $1" >> "$MOCK_INSTALL_LOG"; fi\n')
        self.run_installer("--no-enable")
        self.assertEqual(self.log.read_text().splitlines(),
                         ["opkg update", "opkg install"])

    def test_derivative_must_not_reuse_openwrt_catalog_by_matching_cpu(self):
        self.write("etc/openwrt_release",
                   "DISTRIB_ID='iStoreOS'\nDISTRIB_RELEASE='24.10.8'\n"
                   "DISTRIB_TARGET='x86/64'\nDISTRIB_ARCH='x86_64'\n")
        self.run_installer(success=False)
        self.assertFalse(self.log.exists())

    def test_istoreos_actual_apk_platform_needs_matching_build_revision(self):
        self.arm_apk(arch="x86_64", declared="x86_64")
        package = self.release / "adzero-test.apk"
        package.write_bytes((self.release / "adzero-test.ipk").read_bytes())
        target = ("DISTRIB_ID='iStoreOS'\nDISTRIB_RELEASE='25.12.6'\n"
                  "DISTRIB_TARGET='x86/64'\nDISTRIB_ARCH='x86_64'\n")
        (self.release / "releases.tsv").write_text(
            f"{self.sum}\tadzero-test.apk\tistoreos\t25.12.6\t"
            "x86/64\tx86_64\tapk\t2026102312\n"
        )
        for actual in ("2026102313", "", "2026102312-extra"):
            with self.subTest(actual_revision=actual):
                self.write("etc/openwrt_release",
                           target + (f"DISTRIB_REVISION='{actual}'\n" if actual else ""))
                self.run_installer("--check", success=False)
                self.assertFalse(self.log.exists())
        self.write("etc/openwrt_release",
                   target + "DISTRIB_REVISION='2026102312'\n")
        result = self.run_installer("--check")
        self.assertIn("包格式=apk", result.stdout)
        self.assertFalse(self.log.exists())
        self.run_installer("--no-enable")
        self.assertEqual(self.log.read_text().splitlines(),
                         ["apk check-local", "apk add-local"])

    def test_derivative_declared_arch_mismatch_rejected_before_install(self):
        self.write("etc/openwrt_release",
                   "DISTRIB_ID='ImmortalWrt'\nDISTRIB_RELEASE='24.10.8'\n"
                   "DISTRIB_TARGET='x86/64'\nDISTRIB_ARCH='aarch64_generic'\n")
        (self.release / "releases.tsv").write_text(
            f"{self.sum}\tadzero-test.ipk\timmortalwrt\t24.10.8\tx86/64\tx86_64\tipk\n")
        result = self.run_installer(success=False)
        self.assertIn("架构", result.stderr)
        self.assertFalse(self.log.exists())

    def test_catalog_mismatch_reports_version_before_network_modification(self):
        self.write("etc/openwrt_release",
                   "DISTRIB_ID='OpenWrt'\nDISTRIB_RELEASE='24.10.99'\nDISTRIB_TARGET='x86/64'\n")
        result = self.run_installer(success=False)
        self.assertIn("24.10.99", result.stderr)
        self.assertIn("24.10.8", result.stderr)
        self.assertFalse(self.log.exists())
        self.assertFalse((self.root / "etc/adzero-backups").exists())


    def test_corrupt_package_or_path_traversal_do_not_install(self):
        self.row(sha="0" * 64)
        self.run_installer(success=False)
        self.row(name="../../untrusted.ipk")
        self.run_installer(success=False)
        self.assertFalse(self.log.exists())

    def test_ambiguous_catalog_is_rejected(self):
        path = self.release / "releases.tsv"
        path.write_text(path.read_text() * 2)
        self.run_installer(success=False)
        self.assertFalse(self.log.exists())

    def test_existing_adapter_journal_is_not_overwritten(self):
        self.write("etc/adzero/adapter.json", "recovery-data")
        self.run_installer(success=False)
        self.assertEqual((self.root / "etc/adzero/adapter.json").read_text(), "recovery-data")
        self.assertFalse(self.log.exists())

    def test_no_enable_option(self):
        self.run_installer("--no-enable")
        self.assertNotIn("ctl enable", self.log.read_text())

    def test_other_target_is_reported_even_with_x86_package_manager_fixture(self):
        self.write("etc/openwrt_release", "DISTRIB_ID='OpenWrt'\nDISTRIB_RELEASE='25.12.5'\nDISTRIB_TARGET='mediatek/filogic'\n")
        result = self.run_installer(success=False)
        self.assertIn("target=mediatek/filogic", result.stderr)
        self.assertIn("x86/64", result.stderr)
        self.assertFalse(self.log.exists())
        self.assertFalse((self.root / "etc/adzero-backups").exists())

    def test_missing_target_is_reported_separately_without_installing(self):
        self.write("etc/openwrt_release", "DISTRIB_ID='OpenWrt'\nDISTRIB_RELEASE='25.12.5'\n")
        result = self.run_installer(success=False)
        self.assertIn("缺少 DISTRIB_TARGET", result.stderr)
        self.assertFalse(self.log.exists())
        self.assertFalse((self.root / "etc/adzero-backups").exists())

    def arm_apk(self, arch="aarch64", declared="aarch64_generic"):
        self.write("etc/openwrt_release", "DISTRIB_ID='OpenWrt'\nDISTRIB_RELEASE='25.12.5'\nDISTRIB_REVISION='r33051-f5dae5ece4'\nDISTRIB_TARGET='rockchip/armv8'\nDISTRIB_ARCH='" + declared + "'\n")
        (self.root / "usr/lib/opkg/status").unlink()
        self.write("lib/apk/db/installed", "fixture")
        self.executable(self.bin / "apk", '''#!/bin/sh
if [ "$1" = --print-arch ]; then echo "''' + arch + '''"; exit 0; fi
if [ "$1" = update ]; then echo 'apk update' >> "$MOCK_INSTALL_LOG"; exit "${MOCK_UPDATE_FAIL:-0}"; fi
simulate=0; offline=0
for arg in "$@"; do
  [ "$arg" != --simulate ] || simulate=1
  [ "$arg" != --no-network ] || offline=1
done
case "$simulate:$offline" in
  1:1) echo 'apk check-local' >> "$MOCK_INSTALL_LOG"; exit "${MOCK_APK_LOCAL_FAIL:-0}" ;;
  1:0) echo 'apk check-online' >> "$MOCK_INSTALL_LOG"; exit "${MOCK_APK_ONLINE_FAIL:-0}" ;;
  0:1) echo 'apk add-local' >> "$MOCK_INSTALL_LOG" ;;
  *) echo "apk $1" >> "$MOCK_INSTALL_LOG" ;;
esac
exit "${MOCK_APK_INSTALL_FAIL:-0}"
''')
        package = self.release / "arm-test.apk"
        package.write_bytes(b"ARM SDK package fixture, not executed")
        sha = hashlib.sha256(package.read_bytes()).hexdigest()
        (self.release / "releases.tsv").write_text(f"{sha}\tarm-test.apk\topenwrt\t25.12.5\trockchip/armv8\taarch64_generic\tapk\n")

    def test_user_rockchip_metadata_selects_arm_apk_and_enables(self):
        self.arm_apk()
        result = self.run_installer()
        self.assertIn("arm-test.apk", result.stdout)
        self.assertEqual(self.log.read_text().splitlines(), ["apk check-local", "apk add-local", "ctl enable"])

    def crlf_catalog(self):
        path = self.release / 'releases.tsv'
        path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))

    def test_crlf_catalog_selects_exact_ipk(self):
        self.crlf_catalog()
        self.run_installer('--no-enable')
        self.assertEqual(self.log.read_text().splitlines(), ['opkg update', 'opkg install'])

    def test_crlf_catalog_selects_user_rockchip_apk_and_upgrades(self):
        self.arm_apk()
        self.crlf_catalog()
        self.upgrade_fixture()
        result = self.run_installer('--upgrade', '--no-enable')
        self.assertIn('arm-test.apk', result.stdout)
        self.assertEqual(self.log.read_text().splitlines(), ['apk check-local', 'ctl disable', 'apk add-local'])

    def test_crlf_duplicates_still_stop_before_restore_and_install(self):
        self.arm_apk()
        self.crlf_catalog()
        path = self.release / 'releases.tsv'
        path.write_bytes(path.read_bytes() * 2)
        journal = self.upgrade_fixture()
        result = self.run_installer('--upgrade', success=False)
        self.assertIn('target=rockchip/armv8', result.stderr)
        self.assertTrue(journal.exists())
        self.assertFalse(self.log.exists())
        self.assertFalse((self.root / 'etc/adzero-backups').exists())

    def test_apk_repository_arch_spelling_is_accepted_for_exact_target(self):
        self.arm_apk(arch="aarch64_generic")
        self.run_installer("--no-enable")
        self.assertEqual(self.log.read_text().splitlines(), ["apk check-local", "apk add-local"])

    def test_rockchip_mismatched_apk_cpu_is_rejected_before_install(self):
        self.arm_apk(arch="x86_64")
        self.run_installer(success=False)
        self.assertFalse(self.log.exists())

    def test_apk_local_upgrade_ignores_broken_repository_and_keeps_flags_for_install(self):
        self.arm_apk()
        self.upgrade_fixture()
        self.env['MOCK_UPDATE_FAIL']='1'
        result=self.run_installer('--upgrade')
        self.assertIn('无需刷新软件源',result.stdout)
        self.assertEqual(self.log.read_text().splitlines(),['apk check-local','ctl disable','apk add-local','ctl enable'])

    def test_apk_missing_dependency_repository_error_preserves_old_protection(self):
        self.arm_apk()
        journal=self.upgrade_fixture()
        self.env.update(MOCK_APK_LOCAL_FAIL='1',MOCK_UPDATE_FAIL='1')
        result=self.run_installer('--upgrade',success=False)
        self.assertIn('尚未关闭旧版保护',result.stderr)
        self.assertEqual(self.log.read_text().splitlines(),['apk check-local','apk update'])
        self.assertEqual(journal.read_text(),'private-recovery-data')
        self.assertFalse((self.root/'etc/adzero-backups').exists())
        self.assertFalse((self.root/'tmp/adzero-install.lock').exists())

    def test_apk_missing_dependency_checks_online_before_disable(self):
        self.arm_apk()
        self.upgrade_fixture()
        self.env['MOCK_APK_LOCAL_FAIL']='1'
        self.run_installer('--upgrade')
        self.assertEqual(self.log.read_text().splitlines(),['apk check-local','apk update','apk check-online','ctl disable','apk add','ctl enable'])

    def test_apk_online_preflight_failure_does_not_disable_or_install(self):
        self.arm_apk()
        journal=self.upgrade_fixture()
        self.env.update(MOCK_APK_LOCAL_FAIL='1',MOCK_APK_ONLINE_FAIL='1')
        self.run_installer('--upgrade',success=False)
        self.assertTrue(journal.exists())
        self.assertEqual(self.log.read_text().splitlines(),['apk check-local','apk update','apk check-online'])

    def test_apk_commit_failure_retains_backup_without_claiming_enable_or_rollback(self):
        self.arm_apk()
        self.upgrade_fixture()
        self.env['MOCK_APK_INSTALL_FAIL']='1'
        result=self.run_installer('--upgrade',success=False)
        self.assertIn('核对软件包状态',result.stderr)
        self.assertEqual(self.log.read_text().splitlines(),['apk check-local','ctl disable','apk add-local'])
        self.assertTrue(list((self.root/'etc/adzero-backups').glob('*/state.tar.gz')))

    def test_opkg_repository_error_preserves_old_protection(self):
        journal=self.upgrade_fixture()
        self.env['MOCK_UPDATE_FAIL']='1'
        self.run_installer('--upgrade',success=False)
        self.assertTrue(journal.exists())
        self.assertEqual(self.log.read_text().splitlines(),['opkg update'])

    def test_rockchip_mismatched_declared_abi_is_rejected(self):
        self.arm_apk(declared="aarch64_cortex-a53")
        self.run_installer(success=False)
        self.assertFalse(self.log.exists())

    def test_rockchip_cannot_select_x86_or_other_release_catalog(self):
        self.arm_apk()
        (self.release / "releases.tsv").write_text(f"{self.sum}\tadzero-test.ipk\topenwrt\t25.12.5\tx86/64\tx86_64\tapk\n")
        self.run_installer(success=False)
        self.assertFalse(self.log.exists())

    def upgrade_fixture(self, mode="success"):
        journal = self.write("etc/adzero/adapter.json", "private-recovery-data")
        self.env.update(MOCK_UPGRADE_MODE=mode, MOCK_UPGRADE_JOURNAL=str(journal), MOCK_UPGRADE_BUSY=str(self.base / "busy"))
        self.executable(self.bin / "sleep", '#!/bin/sh\necho "wait $1" >> "$MOCK_INSTALL_LOG"\nrm -f "$MOCK_UPGRADE_BUSY"\n')
        self.executable(self.root / "usr/libexec/adzero/ctl", '''#!/bin/sh
echo "ctl $1" >> "$MOCK_INSTALL_LOG"
if [ "$1" = disable ]; then
    if [ "$MOCK_UPGRADE_MODE" = busy_forever ] || [ -e "$MOCK_UPGRADE_BUSY" ]; then
        if [ "$MOCK_UPGRADE_MODE" = modern_busy ]; then
            [ "$ADZERO_LOCK_WAIT_SECONDS" = 0 ] || exit 3
            echo 'AdZero: 等待操作锁超时；可能仍有操作或遗留锁。请检查进程后重试，勿直接删除锁。' >&2
        else
            echo 'AdZero: 另一项操作正在进行；若异常断电可检查临时锁。' >&2
        fi
        exit 1
    fi
    if [ "$MOCK_UPGRADE_MODE" = restore_failure ]; then echo '恢复失败，保留记录' >&2; exit 1; fi
    if [ "$MOCK_UPGRADE_MODE" != false_success ]; then rm -f "$MOCK_UPGRADE_JOURNAL"; fi
fi
''')
        return journal

    def test_upgrade_verifies_package_and_backs_up_state_before_restore(self):
        import tarfile
        self.upgrade_fixture()
        self.run_installer("--upgrade")
        self.assertEqual(self.log.read_text().splitlines(), ["opkg update", "ctl disable", "opkg install", "ctl enable"])
        backups = list((self.root / "etc/adzero-backups").glob("*/state.tar.gz"))
        self.assertEqual(len(backups), 1)
        with tarfile.open(backups[0]) as archive:
            self.assertEqual(archive.extractfile("adzero/adapter.json").read(), b"private-recovery-data")
        self.assertEqual(backups[0].stat().st_mode & 0o077, 0)

    def test_upgrade_bad_checksum_does_not_disable_current_protection(self):
        journal = self.upgrade_fixture()
        self.row(sha="0" * 64)
        self.run_installer("--upgrade", success=False)
        self.assertTrue(journal.exists())
        self.assertFalse(self.log.exists())

    def test_upgrade_retries_only_legacy_busy_then_installs(self):
        self.upgrade_fixture()
        Path(self.env["MOCK_UPGRADE_BUSY"]).touch()
        result = self.run_installer("--upgrade")
        self.assertIn("最多 3 秒", result.stdout)
        self.assertEqual(self.log.read_text().splitlines(), ["opkg update", "ctl disable", "wait 1", "ctl disable", "opkg install", "ctl enable"])

    def test_upgrade_retries_modern_zero_wait_busy_without_nested_30_second_wait(self):
        self.upgrade_fixture("modern_busy")
        Path(self.env["MOCK_UPGRADE_BUSY"]).touch()
        self.run_installer("--upgrade")
        self.assertEqual(self.log.read_text().splitlines(), ["opkg update", "ctl disable", "wait 1", "ctl disable", "opkg install", "ctl enable"])

    def test_upgrade_persistent_busy_preserves_lock_and_recovery(self):
        journal = self.upgrade_fixture("busy_forever")
        lock = self.root / "tmp/adzero.lock"
        lock.mkdir(parents=True)
        result = self.run_installer("--upgrade", success=False)
        self.assertIn("没有删除锁或安装新包", result.stderr)
        self.assertTrue(journal.exists())
        self.assertTrue(lock.is_dir())
        operations = self.log.read_text().splitlines()
        self.assertEqual(operations.count("ctl disable"), 0)
        self.assertFalse(any(x.startswith("opkg") for x in operations))

    def test_upgrade_restore_failure_is_not_retried_or_installed(self):
        journal = self.upgrade_fixture("restore_failure")
        self.run_installer("--upgrade", success=False)
        self.assertTrue(journal.exists())
        self.assertEqual(self.log.read_text().splitlines(), ["opkg update", "ctl disable"])

    def test_upgrade_exit_zero_with_remaining_journal_is_rejected(self):
        journal = self.upgrade_fixture("false_success")
        self.run_installer("--upgrade", success=False)
        self.assertTrue(journal.exists())
        self.assertEqual(self.log.read_text().splitlines(), ["opkg update", "ctl disable"])

    def test_upgrade_no_enable_leaves_protection_off(self):
        self.upgrade_fixture()
        self.run_installer("--upgrade", "--no-enable")
        self.assertEqual(self.log.read_text().splitlines(), ["opkg update", "ctl disable", "opkg install"])

    def test_second_installer_does_not_remove_first_installer_lock(self):
        lock = self.root / "tmp/adzero-install.lock"
        lock.mkdir(parents=True)
        self.run_installer("--upgrade", success=False)
        self.assertTrue(lock.is_dir())
        self.assertFalse(self.log.exists())

    def live_watcher(self, hold=False, args=()):
        """Real Linux PIDs/signals and child work, with no router service calls."""
        self.env["MOCK_WATCH_BASE"] = str(self.base)
        self.env["ADZERO_UPGRADE_WAIT_SECONDS"] = "5"
        self.upgrade_fixture()
        (self.root / "tmp").mkdir(exist_ok=True)
        (self.bin / "sleep").unlink()  # Real sleep for lifecycle/signal tests.
        if hold:
            self.executable(self.root / "usr/libexec/adzero/worker", '''#!/bin/sh
mkdir "$ADZERO_INSTALL_ROOT/tmp/adzero.lock" || exit 1
echo "$$ reconcile" > "$ADZERO_INSTALL_ROOT/tmp/adzero.lock/owner"
touch "$MOCK_WATCH_BASE/started"
/bin/sleep 1.2
echo finished-before-backup > "$MOCK_UPGRADE_JOURNAL"
rm "$ADZERO_INSTALL_ROOT/tmp/adzero.lock/owner"
rmdir "$ADZERO_INSTALL_ROOT/tmp/adzero.lock"
touch "$MOCK_WATCH_BASE/finished"
''')
        watch = self.root / "usr/libexec/adzero/watch"
        self.executable(watch, '''#!/bin/sh
if [ -x "$ADZERO_INSTALL_ROOT/usr/libexec/adzero/worker" ]; then
    "$ADZERO_INSTALL_ROOT/usr/libexec/adzero/worker"
fi
touch "$MOCK_WATCH_BASE/idle"
while :; do /bin/sleep 0.05; done
''')
        process = subprocess.Popen(["/bin/sh", str(watch), *args], env=self.env, start_new_session=True)
        def stop():
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGCONT)
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        self.addCleanup(stop)
        ready = self.base / ("started" if hold else "idle")
        self.wait_for(lambda: ready.exists())
        return process

    def wait_for(self, condition):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            if condition():
                return
            time.sleep(0.02)
        self.fail("Lifecycle condition did not complete")

    def process_state(self, process):
        return Path(f"/proc/{process.pid}/stat").read_text().rsplit(") ", 1)[1].split()[0]

    def test_live_scheduler_paused_child_finishes_and_backup_is_after_drain(self):
        import tarfile
        process = self.live_watcher(hold=True)
        result = self.run_installer("--upgrade")
        self.assertIn("已暂停后台调度", result.stdout)
        self.assertTrue((self.base / "finished").exists())
        self.assertIsNone(process.poll())
        self.wait_for(lambda: self.process_state(process) != "T")
        backup = next((self.root / "etc/adzero-backups").glob("*/state.tar.gz"))
        with tarfile.open(backup) as archive:
            self.assertEqual(archive.extractfile("adzero/adapter.json").read(), b"finished-before-backup\n")
        self.assertFalse((self.root / "tmp/adzero-install.lock").exists())

    def test_live_scheduler_resumes_when_existing_lock_never_drains(self):
        process = self.live_watcher()
        self.env["ADZERO_UPGRADE_WAIT_SECONDS"] = "1"
        lock = self.root / "tmp/adzero.lock"
        lock.mkdir()
        (lock / "owner").write_text("999999 reconcile\n")
        self.run_installer("--upgrade", success=False)
        self.assertEqual((lock / "owner").read_text(), "999999 reconcile\n")
        self.wait_for(lambda: self.process_state(process) != "T")
        self.assertFalse(self.log.exists())

    def test_live_scheduler_resumes_after_real_restore_failure(self):
        process = self.live_watcher()
        self.env["MOCK_UPGRADE_MODE"] = "restore_failure"
        self.run_installer("--upgrade", success=False)
        self.wait_for(lambda: self.process_state(process) != "T")
        self.assertEqual(self.log.read_text().splitlines(), ["opkg update", "ctl disable"])
        self.assertTrue((self.root / "etc/adzero/adapter.json").exists())

    def test_live_scheduler_resumes_after_preflight_repository_failure(self):
        process=self.live_watcher()
        self.env['MOCK_UPDATE_FAIL']='1'
        self.run_installer('--upgrade',success=False)
        self.wait_for(lambda:self.process_state(process)!='T')
        self.assertTrue((self.root/'etc/adzero/adapter.json').exists())
        self.assertFalse((self.root/'tmp/adzero-install.lock').exists())
        self.assertEqual(self.log.read_text().splitlines(),['opkg update'])

    def test_previously_stopped_scheduler_is_not_resumed(self):
        process = self.live_watcher()
        os.kill(process.pid, signal.SIGSTOP)
        self.wait_for(lambda: self.process_state(process) == "T")
        self.run_installer("--upgrade")
        self.assertEqual(self.process_state(process), "T")

    def test_scheduler_exits_before_cleanup_without_missing_proc_warning(self):
        process = self.live_watcher()
        lock = self.root / "tmp/adzero.lock"
        lock.mkdir()
        command = shlex.split(os.environ.get("ADZERO_TEST_SHELL", "sh"))
        output_path = self.base / "installer-output"
        output_file = output_path.open("w")
        self.addCleanup(output_file.close)
        installer = subprocess.Popen(command + [str(ROOT / "scripts/install.sh"), "--local-dir", str(self.release), "--upgrade"], env=self.env, stdout=output_file, stderr=subprocess.PIPE, text=True)
        try:
            # Wait for the installer's post-STOP identity check to finish.
            # Observing T alone can race that check and test an earlier exit.
            self.wait_for(lambda: "已暂停后台调度" in output_path.read_text())
            self.assertEqual(self.process_state(process), "T")
            os.kill(process.pid, signal.SIGTERM)
            os.kill(process.pid, signal.SIGCONT)
            process.wait(timeout=3)
            lock.rmdir()
            _, error = installer.communicate(timeout=10)
            output = output_path.read_text()
            self.assertEqual(installer.returncode, 0, output + error)
            self.assertNotIn("/proc/", error)
            self.assertNotIn("can't open", error)
        finally:
            if installer.poll() is None:
                installer.kill()
                installer.communicate(timeout=3)

    def test_similar_command_with_extra_argument_is_not_signalled(self):
        process = self.live_watcher(args=("unrelated",))
        result = self.run_installer("--upgrade")
        self.assertNotIn("已暂停后台调度", result.stdout)
        self.assertNotEqual(self.process_state(process), "T")

    def test_signal_interrupt_resumes_scheduler_preserves_recovery_and_lock(self):
        process = self.live_watcher()
        lock = self.root / "tmp/adzero.lock"
        lock.mkdir()
        self.env["ADZERO_UPGRADE_WAIT_SECONDS"] = "10"
        command = shlex.split(os.environ.get("ADZERO_TEST_SHELL", "sh"))
        installer = subprocess.Popen(command + [str(ROOT / "scripts/install.sh"), "--local-dir", str(self.release), "--upgrade"], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.wait_for(lambda: self.process_state(process) == "T")
            installer.send_signal(signal.SIGTERM)
            installer.communicate(timeout=5)
            self.assertEqual(installer.returncode, 130)
            self.wait_for(lambda: self.process_state(process) != "T")
            self.assertTrue(lock.is_dir())
            self.assertTrue((self.root / "etc/adzero/adapter.json").exists())
            self.assertFalse((self.root / "tmp/adzero-install.lock").exists())
            self.assertFalse(self.log.exists())
        finally:
            if installer.poll() is None:
                installer.kill()
                installer.communicate(timeout=3)
