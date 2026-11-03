#!/usr/bin/env python3
"""Real pool/ucode/DNS startup in a private netns; async service manager fixture.

No router, LAN, proxy selection, UCI commit or host firewall is modified.
--baseline optionally reproduces an older adapter function with fractional
BusyBox sleep disabled. The supplied source is executed only in the chroot.
"""
from pathlib import Path
import argparse,hashlib,io,json,os,shutil,socket,socketserver,struct,subprocess,sys,tarfile,tempfile,threading,time,urllib.request
from check_openwrt_runtime import ROOT,SHA256,URL
from prepare_dns_socket import prepare,install
from build_sdk import source_hash
from check_dns_core import question,recv_exact
TIMEOUT='coreutils-timeout_9.7-r1_x86_64.ipk'
TIMEOUT_SHA='a011462f8462400ba6699066821f27ee6ddf6aea75c612087303c1f22de2796e'

def response(raw):
    kind=struct.unpack('!H',raw[-4:-2])[0];ip=socket.inet_pton(socket.AF_INET6 if kind==28 else socket.AF_INET,'2001:db8::9' if kind==28 else '192.0.2.9')
    return raw[:2]+struct.pack('!HHHHH',0x8180,1,1,0,0)+raw[12:]+b'\xc0\x0c'+struct.pack('!HHIH',kind,1,60,len(ip))+ip

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inside',action='store_true');parser.add_argument('--baseline',type=Path);a=parser.parse_args()
    rootfs=ROOT/'artifacts/runtime/openwrt-24.10.8-x86-64-rootfs.tar.gz';extra=ROOT/'artifacts/runtime/passwall-extras'/TIMEOUT
    if not a.inside:
        if os.geteuid()!=0:raise SystemExit('Run in Linux/WSL as root')
        prepare(ROOT,None)
        for path,url,digest in [(rootfs,URL,SHA256),(extra,'https://archive.openwrt.org/releases/24.10.8/packages/x86_64/packages/'+TIMEOUT,TIMEOUT_SHA)]:
            if not path.exists():
                path.parent.mkdir(parents=True,exist_ok=True)
                with urllib.request.urlopen(url,timeout=60) as r:path.write_bytes(r.read(20*1024*1024))
            assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
        return subprocess.call(['unshare','--net','--mount','--propagation','private',sys.executable,__file__,'--inside',*(['--baseline',str(a.baseline.resolve())] if a.baseline else [])])
    subprocess.run(['ip','link','set','lo','up'],check=True)
    for address in ['192.0.2.53','192.0.2.54','192.0.2.55','192.0.2.56','192.0.2.57','192.0.2.58']:subprocess.run(['ip','addr','add',address+'/32','dev','lo'],check=True)
    class TCP(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            try:
                if getattr(self.server,'socks',False):
                    assert recv_exact(self.request,3)==b'\x05\x01\x00';self.request.sendall(b'\x05\x00')
                    assert recv_exact(self.request,4)==b'\x05\x01\x00\x01';address=socket.inet_ntoa(recv_exact(self.request,4));port=struct.unpack('!H',recv_exact(self.request,2))[0]
                    assert address in ['192.0.2.54','192.0.2.56','192.0.2.58'] and port==53
                    self.server.socks_hits+=1;self.request.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00')
                while True:
                    raw=recv_exact(self.request,struct.unpack('!H',recv_exact(self.request,2))[0]);out=response(raw)
                    if getattr(self.server,'fail_dns',False) or getattr(self.server,'socks',False) and address=='192.0.2.58':out=raw[:2]+struct.pack('!HHHHH',0x8182,1,0,0,0)+raw[12:]
                    self.request.sendall(struct.pack('!H',len(out))+out)
            except OSError:pass
    services=[]
    for address in ['192.0.2.53','192.0.2.54','192.0.2.55','192.0.2.56','192.0.2.58']:
        server=socketserver.ThreadingTCPServer((address,53),TCP);server.daemon_threads=True;server.fail_dns=address=='192.0.2.58';services.append(server);threading.Thread(target=server.serve_forever,daemon=True).start()
    class UDP(socketserver.BaseRequestHandler):
        def handle(self):raw,s=self.request;s.sendto(response(raw),self.client_address)
    udp=socketserver.ThreadingUDPServer(('192.0.2.57',53),UDP);services.append(udp);threading.Thread(target=udp.serve_forever,daemon=True).start()
    socks=socketserver.ThreadingTCPServer(('127.0.0.1',0),TCP);socks.daemon_threads=True;socks.socks=True;socks.socks_hits=0;services.append(socks);threading.Thread(target=socks.serve_forever,daemon=True).start()
    rows=[]
    with tempfile.TemporaryDirectory(prefix='adzero-pool-runtime-') as tmp:
        base=Path(tmp)
        with tarfile.open(rootfs) as t:t.extractall(base,filter='fully_trusted')
        install(ROOT,base)
        with tarfile.open(extra) as outer:
            member=next(m for m in outer if m.name.endswith('data.tar.gz'))
            with tarfile.open(fileobj=io.BytesIO(outer.extractfile(member).read())) as t:t.extractall(base,filter='fully_trusted')
        for name in ['proc','dev','tmp','etc/adzero','etc/daed']:(base/name).mkdir(parents=True,exist_ok=True)
        os.mknod(base/'dev/null',0o666|0o20000,os.makedev(1,3));os.mknod(base/'dev/urandom',0o666|0o20000,os.makedev(1,9))
        shutil.copytree(ROOT/'luci-app-adzero/root/usr/libexec/adzero',base/'usr/libexec/adzero',dirs_exist_ok=True)
        # A symlink must be unlinked before writing, to preserve BusyBox itself.
        sleeper=base/'bin/sleep';sleeper.unlink(missing_ok=True);sleeper.write_text('#!/bin/sh\ncase "$1" in *.*) echo called >/tmp/fractional-sleep-called; echo "sleep: invalid number" >&2; exit 1;; esac\nexec /bin/busybox sleep "$@"\n');sleeper.chmod(0o755)
        init=base/'etc/init.d/adzero-dns';init.write_text('#!/bin/sh\n[ "$1" != start ] || touch /tmp/pool-start-request\n');init.chmod(0o755)
        (base/'etc/daed/adzero-cn-domains.txt').write_text('cn.example\n')
        prefix="""import * as fs from 'fs';import * as route from '/usr/libexec/adzero/routing-lib.uc';import * as progress from '/usr/libexec/adzero/progress-lib.uc';import * as poolstate from '/usr/libexec/adzero/dns-pool-state.uc';import {now as monotime} from '/usr/libexec/adzero/dns-io.uc';
const root='',state_dir='/etc/adzero';let prepare_pool,verify_pool,c=fs.readfile('/tmp/profile'),dns_profile=route.profile(json(c)),uci={get:()=> 'tcp'};
function atomic(path,raw,mode){let f=fs.open(path,'wx',mode);if(!f)die('write');f.write(raw);f.close();}
function pool_needed(plugin){return true;}function service_command(plugin,action){return ['/bin/sh','/etc/init.d/adzero-dns',action];}function timeout_helper(){return '/usr/libexec/timeout-coreutils';}
"""
        def fragment(path):
            text=path.read_text();start='verify_pool=function(plugin){' if 'verify_pool=function(plugin){' in text else 'prepare_pool=function(plugin){';return text[text.index(start):text.index('\nstrict_target=function(plugin){')]
        suffix="""let start=monotime(),ok=false,error=null;try{prepare_pool('nailink');ok=true;}catch(e){error=e.message;}print(sprintf('%J',{ok,error,elapsed:monotime()-start}));exit(ok?0:1);"""
        (base/'tmp/prepare.uc').write_text(prefix+fragment(ROOT/'luci-app-adzero/root/usr/libexec/adzero/adapter.uc')+suffix)
        if a.baseline:(base/'tmp/baseline.uc').write_text(prefix+fragment(a.baseline)+suffix)
        subprocess.run(['mount','--bind','/proc',str(base/'proc')],check=True)
        def attempt(label,profile,delay=0,occupied=None,baseline=False,expected_reason=None,reuse_failure=False):
            for name in ['pool-start-request','adzero-dns-pool.json','fractional-sleep-called']:(base/'tmp'/name).unlink(missing_ok=True)
            (base/'etc/adzero/dns-pool-needed').unlink(missing_ok=True)
            (base/'tmp/profile').write_text(json.dumps(profile))
            (base/'etc/config/adzero').write_text("config adzero 'main'\n option enabled '1'\n option routing_enabled '1'\n option dns_profile '"+json.dumps(profile)+"'\n")
            (base/'tmp/job').mkdir(exist_ok=True)
            (base/'tmp/job/reason').unlink(missing_ok=True)
            childenv={k:v for k,v in os.environ.items() if not k.startswith('ADZERO_') and k not in ['LD_LIBRARY_PATH','RUBYLIB']};childenv.update(PATH='/usr/sbin:/usr/bin:/sbin:/bin',ADZERO_JOB_CONTEXT='/tmp/job')
            stop=threading.Event();children=[]
            def launch():
                while not stop.wait(.005):
                    if (base/'tmp/pool-start-request').exists():break
                if not stop.wait(delay):children.append(subprocess.Popen(['chroot',tmp,'/usr/bin/ucode','-S','/usr/libexec/adzero/dns-pool.uc'],env=childenv,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
            hold=None
            if occupied:
                port,tcp=occupied;hold=socket.socket(socket.AF_INET,socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM);hold.bind(('127.0.0.1',port))
                if tcp:hold.listen()
            worker=threading.Thread(target=launch);worker.start()
            try:
                p=subprocess.run(['chroot',tmp,'/usr/bin/ucode','-S','/tmp/baseline.uc' if baseline else '/tmp/prepare.uc'],env=childenv,text=True,capture_output=True,timeout=7)
                r=json.loads(p.stdout);r.update(case=label,code=p.returncode,reason=(base/'tmp/job/reason').read_text() if (base/'tmp/job/reason').exists() else None)
                if baseline:assert not r['ok'] and r['elapsed']<.6 and (base/'tmp/fractional-sleep-called').exists(),r
                elif occupied:assert not r['ok'] and r['reason']=='dns_pool_bind_failed',r
                elif expected_reason:assert not r['ok'] and r['reason']==expected_reason,r
                else:
                    assert p.returncode==0 and r['ok'] and not (base/'tmp/fractional-sleep-called').exists(),(r,p.stderr)
                    checked=0
                    for port in [5356,5357,5358]:
                        for tcp in [False,True]:
                            for kind in [1,28]:
                                for name in ['cn.example','global.example']:
                                    raw=question(name,kind)
                                    with socket.socket(socket.AF_INET,socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM) as s:
                                        s.settimeout(3);s.connect(('127.0.0.1',port));s.sendall((struct.pack('!H',len(raw)) if tcp else b'')+raw)
                                        out=recv_exact(s,struct.unpack('!H',recv_exact(s,2))[0]) if tcp else s.recv(4096)
                                    assert out[:2]==raw[:2] and out[3]&15==0 and struct.unpack('!H',out[6:8])[0]==1,(port,tcp,kind,out.hex());checked+=1
                    r['dns_queries']=checked
                    if reuse_failure:
                        for server in services[:5]:server.fail_dns=True
                        retry=subprocess.run(['chroot',tmp,'/usr/bin/ucode','-S','/tmp/prepare.uc'],env=childenv,text=True,capture_output=True,timeout=7)
                        assert retry.returncode!=0 and (base/'tmp/job/reason').read_text()=='dns_pool_domestic_failed',(retry.stdout,retry.stderr)
                        r['reuse_upstream_failure_rejected']=True
                        for server in services[:5]:server.fail_dns=server.server_address[0]=='192.0.2.58'
                rows.append(r)
            finally:
                stop.set();worker.join(2)
                for p in children:
                    if p.poll() is None:p.terminate()
                    p.wait(timeout=3)
                if hold:hold.close()
        p={'domestic':{'servers':['192.0.2.53','192.0.2.55'],'active':'192.0.2.53','mode':'parallel'},'foreign':{'servers':['192.0.2.54','192.0.2.56'],'active':'192.0.2.54'}}
        try:
            if a.baseline:attempt('old_fractional_wait',p,.65,baseline=True)
            attempt('delayed_650ms_domestic_parallel',p,.65)
            p['domestic'].pop('mode');p['foreign']['mode']='parallel';p['proxy_port']=socks.server_address[1];attempt('foreign_parallel',p)
            p['domestic']['mode']='parallel';attempt('both_parallel',p)
            strict=json.loads(json.dumps(p));strict['domestic'].pop('mode');strict['foreign'].pop('mode');strict.update(strict=True,proxy_port=socks.server_address[1]);attempt('strict_via_local_socks',strict);assert socks.socks_hits>0
            for port in [5356,5357,5358]:
                for tcp in [False,True]:attempt(f'occupied_{port}_'+('tcp' if tcp else 'udp'),p,occupied=(port,tcp))
            attempt('ready_after_failed_starts',p,reuse_failure=True)
            bad=json.loads(json.dumps(p));bad['domestic'].update(servers=['192.0.2.57'],active='192.0.2.57')
            attempt('listener_ready_but_tcp_unreachable',bad,expected_reason='dns_pool_domestic_failed')
            bad['domestic'].update(servers=['192.0.2.58'],active='192.0.2.58');attempt('listener_ready_but_servfail',bad,expected_reason='dns_pool_domestic_failed')
            bad=json.loads(json.dumps(p));bad['foreign'].update(servers=['192.0.2.58'],active='192.0.2.58');attempt('socks_dns_failure_no_direct_fallback',bad,expected_reason='dns_pool_foreign_failed')
            def preflight(label,profile,plugin='nailink',mode='tcp',reason=None):
                tracked=[base/'etc/config/adzero',base/'etc/adzero/dns-pool-needed'];before=[f.read_bytes() if f.exists() else None for f in tracked]
                run=subprocess.run(['chroot',tmp,'/usr/bin/ucode','-S','/usr/libexec/adzero/dns-pool.uc','check-profile',plugin,json.dumps(profile),mode],capture_output=True,text=True,timeout=10)
                assert run.returncode==0,run.stderr
                r=json.loads(run.stdout);assert r==({'ready':False,'reason':reason} if reason else {'ready':True}),r
                assert before==[f.read_bytes() if f.exists() else None for f in tracked]
                rows.append(dict(case=label,**r))
            preflight('candidate_socks_not_stored_uci',p)
            missing={**p,'proxy_port':0};preflight('missing_socks_rejected',missing,reason='dns_socks_required')
            udp_only=json.loads(json.dumps(p));udp_only['domestic'].update(servers=['192.0.2.57'],active='192.0.2.57')
            q=question('cn.example',1)
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:s.settimeout(2);s.sendto(q,('192.0.2.57',53));assert s.recv(4096)==response(q)
            (base/'tmp/io-test.uc').write_text("import * as io from '/usr/libexec/adzero/dns-io.uc';print(sprintf('%J',[io.test('192.0.2.57',false,0,false),io.test('192.0.2.57',false,0,true)]));")
            io_run=subprocess.run(['chroot',tmp,'/usr/bin/ucode','-S','/tmp/io-test.uc'],capture_output=True,text=True,timeout=6);assert io_run.returncode==0,io_run.stderr
            io_rows=json.loads(io_run.stdout);assert io_rows[0]['ok'] and io_rows[0]['path']=='direct_udp' and not io_rows[1]['ok'] and io_rows[1]['path']=='direct_tcp',io_rows
            rows.append(dict(case='test_uses_actual_udp_or_tcp',results=io_rows))
            preflight('udp_success_does_not_prove_tcp',udp_only,reason='dns_pool_domestic_failed')
            preflight('passwall_domestic_same_tcp_risk',udp_only,'passwall','xray',reason='dns_passwall_mode')
            udp_only['foreign'].pop('mode');preflight('passwall_xray_domestic_preflight',udp_only,'passwall','xray',reason='dns_pool_domestic_failed')
            preflight('passwall_tcp_via_socks',p,'passwall','tcp')
        finally:subprocess.run(['umount',str(base/'proc')],check=True)
    for server in services:server.shutdown();server.server_close()
    report={'source_sha256':source_hash(ROOT/'luci-app-adzero'),'cases':rows,'scope':'real OpenWrt ucode, production prepare function and pool, loopback DNS in private netns; async service manager fixture; no router or proxy acceptance'}
    (ROOT/'artifacts/runtime/dns-pool-start-result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':sys.exit(main())
