"""Real production DNS/pcap decoding and correlation, with synthetic wire packets.

These tests do not claim LAN capture: check_observe_runtime.py covers the real
tcpdump/ucode process lifecycle and isolated UDP traffic separately.
"""
import base64
import ipaddress
import json
import os
from pathlib import Path
import shlex
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
UCODE = shlex.split(os.environ.get('ADZERO_UCODE', ''))


def dns(name='ads.example', ident=42, response=False, rcode=0, address=None,
        qtype=1, tc=False):
    qname = b''.join(bytes([len(p)]) + p.encode() for p in name.split('.')) + b'\0'
    flags = 0x100 | (0x8000 if response else 0) | rcode | (0x200 if tc else 0)
    payload = struct.pack('!HHHHHH', ident, flags, 1, bool(address), 0, 0)
    payload += qname + struct.pack('!HH', qtype, 1)
    if address:
        raw = ipaddress.ip_address(address).packed
        payload += b'\xc0\x0c' + struct.pack('!HHIH', 1 if len(raw)==4 else 28, 1, 60, len(raw)) + raw
    return payload


def packet(payload, response=False, v6=False, tcp=False, seq=1, vlan=False, link=1):
    src = '2001:db8::2' if v6 else '192.168.2.10'
    dst = '2001:db8::1' if v6 else '192.168.2.1'
    sport, dport = 52000, 53
    if response:
        src, dst, sport, dport = dst, src, dport, sport
    if tcp:
        transport = struct.pack('!HHIIBBHHH', sport,dport,seq,0,0x50,0x18,4096,0,0) + payload
    else:
        transport = struct.pack('!HHHH',sport,dport,8+len(payload),0)+payload
    protocol = 6 if tcp else 17
    if v6:
        network=struct.pack('!IHBB',6<<28,len(transport),protocol,64)+ipaddress.ip_address(src).packed+ipaddress.ip_address(dst).packed
        ethertype=0x86dd
    else:
        network=struct.pack('!BBHHHBBH4s4s',0x45,0,20+len(transport),0,0,64,protocol,0,ipaddress.ip_address(src).packed,ipaddress.ip_address(dst).packed)
        ethertype=0x0800
    if link==1:
        header=b'\0'*12+struct.pack('!H',ethertype)
        if vlan:header=b'\0'*12+struct.pack('!HHH',0x8100,1,ethertype)
    elif link==113:header=b'\0'*14+struct.pack('!H',ethertype)
    elif link==276:header=struct.pack('!HHI',ethertype,0,4)+b'\0'*12
    else:header=b''
    return header+network+transport


def pcap(*packets, little=True, link=1):
    fmt='<' if little else '>'
    raw=struct.pack(fmt+'IHHIIII',0xa1b2c3d4,2,4,0,0,4096,link)
    for n,p in enumerate(packets):raw+=struct.pack(fmt+'IIII',n,0,len(p),len(p))+p
    return raw


@unittest.skipUnless(UCODE,'set ADZERO_UCODE to a real OpenWrt ucode runtime')
class ObserveTest(unittest.TestCase):
    def run_code(self,operation,success=True,observer_root=None,**data):
        env={**os.environ}
        if observer_root is not None:env['ADZERO_OBSERVER_ROOT']=str(observer_root)
        result=subprocess.run(UCODE+[str(ROOT/'scripts/observe_transform.uc')],input=json.dumps({'operation':operation,**data}),env=env,text=True,capture_output=True)
        self.assertEqual(result.returncode==0,success,result.stderr)
        return json.loads(result.stdout) if success else None

    def decode(self,payload):return self.run_code('dns',data=base64.b64encode(payload).decode())
    def packets(self,raw,**args):return self.run_code('pcap',data=base64.b64encode(raw).decode(),**args)

    def test_empty_noerror_nxdomain_refused_are_negative(self):
        for rcode in (0,3,5):
            self.assertTrue(self.decode(dns(response=True,rcode=rcode))['blocked'])

    def test_capture_micro_and_nano_timestamps_both_endians(self):
        frame=packet(dns())
        for little in (False,True):
            for nano in (False,True):
                order='<' if little else '>'
                magic=0xa1b23c4d if nano else 0xa1b2c3d4
                raw=struct.pack(order+'IHHIIII',magic,2,4,0,0,262144,1)
                fraction=123456789 if nano else 123456
                raw+=struct.pack(order+'IIII',1792684800,fraction,len(frame),len(frame))+frame
                self.assertEqual(self.packets(raw,chunk=7)[0]['captured_us'],1792684800123456)
                invalid=struct.pack(order+'IHHIIII',magic,2,4,0,0,262144,1)
                invalid+=struct.pack(order+'IIII',1,1000000000 if nano else 1000000,len(frame),len(frame))+frame
                self.assertIsNone(self.packets(invalid)[0]['captured_us'])

    def test_latency_uses_capture_time_not_collector_delay_and_separates_blocks(self):
        result=self.run_code('tracker',rules=['ads.example'],events=[
            {'at':1,'dns':self.event(domain='normal.example',captured_us=1000000)},
            {'at':7,'dns':self.event(True,domain='normal.example',positive=True,blocked=False,captured_us=1003500)},
            {'at':8,'dns':self.event(ident=43,captured_us=2000000)},
            {'at':9,'dns':self.event(True,ident=43,captured_us=2000200)}])
        self.assertEqual(result['latency']['normal'],{'samples':1,'average_ms':3.5,'maximum_ms':3.5,'slow':0})
        self.assertEqual(result['latency']['blocked']['average_ms'],0.2)

    def test_latency_ignores_missing_or_invalid_clock_samples(self):
        for request,response in [(None,None),(1000000,999999),(-1,10),(0,15000001),('100','200')]:
            result=self.track([self.event(captured_us=request),self.event(True,captured_us=response)])
            self.assertEqual(result['counts']['blocked'],1)
            self.assertEqual(result['latency']['blocked']['samples'],0)
            self.assertIsNone(result['latency']['blocked']['average_ms'])

    def test_unanswered_is_observation_not_fabricated_blocks_and_counts_once(self):
        result=self.run_code('tracker',rules=['ads.example'],events=[
            {'at':1,'dns':self.event()}, {'at':1.1,'dns':self.event()},
            {'at':2,'dns':self.event(ident=43,protocol='tcp')}, {'at':18}, {'at':40}])
        self.assertEqual(result['latency']['unanswered'],{'udp':1,'tcp':1})
        self.assertEqual(result['counts']['blocked'],0)

    def test_latency_resets_with_connection_and_counter_reset(self):
        events=[{'at':1,'dns':self.event(captured_us=0)},{'at':2,'dns':self.event(True,captured_us=1000)}]
        for change in [{'reset':True},{'configure':True,'generation':'new','active':True}]:
            result=self.run_code('tracker',rules=['ads.example'],events=events+[change])
            self.assertEqual(result['latency']['blocked']['samples'],0)
            self.assertEqual(result['latency']['unanswered'],{'udp':0,'tcp':0})

    def test_latency_slow_threshold_mean_duplicates_and_failed_response(self):
        events=[]
        for ident,elapsed in [(1,499999),(2,500000),(3,1000000)]:
            events.extend([{'at':ident,'dns':self.event(domain='normal.example',ident=ident,captured_us=0)},
                           {'at':ident+0.1,'dns':self.event(True,domain='normal.example',ident=ident,positive=True,blocked=False,captured_us=elapsed)}])
        events.append(events[-1])
        result=self.run_code('tracker',rules=['ads.example'],events=events)
        self.assertEqual(result['latency']['normal']['samples'],3)
        self.assertEqual(result['latency']['normal']['slow'],2)
        self.assertAlmostEqual(result['latency']['normal']['average_ms'],666.6663333,places=4)
        failed=self.track([self.event(captured_us=0),self.event(True,captured_us=1000,blocked=False,positive=False)])
        self.assertEqual(failed['latency']['normal']['samples']+failed['latency']['blocked']['samples'],0)

    def test_timeout_servfail_truncated_and_positive_are_not_blocks(self):
        for data in (dns(),dns(response=True,rcode=2),dns(response=True,tc=True),dns(response=True,address='1.2.3.4')):
            self.assertFalse(self.decode(data)['blocked'])

    def test_zero_addresses_v4_v6(self):
        for address in ('0.0.0.0','::','127.0.0.1','::1'):
            self.assertTrue(self.decode(dns(response=True,address=address))['blocked'])

    def test_truncated_address_is_not_evidence_of_filter_bypass(self):
        result=self.decode(dns(response=True,address='198.51.100.7',tc=True))
        self.assertFalse(result['positive'])
        self.assertFalse(result['blocked'])

    def test_invalid_compression_lengths_and_questions(self):
        for raw in (b'',b'\0'*12,struct.pack('!HHHHHH',1,0x8000,1,0,0,0)+b'\xc0\x0c'+b'\0'*4,dns(response=True,address='1.2.3.4')[:-1]):
            self.assertIsNone(self.decode(raw))

    def test_udp_v4_v6_vlan_sll_and_chunking(self):
        for v6 in (False,True):
            for link in (1,113,276,101):
                raw=pcap(packet(dns(),v6=v6,link=link),packet(dns(response=True),response=True,v6=v6,link=link),link=link)
                messages=self.packets(raw,chunk=7)
                self.assertEqual(len(messages),2,(v6,link))
                self.assertEqual(messages[0]['name'],'ads.example')
                self.assertTrue(messages[1]['blocked'])
        self.assertEqual(len(self.packets(pcap(packet(dns(),vlan=True)))),1)

    def test_big_endian_and_bad_capture(self):
        self.assertEqual(len(self.packets(pcap(packet(dns()),little=False))),1)
        self.run_code('pcap',data=base64.b64encode(b'bad!'*10).decode(),success=False)

    def test_tcp_split_message_and_retransmit(self):
        data=dns(response=True,rcode=3);framed=struct.pack('!H',len(data))+data
        pieces=[packet(framed[:8],response=True,tcp=True,seq=100),packet(framed[:8],response=True,tcp=True,seq=100),packet(framed[8:],response=True,tcp=True,seq=108)]
        messages=self.packets(pcap(*pieces))
        self.assertEqual(len(messages),1)
        self.assertTrue(messages[0]['blocked'])

    def event(self,response=False,domain='ads.example',ident=42,**changes):
        data={'name':domain,'id':ident,'qtype':1,'response':response,'from':'192.168.2.1' if response else '192.168.2.10','to':'192.168.2.10' if response else '192.168.2.1','sport':53 if response else 52000,'dport':52000 if response else 53,'protocol':'udp','positive':False,'blocked':response}
        return {**data,**changes}

    def track(self,events,test=None):
        return self.run_code('tracker',rules=['ads.example'],test=test,events=[{'at':n+1,'dns':e} for n,e in enumerate(events)])

    def test_queries_alone_and_unrelated_nxdomain_never_count(self):
        result=self.track([self.event(),self.event(domain='other.example',ident=43),self.event(True,domain='other.example',ident=43)])
        self.assertEqual(result['counts'],{'queries':2,'blocked':0,'answered':1,'overflow':0,'matched':1,'matched_answered':0,'matched_positive':0,'matched_error':0})

    def test_duplicate_query_and_response_are_counted_once(self):
        result=self.track([self.event(),self.event(),self.event(True),self.event(True)])
        self.assertEqual(result['counts']['queries'],1)
        self.assertEqual(result['counts']['blocked'],1)

    def test_reset_clears_all_period_counters_and_drops_inflight_old_answers(self):
        result=self.run_code('tracker',rules=['ads.example'],events=[
            {'at':1,'dns':self.event()}, {'reset':True}, {'at':2,'dns':self.event(True)},
            {'at':3,'dns':self.event(ident=43)}, {'at':4,'dns':self.event(True,ident=43)}])
        self.assertEqual(result['counts']['queries'],1)
        self.assertEqual(result['counts']['blocked'],1)
        self.assertEqual(result['counts']['answered'],1)
        self.assertEqual(result['counts'],result['current'])

    def test_cooked_bridge_duplicate_views_and_redirected_requests(self):
        # Physical ingress retains :53, bridge ingress is already DNATed;
        # bridge and member egress both carry the same original DNS reply.
        query=packet(dns(),link=276)
        redirected=bytearray(query);struct.pack_into('!H',redirected,42,43771)
        reply=packet(dns(response=True,rcode=5),response=True,link=276)
        events=self.packets(pcap(query,bytes(redirected),reply,reply,link=276),chunk=13)
        self.assertEqual(len(events),3)
        result=self.run_code('tracker',rules=['ads.example'],events=[{'at':i+1,'dns':e} for i,e in enumerate(events)])
        self.assertEqual(result['counts']['queries'],1)
        self.assertEqual(result['counts']['blocked'],1)
        self.assertEqual(result['counts']['answered'],1)

    def test_rule_hit_servfail_is_explained_but_never_counted_as_block(self):
        failed=self.decode(dns(response=True,rcode=2))
        self.assertEqual(failed['rcode'],2)
        result=self.track([self.event(),self.event(True,rcode=2,blocked=False),self.event(ident=43,domain='normal.example'),self.event(True,ident=43,domain='normal.example',rcode=2,blocked=False)])
        self.assertEqual(result['counts']['matched_error'],1)
        self.assertEqual(result['counts']['blocked'],0)
        self.assertEqual(result['counts']['answered'],2)
        self.assertEqual(result['recorded'],[])

    def test_reset_request_rejects_stale_future_wrong_boot_or_untyped_time(self):
        request={'schema':1,'nonce':'a'*32,'boot':'boot-one','requested':100.5}
        self.assertTrue(self.run_code('reset-request',request=request,boot='boot-one',at=101))
        for change in [{'requested':85},{'requested':102},{'requested':'100.5'},{'boot':'boot-two'},{'nonce':'bad'},{'schema':2}]:
            self.assertFalse(self.run_code('reset-request',request={**request,**change},boot='boot-one',at=101))

    def test_parent_rule_and_actual_positive_answer(self):
        result=self.track([self.event(domain='sub.ads.example'),self.event(True,domain='sub.ads.example'),self.event(ident=43),self.event(True,ident=43,blocked=False,positive=True)])
        self.assertEqual(result['counts']['blocked'],1)
        self.assertEqual(result['counts']['answered'],2)
        self.assertEqual(result['current']['matched_positive'],1)
        self.assertEqual(result['current']['matched'],2)
        self.assertEqual(len(result['recorded']),1)

    def test_only_normal_traffic_can_grow_queries_without_growing_blocks(self):
        events=[]
        for n in range(10):events.extend([self.event(domain='normal.example',ident=n),self.event(True,domain='normal.example',ident=n,blocked=False,positive=True)])
        result=self.track(events)
        self.assertEqual(result['counts']['queries'],10)
        self.assertEqual(result['counts']['blocked'],0)
        self.assertEqual(result['current']['matched'],0)

    def test_positive_rule_hit_is_diagnostic_not_fabricated_block(self):
        result=self.track([self.event(),self.event(True,positive=True,blocked=False)])
        self.assertEqual(result['counts']['blocked'],0)
        self.assertEqual(result['current']['matched_positive'],1)
        self.assertEqual(result['recorded'],[])

    def test_context_change_resets_path_diagnostics_keeps_boot_counts(self):
        result=self.run_code('tracker',rules=['ads.example'],events=[{'at':1,'dns':self.event()},{'at':2,'dns':self.event(True,positive=True,blocked=False)},{'configure':True,'generation':'passwall','active':True}])
        self.assertEqual(result['counts']['matched_positive'],1)
        self.assertEqual(result['current']['matched_positive'],0)

    def test_missing_or_wrong_transaction_cannot_count(self):
        result=self.track([self.event(),self.event(True,ident=43),self.event(True,**{'from':'8.8.8.8'})])
        self.assertEqual(result['counts']['blocked'],0)

    def test_config_change_and_disabled_capture_invalidate_pending(self):
        for active in (False,True):
            result=self.run_code('tracker',rules=['ads.example'],events=[{'at':1,'dns':self.event()},{'configure':True,'generation':'changed','active':active},{'at':2,'dns':self.event(True)}])
            self.assertEqual(result['counts']['blocked'],0)

    def test_terminal_challenge_requires_marker_same_client_and_both_results(self):
        test={'nonce':'a'*32,'sample':'ads.example','control':'www.baidu.com','deadline':120}
        events=[self.event(domain='a'*32+'.adzero.invalid'),self.event(),self.event(True),self.event(domain='www.baidu.com',ident=45),self.event(True,domain='www.baidu.com',ident=45,positive=True,blocked=False)]
        result=self.track(events,test)
        self.assertTrue(result['test']['marker'] and result['test']['normal'] and result['test']['blocked'])
        self.assertEqual(result['counts']['queries'],0,'test traffic must not inflate counts')
        self.assertEqual(result['recorded'],[],'verification must not enter recent browsing records')
        events[2]['to']='192.168.2.99'
        result=self.track(events,test)
        self.assertFalse(result['test'].get('blocked',False))

    def test_statistics_reset_preserves_inflight_effect_verification(self):
        test={'nonce':'a'*32,'sample':'ads.example','control':'www.baidu.com','deadline':120}
        result=self.run_code('tracker',rules=['ads.example'],test=test,events=[
            {'at':1,'dns':self.event(domain='a'*32+'.adzero.invalid')},
            {'at':2,'dns':self.event()}, {'reset':True},
            {'at':3,'dns':self.event(True)},
            {'at':4,'dns':self.event(domain='www.baidu.com',ident=45)},
            {'at':5,'dns':self.event(True,domain='www.baidu.com',ident=45,positive=True,blocked=False)}])
        self.assertTrue(result['test']['marker'] and result['test']['normal'] and result['test']['blocked'])
        self.assertTrue(all(value==0 for value in result['counts'].values()))
        self.assertEqual(result['counts'],result['current'])
        self.assertEqual(result['recorded'],[])

    def test_recent_is_opt_in_bounded_aggregated_and_expires(self):
        steps=[{'dns':{'name':'ads.example','qtype':1},'at':1,'wall':100,'snapshot':True},
               {'configure':True,'token':'a'*32,'deadline':305,'at':5}]
        for n in range(35):steps.append({'dns':{'name':f'ads{n}.example','qtype':1},'at':6+n,'wall':100+n})
        steps.extend([{'dns':{'name':'ads34.example','qtype':28},'at':50,'wall':200,'snapshot':True},{'at':306,'snapshot':True}])
        result=self.run_code('recent',steps=steps)
        self.assertFalse(result[0]['enabled']);self.assertEqual(result[0]['items'],[])
        self.assertEqual(len(result[1]['items']),30)
        self.assertEqual(result[1]['items'][0],{'domain':'ads34.example','count':2,'qtypes':[1,28],'at':200})
        self.assertEqual(result[1]['total'],36)
        self.assertEqual(result[2]['items'],[]);self.assertFalse(result[2]['enabled'])

    def test_recent_new_token_and_stop_erase_domains(self):
        steps=[{'configure':True,'token':'a'*32,'deadline':300,'at':1},{'dns':{'name':'ads.example','qtype':1},'at':2,'wall':100},
               {'configure':True,'token':'b'*32,'deadline':303,'at':3,'snapshot':True},
               {'configure':True,'token':None,'deadline':0,'at':4,'snapshot':True}]
        result=self.run_code('recent',steps=steps)
        self.assertEqual(result[0]['items'],[]);self.assertEqual(result[1]['items'],[])

    def test_recent_request_requires_generation_nonce_and_short_deadline(self):
        request={'schema':1,'enabled':True,'nonce':'a'*32,'generation':'passwall','deadline':300}
        self.assertTrue(self.run_code('recent-request',request=request,generation='passwall',at=1))
        for changes in ({'generation':'old'},{'deadline':0},{'deadline':999},{'nonce':'bad'},{'enabled':False}):
            self.assertFalse(self.run_code('recent-request',request={**request,**changes},generation='passwall',at=1))

    def journal(self,plugin='nailink',stage='configured_unverified'):
        # Same order used by adapter.uc: files precede flags and stage.
        return {'schema':1,'plugin':plugin,'files':[{'path':'/etc/daed/adzero-geosite.dat','kind':'owned','before':None,'after':'A'*131072}], 'flags':[],'stage':stage}

    def contexts(self,steps):
        with tempfile.TemporaryDirectory(prefix='adzero-observe-context-') as root:
            (Path(root)/'etc/adzero').mkdir(parents=True)
            return self.run_code('adapter-context',observer_root=root,steps=steps)

    def test_adapter_stage_after_large_provider_is_found_for_each_plugin(self):
        steps=[]
        for plugin in ('nailink','passwall','openclash','nikki','ssrplus','none'):
            raw=json.dumps(self.journal(plugin))
            self.assertGreater(raw.index('"stage"'),1024)
            steps.append({'raw':raw})
        result=self.contexts(steps)
        self.assertTrue(all(v['stage']=='configured_unverified' and v['reason'] is None for v in result['values']))
        self.assertEqual(result['reads'],6)

    def test_large_journal_metadata_is_cached_instead_of_reparsed_each_tick(self):
        result=self.contexts([{'raw':json.dumps(self.journal()),'repeat':1000}])
        self.assertEqual(result['reads'],1)
        self.assertEqual(len(result['values']),1000)

    def test_atomic_journal_replacement_invalidates_cached_stage(self):
        result=self.contexts([{'raw':json.dumps(self.journal(stage=stage)),'repeat':2} for stage in ('preparing','configured_unverified','restoring','configured_unverified')])
        self.assertEqual([v['stage'] for v in result['values']],['preparing']*2+['configured_unverified']*2+['restoring']*2+['configured_unverified']*2)
        self.assertEqual(result['reads'],4)

    def test_nested_stage_or_quoted_stage_cannot_override_root_recovery_stage(self):
        journal=self.journal(stage='recovery_required');journal['files'][0].update(stage='configured_unverified',before='"stage":"configured_unverified"')
        result=self.contexts([{'raw':json.dumps(journal)}])['values'][0]
        self.assertEqual(result['reason'],'adapter_pending')
        self.assertEqual(result['stage'],'recovery_required')

    def test_invalid_journal_fails_closed_and_is_not_reparsed_each_tick(self):
        for raw in ('not json',json.dumps({'schema':1,'plugin':'nailink','files':{},'stage':'configured_unverified'})):
            result=self.contexts([{'raw':raw,'repeat':5}])
            self.assertEqual(result['reads'],1)
            self.assertTrue(all(v['reason']=='adapter_state_invalid' for v in result['values']))

    def test_absent_or_removed_journal_does_not_block_direct_dns_statistics(self):
        result=self.contexts([{}, {'raw':json.dumps(self.journal())}, {'remove':True}])
        self.assertEqual(result['values'][0],{'present':False})
        self.assertEqual(result['values'][2],{'present':False})
        self.assertEqual(result['reads'],1)

    def test_world_readable_or_symlink_journal_is_not_read(self):
        for kind in ('permissions','symlink'):
            with tempfile.TemporaryDirectory() as root:
                path=Path(root)/'etc/adzero/adapter.json';path.parent.mkdir(parents=True)
                source=path if kind=='permissions' else path.with_name('target.json')
                source.write_text(json.dumps(self.journal()));source.chmod(0o644 if kind=='permissions' else 0o600)
                if kind=='symlink':path.symlink_to(source)
                result=self.run_code('adapter-context',observer_root=root,steps=[{}])
                self.assertEqual(result['reads'],0)
                self.assertEqual(result['values'][0]['reason'],'adapter_state_invalid')
