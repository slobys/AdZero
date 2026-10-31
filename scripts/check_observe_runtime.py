#!/usr/bin/env python3
"""Real OpenWrt tcpdump, ucode and dnsmasq in a private network/mount namespace.

Run as root under Linux/WSL. Only disposable chroot files and namespace-local
addresses/processes are used. Dependencies are downloaded before isolation;
the isolated capture test has no real router, household traffic or WAN access.
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from test_observe import dns
from check_openwrt_runtime import SHA256
from prepare_dns_socket import install as install_socket, prepare as prepare_socket

EXTRAS={
    'libpcap1_1.10.5-r2_x86_64.ipk':'51c6bc3cc46fa1cd964a3fe7c2cdb283aced09c888bd6e61cc8d0ff0070649a2',
    'tcpdump-mini_4.99.5-r1_x86_64.ipk':'fa88889694215128b26ad3d94e8b05b8c052475b90d45d2b20394668a94d0fb2',
    'ucode-mod-uloop_2025.07.18~3f64c808-r1_x86_64.ipk':'1c0c07ecd7e820b9a70347284d3c26d4d7760e2224f48e5eadee50a6e0030095',
}


def prepare_extras(download):
    files=[]
    for filename,digest in EXTRAS.items():
        cache=ROOT/'artifacts/runtime/observe-extras'/filename
        if not cache.exists():
            if not download:raise RuntimeError('Prepare observer dependencies before network isolation')
            with urllib.request.urlopen('https://archive.openwrt.org/releases/24.10.8/packages/x86_64/base/'+filename,timeout=30) as response:data=response.read()
            if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('Observer dependency SHA-256 mismatch')
            cache.parent.mkdir(parents=True,exist_ok=True);cache.write_bytes(data)
        if hashlib.sha256(cache.read_bytes()).hexdigest()!=digest:raise ValueError('Cached observer dependency SHA-256 mismatch')
        files.append(cache)
    return files


def main():
    if os.geteuid()!=0:raise SystemExit('Run as root in Linux/WSL')
    if '--inside' not in sys.argv:
        prepare_socket(ROOT,None)
        prepare_extras(download=True)
        return subprocess.call(['unshare','--net','--mount','--propagation','private',sys.executable,__file__,'--inside'])
    subprocess.run(['ip','link','set','lo','up'],check=True)
    for ip in ('192.168.2.1','192.168.2.10'):subprocess.run(['ip','addr','add',ip+'/32','dev','lo'],check=True)
    archive=ROOT/'artifacts/runtime/openwrt-24.10.8-x86-64-rootfs.tar.gz'
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==SHA256
    with tempfile.TemporaryDirectory(prefix='adzero-observe-') as temp:
        base=Path(temp)
        with tarfile.open(archive) as tar:tar.extractall(base,filter='fully_trusted')
        install_socket(ROOT,base)
        for cache in prepare_extras(download=False):
            with tarfile.open(cache) as outer:
                member=next(m for m in outer.getmembers() if m.name.endswith('data.tar.gz'))
                with tarfile.open(fileobj=io.BytesIO(outer.extractfile(member).read())) as tar:tar.extractall(base,filter='fully_trusted')
        for relative in ('etc/adzero','tmp','proc','sys','dev','usr/libexec/adzero','var/run/adblock-fast'):(base/relative).mkdir(parents=True,exist_ok=True)
        subprocess.run(['mount','--bind','/proc',str(base/'proc')],check=True)
        # A fresh sysfs exposes the disposable network namespace's bridge
        # membership/ifindexes instead of the workstation's network devices.
        subprocess.run(['mount','-t','sysfs','-o','nosuid,noexec,nodev','sysfs',str(base/'sys')],check=True)
        os.mknod(base/'dev/null',0o666|0o20000,os.makedev(1,3))
        os.mknod(base/'dev/urandom',0o666|0o20000,os.makedev(1,9))
        for f in (ROOT/'luci-app-adzero/root/usr/libexec/adzero').iterdir():
            if f.is_file():shutil.copyfile(f,base/'usr/libexec/adzero'/f.name);(base/'usr/libexec/adzero'/f.name).chmod(0o755)
        (base/'etc/config/adzero').write_text("config adzero 'main'\n option enabled '1'\n")
        (base/'etc/config/adblock-fast').write_text("config adblock-fast 'config'\n option enabled '1'\n option dns 'dnsmasq.servers'\n")
        (base/'etc/adzero/managed').write_text('1\n')
        (base/'var/run/adblock-fast/dnsmasq.servers').write_text('server=/doubleclick.net/\n')
        # Real adapter serialization puts potentially large provider strings
        # before the top-level stage. The original 1 KiB prefix missed it.
        journal={'schema':1,'plugin':'nailink','files':[{'path':'/etc/daed/adzero-geosite.dat','kind':'owned','before':None,'after':'A'*2097152}],'flags':[],'stage':'configured_unverified'}
        journal_path=base/'etc/adzero/adapter.json'
        def replace_journal(stage):
            journal['stage']=stage;temp=journal_path.with_suffix('.next')
            temp.write_text(json.dumps(journal));temp.chmod(0o600);temp.replace(journal_path)
        replace_journal('configured_unverified')
        stage_offset=journal_path.read_text().index('"stage"');assert stage_offset>1024
        # Only netifd discovery is a fixture. Capture, packets, DNS responses,
        # timers, UCI, process lifecycle and state files are native binaries.
        (base/'bin/ubus').write_text('#!/bin/sh\necho \'{"l3_device":"lo"}\'\n');(base/'bin/ubus').chmod(0o755)
        log=(ROOT/'artifacts/runtime/observe-runtime.log').open('w+')
        procs=[]
        def run(*args):return subprocess.run(['chroot',temp,*args],capture_output=True,text=True,timeout=8)
        def query(name,ident=42,tcp=False):
            with socket.socket(socket.AF_INET,socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM) as sock:
                sock.settimeout(2);sock.bind(('192.168.2.10',0));raw=dns(name,ident=ident)
                if tcp:
                    sock.connect(('192.168.2.1',53));sock.sendall(struct.pack('!H',len(raw))+raw)
                    def exact(n):
                        data=b''
                        while len(data)<n:
                            part=sock.recv(n-len(data));assert part;data+=part
                        return data
                    return exact(struct.unpack('!H',exact(2))[0])
                sock.sendto(raw,('192.168.2.1',53));return sock.recvfrom(4096)[0]
        def status():
            p=run('/usr/libexec/adzero/observe','status');assert p.returncode==0,p.stderr
            return json.loads(p.stdout)
        def wait_for(check):
            value=None
            for _ in range(80):
                value=status()
                if check(value):return value
                if len(procs)>1 and procs[-1].poll() is not None:break
                time.sleep(.1)
            log.flush()
            state_file=base/'tmp/adzero-observe/status.json'
            raw=state_file.read_text() if state_file.exists() else 'missing'
            raise AssertionError((value,raw,[p.poll() for p in procs],(ROOT/'artifacts/runtime/observe-runtime.log').read_text()))
        try:
            procs.append(subprocess.Popen(['chroot',temp,'/usr/sbin/dnsmasq','--no-daemon','--conf-file=/dev/null','--no-resolv','--no-hosts','--bind-interfaces','--listen-address=192.168.2.1','--user=root','--server=/doubleclick.net/','--server=/adzero.invalid/','--host-record=www.baidu.com,1.2.3.4','--host-record=allowed-by-path.example,1.2.3.4'],stdout=log,stderr=log))
            time.sleep(.2)
            procs.append(subprocess.Popen(['chroot',temp,'/usr/libexec/adzero/observe','run'],env={**os.environ,'ADZERO_OBSERVER_DEBUG':'1'},stdout=log,stderr=log))
            value=wait_for(lambda s:s.get('available') and s.get('active'))
            time.sleep(.3)
            query('doubleclick.net');query('www.baidu.com',43)
            # The timer may publish the second query before its response is
            # consumed. Wait for that response instead of sampling mid-flight.
            value=wait_for(lambda s:s.get('blocked')==1 and s.get('queries')==2 and s.get('answered')==2)
            assert value['answered']==2,value
            query('doubleclick.net',51,tcp=True);query('www.baidu.com',52,tcp=True)
            value=wait_for(lambda s:s.get('blocked')==2 and s.get('queries')==4)
            result=run('/usr/libexec/adzero/observe','start-test');test=json.loads(result.stdout);assert test['ok'],test
            # The timer imports the fresh challenge before its marker arrives.
            wait_for(lambda s:s.get('verification',{}).get('state')=='waiting')
            names=[line.split()[-1] for line in test['commands'].splitlines()]
            for i,name in enumerate(names):query(name,100+i)
            value=wait_for(lambda s:s.get('verification',{}).get('state')=='passed')
            assert value['queries']==4 and value['blocked']==2,value
            assert value['verification']['client']=='192.168.2.10',value
            # Rule/config changes invalidate the receipt, without network restart.
            (base/'etc/config/adzero').write_text("config adzero 'main'\n option enabled '1'\n option routing_enabled '1'\n")
            wait_for(lambda s:s.get('verification',{}).get('state')=='idle')
            procs[-1].terminate();procs[-1].wait(timeout=4)
            procs.append(subprocess.Popen(['chroot',temp,'/usr/libexec/adzero/observe','run'],stdout=log,stderr=log))
            value=wait_for(lambda s:s.get('available') and s.get('active'))
            assert value['queries']==4 and value['blocked']==2,value
            replace_journal('recovery_required')
            wait_for(lambda s:s.get('available') and not s.get('active'))
            assert status()['reason']=='adapter_pending'
            query('doubleclick.net',150)
            time.sleep(1.1);value=status();assert value['queries']==4 and value['blocked']==2,value
            replace_journal('configured_unverified')
            wait_for(lambda s:s.get('available') and s.get('active'))
            query('doubleclick.net',151);query('www.baidu.com',152)
            value=wait_for(lambda s:s.get('queries')==6 and s.get('blocked')==3)
            # Background switch metadata arrives through the same small snapshot.
            journal.update(plugin='passwall',count=246925,split={'mode':'proxy'},runtime_supported=True,runtime_samples=['doubleclick.net'])
            replace_journal('configured_unverified')
            value=wait_for(lambda s:s.get('connection',{}).get('plugin')=='passwall')
            assert value['connection']['domains']==246925 and not value['connection']['confirmed'],value
            assert value['active'] and value['connection']['runtime']['state']=='missing',value
            assert value['queries']==6 and value['blocked']==3 and value['current']['matched']==0,value
            assert json.loads(run('/usr/libexec/adzero/observe','recent-start').stdout)['ok']
            wait_for(lambda s:s.get('recent',{}).get('enabled'))
            query('doubleclick.net',201);query('www.baidu.com',202)
            value=wait_for(lambda s:s.get('queries')==8 and s.get('blocked')==4)
            assert value['recent']['items'][0]['domain']=='doubleclick.net' and len(value['recent']['items'])==1,value
            assert json.loads(run('/usr/libexec/adzero/observe','recent-stop').stdout)['ok']
            assert status()['recent']['items']==[],'stop must hide even a snapshot awaiting its next timer'
            wait_for(lambda s:not s.get('recent',{}).get('enabled'))
            query('doubleclick.net',203)
            value=wait_for(lambda s:s.get('queries')==9 and s.get('blocked')==5)
            assert value['recent']['items']==[],value
            assert json.loads(run('/usr/libexec/adzero/observe','recent-start').stdout)['ok']
            wait_for(lambda s:s.get('recent',{}).get('enabled'))
            query('doubleclick.net',204)
            wait_for(lambda s:s.get('queries')==10 and s.get('blocked')==6 and len(s.get('recent',{}).get('items',[]))==1)
            assert json.loads(run('/usr/libexec/adzero/observe','recent-start').stdout)['ok']
            assert status()['recent']['items']==[],'a new recording must not expose the previous session while waiting for its first tick'
            value=wait_for(lambda s:s.get('recent',{}).get('enabled'))
            assert 'recent_token' not in value,'private recording token must not leave snapshot API'
            control=base/'tmp/adzero-observe/recent.json';data=json.loads(control.read_text());data['deadline']=0
            control.write_text(json.dumps(data));control.chmod(0o600)
            assert not status()['recent']['items'],'expired request cannot expose old domains'
            # A matching rule with a real positive answer diagnoses bypass; it
            # is never silently added to the blocked count, including fake-IP.
            (base/'var/run/adblock-fast/dnsmasq.servers').write_text('server=/doubleclick.net/\nserver=/allowed-by-path.example/\n')
            wait_for(lambda s:s.get('current',{}).get('queries')==0)
            query('allowed-by-path.example',205)
            value=wait_for(lambda s:s.get('queries')==11 and s.get('current',{}).get('matched_positive')==1)
            assert value['blocked']==6 and value['current']['blocked']==0,value
            collector_before=json.loads((base/'tmp/adzero-observe/status.json').read_text())['pid']
            reset=json.loads(run('/usr/libexec/adzero/observe','reset-stats').stdout);assert reset['ok'],reset
            value=wait_for(lambda s:s.get('reset_token')==reset['token'])
            assert value['queries']==0 and value['blocked']==0 and value['current']['queries']==0,value
            assert value['latency']['normal']['samples']==0 and value['latency']['blocked']['samples']==0,value
            assert json.loads((base/'tmp/adzero-observe/status.json').read_text())['pid']==collector_before,'reset restarted collector'
            query('doubleclick.net',300);query('www.baidu.com',301);query('doubleclick.net',302,tcp=True)
            value=wait_for(lambda s:s.get('queries')==3 and s.get('blocked')==2 and s.get('answered')==3)
            assert value['latency']['normal']['samples']==1 and value['latency']['blocked']['samples']==2,value
            assert value['latency']['normal']['average_ms']>=0 and value['latency']['blocked']['average_ms']>=0,value
            time.sleep(1.2);assert status()['queries']==3,'same reset request was applied twice'
            procs[-1].terminate();procs[-1].wait(timeout=4)
            procs.append(subprocess.Popen(['chroot',temp,'/usr/libexec/adzero/observe','run'],stdout=log,stderr=log))
            value=wait_for(lambda s:s.get('available') and s.get('active'))
            assert value['queries']==3 and value['blocked']==2 and value['reset_token']==reset['token'],value
            # Reproduce a proxy's bridge-port DNAT with a real remote client.
            # Everything below remains in private network/mount namespaces.
            client=subprocess.Popen(['unshare','--net',sys.executable,'-u','-c','import time; print("ready",flush=True); time.sleep(120)'],stdout=subprocess.PIPE)
            procs.append(client)
            assert client.stdout.readline().strip()==b'ready'
            def ip(*args):subprocess.run(['ip',*args],check=True,capture_output=True)
            def client_run(*args,input=None):
                return subprocess.run(['nsenter','-t',str(client.pid),'-n',*args],input=input,capture_output=True,text=True,check=True,timeout=8)
            ip('link','add','br-observe','type','bridge')
            ip('link','add','ad-lan','type','veth','peer','name','ad-client')
            ip('link','set','ad-lan','master','br-observe');ip('link','set','ad-lan','up')
            ip('addr','add','192.168.3.1/24','dev','br-observe');ip('link','set','br-observe','up')
            ip('link','set','ad-client','netns',str(client.pid))
            client_run('ip','addr','add','192.168.3.10/24','dev','ad-client');client_run('ip','link','set','ad-client','up')
            # Unrelated WAN-like interface: port-53 traffic here must not count.
            ip('link','add','ad-wan','type','veth','peer','name','ad-outside')
            ip('addr','add','198.51.100.1/24','dev','ad-wan');ip('link','set','ad-wan','up')
            ip('link','set','ad-outside','netns',str(client.pid))
            client_run('ip','addr','add','198.51.100.2/24','dev','ad-outside');client_run('ip','link','set','ad-outside','up')
            nft='table inet observe_test {\n chain dns {\n type nat hook prerouting priority -101; policy accept;\n iifname { "ad-lan", "br-observe" } udp dport 53 redirect to :11553\n iifname { "ad-lan", "br-observe" } tcp dport 53 redirect to :11553\n }\n}\n'
            applied=subprocess.run(['chroot',temp,'/usr/sbin/nft','-f','-'],input=nft,capture_output=True,text=True)
            assert applied.returncode==0,applied.stderr
            # Stop only the isolated observer; the dnsmasq fixtures stay up.
            observer=procs[-2];observer.terminate();observer.wait(timeout=4)
            (base/'bin/ubus').write_text('#!/bin/sh\necho \'{"l3_device":"br-observe"}\'\n')
            bridge_dns=subprocess.Popen(['chroot',temp,'/usr/sbin/dnsmasq','--no-daemon','--conf-file=/dev/null','--no-resolv','--no-hosts','--bind-interfaces','--listen-address=192.168.3.1','--port=11553','--user=root','--server=/doubleclick.net/','--host-record=www.baidu.com,1.2.3.4'],stdout=log,stderr=log)
            procs.append(bridge_dns)
            procs.append(subprocess.Popen(['chroot',temp,'/usr/libexec/adzero/observe','run'],stdout=log,stderr=log))
            value=wait_for(lambda s:s.get('available') and s.get('device')=='br-observe')
            baseline={k:value[k] for k in ('queries','answered','blocked')}
            time.sleep(.4)
            query_code='''import base64,socket,struct,sys
raw=base64.b64decode(sys.argv[1]);tcp=sys.argv[2]=='tcp';target=sys.argv[3]
s=socket.socket(socket.AF_INET,socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM);s.settimeout(2)
if target=='198.51.100.1':s.sendto(raw,(target,53));sys.exit(0)
if tcp:
 s.connect((target,53));s.sendall(struct.pack('!H',len(raw))+raw);r=s.recv(4096)[2:]
else:s.sendto(raw,(target,53));r=s.recv(4096)
assert len(r)>=12 and r[:2]==raw[:2];print(struct.unpack('!H',r[2:4])[0]&15)
'''
            for tcp in (False,True):
                for i,domain in enumerate(('doubleclick.net','www.baidu.com')):
                    value=client_run(sys.executable,'-c',query_code,base64.b64encode(dns(domain,ident=700+i+10*tcp)).decode(),'tcp' if tcp else 'udp','192.168.3.1')
                    assert value.stdout.strip() in ('0','3'),value.stdout
            value=wait_for(lambda s:s.get('queries')==baseline['queries']+4 and s.get('blocked')==baseline['blocked']+2 and s.get('answered')==baseline['answered']+4)
            client_run(sys.executable,'-c',query_code,base64.b64encode(dns('unrelated-wan.example',ident=799)).decode(),'udp','198.51.100.1')
            time.sleep(1.2)
            assert status()['queries']==baseline['queries']+4,'any-device capture included unrelated WAN traffic or duplicated a bridge view'
            report={'source_sha256':__import__('build_sdk').source_hash(ROOT/'luci-app-adzero'),'reset_without_restart':True,'reset_request_applied_once':True,'same_boot_restart_preserves_reset':True,'queries_after_reset':3,'blocked_after_reset':2,'native':'OpenWrt 24.10.8 tcpdump-mini/ucode/uloop/dnsmasq','queries':11,'blocked':6,'udp_tcp':'passed','terminal_challenge':'passed','large_journal_bytes':journal_path.stat().st_size,'stage_offset':stage_offset,'recovery_then_resume_counts':'passed','test_queries_excluded':True,'config_invalidates_receipt':True,'same_boot_restart_preserves_counts':True,'unconfirmed_configuration_not_counted':True,'live_passwall_metadata':'passed','recent_opt_in_stop_expiry':'passed','positive_rule_hit_not_counted_as_block':'passed','scope':'isolated namespace; netifd interface discovery fixture; not a router/LAN certificate'}
            report['bridge_redirect_udp_tcp']={'queries':4,'blocked':2,'answered':4,'wan_excluded':True,'duplicate_views_counted_once':True}
            print(json.dumps(report,indent=2));(ROOT/'artifacts/runtime/observe-result.json').write_text(json.dumps(report,indent=2)+'\n')
        finally:
            for p in reversed(procs):p.terminate()
            for p in reversed(procs):
                try:p.wait(timeout=4)
                except subprocess.TimeoutExpired:p.kill();p.wait()
            log.close()
            subprocess.run(['umount',str(base/'sys')],check=True)
            subprocess.run(['umount',str(base/'proc')],check=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
