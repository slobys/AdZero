"""Unified AdZero entrypoint: fifteen exact firmware routes and no unsafe installs.

Every firmware package database, curl, child installer and APK executable is a
temporary fixture. No real router, network, DNS or package database is touched.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "scripts/install-auto.sh"

# Exact release channels: five regular, four ARM64, three iStoreOS 25.12 APK, three 24.10 IPK.
ROUTES = (
    ("OpenWrt", "24.10.8", "x86/64", "x86_64", "r100-test", "opkg", "regular"),
    ("OpenWrt", "25.12.5", "x86/64", "x86_64", "r100-test", "apk", "regular"),
    ("OpenWrt", "25.12.5", "rockchip/armv8", "aarch64_generic", "r100-test", "apk", "regular"),
    ("ImmortalWrt", "24.10.6", "x86/64", "x86_64", "r100-test", "opkg", "regular"),
    ("ImmortalWrt", "25.12.2", "x86/64", "x86_64", "r100-test", "apk", "regular"),
    ("OpenWrt", "25.12.6", "rockchip/armv8", "aarch64_generic", "r100-test", "apk", "arm64"),
    ("OpenWrt", "25.12.6", "mediatek/filogic", "aarch64_cortex-a53", "r100-test", "apk", "arm64"),
    ("ImmortalWrt", "25.12.2", "rockchip/armv8", "aarch64_generic", "r100-test", "apk", "arm64"),
    ("ImmortalWrt", "25.12.2", "mediatek/filogic", "aarch64_cortex-a53", "r100-test", "apk", "arm64"),
    ("iStoreOS", "25.12.6", "x86/64", "x86_64", "2026102312", "apk", "istoreos"),
    ("iStoreOS", "25.12.6", "armsr/armv8", "aarch64_generic", "2026102312", "apk", "istoreos"),
    ("iStoreOS", "25.12.6", "rockchip/armv8", "aarch64_generic", "2026102312", "apk", "istoreos"),
    ("iStoreOS", "24.10.8", "x86/64", "x86_64", "2026073111", "opkg", "istoreos24108"),
    ("iStoreOS", "24.10.8", "armsr/armv8", "aarch64_generic", "2026073111", "opkg", "istoreos24108"),
    ("iStoreOS", "24.10.8", "rockchip/armv8", "aarch64_generic", "2026073111", "opkg", "istoreos24108"),
)
BASENAMES = {
    "regular": "install.sh",
    "arm64": "install-arm64-beta.sh",
    "istoreos": "install-istoreos-beta.sh",
    "istoreos24108": "install-istoreos-24108-beta.sh",
}
PIN_VARIABLES = {
    "regular": "PIN_REGULAR", "arm64": "PIN_ARM64",
    "istoreos": "PIN_ISTOREOS", "istoreos24108": "PIN_ISTOREOS24108",
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class UnifiedInstallerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="adzero-auto-route-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.router = self.base / "router"
        (self.router / "etc").mkdir(parents=True)
        (self.router / "lib/apk/db").mkdir(parents=True)
        (self.router / "usr/lib/opkg").mkdir(parents=True)
        (self.router / "tmp/sysinfo").mkdir(parents=True)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.sources = self.base / "scripts"
        self.sources.mkdir()
        self.exec_log = self.base / "executions.txt"
        self.curl_log = self.base / "downloads.txt"
        for channel, basename in BASENAMES.items():
            # The shim logs the requested operation; it cannot install a package.
            body = (
                "#!/bin/sh\n"
                "printf '%s\\n' 'child:" + channel + ":'\"$1\" >> \"$MOCK_DISPATCH_LOG\"\n"
                "case \"$1\" in --menu|--check) exit 0 ;; *) exit 85 ;; esac\n"
                "#" + "safe-stub-" * 75 + "\n"
            )
            (self.sources / basename).write_text(body, encoding="utf-8")
        curl = self.bin / "curl"
        curl.write_text("""#!/bin/sh
out=
url=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --output) out=$2; shift 2 ;;
        https://*) url=$1; shift ;;
        *) shift ;;
    esac
done
[ -n "$url" ] && [ -n "$out" ] || exit 70
echo "$url" >> "$MOCK_CURL_LOG"
[ "$MOCK_CURL_ERROR" != 1 ] || exit 22
case "$url" in
    https://raw.githubusercontent.com/slobys/AdZero/*/scripts/install.sh) base=install.sh ;;
    https://github.com/slobys/AdZero/releases/download/v0.21.5-arm64-beta.2/install-arm64-beta.sh) base=install-arm64-beta.sh ;;
    https://github.com/slobys/AdZero/releases/download/v0.21.5-istoreos-beta.3/install-istoreos-beta.sh) base=install-istoreos-beta.sh ;;
    https://github.com/slobys/AdZero/releases/download/v0.21.5-istoreos-24108-beta.1/install-istoreos-24108-beta.sh) base=install-istoreos-24108-beta.sh ;;
    *) echo wrong-provenance >&2; exit 74 ;;
esac
cp "$MOCK_INSTALLERS/$base" "$out" || exit 73
[ "$MOCK_BAD_DIGEST" != 1 ] || echo tampered >> "$out"
""", encoding="utf-8")
        curl.chmod(0o755)
        apk = self.bin / "apk"
        apk.write_text("""#!/bin/sh
if [ "$1" = --print-arch ]; then
    printf '%s\\n' "$MOCK_APK_ARCH"
    exit 0
fi
[ "$MOCK_FORCE_APK_QUERY_FAIL" != 1 ] || exit 1
while [ "$#" -gt 0 ] && [ "$1" != info ]; do shift; done
[ "$#" -ge 3 ] && [ "$2" = -e ] || exit 88
shift 2
for package do
    grep -qx "P:$package" "$MOCK_APK_DB" || exit 1
done
exit 0
""")
        apk.chmod(0o755)
        opkg = self.bin / "opkg"
        opkg.write_text("#!/bin/sh\nexit 89\n")
        opkg.chmod(0o755)

        text = SOURCE.read_text(encoding="utf-8")
        for channel, variable in PIN_VARIABLES.items():
            pattern = rf"{variable}='[a-f0-9]{{64}}'"
            replacement = f"{variable}='{sha(self.sources/BASENAMES[channel])}'"
            text, count = re.subn(pattern, replacement, text)
            self.assertEqual(count, 1, (channel, count))
        self.script = self.base / "install-auto.sh"
        self.script.write_text(text, encoding="utf-8")

    def configure(self, row):
        fw, version, target, arch, revision, pm, _channel = row
        (self.router / "etc/openwrt_release").write_text(
            f"DISTRIB_ID='{fw}'\nDISTRIB_RELEASE='{version}'\n"
            f"DISTRIB_TARGET='{target}'\nDISTRIB_ARCH='{arch}'\n"
            f"DISTRIB_REVISION='{revision}'\n", encoding="utf-8"
        )
        opkg = self.router / "usr/lib/opkg/status"
        apk = self.router / "lib/apk/db/installed"
        opkg.unlink(missing_ok=True)
        apk.unlink(missing_ok=True)
        if pm == "opkg":
            opkg.write_text("Package: base-files\n")
        else:
            apk.write_text("P:base-files\nP:luci-base\nP:rpcd\n")
        board = self.router / "tmp/sysinfo/board_name"
        board.unlink(missing_ok=True)
        if fw == "iStoreOS" and target == "rockchip/armv8":
            board.write_text("fastrhino,r6xs\n")

    def execute(self, row, *args, success=True, additions=None):
        self.configure(row)
        additions = additions or {}
        if additions.get("MOCK_STALE_OPKG") == "1":
            (self.router / "usr/lib/opkg/status").write_text(
                "Package: stale-opkg\nVersion: 1\n"
            )
        database = self.router / "lib/apk/db/installed"
        if additions.get("MOCK_RELOCATED_APK_DB") == "1":
            relocated = self.router / "tmp/active-apk-installed"
            database.replace(relocated)
            database = relocated
        arch = row[3]
        cpu = "x86_64" if arch == "x86_64" else "aarch64"
        env = {
            **os.environ,
            "PATH": str(self.bin) + ":" + os.environ["PATH"],
            "ADZERO_AUTO_TEST_MODE": "1", "ADZERO_AUTO_TEST_ROOT": str(self.router),
            "MOCK_INSTALLERS": str(self.sources),
            "MOCK_CURL_LOG": str(self.curl_log), "MOCK_DISPATCH_LOG": str(self.exec_log),
            "MOCK_APK_ARCH": cpu,
            "MOCK_APK_DB": str(database),
            **additions,
        }
        shell = shlex.split(os.environ.get("ADZERO_TEST_SHELL", "sh"))
        process = subprocess.run(shell + [str(self.script), *args], env=env,
                                 text=True, capture_output=True, timeout=22)
        self.assertEqual(process.returncode == 0, success,
                         process.stdout + process.stderr)
        return process

    def test_all_fifteen_exact_firmware_routes_detect_without_download(self):
        for row in ROUTES:
            with self.subTest(firmware=row[0], version=row[1], target=row[2]):
                self.exec_log.unlink(missing_ok=True)
                self.curl_log.unlink(missing_ok=True)
                result = self.execute(row, "--detect")
                self.assertIn("已识别：", result.stdout)
                self.assertIn("自动选择：", result.stdout)
                self.assertIn(row[1], result.stdout)
                self.assertIn(row[2], result.stdout)
                self.assertIn('固件修订：' + row[4], result.stdout)
                if row[0] == 'iStoreOS' and row[2] == 'rockchip/armv8':
                    self.assertIn('已确认板型：fastrhino,r6xs', result.stdout)
                else:
                    self.assertNotIn('已确认板型：', result.stdout)
                self.assertFalse(self.curl_log.exists())
                self.assertFalse(self.exec_log.exists())

    def test_all_fifteen_exact_firmware_routes_open_only_correct_menu(self):
        for row in ROUTES:
            with self.subTest(firmware=row[0], version=row[1], target=row[2]):
                self.exec_log.unlink(missing_ok=True)
                self.curl_log.unlink(missing_ok=True)
                result = self.execute(row)  # no args means menu, never install
                self.assertIn("SHA-256", result.stdout)
                self.assertEqual(self.exec_log.read_text().strip(), "child:" + row[-1] + ":--menu")
                url = self.curl_log.read_text().strip()
                self.assertTrue(url.startswith("https://"))
                self.assertTrue(url.endswith("/" + BASENAMES[row[-1]]))
                self.assertEqual(len(self.curl_log.read_text().splitlines()), 1)

    def test_selected_check_only_delegates_exact_readonly_check(self):
        for index in (0, 5, 9, 12):
            row = ROUTES[index]
            with self.subTest(route=row[-1]):
                self.exec_log.unlink(missing_ok=True)
                self.execute(row, "--check")
                self.assertEqual(self.exec_log.read_text().strip(), "child:" + row[-1] + ":--check")

    def test_unknown_fw_version_target_arch_and_revision_are_blocked_before_curl(self):
        cases = (
            ("OtherWrt", "25.12.5", "x86/64", "x86_64", "r1", "apk", "regular"),
            ("OpenWrt", "25.12.6", "x86/64", "x86_64", "r1", "apk", "regular"),
            ("OpenWrt", "25.12.5", "mediatek/filogic", "aarch64_cortex-a53", "r1", "apk", "arm64"),
            ("ImmortalWrt", "25.12.2", "mediatek/filogic", "aarch64_generic", "r1", "apk", "arm64"),
            ("iStoreOS", "25.12.6", "armsr/armv8", "aarch64_generic", "2026102313", "apk", "istoreos"),
            ("iStoreOS", "24.10.7", "armsr/armv8", "aarch64_generic", "2026073111", "opkg", "istoreos24108"),
            ("iStoreOS", "24.10.8", "armsr/armv8", "aarch64_generic", "2026073112", "opkg", "istoreos24108"),
            ("iStoreOS", "24.10.8", "mediatek/filogic", "aarch64_cortex-a53", "2026073111", "opkg", "istoreos24108"),
        )
        for row in cases:
            with self.subTest(row=row):
                self.curl_log.unlink(missing_ok=True)
                self.exec_log.unlink(missing_ok=True)
                self.execute(row, "--menu", success=False)
                self.assertFalse(self.curl_log.exists())
                self.assertFalse(self.exec_log.exists())

    def test_opkg_vs_apk_ambiguous_or_actual_cpu_mismatch_refuses(self):
        row = ROUTES[5]
        self.execute(row, "--detect", success=False, additions={"MOCK_APK_ARCH": "x86_64"})
        self.assertFalse(self.curl_log.exists())
        row = ROUTES[0]
        self.configure(row)
        (self.router/"lib/apk/db/installed").write_text("P:base-files\n")
        env = {**os.environ, "PATH": str(self.bin)+":"+os.environ["PATH"],
               "ADZERO_AUTO_TEST_ROOT": str(self.router), "ADZERO_AUTO_TEST_MODE": "1"}
        result = subprocess.run(["sh", str(self.script), "--detect"], env=env,
                                text=True, capture_output=True, timeout=8)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OPKG", result.stderr)

    def test_istoreos_r6xs_wrong_or_missing_board_is_refused(self):
        row = ROUTES[-1]
        self.configure(row)
        board = self.router/"tmp/sysinfo/board_name"
        for state in ("unsupported", "missing", "symlink"):
            with self.subTest(state=state):
                board.unlink(missing_ok=True)
                if state == "unsupported":
                    board.write_text("unknown,other-rockchip\n")
                elif state == "symlink":
                    board.symlink_to(self.router/"etc/openwrt_release")
                env = {**os.environ, "PATH": str(self.bin)+":"+os.environ["PATH"],
                       "MOCK_APK_ARCH": "aarch64",
                       "ADZERO_AUTO_TEST_ROOT": str(self.router), "ADZERO_AUTO_TEST_MODE": "1"}
                result = subprocess.run(["sh", str(self.script), "--detect"], env=env,
                                        text=True, capture_output=True, timeout=8)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.curl_log.exists())

    def test_istoreos_24108_exact_opkg_and_r6xs_board_only(self):
        # Positive routing is only for the original OPKG firmware revision.
        for row in ROUTES[12:15]:
            with self.subTest(target=row[2]):
                self.exec_log.unlink(missing_ok=True)
                self.execute(row,"--menu")
                self.assertEqual(self.exec_log.read_text().strip(),
                                 "child:istoreos24108:--menu")
        # A superficially compatible 24.10 device with an APK database is
        # not allowed to use the 24.10 native IPK packages.
        row=ROUTES[12]
        self.configure(row)
        (self.router/"lib/apk/db/installed").write_text("P:base-files\n")
        env={**os.environ,
             "PATH":str(self.bin)+":"+os.environ["PATH"],
             "ADZERO_AUTO_TEST_MODE":"1",
             "ADZERO_AUTO_TEST_ROOT":str(self.router)}
        denied=subprocess.run(["sh",str(self.script),"--detect"],
                              env=env,capture_output=True,text=True,timeout=10)
        self.assertNotEqual(denied.returncode,0)
        self.assertIn("OPKG",denied.stderr)

    def test_real_apk_inventory_ignores_relocated_db_and_old_opkg_artifacts(self):
        # Reproduces the reporter's 25.12.6 launcher rejection: a live APK
        # inventory is valid even if a previous OPKG status file is left
        # behind or the installed database is mounted outside the old path.
        row = ROUTES[9]
        self.curl_log.unlink(missing_ok=True)
        result = self.execute(row, "--detect", additions={
            "MOCK_STALE_OPKG": "1",
            "MOCK_RELOCATED_APK_DB": "1",
        })
        self.assertIn("iStoreOS", result.stdout)
        self.assertIn("自动选择：", result.stdout)
        self.assertFalse(self.curl_log.exists())
        self.execute(row, "--menu", additions={
            "MOCK_STALE_OPKG": "1",
            "MOCK_RELOCATED_APK_DB": "1",
        })
        self.assertIn("child:istoreos:--menu", self.exec_log.read_text())

    def test_real_apk_query_failure_blocks_before_any_download(self):
        row = ROUTES[9]
        self.curl_log.unlink(missing_ok=True)
        self.exec_log.unlink(missing_ok=True)
        response = self.execute(row, "--detect", success=False, additions={
            "MOCK_FORCE_APK_QUERY_FAIL": "1",
        })
        self.assertIn("APK 已安装包查询失败", response.stderr)
        self.assertFalse(self.curl_log.exists())
        self.assertFalse(self.exec_log.exists())

    def test_tampered_download_and_https_failure_never_execute(self):
        row = ROUTES[9]
        for key in ("MOCK_BAD_DIGEST", "MOCK_CURL_ERROR"):
            with self.subTest(failure=key):
                self.exec_log.unlink(missing_ok=True)
                self.execute(row, "--menu", success=False, additions={key:"1"})
                self.assertFalse(self.exec_log.exists())

    def test_refuses_direct_install_or_unsafe_test_root_override(self):
        row = ROUTES[0]
        for args in (("--install",), ("--enable",), ("--menu", "--check")):
            self.execute(row, *args, success=False)
            self.assertFalse(self.curl_log.exists())
        self.configure(row)
        env = {**os.environ, "ADZERO_AUTO_TEST_ROOT": str(self.router)}
        response = subprocess.run(["sh",str(self.script),"--detect"],
                                  text=True,capture_output=True,env=env,timeout=8)
        self.assertNotEqual(response.returncode,0)

    def test_pinned_public_beta_digests_and_regular_source_match_evidence(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("eval ", source)
        self.assertNotIn("sh -c",source)
        sha_from = lambda name: re.search(rf"{name}='([a-f0-9]{{64}})'", source).group(1)
        self.assertEqual(sha_from("PIN_REGULAR"), sha(REPO/"scripts/install.sh"))
        for name,path in (
            ("PIN_ARM64", "docs/evidence/arm64-public-beta-0.21.5-beta2-assets.json"),
            ("PIN_ISTOREOS", "docs/evidence/istoreos-25.12.6/public-beta-0.21.5-beta3-assets.json"),
            ("PIN_ISTOREOS24108", "docs/evidence/istoreos-24.10.8/public-beta-0.21.5-native-build-assets.json")
        ):
            metadata = json.loads((REPO/path).read_text(encoding="utf-8"))
            self.assertEqual(sha_from(name),metadata["installer"]["sha256"])
        self.assertIn("eecd25ff3d7911530fdea1dfded5f3a399aee777", source)
        self.assertIn("curl --fail --location --proto '=https' --proto-redir '=https'",source)
        self.assertIn("sh -n \"$TMP/install-selected.sh\"",source)
        self.assertNotIn("api.github.com/repos/", source)


if __name__ == "__main__":
    unittest.main()
