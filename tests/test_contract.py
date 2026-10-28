#!/usr/bin/env python3
"""Static LuCI/OpenWrt packaging and least-privilege contract tests."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "luci-app-adzero"


class PackageContractTest(unittest.TestCase):
    def test_menu_and_view_path_match(self):
        menu = json.loads((PACKAGE / "root/usr/share/luci/menu.d/luci-app-adzero.json").read_text())
        entry = menu["admin/services/adzero"]
        self.assertEqual(entry["title"], "AdZero")
        self.assertEqual(entry["action"], {"type": "view", "path": "adzero/overview"})
        self.assertEqual(entry["depends"]["acl"], ["luci-app-adzero"])
        self.assertTrue((PACKAGE / "htdocs/luci-static/resources/view/adzero/overview.js").exists())

    def test_no_unbounded_rpc_commands(self):
        acl = json.loads((PACKAGE / "root/usr/share/rpcd/acl.d/luci-app-adzero.json").read_text())
        item = acl["luci-app-adzero"]
        allowed = {
            "/usr/libexec/adzero/ctl status",
        }
        found = set(item["read"]["file"]) | set(item["write"]["file"])
        self.assertEqual(found, allowed)
        self.assertEqual(set(item["read"]["file"]), {"/usr/libexec/adzero/ctl status"})
        self.assertNotIn("*", json.dumps(item["read"]["file"]))
        self.assertNotIn("*", json.dumps(item["write"]["file"]))
        self.assertEqual(item["read"]["uci"], ["adzero"])
        self.assertEqual(item["write"]["uci"], ["adzero"])
        self.assertEqual(item["write"]["ubus"], {"adzero": ["enable", "disable", "refresh", "reload", "routing_on", "routing_off", "allow", "temporary", "revoke", "pause", "resume", "source_add", "source_toggle", "source_remove", "source_refresh", "source_verify", "settings_apply", "verification_start", "recent_set", "stats_reset", "controls_apply", "enhance_save", "dns_test", "update_start"]})
        self.assertEqual(item["read"]["ubus"]["adzero"], ["lookup", "source_list", "job_status", "observation", "enhance_status", "update_check"])

    def test_default_is_off_and_dependencies_explicit(self):
        config = (PACKAGE / "root/etc/config/adzero").read_text()
        makefile = (PACKAGE / "Makefile").read_text()
        self.assertIn("option enabled '0'", config)
        self.assertIn("PKG_VERSION:=0.21.5", makefile)
        self.assertEqual((PACKAGE / "root/usr/share/adzero/version").read_text().strip(), "0.21.5")
        self.assertTrue((PACKAGE / "root/usr/libexec/adzero/update").exists())
        self.assertIn("+adblock-fast", makefile)
        self.assertIn("+rpcd-mod-file", makefile)
        self.assertIn("+ca-bundle", makefile)
        self.assertIn("+tcpdump ", makefile)
        self.assertNotIn("+tcpdump-mini", makefile)
        # The vendor mini package provides tcpdump; an installed full
        # tcpdump must not be replaced by a mini-only dependency.
        observer = (PACKAGE / "root/usr/libexec/adzero/observe").read_text()
        self.assertIn("command -v tcpdump", observer)
        self.assertIn("不影响广告过滤", observer)
        self.assertIn("Package/luci-app-adzero/prerm", makefile)
        self.assertIn("/usr/libexec/adzero/ctl disable", makefile)

    def test_manager_no_dns_or_firewall_rule_rewrite_command(self):
        script = (PACKAGE / "root/usr/libexec/adzero/ctl").read_text()
        self.assertIn("adblock-fast.config.force_dns 0", script)
        self.assertIn("adblock-fast.config.download_allow_insecure 0", script)
        self.assertIn("DAED_VERSION", script)
        self.assertIn("client_dns_verified", script)
        for forbidden in (
            r"^\s*(?:nft|iptables|ip6tables|ip|tc)\s+(?:add|delete|flush|replace|insert|rule)",
            r"^\s*uci\s+(?:set|commit)\s+dhcp",
            r"^\s*uci\s+(?:set|commit)\s+daed",
        ):
            self.assertNotRegex(script, re.compile(forbidden, re.MULTILINE))


if __name__ == "__main__":
    unittest.main()
