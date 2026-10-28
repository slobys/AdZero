"""Audit the isolated iStoreOS VM acceptance evidence without a router or VM."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs/evidence/istoreos-25.12.6/native-apk-virtual-machine-acceptance.json"
ORIGINAL = ROOT / "docs/evidence/v0.21.5/istoreos-original-image.json"
SHA = re.compile(r"^[a-f0-9]{64}$")


class NativeIstoreVmEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = json.loads(REPORT.read_text())
        cls.original = json.loads(ORIGINAL.read_text())

    def test_original_vendor_image_and_exact_revision(self):
        result = self.result
        self.assertEqual(result["original_image"]["sha256_gzip"],
                         self.original["sha256"])
        self.assertEqual(result["original_image"]["identity"],
                         self.original["actual_system_release"])
        self.assertEqual(result["original_image"]["identity"]["DISTRIB_ID"],
                         "iStoreOS")
        self.assertEqual(result["original_image"]["identity"]["DISTRIB_RELEASE"],
                         "25.12.6")
        self.assertEqual(result["original_image"]["identity"]["DISTRIB_REVISION"],
                         "2026102312")
        self.assertRegex(result["original_image"]["sha256_raw"], SHA)

    def test_ephemeral_no_network_install_preserves_original_full_tcpdump(self):
        report = self.result
        vm = report["isolated_vm"]
        self.assertTrue(vm["qemu_kvm"])
        self.assertTrue(vm["network_restricted"])
        self.assertTrue(vm["original_system_disk_snapshot"])
        self.assertTrue(vm["offline_package_media_ext4_readonly"])
        self.assertEqual(vm["apk_simulate_no_network_exit"], 0)
        self.assertEqual(vm["apk_install_no_network_exit"], 0)
        self.assertFalse(vm["full_tcpdump_replaced_by_solver"])
        self.assertTrue(vm["original_full_tcpdump_preserved"])
        self.assertTrue(vm["luci_app_adzero_registered"])
        self.assertTrue(vm["etc_config_adzero_present"])
        self.assertFalse(vm["adzero_enabled_by_default"])

    def test_six_vendor_deps_and_fail_closed_not_published(self):
        report = self.result
        missing = report["vendor_built_missing_packages"]
        self.assertEqual(len(missing), 6)
        self.assertEqual(set(missing), {
            "adblock-fast", "conntrack", "coreutils-timeout",
            "libnetfilter-cthelper0", "libnetfilter-cttimeout1",
            "libnetfilter-queue1",
        })
        for value in missing.values():
            self.assertRegex(value["sha256"], SHA)
            self.assertIn(value["arch"], ("x86_64", "noarch"))
            self.assertTrue(value["version"])
        self.assertEqual(report["direct_dependency_provider"], "tcpdump")
        self.assertTrue(report["mini_provides_tcpdump_in_original_vendor_source"])
        self.assertEqual(report["unresolved_dependencies"], [])
        self.assertFalse(report["publishable"])
        self.assertFalse(report["native_vendor_build"]["publishable"])
        self.assertNotEqual(report["sdk_build"], "passed")
        self.assertIn("no_real_lan_test", report["device_acceptance"])
        self.assertEqual(report["real_router_dns_nailink_ipv4_ipv6_failover_reboot_upgrade_rollback"],
                         "not_run")
        self.assertIn("unreleased", report["native_vendor_build"]["candidate_version"])


if __name__ == "__main__":
    unittest.main()
