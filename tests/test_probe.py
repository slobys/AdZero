"""Read-only capability inventory on isolated firmware/process/UCI fixtures."""
import json
import os
from pathlib import Path
import subprocess
import shlex
import tempfile
import unittest
import re

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "luci-app-adzero/root/usr/libexec/adzero/probe"
UCODE = shlex.split(os.environ.get("ADZERO_UCODE", ""))
COMMAND = r'''#!/usr/bin/env python3
import json, os, pathlib, re, sys
data = json.loads(pathlib.Path(os.environ['PROBE_FIXTURE']).read_text())
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
if name == 'uname':
    print(data.get('cpu', 'x86_64') if args == ['-m'] else '6.6.0-fixture')
elif name == 'opkg':
    assert args == ['print-architecture']
    print('arch all 1\narch noarch 1\narch generic 5\narch ' + data.get('arch', 'x86_64') + ' 10')
elif name == 'apk':
    if 'info' in args:
        if data.get('apk_inventory') and args[-1] in data['apk_inventory']:
            print(args[-1])
            raise SystemExit(0)
        raise SystemExit(1)
    assert args == ['--print-arch']
    print(data.get('arch', 'x86_64'))
else:
    raise SystemExit(90)
'''


@unittest.skipUnless(UCODE, "requires real OpenWrt ucode/UCI")
class ProbeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = self.root / "fixture.json"
        self.data = {"uci": {}, "running": []}
        self.env = {**os.environ, "ADZERO_PROBE_ROOT": str(self.root), "PROBE_FIXTURE": str(self.fixture)}
        for name in ("uname", "opkg", "apk"):
            path = self.root / "bin" / name
            path.parent.mkdir(exist_ok=True)
            path.write_text(COMMAND)
            path.chmod(0o755)
            self.env["ADZERO_" + name.upper()] = str(path)
        self.env["ADZERO_APK"] = str(self.root / "missing-apk")
        self.write("etc/openwrt_release", "DISTRIB_ID='OpenWrt'\nDISTRIB_RELEASE='24.10.8'\nDISTRIB_TARGET='x86/64'\n")
        self.write("www/luci-static/resources/view.js", "fixture")
        self.write('proc/meminfo', 'MemTotal: 3962420 kB\n')
        core = self.root / 'nailink.json'
        core.write_text('{"running":true}')
        self.env['ADZERO_PROBE_NAILINK_FIXTURE'] = str(core)

    def write(self, name, content=""):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def probe(self):
        self.fixture.write_text(json.dumps(self.data))
        # Real libuci and /proc-shaped fixtures, not per-key command mocks.
        configs = {}
        for key, value in self.data.get('uci', {}).items():
            parts = key.split('.', 2)
            config, section = parts[:2]
            anon = re.fullmatch(r'@([a-zA-Z0-9_]+)\[([0-9]+)\]', section)
            record = configs.setdefault(config, {}).setdefault(section, {
                'type': anon[1] if anon else 'fixture', 'name': None if anon else section, 'options': {}})
            if len(parts) == 2:
                record['type'] = value
            else:
                record['options'][parts[2]] = value
        for config, sections in configs.items():
            content = ''
            for record in sections.values():
                content += 'config ' + record['type'] + (' ' + json.dumps(record['name']) if record['name'] else '') + '\n'
                for name, value in record['options'].items():
                    content += ' option ' + name + ' ' + json.dumps(str(value)) + '\n'
            self.write('etc/config/' + config, content)
        for path in self.root.glob('proc/[0-9]*/comm'):
            path.unlink()
        for index, name in enumerate(self.data.get('running', [])):
            self.write(f'proc/{index+1}/comm', name + '\n')
        services = {name: {'instances': {'main': {'running': value}}} for name, value in self.data.get('services', {}).items()}
        service_file = self.root / 'services.json'
        service_file.write_text(self.data.get('services_raw', json.dumps(services)))
        self.env['ADZERO_PROBE_SERVICE_FIXTURE'] = str(service_file)
        before = {str(p): p.read_bytes() for p in (self.root / 'etc/config').glob('*')}
        result = subprocess.run(UCODE + [str(PROBE.with_suffix('.uc'))], env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({str(p): p.read_bytes() for p in (self.root / 'etc/config').glob('*')}, before, "probe must not mutate UCI")
        return json.loads(result.stdout)

    def test_firmware_cpu_and_package_matrix(self):
        for vendor in ("OpenWrt", "ImmortalWrt", "iStoreOS"):
            for version, manager, cpu, arch in (
                ("24.10.8", "opkg", "x86_64", "x86_64"),
                ("25.12.5", "apk", "aarch64", "aarch64_cortex-a53"),
                ("24.10.3", "opkg", "armv7l", "arm_cortex-a7_neon-vfpv4"),
                ("25.12.6", "apk", "mips", "mips_24kc"),
                ("24.10.8", "opkg", "mips", "mipsel_24kc"),
                ("25.12.5", "apk", "riscv64", "riscv64_riscv64"),
                ("24.10.8", "opkg", "i686", "i386_pentium4"),
                ("25.12.5", "apk", "mips64", "mips64_octeonplus"),
            ):
                with self.subTest(vendor=vendor, version=version, arch=arch):
                    self.write("etc/openwrt_release", f"DISTRIB_ID='{vendor}'\nDISTRIB_RELEASE='{version}'\n")
                    self.env["ADZERO_OPKG"] = str(self.root / ("bin/opkg" if manager == "opkg" else "missing-opkg"))
                    self.env["ADZERO_APK"] = str(self.root / ("bin/apk" if manager == "apk" else "missing-apk"))
                    self.data.update(cpu=cpu, arch=arch)
                    status = self.probe()["platform"]
                    self.assertEqual(status["family"], vendor.lower())
                    self.assertEqual(status["version"], version)
                    self.assertEqual(status["cpu"], cpu)
                    self.assertEqual(status["package_arch"], arch)
                    self.assertEqual(status["package_manager"], manager)
                    self.assertEqual(status["validation"], "not_device_tested")

    def test_package_format_follows_installed_database_not_version(self):
        self.env["ADZERO_APK"] = str(self.root / "bin/apk")
        self.write("lib/apk/db/installed")
        self.assertEqual(self.probe()["platform"]["package_manager"], "apk")
        self.write("usr/lib/opkg/status")
        self.assertEqual(self.probe()["platform"]["package_manager"], "ambiguous")

    def test_two_binaries_without_database_are_ambiguous(self):
        self.env["ADZERO_APK"] = str(self.root / "bin/apk")
        self.assertEqual(self.probe()["platform"]["package_manager"], "ambiguous")

    def test_native_apk_inventory_handles_relocated_database_and_stale_opkg(self):
        self.env['ADZERO_APK'] = str(self.root / 'bin/apk')
        self.data['apk_inventory'] = ['base-files', 'luci-base', 'rpcd']
        self.write('usr/lib/opkg/status', 'stale database')
        self.assertEqual(self.probe()['platform']['package_manager'], 'apk')

    def test_incomplete_apk_inventory_does_not_override_two_databases(self):
        self.env['ADZERO_APK'] = str(self.root / 'bin/apk')
        self.data['apk_inventory'] = ['base-files', 'luci-base']
        self.write('usr/lib/opkg/status', 'old')
        self.write('lib/apk/db/installed', 'incomplete')
        self.assertEqual(self.probe()['platform']['package_manager'], 'ambiguous')

    def test_missing_tools_are_unknown(self):
        self.env["ADZERO_OPKG"] = str(self.root / "missing-opkg")
        self.assertEqual(self.probe()["platform"]["package_manager"], "unknown")

    def test_plugins_share_core_without_false_runtime_attribution(self):
        for name in ("daed", "passwall", "passwall2", "openclash", "nikki"):
            self.write("etc/config/" + name)
        self.data["running"] = ["mihomo", "xray", "sing-box"]
        self.data["uci"] = {"openclash.config.enable": "1", "nikki.config.enabled": "0"}
        status = self.probe()
        self.assertEqual(status["cores"], ["mihomo", "xray", "sing-box"])
        for plugin in status["plugins"]:
            self.assertEqual(plugin["runtime"], "stopped" if plugin['id']=='nailink' else "unknown")
            self.assertEqual(plugin["integration"], "unverified")
        self.assertEqual(status["dns"]["proxy_count"], 5)
        self.assertEqual(status["dns"]["active_proxy_count"], 1)

    def test_exact_procd_service_and_daed_process(self):
        self.write("etc/config/openclash")
        self.write("etc/config/nikki")
        self.data["services"] = {"nikki": True, "openclash": False}
        self.data["running"] = ["daed"]
        self.data["uci"] = {"nikki.proxy.ipv4_dns_hijack": "1"}
        plugins = {p["id"]: p for p in self.probe()["plugins"]}
        self.assertEqual(plugins["nailink"]["runtime"], "running")
        self.assertEqual(plugins["nikki"]["runtime"], "running")
        self.assertEqual(plugins["nikki"]["dns_hint"], "plugin_redirect")
        self.assertEqual(plugins["openclash"]["runtime"], "stopped")

    def test_ssrplus_selection_is_not_a_boolean_enable_flag(self):
        self.data['uci']={'shadowsocksr.@global[0].global_server':'node_main'}
        self.write('proc/800/cmdline','/var/etc/ssrplus/bin/ss-redir\0-c\0/var/etc/ssrplus/tcp-only-ssr-retcp.json\0')
        plugins={p['id']:p for p in self.probe()['plugins']}
        self.assertTrue(plugins['ssrplus']['active'])
        self.assertTrue(plugins['ssrplus']['adapter_supported'])
        self.assertFalse(plugins['ssrplus']['routing_supported'])
        self.data['uci']['shadowsocksr.@global[0].global_server']='nil'
        (self.root/'proc/800/cmdline').unlink()
        self.assertFalse(next(p for p in self.probe()['plugins'] if p['id']=='ssrplus')['active'])

    def test_stopped_ssrplus_node_does_not_conflict_with_passwall(self):
        self.data['uci']={'shadowsocksr.@global[0].global_server':'saved_node','passwall.@global[0].enabled':'1'}
        self.write('proc/800/cmdline','/usr/bin/xray\0run\0-c\0/tmp/etc/passwall/acl/default/config.json\0')
        status=self.probe()
        self.assertEqual(status['dns']['active_proxies'],['passwall'])
        self.assertEqual(next(p for p in status['plugins'] if p['id']=='ssrplus')['runtime'],'stopped')

    def test_ssrplus_config_ownership_handles_symlinked_core_and_excludes_dns_tools(self):
        self.data['uci']={'shadowsocksr.@global[0].global_server':'saved_node'}
        path='proc/800/cmdline'
        self.write(path,'/usr/bin/xray\0run\0-c\0/var/etc/ssrplus/tcp-udp-ssr-retcp.json\0')
        self.assertEqual(self.probe()['dns']['active_proxies'],['ssrplus'])
        for raw in ('/var/etc/ssrplus/bin/dns2tcp\0-L\0localhost\0','/var/etc/ssrplus/bin/ssr-redir\0-c\0/var/etc/ssrplus/ssr-server1.json\0'):
            self.write(path,raw)
            self.assertEqual(self.probe()['dns']['active_proxies'],[])

    def test_homeproxy_server_or_log_cleaner_is_not_a_client(self):
        self.data['uci']={'homeproxy.config.main_node':'nil'}
        self.data['services_raw']=json.dumps({'homeproxy':{'instances':{'sing-box-s':{'running':True},'log-cleaner':{'running':True},'sing-box-c':{'running':False}}}})
        plugin=next(p for p in self.probe()['plugins'] if p['id']=='homeproxy')
        self.assertFalse(plugin['active']);self.assertEqual(plugin['runtime'],'stopped')
        self.data['services_raw']=json.dumps({'homeproxy':{'instances':{'sing-box-c':{'running':True}}}})
        self.assertTrue(next(p for p in self.probe()['plugins'] if p['id']=='homeproxy')['active'])

    def test_absent_homeproxy_client_overrides_saved_node_and_server_service(self):
        self.data['uci']={'homeproxy.config.main_node':'saved_node','passwall.@global[0].enabled':'1'}
        for services in ({},{'homeproxy':{'instances':{'sing-box-s':{'running':True},'log-cleaner':{'running':True}}}}):
            self.data['services_raw']=json.dumps(services)
            status=self.probe()
            self.assertEqual(status['dns']['active_proxies'],['passwall'])
            self.assertEqual(next(p for p in status['plugins'] if p['id']=='homeproxy')['runtime'],'stopped')
        self.data['services_raw']='{broken'
        status=self.probe()
        self.assertEqual(status['dns']['uncertain_proxies'],['homeproxy'])
        self.assertEqual(status['dns']['active_proxies'],['passwall'])

    def test_nailink_manager_stays_running_after_core_stop_selects_passwall(self):
        self.data.update(running=['daed','xray'], uci={'daed.main.enabled':'1','passwall.@global[0].enabled':'1'})
        Path(self.env['ADZERO_PROBE_NAILINK_FIXTURE']).write_text('{"running":false}')
        status = self.probe()
        plugins = {p['id']:p for p in status['plugins']}
        self.assertEqual(plugins['nailink']['manager_runtime'],'running')
        self.assertEqual(plugins['nailink']['runtime'],'stopped')
        self.assertFalse(plugins['nailink']['active'])
        self.assertEqual(status['dns']['active_proxies'],['passwall'])
        self.assertEqual(status['dns']['active_proxy_count'],1)
        self.assertEqual(status['dns']['uncertain_proxies'],[])

    def test_saved_enabled_nailink_with_no_daemon_is_stopped(self):
        self.data['uci']={'daed.main.enabled':'1','passwall.@global[0].enabled':'1'}
        status=self.probe()
        self.assertEqual(status['dns']['active_proxies'],['passwall'])

    def test_unavailable_core_query_is_unknown_instead_of_conflicting_or_stopped(self):
        self.data.update(running=['daed'],uci={'daed.main.enabled':'1','passwall.@global[0].enabled':'1'})
        for payload in ('null','{broken','{"running":"false"}'):
            Path(self.env['ADZERO_PROBE_NAILINK_FIXTURE']).write_text(payload)
            status=self.probe()
            self.assertEqual(status['dns']['uncertain_proxies'],['nailink'])
            self.assertEqual(status['dns']['active_proxies'],['passwall'])
            self.assertEqual(status['plugins'][0]['runtime'],'unknown')

    def test_two_really_active_proxies_are_named(self):
        self.data.update(running=['daed'],uci={'daed.main.enabled':'1','passwall.@global[0].enabled':'1'})
        self.assertEqual(self.probe()['dns']['active_proxies'],['nailink','passwall'])

    def test_stopped_service_overrides_saved_enabled_setting(self):
        self.data.update(services={'openclash':False,'nikki':True},uci={'openclash.config.enable':'1','nikki.config.enabled':'1'})
        self.assertEqual(self.probe()['dns']['active_proxies'],['nikki'])

    def test_private_configuration_is_not_returned(self):
        self.write("etc/config/passwall")
        self.data["uci"] = {"passwall.@global[0].enabled": "1", "passwall.node.password": "SECRET-NODE-TOKEN"}
        self.assertNotIn("SECRET-NODE-TOKEN", json.dumps(self.probe()))

    def test_shell_managed_cores_are_attributed_to_their_own_plugin(self):
        self.data['uci']={'passwall.@global[0].enabled':'1','passwall2.@global[0].enabled':'1',
                          'openclash.config.enable':'1','nikki.config.enabled':'1'}
        self.write('proc/800/cmdline','/usr/bin/xray\0run\0-c\0/tmp/etc/passwall2/acl/default/config.json\0')
        self.write('proc/801/cmdline','/usr/bin/mihomo\0-d\0/etc/nikki/run\0-f\0/etc/nikki/run/config.yaml\0')
        result=self.probe()
        self.assertEqual(result['dns']['active_proxies'],['passwall2','nikki'])
        rows={p['id']:p for p in result['plugins']}
        self.assertEqual(rows['passwall']['runtime'],'stopped')
        self.assertEqual(rows['openclash']['runtime'],'stopped')
        self.assertEqual(rows['passwall2']['runtime_evidence'],'process_config')
        self.assertNotIn('/tmp/etc',json.dumps(result))

    def test_validation_process_cannot_make_a_stopped_proxy_active(self):
        for plugin,config,command in [('openclash','openclash.config.enable','/etc/openclash/core/clash_meta\0-t\0-d\0/etc/openclash\0-f\0/etc/openclash/config.yaml\0'),
                                     ('passwall','passwall.@global[0].enabled','/usr/bin/xray\0run\0-test\0-c\0/tmp/etc/passwall/config.json\0')]:
            self.data['uci']={config:'1'}
            self.write('proc/800/cmdline',command)
            self.assertFalse(next(p for p in self.probe()['plugins'] if p['id']==plugin)['active'])

    def test_passwall_legacy_client_binaries_are_detected_by_configuration(self):
        self.data['uci']={'passwall.@global[0].enabled':'1'}
        for binary in ('v2ray','ss-redir','sslocal','trojan','hysteria2','tuic-client'):
            with self.subTest(binary=binary):
                self.write('proc/800/cmdline','/usr/bin/'+binary+'\0-c\0/tmp/etc/passwall/acl/default/client.json\0')
                plugin=next(p for p in self.probe()['plugins'] if p['id']=='passwall')
                self.assertTrue(plugin['active'])
                self.assertEqual(plugin['runtime_evidence'],'process_config')

    def test_unknown_core_owner_is_uncertain_instead_of_router_dns_fallback(self):
        self.data['uci']={'passwall.@global[0].enabled':'1'}
        self.write('proc/800/cmdline','/usr/bin/xray\0run\0-c\0/custom/new-version.json\0')
        result=self.probe()
        self.assertEqual(result['dns']['active_proxies'],[])
        self.assertEqual(result['dns']['uncertain_proxies'],['passwall'])

    def test_stopped_nailink_native_core_does_not_block_another_proxy(self):
        self.data.update(running=['daed'],uci={'daed.main.enabled':'1','passwall.@global[0].enabled':'1'})
        self.write('proc/800/cmdline','/usr/bin/xray\0run\0-c\0/tmp/etc/passwall/acl/default/client.json\0')
        for core in ('sing-box','mihomo'):
            with self.subTest(core=core):
                Path(self.env['ADZERO_PROBE_NAILINK_FIXTURE']).write_text(json.dumps({'current':core,'runtime':'stopped','running':False}))
                result=self.probe()
                nailink=next(p for p in result['plugins'] if p['id']=='nailink')
                self.assertEqual(nailink['runtime'],'stopped')
                self.assertTrue(nailink['core_checked'])
                self.assertFalse(nailink['active'])
                self.assertEqual(result['dns']['active_proxies'],['passwall'])
                self.assertEqual(result['dns']['uncertain_proxies'],[])

    def test_stopped_nailink_api_cannot_hide_an_owned_worker(self):
        self.data.update(running=['daed'],uci={'daed.main.enabled':'1'})
        for core in ('sing-box','mihomo'):
            with self.subTest(core=core):
                Path(self.env['ADZERO_PROBE_NAILINK_FIXTURE']).write_text(json.dumps({'current':core,'runtime':'stopped','running':False}))
                self.write('proc/800/cmdline','/usr/bin/'+core+'\0-c\0/var/run/daed/cores/active-123/config.json\0')
                result=self.probe()
                self.assertEqual(result['dns']['active_proxies'],[])
                self.assertEqual(result['dns']['uncertain_proxies'],['nailink'])

    def test_stopped_api_or_manager_cannot_hide_two_owned_nailink_cores(self):
        self.data['uci']={'daed.main.enabled':'1'}
        Path(self.env['ADZERO_PROBE_NAILINK_FIXTURE']).write_text(json.dumps({'current':'mihomo','runtime':'stopped','running':False}))
        self.write('proc/800/cmdline','/usr/bin/mihomo\0-f\0/var/run/daed/cores/active-123/config.json\0')
        self.write('proc/801/cmdline','/usr/bin/sing-box\0run\0-c\0/var/run/daed/cores/active-456/config.json\0')
        for manager in (True,False):
            with self.subTest(manager_running=manager):
                self.data['running']=['daed'] if manager else []
                result=self.probe()
                self.assertEqual(result['dns']['active_proxies'],[])
                self.assertEqual(result['dns']['uncertain_proxies'],['nailink'])
                nailink=next(p for p in result['plugins'] if p['id']=='nailink')
                self.assertEqual(nailink['runtime'],'unknown')
                self.assertEqual(nailink['core_status_reason'],'core_process_mismatch')

    def test_nailink_native_cores_are_not_mistaken_for_other_plugins(self):
        self.data.update(running=['daed'],services={'openclash':True},uci={'daed.main.enabled':'1','passwall2.@global[0].enabled':'1','openclash.config.enable':'1'})
        for core,args in [('sing-box','run\0-D\0/var/run/daed/cores/active-123\0-c\0/var/run/daed/cores/active-123/config.json\0'),('mihomo','-d\0/var/run/daed/cores/active-123\0-f\0/var/run/daed/cores/active-123/config.json\0')]:
            with self.subTest(core=core):
                Path(self.env['ADZERO_PROBE_NAILINK_FIXTURE']).write_text(json.dumps({'current':core,'runtime':'ready','running':True,'api_only':False,'private':'SECRET-CORE-STATUS'}))
                self.write('proc/800/cmdline','/usr/bin/'+core+'\0'+args)
                result=self.probe(); rows={p['id']:p for p in result['plugins']}
                self.assertEqual(result['dns']['active_proxies'],['nailink'])
                self.assertEqual(rows['nailink']['core'],core)
                self.assertEqual(rows['openclash']['runtime'],'stopped')
                self.assertEqual(result['dns']['uncertain_proxies'],[])
                self.assertEqual(result['dns']['active_paths'][0]['core'],core)
                self.assertNotIn('SECRET',json.dumps(result))
                self.assertNotIn('/var/run',json.dumps(result))

    def test_nailink_switching_and_api_only_are_never_reported_as_ready(self):
        self.data.update(running=['daed'],uci={'daed.main.enabled':'1'})
        self.write('proc/800/cmdline','/usr/bin/sing-box\0run\0-c\0/var/run/daed/cores/active-123/config.json\0')
        for state in ({'current':'sing-box','runtime':'switching','running':True},{'current':'sing-box','runtime':'ready','running':True,'api_only':True},{'current':'mihomo','runtime':'ready','running':True}):
            with self.subTest(state=state):
                Path(self.env['ADZERO_PROBE_NAILINK_FIXTURE']).write_text(json.dumps(state))
                result=self.probe()
                self.assertEqual(result['dns']['active_proxies'],[])
                self.assertEqual(result['dns']['uncertain_proxies'],['nailink'])

    def test_nailink_validation_and_stale_core_status_cannot_claim_a_worker(self):
        self.data.update(running=['daed'],uci={'daed.main.enabled':'1'})
        Path(self.env['ADZERO_PROBE_NAILINK_FIXTURE']).write_text('{"current":"mihomo","runtime":"ready","running":true}')
        for args in ('-t\0-d\0/var/run/daed/cores/check-123\0-f\0/var/run/daed/cores/check-123/config.json\0','-d\0/var/run/daed/cores/active-other/../check-123\0-f\0/var/run/daed/cores/check-123/config.json\0'):
            self.write('proc/800/cmdline','/usr/bin/mihomo\0'+args)
            self.assertEqual(self.probe()['dns']['active_proxies'],[])

    def test_passwall2_native_and_legacy_clients_are_detected(self):
        self.data['uci']={'passwall2.@global[0].enabled':'1'}
        for core in ('xray','sing-box','ssr-local','sslocal'):
            with self.subTest(core=core):
                self.write('proc/800/cmdline','/usr/bin/'+core+'\0run\0--config=/tmp/etc/passwall2/acl/acl_default.json\0')
                result=self.probe()
                self.assertEqual(result['dns']['active_proxies'],['passwall2'])
                self.assertEqual(next(p for p in result['plugins'] if p['id']=='passwall2')['core'],core)

    def test_log_argument_is_not_process_configuration_ownership(self):
        self.data['uci']={'passwall.@global[0].enabled':'1'}
        self.write('proc/800/cmdline','/usr/bin/xray\0run\0-c\0/custom/config.json\0--log\0/tmp/etc/passwall/log.json\0')
        self.assertEqual(self.probe()['dns']['active_proxies'],[])

    def test_ssrplus_owned_xray_does_not_make_stopped_passwall_uncertain(self):
        self.data['uci']={'shadowsocksr.@global[0].global_server':'saved_node','passwall.@global[0].enabled':'0','passwall2.@global[0].enabled':'0'}
        self.write('proc/800/cmdline','/usr/bin/xray\0run\0-config\0/var/etc/ssrplus/tcp-ssr-retcp.json\0')
        result=self.probe()
        self.assertEqual(result['dns']['active_proxies'],['ssrplus'])
        self.assertEqual(result['dns']['uncertain_proxies'],[])

    def test_nailink_protocol_helper_is_not_a_second_transparent_core(self):
        self.data.update(running=['daed'],uci={'daed.main.enabled':'1','passwall.@global[0].enabled':'0'})
        Path(self.env['ADZERO_PROBE_NAILINK_FIXTURE']).write_text('{"current":"mihomo","runtime":"ready","running":true}')
        self.write('proc/800/cmdline','/usr/bin/mihomo\0-f\0/var/run/daed/cores/active-123/config.json\0')
        self.write('proc/801/cmdline','/usr/bin/sing-box\0run\0-c\0/var/run/daed/cores/active-123/extra/config.json\0')
        result=self.probe()
        self.assertEqual(result['dns']['active_proxies'],['nailink'])
        self.assertEqual(result['dns']['uncertain_proxies'],[])

    def test_openclash_directory_does_not_match_a_similarly_named_plugin(self):
        self.data['uci']={'openclash.config.enable':'1'}
        self.write('proc/800/cmdline','/usr/bin/mihomo\0-d\0/etc/openclash-other\0-f\0/etc/openclash-other/config.yaml\0')
        result=self.probe()
        self.assertEqual(result['dns']['active_proxies'],[])
        self.assertEqual(result['dns']['uncertain_proxies'],['openclash'])

    def test_saved_flag_does_not_override_complete_stopped_process_inventory(self):
        self.data['uci']={'passwall.@global[0].enabled':'1','nikki.config.enabled':'1'}
        self.write('proc/800/cmdline','/sbin/ubusd\0')
        result=self.probe()
        self.assertEqual(result['dns']['active_proxies'],[])
        self.assertEqual(result['dns']['uncertain_proxies'],[])

    def test_release_metadata_is_not_executed_and_json_is_escaped(self):
        payload = 'iStoreOS "娴嬭瘯" \\ $(touch SHOULD_NOT_EXIST)\t'
        self.write("etc/openwrt_release", "DISTRIB_ID='" + payload + "'\nDISTRIB_RELEASE='25.12.5'\n")
        self.assertEqual(self.probe()["platform"]["distribution"], payload)
        self.assertFalse((ROOT / "SHOULD_NOT_EXIST").exists())

    def test_memory_and_multiple_dns_instances_remain_unverified(self):
        self.write("proc/meminfo", "MemTotal: 32768 kB\n")
        self.data["uci"] = {"dhcp.@dnsmasq[0]": "dnsmasq", "dhcp.@dnsmasq[0].port": "0", "dhcp.@dnsmasq[1]": "dnsmasq"}
        status = self.probe()
        self.assertEqual(status["platform"]["resource_hint"], "low_memory")
        self.assertEqual(status["dns"]["dnsmasq_instances"], 2)
        self.assertEqual(status["dns"]["disabled_instances"], 1)
        self.assertEqual(status["dns"]["client_path"], "unverified")
        self.assertEqual(status["dns"]["ipv6_path"], "unverified")

    def test_future_version_is_reported_without_guessing_support(self):
        self.write("etc/openwrt_release", "DISTRIB_ID='OpenWrt'\nDISTRIB_RELEASE='99.01.0'\n")
        self.assertEqual(self.probe()["platform"]["release_track"], "unvalidated")

    def test_os_release_fallback(self):
        (self.root / "etc/openwrt_release").unlink()
        self.write("etc/os-release", 'NAME="ImmortalWrt"\nVERSION_ID="24.10.8"\n')
        self.assertEqual(self.probe()["platform"]["family"], "immortalwrt")

    def test_native_luci_entry_point_is_detected(self):
        (self.root / "www/luci-static/resources/view.js").unlink()
        self.assertFalse(self.probe()["platform"]["modern_luci"])
        self.write("www/luci-static/resources/luci.js", "native entry point")
        self.assertTrue(self.probe()["platform"]["modern_luci"])

    def test_unavailable_service_snapshot_stays_unknown(self):
        self.write('etc/config/openclash')
        for payload in ('{broken', 'null', '[]', '{"openclash":{"instances":false}}'):
            with self.subTest(payload=payload):
                self.data['services_raw'] = payload
                plugins = {p['id']: p for p in self.probe()['plugins']}
                self.assertEqual(plugins['openclash']['runtime'], 'unknown')

    def test_large_dns_inventory_is_bounded(self):
        self.data['uci'] = {f'dhcp.@dnsmasq[{n}]': 'dnsmasq' for n in range(70)}
        status = self.probe()['dns']
        self.assertEqual(status['dnsmasq_instances'], 64)
        self.assertTrue(status['inventory_truncated'])
        self.assertEqual(status['client_path'], 'unverified')
