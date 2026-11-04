"""Real ucode policy compilation; service, router and packet checks are separate."""
import base64
import ipaddress
import json
import os
import shutil
from pathlib import Path
import shlex
import subprocess
import unittest
import test_adapters as adapters
import test_controls as controls

ROOT=Path(__file__).resolve().parents[1]
UCODE=shlex.split(os.environ.get('ADZERO_UCODE',''))

@unittest.skipUnless(UCODE,'requires actual OpenWrt ucode')
class RoutingCompilerTest(unittest.TestCase):
    def compile(self,operation,*args,ok=True):
        p=subprocess.run(UCODE+[str(ROOT/'scripts/routing_transform.uc')],input=json.dumps({'operation':operation,'arguments':args}),text=True,capture_output=True)
        self.assertEqual(p.returncode==0,ok,p.stdout+p.stderr)
        return json.loads(p.stdout) if ok else None

    def test_domain_source_extracts_only_domains_not_the_supplied_server(self):
        self.assertEqual(self.compile('domain_list','# source\nserver=/example.cn/114.114.114.114\nserver=/cn/114.114.114.114\nserver=/example.cn/114.114.114.114\n'),['cn','example.cn'])
        for raw in ('server=/bad;command.cn/114.114.114.114','<html>','server=/bad..cn/1.1.1.1','server=/123/1.1.1.1',''):
            self.compile('domain_list',raw,ok=False)

    def test_cidr_v4_v6_and_network_boundaries(self):
        raw='223.5.5.0/24\n2400:3200::/32\n::1/128\n'
        self.assertEqual(self.compile('cidr_list',raw),sorted(raw.splitlines()))
        for invalid in ('0.0.0.0/0','256.0.0.0/8','223.5.5.1/24','2400::1/32','1::2::/48','::/129','::gg/64','1.2.3/24'):
            self.compile('cidr_list',invalid,ok=False)

    def test_geoip_protobuf_contains_ipv4_and_ipv6(self):
        blob=base64.b64decode(self.compile('geoip',['223.5.5.0/24','2400:3200::/32']))
        self.assertIn(b'ADZERO',blob)
        for addr in ('223.5.5.0','2400:3200::'):self.assertIn(ipaddress.ip_address(addr).packed,blob)

    def test_passwall_specific_domestic_rules_cannot_escape_parent_blockers(self):
        self.assertEqual(self.compile('expand_blockers',['ads.example'],['sub.ads.example','deep.sub.ads.example','notads.example','www.cn.example']),['ads.example','deep.sub.ads.example','sub.ads.example'])

    def test_dae_dns_preserves_existing_exceptions_and_adds_both_groups(self):
        raw='upstream { local: "udp://127.0.0.1:5353" }\nrouting { request { qname(user.example) -> local\n fallback: local } response { fallback: accept } }'
        result=self.compile('dae_dns',raw)
        self.assertIn('qname(user.example) -> local',result)
        self.assertIn('adzero_global: "tcp://1.1.1.1:53"',result)
        self.assertIn('upstream(adzero_cn, adzero_global) -> accept',result)
        self.assertLess(result.index('qname(user.example)'),result.index('qname(ext:'))
        self.assertEqual(self.compile('dae_dns',result),result)
        self.compile('dae_dns','routing { request { fallback: asis } }',ok=False)

    def test_dae_routes_preserve_explicit_rules_and_force_dns_transport(self):
        raw='domain(full: user.example) -> direct\nfallback: proxy\n'
        result=self.compile('dae_routing',raw,'proxy')
        self.assertIn('dip(1.1.1.1) && dport(53) -> proxy',result)
        self.assertIn('dip(ext:"adzero-cn-geoip.dat:adzero") -> direct',result)
        self.assertLess(result.index('user.example'),result.index('domain(ext:'))
        self.assertLess(result.index('domain(ext:'),result.index('fallback:'))
        for target in ('DIRECT','direct','',None):
            if target=='DIRECT':continue
            self.compile('dae_routing',raw,target,ok=False)

    def test_dae_dns_accepts_official_request_only_profile(self):
        raw='upstream { local: "udp://223.5.5.5:53" }\nrouting { request { fallback: local } }'
        result=self.compile('dae_dns',raw)
        self.assertIn('adzero_global',result)
        self.assertNotIn('response {',result)
        self.assertEqual(self.compile('dae_dns',result),result)
        self.compile('dae_dns',raw[:-1]+'response { fallback: accept } response { fallback: accept } }',ok=False)

    def test_mihomo_policy_and_actual_connection_rules_share_the_target(self):
        original={'mode':'global','dns':{'enable':True,'nameserver':['8.8.8.8'],'fallback':['9.9.9.9'],'nameserver-policy':{'+.user.example':['192.168.1.1']},'fake-ip-filter':['+.lan']},'proxy-groups':[{'name':'My Proxy','type':'select','proxies':['node']}],'rules':['DOMAIN,user.example,DIRECT','MATCH,My Proxy']}
        result=self.compile('mihomo',original,'/tmp/cn-domains.txt','/tmp/cn-ips.txt')
        self.assertEqual(result['target'],'My Proxy')
        config=result['config'];self.assertEqual(config['mode'],'rule')
        self.assertEqual(config['rules'][0],'DOMAIN,user.example,DIRECT')
        self.assertEqual(config['rules'][-1],'MATCH,My Proxy')
        self.assertIn('RULE-SET,adzero-cn-domain,DIRECT',config['rules'])
        self.assertEqual(config['dns']['nameserver'],['tcp://1.1.1.1:53#My Proxy'])
        self.assertEqual(config['dns']['direct-nameserver'],['223.5.5.5'])
        self.assertNotIn('fallback',config['dns'])
        self.assertEqual(config['dns']['nameserver-policy']['+.user.example'],['192.168.1.1'])
        self.assertIn('+.lan',config['dns']['fake-ip-filter'])
        self.assertEqual(self.compile('mihomo',config,'/tmp/cn-domains.txt','/tmp/cn-ips.txt')['config'],config)

    def test_mihomo_does_not_guess_an_absent_or_direct_proxy_target(self):
        for target in ('DIRECT','REJECT','missing'):
            self.compile('mihomo',{'dns':{'enable':True},'rules':['MATCH,'+target]},'/tmp/cn.txt','/tmp/ip.txt',ok=False)

@unittest.skipUnless(UCODE,'requires actual OpenWrt ucode')
class RoutingAdapterTest(unittest.TestCase):
    setUp=adapters.AdapterTransactionTest.setUp
    write=adapters.AdapterTransactionTest.write
    run_adapter=adapters.AdapterTransactionTest.run_adapter
    def select(self,*plugins):
        adapters.AdapterTransactionTest.select(self,*plugins)
        self.write('etc/config/adzero',"config adzero 'main'\n option enabled '1'\n option routing_enabled '1'\n")
        self.write('etc/config/dhcp',"config dnsmasq 'main'\n list server '8.8.8.8'\n list server '/lan/192.168.1.1'\n")
        fixture=self.write('routing-fixture.json',json.dumps({'domain_raw':'server=/cn.example/114.114.114.114\n','cidr_raw':'223.5.5.0/24\n2400:3200::/32\n'}))
        self.env['ADZERO_ROUTING_DATA_FIXTURE']=str(fixture)
        self.env['ADZERO_ROUTING_DATA_PROGRAM']=str(ROOT/'luci-app-adzero/root/usr/libexec/adzero/routing-data')
        self.env['ADZERO_UCODE']=shlex.join(UCODE)
        self.write('etc/init.d/dnsmasq','#!/bin/sh\nexit 0\n')
        self.write('etc/config/passwall',"config global 'main'\n option enabled '1'\n option node 'chosen'\n option dns_mode 'xray'\n option chn_list '0'\nconfig nodes 'chosen'\n option type 'Xray'\n")
        self.write('usr/share/passwall/app.sh','# direct_dns_mode USE_DEFAULT_DNS\n')
        self.write('usr/share/passwall/rules/chnlist','cn.example\n')
        self.write('usr/share/passwall/rules/chnroute','223.5.5.0/24\n')

    def custom_dns(self):
        profile={'domestic':{'servers':['119.29.29.29'],'active':'119.29.29.29'},'foreign':{'servers':['8.8.8.8'],'active':'8.8.8.8'}}
        p=self.root/'etc/config/adzero';p.write_text(p.read_text()+" option dns_profile '"+json.dumps(profile)+"'\n")

    def candidate_dns(self,strict=False):
        self.select('passwall')
        old={'domestic':{'servers':['223.5.5.5','119.29.29.29'],'active':'223.5.5.5','mode':'parallel'},'foreign':{'servers':['1.1.1.1','8.8.8.8'],'active':'1.1.1.1','mode':'parallel'}}
        if strict:old.update(strict=True,proxy_port=1070)
        config=self.root/'etc/config/adzero';config.write_text(config.read_text()+" option dns_profile '"+json.dumps(old)+"'\n")
        candidate=json.loads(json.dumps(old));candidate['foreign'].pop('mode');candidate.pop('strict',None)
        return config,candidate

    def test_routing_preflight_uses_repaired_candidate_after_dns_preflight(self):
        config,candidate=self.candidate_dns();before=config.read_bytes()
        plan=self.write('candidate.json',json.dumps({'routing_enabled':True,'dns':candidate}))
        self.run_adapter('dns-check',str(plan))
        self.assertEqual(self.run_adapter('routing-check','1',str(plan)).stdout.strip(),'full')
        self.assertEqual(config.read_bytes(),before)
        self.assertFalse((self.root/'etc/adzero/adapter.json').exists())

    def test_candidate_preflight_still_rejects_foreign_parallel_xray(self):
        config,candidate=self.candidate_dns();before=config.read_bytes();candidate['foreign']['mode']='parallel'
        plan=self.write('candidate.json',json.dumps({'routing_enabled':True,'dns':candidate}))
        result=self.run_adapter('routing-check','1',str(plan),success=False)
        self.assertIn('PassWall',result.stderr);self.assertEqual(config.read_bytes(),before)

    def test_candidate_routing_target_must_match_the_plan(self):
        _,candidate=self.candidate_dns()
        plan=self.write('candidate.json',json.dumps({'routing_enabled':False,'dns':candidate}))
        result=self.run_adapter('routing-check','1',str(plan),success=False)
        self.assertIn('目标',result.stderr)

    def test_candidate_can_disable_strict_and_routing_together(self):
        config,candidate=self.candidate_dns(True);before=config.read_bytes()
        plan=self.write('candidate.json',json.dumps({'routing_enabled':False,'dns':candidate}))
        self.assertEqual(self.run_adapter('routing-check','0',str(plan)).stdout.strip(),'full')
        self.assertEqual(config.read_bytes(),before)

    def test_direct_dns_restart_does_not_inherit_blocked_sigchld(self):
        self.select()
        mask=self.root/'restart-mask'
        self.write('etc/init.d/dnsmasq','#!/bin/sh\ngrep "^SigBlk:" /proc/self/status >> '+shlex.quote(str(mask))+'\n')
        self.run_adapter('apply')
        self.run_adapter('remove')
        values=[int(line.split()[1],16) for line in mask.read_text().splitlines()]
        self.assertEqual(len(values),2)
        self.assertTrue(all(value & 0x10000 == 0 for value in values),values)

    def test_no_proxy_uses_only_domestic_defaults_and_restores_original_dns(self):
        self.select()
        before=(self.root/'etc/config/dhcp').read_text()
        self.run_adapter('apply');self.run_adapter('apply')
        text=(self.root/'etc/config/dhcp').read_text()
        self.assertNotIn('8.8.8.8',text);self.assertIn('223.5.5.5',text);self.assertIn('/lan/192.168.1.1',text)
        self.assertEqual(json.loads(self.run_adapter('status').stdout)['routing']['mode'],'direct')
        self.run_adapter('remove')
        after=(self.root/'etc/config/dhcp').read_text()
        self.assertIn('8.8.8.8',after);self.assertNotIn('223.5.5.5',after);self.assertNotIn('noresolv',after)

    def test_direct_mode_rejects_multiple_dnsmasq_instances_without_mutating(self):
        self.select();p=self.root/'etc/config/dhcp';p.write_text(p.read_text()+"config dnsmasq 'other'\n")
        original=p.read_text();self.run_adapter('apply',success=False);self.assertEqual(p.read_text(),original)

    def test_passwall_updates_dns_and_connection_flags_without_replacing_node(self):
        self.run_adapter('apply')
        after=(self.root/'etc/config/passwall').read_text()
        for expected in ("option chn_list 'direct'","option use_default_dns 'remote'","option direct_dns '223.5.5.5:53'","option remote_dns '1.1.1.1:53'","option node 'chosen'"):self.assertIn(expected,after)
        self.run_adapter('remove');self.custom_dns();self.run_adapter('apply')
        custom=(self.root/'etc/config/passwall').read_text()
        self.assertIn("option direct_dns '119.29.29.29:53'",custom);self.assertIn("option remote_dns '8.8.8.8:53'",custom)
        self.run_adapter('remove');restored=(self.root/'etc/config/passwall').read_text()
        self.assertIn("option chn_list '0'",restored);self.assertNotIn('use_default_dns',restored)

    def test_passwall_keeps_native_chinadns_engine(self):
        path=self.root/'etc/config/passwall'
        path.write_text(path.read_text()+"\nconfig unused 'tail'\n")
        text=path.read_text().replace(" option dns_mode 'xray'", " option dns_mode 'xray'\n option dns_shunt 'chinadns-ng'")
        path.write_text(text)
        self.write('usr/share/passwall/helper_chinadns_add.lua','-- group null block_host\n')
        self.run_adapter('apply')
        after=path.read_text()
        self.assertIn("option dns_shunt 'chinadns-ng'",after)
        self.assertNotIn('use_default_dns',after)
        self.run_adapter('remove')
        self.assertIn("option dns_shunt 'chinadns-ng'",path.read_text())

    def test_passwall_restores_legacy_owned_engine(self):
        self.run_adapter('apply')
        path=self.root/'etc/config/passwall';state=self.root/'etc/adzero/adapter.json'
        data=json.loads(state.read_text())
        data['flags']=[f for f in data['flags'] if f['option']!='dns_shunt']
        data['flags'].append(dict(config='passwall',section='main',option='dns_shunt',before='chinadns-ng',after='dnsmasq'))
        # Model the actual pre-fix recovery journal and its effective setting.
        text='\n'.join(line for line in path.read_text().splitlines() if 'dns_shunt' not in line)+'\n'
        path.write_text(text.replace("option enabled '1'","option enabled '1'\n option dns_shunt 'dnsmasq'"))
        state.write_text(json.dumps(data));self.write('usr/share/passwall/helper_chinadns_add.lua','-- group null block_host\n')
        self.run_adapter('apply')
        self.assertIn("option dns_shunt 'chinadns-ng'",path.read_text())
        self.assertNotIn('use_default_dns',path.read_text())
        self.run_adapter('remove')
        self.assertIn("option dns_shunt 'chinadns-ng'",path.read_text())

    def test_passwall_missing_deadline_helper_fails_before_writes(self):
        self.env['ADZERO_SERVICE_TIMEOUT']=str(self.root/'missing-timeout')
        before=(self.root/'etc/config/passwall').read_bytes()
        self.run_adapter('apply',success=False)
        self.assertEqual((self.root/'etc/config/passwall').read_bytes(),before)
        self.assertFalse((self.root/'etc/passwall/rules/block_host').exists())

    def test_passwall_missing_country_data_is_not_success(self):
        (self.root/'usr/share/passwall/rules/chnroute').unlink()
        self.run_adapter('apply',success=False)
        self.assertNotIn('remote_dns',(self.root/'etc/config/passwall').read_text())

    def test_passwall_country_changes_invalidate_the_unchanged_fast_path(self):
        self.run_adapter('apply')
        country=self.root/'usr/share/passwall/rules/chnlist'
        country.write_text(country.read_text()+'\nsub.ads.example\n')
        self.run_adapter('apply')
        self.assertIn('sub.ads.example',(self.root/'etc/passwall/rules/block_host').read_text())

    def test_stopped_passwall_restores_then_uses_domestic_dns_without_restarting_proxy(self):
        counter=self.root/'restarts'
        self.write('etc/init.d/passwall','#!/bin/sh\necho restart >> '+shlex.quote(str(counter))+'\n')
        self.run_adapter('apply')
        inventory=Path(self.env['ADZERO_ADAPTER_INVENTORY'])
        data=json.loads(inventory.read_text());data['plugins'][0]['runtime']='stopped';inventory.write_text(json.dumps(data))
        self.run_adapter('apply')
        self.assertEqual(counter.read_text().splitlines(),['restart'])
        self.assertNotIn('remote_dns',(self.root/'etc/config/passwall').read_text())
        self.assertEqual(json.loads(self.run_adapter('status').stdout)['routing']['mode'],'direct')
        self.assertIn('223.5.5.5',(self.root/'etc/config/dhcp').read_text())

    def test_restore_retains_later_manual_passwall_route_choice(self):
        self.run_adapter('apply')
        path=self.root/'etc/config/passwall'
        path.write_text(path.read_text().replace("option chn_list 'direct'","option chn_list 'proxy'"))
        self.run_adapter('remove')
        self.assertIn("option chn_list 'proxy'",path.read_text())

    def nailink_fixture(self):
        self.select('nailink')
        p=self.write('profiles.json',json.dumps({'dns':{'id':'dns','dns':{'string':'upstream { local: "udp://223.5.5.5:53" }\nrouting { request { fallback: local } }'}},'traffic':{'id':'route','routing':{'string':'domain(full: user.example) -> direct\nfallback: proxy\n','fallback':{'val':'proxy'}}},'running':True,'runs':0}))
        api=self.write('bin/api','''#!/usr/bin/env python3
import json,sys
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'profiles.json';data=json.loads(p.read_text());r=json.loads(Path(sys.argv[1]).read_text());q=r['query'];v=r['variables']
if r.get('readiness'):out={k:data.get('healthy',True) for k in ['ready','configured','listening','initialized','dns_ready','routing_ready']} | {'core':data.get('health_core',data.get('native_core','dae')),'phase':data.get('health_phase','ready')}
elif '__type(' in q:out={'__type':{'fields':[{'name':'logLevel','type':{'kind':'SCALAR','name':'String'}}]}}
elif 'configs(' in q:out={'configs':[{'id':'global','global':{'logLevel':data.get('global_setting','info')}}],'groups':[{'id':'group','name':'proxy','policy':data.get('group_policy','fixed'),'policyParams':[],'nodes':[{'id':'node','name':'fixture','link':data.get('node_link','fixture-private-node'),'tag':None,'subscriptionID':None}],'subscriptions':[]}]}
elif 'general{' in q:out={'general':{'dae':{'running':data['running'],'modified':data.get('modified',False)}}}
elif 'coreStatus' in q:out={'coreStatus':json.dumps({'current':data['native_core'],'phase':'stable','runtime':data.get('core_phase','ready'),'api_only':False} | ({} if data.get('omit_running') else {'running':data.get('core_phase')!='stopped'}))} if data.get('native_core') else {}
elif 'routings(' in q:out={'routings':[data['traffic']],'groups':[{'name':'proxy'}]}
elif 'routings{' in q:out={'routings':[data['traffic'],*data.get('inactive_routes',[])]}
elif 'dnss(' in q:out={'dnss':[data['dns']]}
elif 'updateDns' in q:data['dns']['dns']['string']=v['dns'];data['modified']=True;data['writes']=data.get('writes',0)+1;out={'updateDns':{'id':'dns'}}
elif 'updateRouting' in q:
 flag=p.parent/('fail-alias-route-once' if 'ADZERO ALIAS BRIDGE' in v['routing'] else 'fail-route-once')
 if flag.exists():flag.unlink();sys.exit(1)
 assert v['id']==data['traffic']['id']
 data['traffic']['routing']['string']=v['routing'];out={'updateRouting':{'id':v['id']}}
elif 'run(dry:false)' in q:
 data['runs']+=1
 if (p.parent/'hold-reload').exists():p.write_text(json.dumps(data));sys.exit(1)
 if (p.parent/'fail-restore-once').exists():
  (p.parent/'fail-restore-once').unlink();p.write_text(json.dumps(data));sys.exit(1)
 data['running']=True;data['modified']=False;out={'run':0}
else:out={}
p.write_text(json.dumps(data));print(json.dumps(out))
''');api.chmod(0o755);self.env['ADZERO_NAILINK_API_FIXTURE']=str(api)
        return p

    def test_nailink_native_core_uses_current_api_and_skips_dae_only_alias_listener(self):
        p=self.nailink_fixture()
        cfg=self.root/'etc/config/adzero';cfg.write_text(cfg.read_text()+" option dns_enhance '{\"enabled\":true,\"cname\":true}'\n")
        for core in ('sing-box','mihomo'):
            with self.subTest(core=core):
                data=json.loads(p.read_text());data.update(native_core=core,running=False);p.write_text(json.dumps(data))
                self.assertEqual(self.run_adapter('check').stdout.strip(),'nailink')
                self.run_adapter('apply')
                status=json.loads(self.run_adapter('status').stdout)
                self.assertEqual(status['core'],core)
                after=json.loads(p.read_text())
                self.assertIn('adzero-geosite.dat',after['dns']['dns']['string'])
                self.assertNotIn('5355',after['dns']['dns']['string'])
                journal=json.loads((self.root/'etc/adzero/adapter.json').read_text())
                self.assertIsNone(journal['alias'])
                self.assertEqual(journal['alias_unavailable'],'native_core_listener_not_supported')
                self.run_adapter('remove')

    def test_nailink_core_switch_is_rejected_before_changing_profiles(self):
        p=self.nailink_fixture();data=json.loads(p.read_text());data.update(native_core='mihomo',core_phase='switching');p.write_text(json.dumps(data))
        original=data['dns']
        self.run_adapter('apply',success=False)
        self.assertEqual(json.loads(p.read_text())['dns'],original)
        self.assertFalse((self.root/'etc/adzero/adapter.json').exists())

    def test_issue_35_adapter_accepts_missing_running_only_with_matching_readiness(self):
        p=self.nailink_fixture()
        for core in ('dae','sing-box','mihomo'):
            with self.subTest(core=core):
                data=json.loads(p.read_text());data.update(native_core=core,omit_running=True);p.write_text(json.dumps(data))
                self.assertEqual(self.run_adapter('check').stdout.strip(),'nailink')
                self.run_adapter('apply')
                self.assertEqual(json.loads(self.run_adapter('status').stdout)['core'],core)
                self.run_adapter('remove')
        data=json.loads(p.read_text());data.update(native_core='dae',health_core='mihomo');p.write_text(json.dumps(data))
        before=p.read_bytes()
        self.run_adapter('apply',success=False)
        self.assertEqual(p.read_bytes(),before)
        self.assertFalse((self.root/'etc/adzero/adapter.json').exists())

    def pending_reload(self):
        p=self.nailink_fixture();self.run_adapter('apply')
        self.write('var/run/adblock-fast/dnsmasq.servers','server=/new-ads.example/\n')
        self.write('hold-reload','1')
        result=self.run_adapter('apply',success=False)
        self.assertIn('no second mutation submitted',result.stderr)
        return p

    def alias_fixture(self):
        p=self.nailink_fixture()
        cfg=self.root/'etc/config/adzero';cfg.write_text(cfg.read_text()+" option dns_enhance '{\"enabled\":true,\"cname\":true}'\n")
        ubus=self.write('bin/ubus',"#!/bin/sh\necho '{\"l3_device\":\"br-lan\",\"ipv4-address\":[{\"address\":\"127.0.0.2\",\"mask\":24}]}'\n");ubus.chmod(0o755)
        self.env['PATH']=str(self.root/'bin')+':'+self.env['PATH']
        helper=self.write('usr/libexec/adzero/dns-native','#!/bin/sh\necho "$1" >> '+shlex.quote(str(self.root/'native-calls'))+'\n[ "$1" != cleanup ] || [ ! -e '+shlex.quote(str(self.root/'fail-cleanup'))+' ]\n');helper.chmod(0o755)
        return p

    def test_nailink_alias_transaction_is_idempotent_and_restores_both_profiles(self):
        p=self.alias_fixture();before=json.loads(p.read_text());self.run_adapter('apply')
        after=json.loads(p.read_text());self.assertIn('tcp+udp://127.0.0.2:5355',after['dns']['dns']['string']);self.assertIn('dport(53) -> must_direct',after['traffic']['routing']['string'])
        self.run_adapter('apply');self.assertEqual(json.loads(p.read_text())['runs'],after['runs'])
        self.run_adapter('remove');restored=json.loads(p.read_text())
        self.assertEqual(restored['dns'],before['dns']);self.assertEqual(restored['traffic'],before['traffic'])
        self.assertEqual((self.root/'native-calls').read_text().splitlines(),['install','install','cleanup'])

    def test_nailink_alias_failed_routing_rolls_back_bridge_and_dns(self):
        p=self.alias_fixture();before=json.loads(p.read_text());self.write('fail-alias-route-once','1');self.run_adapter('apply',success=False)
        restored=json.loads(p.read_text());self.assertEqual(restored['dns'],before['dns']);self.assertEqual(restored['traffic'],before['traffic'])
        self.assertFalse((self.root/'etc/adzero/adapter.json').exists())
        self.assertEqual((self.root/'native-calls').read_text().splitlines(),['install','cleanup'])

    def test_nailink_alias_cleanup_failure_retains_recovery_intent(self):
        p=self.alias_fixture();self.run_adapter('apply');self.write('fail-cleanup','1');self.run_adapter('remove',success=False)
        self.assertTrue(json.loads((self.root/'etc/adzero/adapter.json').read_text())['alias'])
        (self.root/'fail-cleanup').unlink();self.run_adapter('remove');self.assertFalse((self.root/'etc/adzero/adapter.json').exists())

    def test_nailink_existing_dns_bind_is_preserved_and_optional_bridge_skipped(self):
        p=self.alias_fixture();data=json.loads(p.read_text());data['dns']['dns']['string']='bind: "tcp+udp://127.0.0.1:5353"\n'+data['dns']['dns']['string'];p.write_text(json.dumps(data));self.run_adapter('apply')
        self.assertIn('127.0.0.1:5353',json.loads(p.read_text())['dns']['dns']['string'])
        journal=json.loads((self.root/'etc/adzero/adapter.json').read_text());self.assertIsNone(journal['alias']);self.assertEqual(journal['alias_unavailable'],'native_listener_or_routing_unavailable')
        self.assertFalse((self.root/'native-calls').exists())

    def test_nailink_alias_can_be_disabled_without_removing_global_filter(self):
        p=self.alias_fixture();self.run_adapter('apply')
        cfg=self.root/'etc/config/adzero';cfg.write_text(cfg.read_text().replace('"enabled":true','"enabled":false'))
        self.run_adapter('apply');data=json.loads(p.read_text());self.assertNotIn('5355',data['dns']['dns']['string']);self.assertNotIn('ALIAS BRIDGE',data['traffic']['routing']['string']);self.assertIn('adzero-geosite.dat',data['dns']['dns']['string'])
        self.assertIsNone(json.loads((self.root/'etc/adzero/adapter.json').read_text())['alias'])
        self.assertEqual((self.root/'native-calls').read_text().splitlines(),['install','cleanup'])

    def test_nailink_alias_does_not_require_automatic_split_or_a_proxy_fallback(self):
        p=self.alias_fixture();cfg=self.root/'etc/config/adzero';cfg.write_text(cfg.read_text().replace("option routing_enabled '1'","option routing_enabled '0'"))
        data=json.loads(p.read_text());data['traffic']['routing'].update(string='fallback: direct\n',fallback=dict(val='direct'));p.write_text(json.dumps(data));before=json.loads(p.read_text())
        self.run_adapter('apply');self.assertIn('ALIAS BRIDGE',json.loads(p.read_text())['traffic']['routing']['string'])
        self.run_adapter('remove');after=json.loads(p.read_text());self.assertEqual(after['traffic'],before['traffic']);self.assertEqual(after['dns'],before['dns'])

    def test_handoff_from_stopped_nailink_to_passwall_keeps_old_core_stopped(self):
        for routing,replaced in ((True,False),(False,False),(True,True)):
            with self.subTest(routing=routing,replaced=replaced):
                p=self.nailink_fixture()
                if not routing:self.write('etc/config/adzero',"config adzero 'main'\n option routing_enabled '0'\n")
                self.run_adapter('apply')
                original=json.loads(p.read_text())
                original['running']=False
                if replaced:
                    original['traffic']['id']='replacement'
                    original['traffic']['routing']['string']=original['traffic']['routing']['string'].replace(
                        'domain(full: user.example) -> direct','domain(full: changed.example) -> proxy')
                p.write_text(json.dumps(original))
                self.select('passwall')
                inventory=Path(self.env['ADZERO_ADAPTER_INVENTORY']);data=json.loads(inventory.read_text())
                data['plugins'].append({'id':'nailink','installed':True,'enabled':True,'runtime':'stopped','active':False})
                inventory.write_text(json.dumps(data))
                self.run_adapter('apply')
                after=json.loads(p.read_text())
                self.assertFalse(after['running'])
                self.assertEqual(after['runs'],original['runs'])
                self.assertNotIn('ADZERO MANAGED BEGIN',after['dns']['dns']['string'])
                if replaced:
                    self.assertEqual(after['traffic']['id'],'replacement')
                    self.assertEqual(after['traffic']['routing']['string'],
                                     'domain(full: changed.example) -> proxy\n\nfallback: proxy')
                self.assertEqual(json.loads(self.run_adapter('status').stdout)['plugin'],'passwall')
                self.run_adapter('remove')

    def test_pending_reload_preserves_new_dat_and_waits_for_version_completion(self):
        p=self.pending_reload();record=self.root/'etc/adzero/adapter.json';journal=json.loads(record.read_text())
        current=json.loads(p.read_text());self.assertTrue(current['modified'])
        self.assertTrue(journal['nailink_completion']['barrier'])
        self.assertEqual(journal['stage'],'restarting')
        self.assertIn('new-ads.example',Path(journal['files'][0]['path']).read_bytes().decode('latin1'))
        before=p.read_bytes();self.run_adapter('settle',success=False);self.assertEqual(p.read_bytes(),before)
        current['modified']=False;p.write_text(json.dumps(current));before=p.read_bytes()
        self.run_adapter('settle');self.assertEqual(p.read_bytes(),before)
        self.assertEqual(json.loads(record.read_text())['stage'],'configured_unverified')
        self.assertNotIn('nailink_reload_unconfirmed',json.loads(record.read_text()))
        self.run_adapter('apply');self.assertEqual(p.read_bytes(),before)

    def test_ready_old_core_without_version_barrier_cannot_settle(self):
        p=self.pending_reload();record=self.root/'etc/adzero/adapter.json';journal=json.loads(record.read_text())
        journal['nailink_completion']['barrier']=False;record.write_text(json.dumps(journal))
        data=json.loads(p.read_text());data['modified']=False;p.write_text(json.dumps(data))
        before=p.read_bytes();self.run_adapter('settle',success=False)
        self.assertEqual(before,p.read_bytes());self.assertTrue(json.loads(record.read_text())['nailink_reload_unconfirmed'])

    def test_completed_version_without_dataplane_readiness_cannot_settle(self):
        p=self.pending_reload();data=json.loads(p.read_text());data.update(modified=False,healthy=False);p.write_text(json.dumps(data))
        self.run_adapter('settle',success=False)
        self.assertTrue(json.loads((self.root/'etc/adzero/adapter.json').read_text())['nailink_reload_unconfirmed'])

    def test_completed_reload_can_settle_after_native_core_switch(self):
        p=self.pending_reload()
        current=json.loads(p.read_text());current.update(native_core='mihomo',modified=False)
        p.write_text(json.dumps(current));before=p.read_bytes()
        self.run_adapter('settle')
        self.assertEqual(p.read_bytes(),before,'confirming completion must not submit another reload')
        state=json.loads((self.root/'etc/adzero/adapter.json').read_text())
        self.assertFalse(state.get('nailink_reload_unconfirmed'))
        self.assertEqual(state['stage'],'configured_unverified')

    def test_completed_cross_core_reload_can_restore_without_daemon_restart(self):
        p=self.pending_reload();current=json.loads(p.read_text())
        current.update(native_core='sing-box',modified=False);p.write_text(json.dumps(current))
        (self.root/'hold-reload').unlink()
        self.run_adapter('remove')
        self.assertFalse((self.root/'etc/adzero/adapter.json').exists())
        restored=json.loads(p.read_text())
        self.assertNotIn('ADZERO MANAGED BEGIN',restored['dns']['dns']['string'])
        self.assertEqual(restored['native_core'],'sing-box','restoration must preserve the user selected core')

    def test_cross_core_transition_cannot_confirm_pending_reload(self):
        p=self.pending_reload();current=json.loads(p.read_text())
        current.update(native_core='mihomo',core_phase='switching',modified=False)
        p.write_text(json.dumps(current));before=p.read_bytes()
        self.run_adapter('settle',success=False)
        self.run_adapter('remove',success=False)
        self.assertEqual(p.read_bytes(),before)

    def lost_receipt(self):
        p=self.pending_reload();data=json.loads(p.read_text())
        data.update(native_core='mihomo',modified=False);p.write_text(json.dumps(data))
        record=self.root/'etc/adzero/adapter.json';state=json.loads(record.read_text())
        state['nailink_completion']['barrier']=False
        state['nailink_run_identity']={'boot':'fixture','pid':10,'start':'1'}
        record.write_text(json.dumps(state))
        identity=self.write('identity.json',json.dumps(state['nailink_run_identity']))
        self.env['ADZERO_NAILINK_IDENTITY_FIXTURE']=str(identity)
        service=self.write('etc/init.d/daed', '''#!/bin/sh
printf restart >> '''+shlex.quote(str(self.root/'manager-restarts'))+'''
python3 - <<'PY'
import json
from pathlib import Path
p=Path('''+repr(str(identity))+''');state=json.loads(p.read_text());state['pid']+=1;state['start']=str(int(state['start'])+1);p.write_text(json.dumps(state))
p=Path('''+repr(str(p))+''');data=json.loads(p.read_text());data['modified']=False;p.write_text(json.dumps(data))
PY
''');service.chmod(0o755)
        return p,record

    def test_explicit_close_recovers_old_unprovable_receipt_without_router_restart(self):
        p,record=self.lost_receipt();(self.root/'hold-reload').unlink()
        self.run_adapter('remove')
        self.assertEqual((self.root/'manager-restarts').read_text(),'restart')
        self.assertFalse(record.exists())
        self.assertEqual(json.loads(p.read_text())['native_core'],'mihomo')

    def test_background_sync_never_restarts_manager_for_lost_receipt(self):
        p,record=self.lost_receipt();before=record.read_bytes()
        self.run_adapter('settle',success=False)
        self.assertEqual(record.read_bytes(),before)
        self.assertFalse((self.root/'manager-restarts').exists())

    def test_owned_uncommitted_reload_can_close_without_router_restart(self):
        p,record=self.lost_receipt();data=json.loads(p.read_text());data['modified']=True;p.write_text(json.dumps(data))
        self.assertIsNotNone(json.loads(record.read_text())['nailink_completion']['foreign'])
        self.assertNotIn('fixture-private-node',record.read_text())
        self.assertFalse(list((self.root/'etc/adzero').glob('nailink-owner*')))
        self.assertFalse(list((self.root/'tmp').glob('adzero-nailink-owner*')))
        (self.root/'hold-reload').unlink();self.run_adapter('remove')
        self.assertFalse(record.exists());self.assertEqual((self.root/'manager-restarts').read_text(),'restart')

    def test_foreign_pending_node_group_and_global_edits_block_restart(self):
        for key in ('node_link','global_setting','group_policy'):
            with self.subTest(key=key):
                self.write('var/run/adblock-fast/dnsmasq.servers','server=/ads.example/\n')
                p,record=self.lost_receipt();data=json.loads(p.read_text());data.update(modified=True);data[key]='user-edit';p.write_text(json.dumps(data));before=p.read_bytes()
                self.run_adapter('remove',success=False)
                self.assertEqual(p.read_bytes(),before);self.assertFalse((self.root/'manager-restarts').exists())
                # Restore the fixture's foreign input for the next subcase.
                del data[key];data['modified']=False;p.write_text(json.dumps(data))
                (self.root/'hold-reload').unlink();self.run_adapter('remove')
                (self.root/'manager-restarts').unlink()

    def test_close_does_not_repeatedly_restart_for_failed_restoration(self):
        p,record=self.lost_receipt();state=json.loads(record.read_text());state['nailink_recovery_count']=1;record.write_text(json.dumps(state))
        self.run_adapter('remove',success=False)
        self.assertFalse((self.root/'manager-restarts').exists());self.assertTrue(record.exists())

    def test_lost_forward_and_restore_receipts_have_separate_bounded_recovery(self):
        p,record=self.lost_receipt();(self.root/'hold-reload').unlink();self.write('fail-restore-once','1')
        self.run_adapter('remove',success=False)
        state=json.loads(record.read_text());self.assertEqual(state['nailink_recovery_stage'],'forward')
        self.assertFalse(state['nailink_completion']['forward']);runs=json.loads(p.read_text())['runs']
        self.run_adapter('remove')
        self.assertFalse(record.exists());self.assertEqual((self.root/'manager-restarts').read_text(),'restartrestart')
        self.assertEqual(json.loads(p.read_text())['runs'],runs,'already loaded original profiles must not trigger another reload')

    def test_close_preserves_preexisting_pending_foreign_changes(self):
        p=self.nailink_fixture();self.run_adapter('apply');data=json.loads(p.read_text())
        data.update(modified=True,global_setting='user-pending');p.write_text(json.dumps(data));before=p.read_bytes()
        job=self.root/'job';job.mkdir();self.env['ADZERO_JOB_CONTEXT']=str(job)
        self.run_adapter('remove',success=False)
        self.assertEqual(p.read_bytes(),before);self.assertFalse((self.root/'manager-restarts').exists())
        self.assertEqual((job/'reason').read_text(),'nailink_external_edit')

    def test_changed_daemon_options_block_owned_pending_recovery(self):
        p,record=self.lost_receipt();self.write('etc/config/daed',"config daed 'config'\n option enabled '0'\n")
        data=json.loads(p.read_text());data['modified']=True;p.write_text(json.dumps(data));before=p.read_bytes()
        self.run_adapter('remove',success=False)
        self.assertEqual(p.read_bytes(),before);self.assertFalse((self.root/'manager-restarts').exists());self.assertTrue(record.exists())

    def test_closing_intent_blocks_new_forward_application(self):
        self.nailink_fixture();self.write('etc/adzero/close-requested','AdZero close request v1\n')
        self.run_adapter('apply',success=False)
        self.assertFalse((self.root/'etc/adzero/adapter.json').exists())
        self.assertNotIn('adzero-geosite.dat',json.loads((self.root/'profiles.json').read_text())['dns']['dns']['string'])

    def test_owned_pending_stop_can_restore_profiles_without_starting_core(self):
        p,record=self.lost_receipt();data=json.loads(p.read_text());data.update(running=False,native_core='mihomo',core_phase='stopped',modified=False);p.write_text(json.dumps(data))
        self.run_adapter('remove')
        self.assertFalse(record.exists());self.assertFalse(json.loads(p.read_text())['running'])

    def test_lost_receipt_does_not_restart_or_overwrite_external_profile_edit(self):
        p,record=self.lost_receipt();data=json.loads(p.read_text())
        data['dns']['dns']['string']+='\n# user edit';p.write_text(json.dumps(data));before=p.read_bytes()
        self.run_adapter('remove',success=False)
        self.assertFalse((self.root/'manager-restarts').exists())
        self.assertEqual(p.read_bytes(),before)

    def test_transition_during_explicit_close_keeps_all_provider_files(self):
        p=self.nailink_fixture();self.run_adapter('apply')
        data=json.loads(p.read_text());data.update(native_core='mihomo',core_phase='switching')
        p.write_text(json.dumps(data));before=p.read_bytes()
        record=self.root/'etc/adzero/adapter.json';original=record.read_bytes()
        self.run_adapter('remove',success=False)
        self.assertEqual(record.read_bytes(),original)
        self.assertEqual(p.read_bytes(),before)
        self.assertTrue((self.root/'etc/daed/adzero-geosite.dat').exists())

    def test_core_status_transport_error_is_not_treated_as_legacy_dae(self):
        p=self.nailink_fixture();api=Path(self.env['ADZERO_NAILINK_API_FIXTURE'])
        api.write_text(api.read_text().replace("elif 'coreStatus' in q:","elif 'coreStatus' in q:sys.exit(1)\nelif False:"))
        before=p.read_bytes();self.run_adapter('apply',success=False)
        self.assertEqual(p.read_bytes(),before)
        self.assertFalse((self.root/'etc/adzero/adapter.json').exists())

    def test_native_legacy_stopped_barrier_requires_the_run_commit(self):
        p=self.nailink_fixture();data=json.loads(p.read_text())
        data.update(native_core='sing-box',running=False);p.write_text(json.dumps(data))
        api=Path(self.env['ADZERO_NAILINK_API_FIXTURE'])
        api.write_text(api.read_text().replace("'modified':data.get('modified',False)","'modified':data.get('modified',False) if data['running'] else False"))
        self.write('hold-reload','1');self.run_adapter('apply',success=False)
        state=json.loads((self.root/'etc/adzero/adapter.json').read_text())
        self.assertTrue(state['nailink_completion']['legacy_start'])
        self.run_adapter('settle',success=False)
        data=json.loads(p.read_text());data.update(running=True,modified=False);p.write_text(json.dumps(data))
        self.run_adapter('settle')
        self.assertEqual(json.loads(p.read_text())['runs'],1)

    def test_external_profile_change_blocks_pending_completion(self):
        p=self.pending_reload();data=json.loads(p.read_text());data['modified']=False
        data['dns']['dns']['string']+='\n# manual edit';p.write_text(json.dumps(data));before=p.read_bytes()
        self.run_adapter('settle',success=False);self.assertEqual(p.read_bytes(),before)

    def test_external_dat_change_blocks_pending_completion(self):
        p=self.pending_reload();data=json.loads(p.read_text());data['modified']=False;p.write_text(json.dumps(data))
        self.write('etc/daed/adzero-geosite.dat','external')
        self.run_adapter('settle',success=False);self.assertEqual((self.root/'etc/daed/adzero-geosite.dat').read_text(),'external')

    def test_backend_change_blocks_pending_completion(self):
        p=self.pending_reload();data=json.loads(p.read_text());data['modified']=False;p.write_text(json.dumps(data))
        self.write('var/run/adblock-fast/dnsmasq.servers','server=/external.example/\n')
        self.run_adapter('settle',success=False)

    def test_nailink_updates_both_profiles_and_restores_without_starting_stopped_core(self):
        p=self.nailink_fixture()
        before=json.loads(p.read_text());self.run_adapter('apply');after=json.loads(p.read_text())
        self.assertIn('adzero_global',after['dns']['dns']['string']);self.assertIn('adzero-cn-geoip.dat',after['traffic']['routing']['string'])
        self.run_adapter('apply');self.assertEqual(json.loads(p.read_text())['runs'],after['runs'])
        stopped=json.loads(p.read_text());stopped['running']=False;p.write_text(json.dumps(stopped))
        self.run_adapter('apply');restored=json.loads(p.read_text())
        self.assertFalse(restored['running']);self.assertEqual(restored['dns'],before['dns']);self.assertEqual(restored['traffic'],before['traffic'])
        self.assertEqual(json.loads(self.run_adapter('status').stdout)['routing']['mode'],'direct')

    def test_nailink_already_restored_profiles_do_not_submit_duplicate_writes(self):
        p=self.nailink_fixture()
        self.run_adapter('apply')
        journal=json.loads((self.root/'etc/adzero/adapter.json').read_text())
        current=json.loads(p.read_text())
        current['dns']['dns']['string']=journal['dns']['before']
        current['traffic']['routing']['string']=journal['traffic']['before']
        p.write_text(json.dumps(current))
        api=Path(self.env['ADZERO_NAILINK_API_FIXTURE'])
        api.write_text(api.read_text().replace("if 'general{' in q:","if 'updateDns' in q or 'updateRouting' in q:sys.exit(81)\nelif 'general{' in q:"))
        self.run_adapter('remove')
        self.assertFalse((self.root/'etc/adzero/adapter.json').exists())
        after=json.loads(p.read_text())
        self.assertEqual(after['dns'],current['dns'])
        self.assertEqual(after['traffic'],current['traffic'])

    def test_close_restores_only_owned_blocks_from_replaced_route(self):
        p=self.nailink_fixture()
        self.run_adapter('apply')
        data=json.loads(p.read_text())
        data['traffic']['id']='replacement'
        data['traffic']['routing']['string']=data['traffic']['routing']['string'].replace(
            'domain(full: user.example) -> direct','domain(full: new.example) -> proxy')
        p.write_text(json.dumps(data))

        self.write('fail-route-once','1')
        self.run_adapter('remove',success=False)
        record=self.root/'etc/adzero/adapter.json'
        self.assertEqual(json.loads(record.read_text())['traffic']['id'],'replacement')
        self.run_adapter('remove')
        restored=json.loads(p.read_text())
        self.assertEqual(restored['traffic']['id'],'replacement')
        self.assertEqual(restored['traffic']['routing']['string'],
                         'domain(full: new.example) -> proxy\n\nfallback: proxy')
        self.assertEqual(restored['runs'],data['runs']+1)
        self.assertFalse(record.exists())

    def test_replaced_route_requires_exclusive_unmodified_owned_blocks(self):
        p=self.nailink_fixture()
        self.run_adapter('apply')
        original=json.loads(p.read_text())
        for conflict in ('edited', 'old_profile_present', 'other_provider_reference', 'pending_profile'):
            with self.subTest(conflict=conflict):
                data=json.loads(json.dumps(original))
                data['traffic']['id']='replacement'
                if conflict=='edited':
                    data['traffic']['routing']['string']=data['traffic']['routing']['string'].replace(
                        'adzero-cn-geoip.dat','changed-geoip.dat')
                if conflict=='old_profile_present':
                    data['inactive_routes']=[original['traffic']]
                if conflict=='other_provider_reference':
                    data['inactive_routes']=[{'id':'other','routing':{
                        'string':'dip(ext:"adzero-cn-geoip.dat:adzero") -> direct\nfallback: proxy\n'}}]
                if conflict=='pending_profile':
                    data['modified']=True
                p.write_text(json.dumps(data))
                self.run_adapter('remove',success=False)
                self.assertEqual(json.loads(p.read_text()),data)
                status=json.loads(self.run_adapter('status').stdout)
                self.assertEqual(status['recovery_reason'],'nailink_routing_conflict')
                self.assertTrue((self.root/'etc/daed/adzero-cn-geoip.dat').exists())

    def test_nailink_unconfirmed_reload_does_not_start_rollback_mutations(self):
        p=self.nailink_fixture()
        self.run_adapter('apply')
        path=self.root/'etc/adzero/adapter.json'
        journal=json.loads(path.read_text())
        journal['nailink_reload_unconfirmed']=True
        journal['nailink_run_identity']=None
        path.write_text(json.dumps(journal))
        before=p.read_bytes()
        assets={f['path']:Path(f['path']).read_bytes() for f in journal['files']}
        result=self.run_adapter('remove',success=False)
        self.assertIn('no second mutation submitted',result.stderr)
        self.assertEqual(p.read_bytes(),before)
        self.assertEqual(json.loads(path.read_text()),journal)
        self.assertEqual({name:Path(name).read_bytes() for name in assets},assets)

    def test_nailink_fast_toggle_reloads_once_per_direction_and_keeps_filtering(self):
        p=self.nailink_fixture()
        self.write('etc/config/adzero',"config adzero 'main'\n option enabled '1'\n option routing_enabled '0'\n")
        self.run_adapter('apply');original=json.loads(p.read_text());runs=original['runs']
        self.assertEqual(self.run_adapter('routing-check','1').stdout.strip(),'nailink')
        self.write('etc/config/adzero',"config adzero 'main'\n option enabled '1'\n option routing_enabled '1'\n")
        self.run_adapter('reconfigure-nailink');enabled=json.loads(p.read_text())
        self.assertEqual(enabled['runs'],runs+1);self.assertIn('adzero_global',enabled['dns']['dns']['string'])
        self.assertIn('adzero-geosite.dat',enabled['dns']['dns']['string'])
        self.assertEqual(self.run_adapter('routing-check','0').stdout.strip(),'nailink')
        self.write('etc/config/adzero',"config adzero 'main'\n option enabled '1'\n option routing_enabled '0'\n")
        self.env['ADZERO_ROUTING_DATA_FIXTURE']=str(self.root/'must-not-download')
        self.run_adapter('reconfigure-nailink');disabled=json.loads(p.read_text())
        self.assertEqual(disabled['runs'],runs+2)
        self.assertEqual(disabled['dns'],original['dns']);self.assertEqual(disabled['traffic'],original['traffic'])
        self.assertEqual(json.loads(self.run_adapter('status').stdout)['routing']['mode'],'inactive')
        self.run_adapter('remove');self.assertNotIn('ADZERO',p.read_text())

    def test_nailink_preflight_failure_does_not_reload_or_download(self):
        p=self.nailink_fixture()
        self.write('etc/config/adzero',"config adzero 'main'\n option enabled '1'\n option routing_enabled '0'\n")
        self.run_adapter('apply');before=json.loads(p.read_text());before['traffic']['routing']['fallback']['val']='missing'
        p.write_text(json.dumps(before));journal=(self.root/'etc/adzero/adapter.json').read_bytes()
        self.run_adapter('routing-check','1',success=False)
        self.assertEqual(json.loads(p.read_text()),before)
        self.assertEqual((self.root/'etc/adzero/adapter.json').read_bytes(),journal)
        self.assertFalse((self.root/'etc/daed/adzero-cn-geosite.dat').exists())

    def test_nailink_reuses_verified_country_files_and_checks_external_edits(self):
        self.nailink_fixture();self.run_adapter('apply')
        self.env['ADZERO_ROUTING_DATA_PROGRAM']=str(self.root/'must-not-run')
        self.run_adapter('apply')
        # Cached country files remain usable after routing off/on, with a
        # fresh source-policy comparison and actual owned-file verification.
        self.write('etc/config/adzero',"config adzero 'main'\n option enabled '1'\n option routing_enabled '0'\n")
        self.run_adapter('reconfigure-nailink')
        self.write('etc/config/adzero',"config adzero 'main'\n option enabled '1'\n option routing_enabled '1'\n")
        self.run_adapter('reconfigure-nailink')
        data=self.root/'etc/daed/adzero-cn-geosite.dat';data.write_bytes(b'external-edit')
        self.run_adapter('apply',success=False)
        self.assertEqual(data.read_bytes(),b'external-edit')

    @unittest.skipUnless(shutil.which('yq'),'real yq required')
    def test_nikki_split_is_idempotent_and_preserves_custom_exceptions(self):
        self.select('nikki')
        self.write('etc/config/nikki',"config nikki 'config'\n option enabled '1'\nconfig mixin 'mixin'\n option mixin_file_content '1'\n option dns_nameserver '1'\n option dns_nameserver_policy '1'\n option mode 'global'\n")
        mixin=self.write('etc/nikki/mixin.yaml',json.dumps({'nikki-rules':['DOMAIN,user.cn.example,My Proxy']}))
        before=mixin.read_bytes()
        base=self.write('etc/nikki/base.json',json.dumps({'mode':'global','dns':{'enable':True,'nameserver':['8.8.8.8'],'nameserver-policy':{'+.user.example':['192.168.1.1']},'fake-ip-filter':['+.lan']},'proxy-groups':[{'name':'My Proxy','type':'select','proxies':['node']}],'rules':['MATCH,My Proxy']}))
        run=self.write('etc/nikki/run/config.yaml',base.read_text())
        counter=self.root/'restarts'
        script='#!/bin/sh\necho restart >> '+shlex.quote(str(counter))+'\nyq -o=json eval-all \'. as $item ireduce ({}; . * $item ) | .rules = .nikki-rules + .rules | del(.nikki-rules)\' '+shlex.join([str(base),str(mixin)])+' > '+shlex.quote(str(run))+'\n'
        self.write('etc/init.d/nikki',script)
        self.run_adapter('apply');after=json.loads(run.read_text())
        self.assertEqual(after['mode'],'rule');self.assertEqual(after['dns']['nameserver'],['tcp://1.1.1.1:53#My Proxy'])
        self.assertLess(after['rules'].index('DOMAIN,user.cn.example,My Proxy'),after['rules'].index('RULE-SET,adzero-cn-domain,DIRECT'))
        self.assertEqual(after['dns']['nameserver-policy']['+.user.example'],['192.168.1.1'])
        self.run_adapter('apply');self.assertEqual(counter.read_text().splitlines(),['restart'])
        self.run_adapter('remove');self.custom_dns();self.run_adapter('apply')
        self.assertEqual(json.loads(run.read_text())['dns']['nameserver'],['tcp://8.8.8.8:53#My Proxy'])
        self.assertEqual(json.loads(run.read_text())['dns']['direct-nameserver'],['119.29.29.29'])
        self.run_adapter('remove');self.assertEqual(mixin.read_bytes(),before)
        self.assertIn("option dns_nameserver '1'",(self.root/'etc/config/nikki').read_text())

    @unittest.skipUnless(shutil.which('yq'),'real yq required')
    def test_nikki_core_only_change_is_not_hidden_by_the_fast_path(self):
        controls.ControlsTest.select(self,'nikki')
        self.write('etc/config/adzero',"config adzero 'main'\n option enabled '1'\n option routing_enabled '0'\n")
        p=self.root/'etc/config/nikki';p.write_text(p.read_text().replace("option enabled '1'","option enabled '1'\n option core_only '0'"))
        self.run_adapter('apply')
        p.write_text(p.read_text().replace("option core_only '0'","option core_only '1'"))
        self.run_adapter('apply',success=False)
        self.assertIn("option core_only '1'",p.read_text())

    @unittest.skipUnless(shutil.which('ruby'),'real Ruby required')
    def test_openclash_generated_config_contains_dns_and_connection_rules(self):
        self.select('openclash')
        self.write('etc/config/openclash', "config openclash 'config'\n option config_path '/etc/openclash/config/config.yaml'\n")
        custom=self.write('etc/openclash/custom/openclash_custom_overwrite.sh','#!/bin/sh\nexit 0\n')
        base=self.write('etc/openclash/source.json',json.dumps({'mode':'global','dns':{'enable':True,'nameserver':['8.8.8.8']},'proxy-groups':[{'name':'My Proxy','type':'select','proxies':['node']}],'rules':['DOMAIN,user.example,DIRECT','MATCH,My Proxy']}))
        run=self.root/'etc/openclash/config.yaml';self.write('etc/openclash/core/clash_meta','#!/bin/sh\nexit 0\n').chmod(0o755)
        adapter=UCODE+[str(ROOT/'luci-app-adzero/root/usr/libexec/adzero/adapter.uc'),'--','hook-openclash','/etc/openclash/config.yaml']
        self.write('etc/init.d/openclash','#!/bin/sh\ncp '+shlex.join([str(base),str(run)])+'\nif grep -q "ADZERO MANAGED BEGIN" '+shlex.quote(str(custom))+'; then\n'+shlex.join(adapter)+'\nfi\n')
        self.run_adapter('apply');config=json.loads(run.read_text())
        self.assertEqual(config['mode'],'rule');self.assertIn('RULE-SET,adzero-cn-domain,DIRECT',config['rules'])
        self.assertEqual(config['dns']['nameserver'],['tcp://1.1.1.1:53#My Proxy'])
        self.run_adapter('remove');self.custom_dns();self.run_adapter('apply')
        self.assertEqual(json.loads(run.read_text())['dns']['nameserver'],['tcp://8.8.8.8:53#My Proxy'])
        self.assertEqual(json.loads(run.read_text())['dns']['direct-nameserver'],['119.29.29.29'])
        self.run_adapter('remove');self.assertEqual(json.loads(run.read_text()),json.loads(base.read_text()))

@unittest.skipUnless(UCODE,'requires actual OpenWrt ucode')
class RoutingControllerTest(unittest.TestCase):
    def setUp(self):
        self.fixture=controls.ControlsTest();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root
        self.fixture.write('inventory.json',json.dumps({'plugins':[],'dns':{'active_proxy_count':0}}))
        self.fixture.write('etc/config/dhcp',"config dnsmasq 'main'\n list server '8.8.8.8'\n")
        self.fixture.write('etc/init.d/dnsmasq','#!/bin/sh\nexit 0\n',True)

    def nailink(self):
        f=self.fixture;p=RoutingAdapterTest.nailink_fixture(f)
        data=f.write('routing-fixture.json',json.dumps({'domain_raw':'server=/cn.example/114.114.114.114\n','cidr_raw':'223.5.5.0/24\n'}))
        f.env.update(ADZERO_ROUTING_DATA_FIXTURE=str(data),ADZERO_ROUTING_DATA_PROGRAM=str(ROOT/'luci-app-adzero/root/usr/libexec/adzero/routing-data'),ADZERO_UCODE=shlex.join(UCODE))
        return p

    def passwall(self):
        f=self.fixture;f.select('passwall')
        f.write('etc/config/passwall',"config global 'main'\n option enabled '1'\n option dns_mode 'xray'\n option dns_shunt 'chinadns-ng'\n option node 'chosen'\nconfig nodes 'chosen'\n option type 'Xray'\n")
        f.write('usr/share/passwall/app.sh','# direct_dns_mode USE_DEFAULT_DNS\n')
        f.write('usr/share/passwall/helper_chinadns_add.lua','-- group null block_host\n')
        for name in ['chnlist','chnroute']:f.write('usr/share/passwall/rules/'+name,'cn.example\n')
        count=self.root/'passwall-restarts'
        f.write('proc/901/comm','chinadns-ng\n')
        f.write('proc/901/stat','901 (chinadns-ng) S 1 '+'0 '*19+'\n')
        f.write('proc/901/cmdline','/tmp/etc/passwall/bin/chinadns-ng\0-C\0/tmp/etc/passwall/acl/default/chinadns_ng.conf\0')
        generator=self.root/'dns-fixture.py'
        generator.write_text(generator.read_text()+"\n(d/'dnsmasq.conf').write_text('port=11400\\nserver=127.0.0.1#15354\\n')\n(d/'chinadns_ng.conf').write_text('bind-port 15354\\ngroup null\\ngroup-dnl /tmp/etc/passwall_tmp/user_rules/block_host\\n')\np=r/'tmp/etc/passwall_tmp/user_rules';p.mkdir(parents=True,exist_ok=True);(p/'block_host').write_text('\\n'.join(names)+'\\n')\n")
        f.write('etc/init.d/passwall','#!/bin/sh\necho restart >> '+shlex.quote(str(count))+'\nexec python3 '+shlex.quote(str(generator))+'\n',True)
        return count

    def test_passwall_routing_toggle_keeps_filter_and_restarts_once(self):
        count=self.passwall();f=self.fixture;f.ctl('enable')
        before=len(count.read_text().splitlines())
        f.ctl('routing-on');self.assertEqual(len(count.read_text().splitlines()),before+1)
        self.assertIn('ads.example',(self.root/'etc/passwall/rules/block_host').read_text())
        f.ctl('routing-off');self.assertEqual(len(count.read_text().splitlines()),before+2)
        self.assertIn("dns_shunt 'chinadns-ng'",(self.root/'etc/config/passwall').read_text())
        self.assertEqual(f.status()['state'],'configured_unverified')

    def test_dns_correction_can_migrate_stopped_nailink_to_xray_passwall(self):
        p=self.nailink();f=self.fixture;f.ctl('enable');f.ctl('routing-on')
        previous=json.loads(p.read_text());previous['running']=False;p.write_text(json.dumps(previous))
        count=self.passwall()
        old={'domestic':{'servers':['223.5.5.5'],'active':'223.5.5.5'},'foreign':{'servers':['1.1.1.1','8.8.8.8'],'active':'1.1.1.1','mode':'parallel'}}
        f.write('etc/config/adzero',"config adzero 'main'\n option enabled '1'\n option routing_enabled '1'\n option dns_profile '"+json.dumps(old)+"'\n")
        data=json.loads(f.ctl('sources-list').stdout);candidate=json.loads(json.dumps(data['dns']));candidate['foreign'].pop('mode')
        items=[{k:i[k] for k in ['id','name','url','format','enabled']} for i in data['items']]
        plan=f.write('candidate.json',json.dumps({'revision':data['revision'],'items':items,'routing_before':True,'routing_enabled':True,'dns_before':data['dns'],'dns':candidate}))
        starts=(self.root/'start-events').read_bytes()
        f.ctl('settings-apply',str(plan))
        self.assertEqual(f.status()['adapter']['plugin'],'passwall')
        self.assertEqual((self.root/'start-events').read_bytes(),starts)
        self.assertEqual(len(count.read_text().splitlines()),1)
        self.assertIn("dns_mode 'xray'",(self.root/'etc/config/passwall').read_text())
        self.assertFalse(json.loads(p.read_text())['running'])
        self.assertEqual(f.query('ads.example')['match'],'exact')

    def test_passwall_failed_fast_change_restores_preference_and_rules(self):
        count=self.passwall();f=self.fixture;f.ctl('enable')
        f.write('etc/init.d/passwall','#!/bin/sh\nif [ ! -f '+shlex.quote(str(self.root/'failed-once'))+' ]; then touch '+shlex.quote(str(self.root/'failed-once'))+'; exit 1; fi\nexec python3 '+shlex.quote(str(self.root/'dns-fixture.py'))+'\n',True)
        f.ctl('routing-on',ok=False)
        self.assertFalse(f.status()['policy']['routing_enabled'])
        self.assertIn('ads.example',(self.root/'etc/passwall/rules/block_host').read_text())
        self.assertIn("dns_shunt 'chinadns-ng'",(self.root/'etc/config/passwall').read_text())

    def test_nailink_controller_switches_once_and_keeps_the_backend_running(self):
        p=self.nailink();f=self.fixture;f.ctl('enable');before=json.loads(p.read_text())
        f.ctl('routing-on');on=json.loads(p.read_text());self.assertEqual(on['runs'],before['runs']+1)
        self.assertEqual(f.status()['adapter']['routing']['mode'],'proxy')
        f.ctl('routing-off');off=json.loads(p.read_text());self.assertEqual(off['runs'],before['runs']+2)
        self.assertEqual(off['dns'],before['dns']);self.assertEqual(off['traffic'],before['traffic'])
        self.assertEqual(f.status()['state'],'configured_unverified');self.assertEqual(f.query('ads.example')['match'],'exact')

    def test_nailink_partial_update_failure_restores_both_profiles_and_preference(self):
        p=self.nailink();f=self.fixture;f.ctl('enable');before=json.loads(p.read_text())
        f.write('fail-route-once','1');f.ctl('routing-on',ok=False);after=json.loads(p.read_text())
        self.assertEqual(after['dns'],before['dns']);self.assertEqual(after['traffic'],before['traffic'])
        self.assertFalse(f.status()['policy']['routing_enabled']);self.assertEqual(f.status()['state'],'configured_unverified')
        self.assertFalse((self.root/'etc/adzero/routing-pending.json').exists())

    def test_pending_rule_reload_does_not_roll_back_sources_or_restart_backend(self):
        p=self.nailink();f=self.fixture;f.ctl('enable')
        fetch=f.write('bin/fetch','#!/bin/sh\nprintf "added.example\\n" > "$2"\n',True)
        f.env['ADZERO_SOURCE_FETCH_FIXTURE']=str(fetch)
        f.write('source-downloads.json',json.dumps({'https://rules.example/added':['added.example']}))
        f.write('hold-reload','1');before=json.loads(p.read_text())
        f.ctl('source-add','','New','https://rules.example/added','domains',ok=False)
        after=json.loads(p.read_text());self.assertEqual(after['runs'],before['runs']+1)
        pending=self.root/'etc/adzero/sources-pending.json';self.assertTrue(pending.exists())
        output=self.root/'var/run/adblock-fast/dnsmasq.servers';self.assertIn('added.example',output.read_text());body=output.read_bytes()
        journal=json.loads((self.root/'etc/adzero/adapter.json').read_text());self.assertEqual(journal['count'],4)
        f.ctl('reconcile',ok=False);self.assertEqual(output.read_bytes(),body)
        self.assertEqual(json.loads(p.read_text())['runs'],after['runs'])
        after['modified']=False;p.write_text(json.dumps(after));f.ctl('reconcile')
        self.assertEqual(f.status()['state'],'configured_unverified');self.assertFalse(pending.exists())
        self.assertEqual(output.read_bytes(),body);self.assertEqual(json.loads(p.read_text())['runs'],after['runs'])

    def test_pending_routing_reload_keeps_target_preference_until_confirmed(self):
        p=self.nailink();f=self.fixture;f.ctl('enable');f.write('hold-reload','1')
        f.ctl('routing-on',ok=False)
        self.assertTrue(f.status()['policy']['routing_enabled'])
        current=json.loads(p.read_text());self.assertTrue(current['modified']);current['modified']=False;p.write_text(json.dumps(current))
        f.ctl('reconcile');self.assertEqual(f.status()['state'],'configured_unverified')
        self.assertFalse((self.root/'etc/adzero/routing-pending.json').exists())
        self.assertEqual(json.loads(p.read_text())['runs'],current['runs'])

    def test_pending_batch_is_confirmed_without_reapplying_or_losing_selection(self):
        p=self.nailink();f=self.fixture;f.ctl('enable')
        fetch=f.write('bin/fetch','#!/bin/sh\nprintf "added.example\\n" > "$2"\n',True)
        f.env['ADZERO_SOURCE_FETCH_FIXTURE']=str(fetch)
        url='https://rules.example/batch';f.write('source-downloads.json',json.dumps({url:['added.example']}))
        data=json.loads(f.ctl('sources-list').stdout)
        items=[{k:i[k] for k in ['id','name','url','format','enabled']} for i in data['items']]
        items.append({'id':None,'name':'Batch','url':url,'format':'domains','enabled':True})
        plan=f.write('batch.json',json.dumps({'revision':data['revision'],'items':items,'routing_before':False,'routing_enabled':False}))
        f.write('hold-reload','1');before=json.loads(p.read_text())
        f.ctl('settings-apply',str(plan),ok=False)
        after=json.loads(p.read_text());self.assertEqual(after['runs'],before['runs']+1)
        pending=self.root/'etc/adzero/sources-pending.json';self.assertTrue(pending.exists())
        self.assertTrue(any(i['url']==url and i['enabled'] for i in json.loads(f.ctl('sources-list').stdout)['items']))
        after['modified']=False;p.write_text(json.dumps(after));f.ctl('reconcile')
        self.assertFalse(pending.exists());self.assertEqual(f.status()['state'],'configured_unverified')
        self.assertEqual(json.loads(p.read_text())['runs'],after['runs'])

    def test_initial_enable_pending_keeps_ownership_until_background_confirmation(self):
        p=self.nailink();f=self.fixture;f.write('hold-reload','1');f.ctl('enable',ok=False)
        state=f.status();self.assertTrue(state['managed']);self.assertTrue(state['configured'])
        self.assertTrue(state['adapter']['reload_pending']);self.assertEqual(state['backend']['state'],'ready')
        current=json.loads(p.read_text());self.assertEqual(current['runs'],1)
        current['modified']=False;p.write_text(json.dumps(current));f.ctl('reconcile')
        self.assertEqual(f.status()['state'],'configured_unverified')
        self.assertEqual(json.loads(p.read_text())['runs'],1)

    def test_routing_toggle_preserves_filtering_and_restores_original_dns(self):
        f=self.fixture;f.ctl('enable');f.ctl('routing-on')
        s=f.status();self.assertEqual(s['state'],'configured_unverified');self.assertTrue(s['policy']['routing_enabled']);self.assertEqual(s['adapter']['routing']['mode'],'direct')
        self.assertEqual(f.query('ads.example')['match'],'exact')
        f.ctl('routing-off');self.assertFalse(f.status()['policy']['routing_enabled']);self.assertEqual(f.status()['state'],'configured_unverified')
        self.assertIn('8.8.8.8',(self.root/'etc/config/dhcp').read_text())
        self.assertFalse((self.root/'etc/adzero/routing-pending.json').exists())

    def test_pause_disable_and_reenable_keep_choice_but_restore_network(self):
        f=self.fixture;f.ctl('enable');f.ctl('routing-on');f.ctl('pause','300')
        self.assertEqual(f.status()['state'],'paused');self.assertIn('8.8.8.8',(self.root/'etc/config/dhcp').read_text())
        f.ctl('resume');self.assertEqual(f.status()['adapter']['routing']['mode'],'direct')
        f.ctl('disable');self.assertTrue(f.status()['policy']['routing_enabled']);self.assertEqual(f.status()['state'],'disabled')
        f.ctl('enable');self.assertEqual(f.status()['adapter']['routing']['mode'],'direct')

    def test_failed_dns_reload_rolls_back_preference_and_keeps_ad_filtering(self):
        f=self.fixture;f.ctl('enable');flag=f.write('fail-dns-once','1')
        f.write('etc/init.d/dnsmasq','#!/bin/sh\nif [ -f '+shlex.quote(str(flag))+' ]; then rm '+shlex.quote(str(flag))+'; exit 1; fi\nexit 0\n',True)
        f.ctl('routing-on',ok=False);s=f.status()
        self.assertFalse(s['policy']['routing_enabled']);self.assertEqual(s['state'],'configured_unverified');self.assertEqual(f.query('ads.example')['match'],'exact')
        self.assertIn('8.8.8.8',(self.root/'etc/config/dhcp').read_text());self.assertFalse((self.root/'etc/adzero/routing-pending.json').exists())

if __name__=='__main__':unittest.main()
