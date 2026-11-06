#!/usr/bin/env python3
"""Real NaiLink CGI + API-only daemon + rpcd in a disposable rootfs.

The pinned daemon never starts the proxy kernel. No LAN, router credentials,
production database, DNS service or eBPF changes are involved.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import urllib.request
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor

from build_sdk import source_hash
from prepare_dns_socket import install as install_socket
from check_openwrt_runtime import ROOT, SHA256 as ROOTFS_SHA256

PACKAGE_SHA256 = '666dc4d2313a9954ae8be5ae023f5cbe99a12240de80d2d4ce0afc8f977d9d5b'
PACKAGE_URL = 'https://github.com/slobys/NaiLink/releases/download/openwrt-v0.6.30/daed-router-0.6.30-x86_64.ipk'


def payload_file(package, name):
    raw = package.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PACKAGE_SHA256
    if raw.startswith(b'!<arch>\n'):
        offset = 8
        data = None
        while offset < len(raw):
            header = raw[offset:offset + 60]
            size = int(header[48:58])
            offset += 60
            if header[:16].decode().strip().rstrip('/') == 'data.tar.gz':
                data = raw[offset:offset + size]
                break
            offset += size + size % 2
        assert data is not None
    else:
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:*') as archive:
            member = next(m for m in archive.getmembers() if m.name.endswith('data.tar.gz'))
            data = archive.extractfile(member).read()
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        member = next(m for m in archive.getmembers() if m.name.lstrip('./') == name)
        return archive.extractfile(member).read()


def main(package=None):
    if os.geteuid() != 0:
        raise SystemExit('Run as root in Linux/WSL; disposable chroot only')
    if package is None:
        package = ROOT / 'artifacts/runtime/nailink-0.6.30-x86_64.ipk'
        if not package.exists():
            with urllib.request.urlopen(PACKAGE_URL, timeout=90) as response:
                raw = response.read(40 * 1024 * 1024)
            assert hashlib.sha256(raw).hexdigest() == PACKAGE_SHA256
            package.write_bytes(raw)
    rootfs = ROOT / 'artifacts/runtime/openwrt-24.10.8-x86-64-rootfs.tar.gz'
    assert hashlib.sha256(rootfs.read_bytes()).hexdigest() == ROOTFS_SHA256
    gateway = payload_file(package, 'usr/libexec/daed/luci-proxy')
    daemon = payload_file(package, 'usr/bin/daed')
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'ADZERO_ADAPTER_ROOT': '/',
           'ADZERO_ADAPTER_INVENTORY': '/tmp/inventory.json'}
    checks = []
    with tempfile.TemporaryDirectory(prefix='adzero-nailink-gateway-') as temporary:
        base = Path(temporary)
        with tarfile.open(rootfs) as archive:
            archive.extractall(base, filter='fully_trusted')
        install_socket(ROOT,base)

        def put(name, data, mode=0o644):
            target = base / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data if isinstance(data, bytes) else data.encode())
            target.chmod(mode)
            return target

        def run(*args, timeout=15, input=None):
            if args[:2]==('/bin/sh','/usr/libexec/adzero/ctl'):
                # This fixture intentionally has a synthetic /proc for later
                # reload-identity tests. Mirror only this real ctl process,
                # before releasing its gate, so lock identity can be checked.
                gate='/tmp/ctl-ready-'+os.urandom(8).hex()
                command=['/bin/sh','-c','while [ ! -f "$1" ]; do sleep 0.01; done; shift; exec "$@"','adzero-ctl-gate',gate,*args]
                child=subprocess.Popen(['chroot',temporary,*command],env=env,text=True,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                identity=base/'proc'/str(child.pid)/'stat'
                try:
                    put('proc/'+str(child.pid)+'/stat',Path(f'/proc/{child.pid}/stat').read_bytes())
                    put(gate.lstrip('/'),'ready\n',0o600)
                    stdout,stderr=child.communicate(input=input,timeout=timeout)
                    return subprocess.CompletedProcess(args,child.returncode,stdout,stderr)
                finally:
                    if child.poll() is None:child.kill();child.communicate(timeout=5)
                    (base/gate.lstrip('/')).unlink(missing_ok=True)
                    identity.unlink(missing_ok=True)
                    if identity.parent.exists():identity.parent.rmdir()
            return subprocess.run(['chroot', temporary, *args], env=env, text=True,
                                  capture_output=True, timeout=timeout, input=input)

        def adapter(action='nailink-status'):
            return run('/usr/bin/ucode', '-S', '/usr/libexec/adzero/adapter.uc', '--', action)

        def query(query,variables=None):
            sid=json.loads(run('/bin/ubus','call','session','create','{"timeout":60}').stdout)['ubus_rpc_session']
            try:
                grant={'ubus_rpc_session':sid,'scope':'access-group','objects':[['luci-app-daed-router','write']]}
                assert run('/bin/ubus','call','session','grant',json.dumps(grant)).returncode==0
                payload=json.dumps({'query':query,'variables':variables or {}})
                value=run('/usr/bin/env','REQUEST_METHOD=POST','REQUEST_URI=/cgi-bin/daed-router/graphql',
                    'SCRIPT_NAME=/cgi-bin/daed-router','PATH_INFO=/graphql','SERVER_PROTOCOL=HTTP/1.1',
                    'HTTP_HOST=localhost','HTTP_ORIGIN=http://localhost','HTTP_COOKIE=daed_luci_session='+sid,
                    'CONTENT_TYPE=application/json','CONTENT_LENGTH='+str(len(payload)),'/usr/libexec/daed/luci-proxy',input=payload)
                assert value.returncode==0,value.stderr
                result=json.loads(value.stdout.split('\n\n',1)[1]);assert not result.get('errors'),result
                return result['data']
            finally: run('/bin/ubus','call','session','destroy',json.dumps({'ubus_rpc_session':sid}))

        def snapshot():
            with sqlite3.connect(base / 'etc/daed/wing.db') as db:
                names = [r[0] for r in db.execute("select name from sqlite_master where type='table' order by name")]
                return {name: db.execute('select * from "' + name.replace('"', '""') + '" order by rowid').fetchall() for name in names}

        for directory in ['dev', 'tmp', 'etc/adzero', 'var/run/ubus', 'proc/self']:
            (base / directory).mkdir(parents=True, exist_ok=True)
        os.mknod(base / 'dev/null', 0o666 | 0o20000, os.makedev(1, 3))
        os.mknod(base / 'dev/urandom', 0o666 | 0o20000, os.makedev(1, 9))
        (base / 'var/run/ubus/ubus.sock').symlink_to('/tmp/ubus.sock')
        for source in (ROOT / 'luci-app-adzero/root/usr/libexec/adzero').iterdir():
            if source.is_file():
                put('usr/libexec/adzero/' + source.name, source.read_bytes(), 0o755)
        put('usr/libexec/daed/luci-proxy', gateway, 0o755)
        put('usr/bin/daed', daemon, 0o755)
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        put('etc/config/daed', f"config daed 'main'\n option listen_port '{port}'\n")
        config = put('etc/config/adzero', "config adzero 'main'\n option enabled '0'\n option routing_enabled '0'\n")
        backend_config = put('etc/config/adblock-fast', "config adblock-fast 'config'\n option enabled '0'\n option dns 'dnsmasq.servers'\n")
        put('etc/init.d/adblock-fast', '#!/bin/sh\ntouch /tmp/backend-was-called\nexit 0\n', 0o755)
        put('tmp/inventory.json', json.dumps({'plugins': [{'id': 'nailink', 'installed': True, 'enabled': True, 'runtime': 'running'}]}))
        # procd process inventory and /proc identity records below are fixtures;
        # gateway/backend authorization and ubus execution remain real.
        put('usr/share/rpcd/ucode/adzero_service_fixture', "import * as fs from 'fs';\nreturn {service:{list:{args:{name:''},call:()=>json(fs.readfile('/tmp/service-data.json'))}}};\n")
        put('tmp/service-data.json','{}')
        processes = []
        with (ROOT / 'artifacts/runtime/nailink-gateway.log').open('w+') as log:
            def prepare():
                value = run('/usr/libexec/daed/luci-proxy', '--prepare', timeout=30)
                assert value.returncode == 0, 'isolated prepare failed'

            def start():
                process = subprocess.Popen(['chroot', temporary, '/usr/bin/daed', 'run', '--api-only',
                    '--config', '/etc/daed', '--listen', f'127.0.0.1:{port}'], env=env, stdout=log, stderr=log)
                processes.append(process)
                for _ in range(80):
                    try:
                        request = urllib.request.Request(f'http://127.0.0.1:{port}/graphql', b'{"query":"{healthCheck}"}', {'Content-Type': 'application/json'})
                        with urllib.request.urlopen(request, timeout=.3) as response:
                            assert json.load(response)['data']['healthCheck'] == 1
                        return process
                    except Exception:
                        assert process.poll() is None, 'isolated API-only daemon exited'
                        time.sleep(.1)
                raise AssertionError('isolated API-only daemon not ready')

            try:
                processes.append(subprocess.Popen(['chroot', temporary, '/sbin/ubusd', '-s', '/tmp/ubus.sock'], env=env, stdout=log, stderr=log))
                time.sleep(.2)
                processes.append(subprocess.Popen(['chroot', temporary, '/sbin/rpcd', '-s', '/tmp/ubus.sock'], env=env, stdout=log, stderr=log))
                time.sleep(.3)
                prepare()
                process = start()
                value = adapter()
                assert value.returncode == 0 and json.loads(value.stdout) == {'authorized': True, 'running': False, 'modified': False}, value.stderr
                checks.append('real CGI + daemon JWT + rpcd authorization')
                with ThreadPoolExecutor(max_workers=4) as pool:
                    concurrent=list(pool.map(lambda _:adapter(),range(12)))
                assert all(v.returncode==0 and json.loads(v.stdout)['authorized'] for v in concurrent),[v.stderr for v in concurrent if v.returncode]
                assert not list((base/'etc/adzero').glob('graphql-*')),'request/response files were not cleaned'
                checks.append('concurrent root diagnostics use distinct session-scoped request/response files and preserve all responses')
                dns_text='upstream { local: "udp://223.5.5.5:53" }\nrouting { request { fallback: local } }'
                created=query('mutation($dns:String!){createDns(name:"version-barrier",dns:$dns){id}}',{'dns':dns_text})['createDns']['id']
                with sqlite3.connect(base/'etc/daed/wing.db') as db:
                    version=db.execute('select version from dns where name=?',('version-barrier',)).fetchone()[0]
                query('mutation($id:ID!,$dns:String!){updateDns(id:$id,dns:$dns){id}}',{'id':created,'dns':dns_text})
                with sqlite3.connect(base/'etc/daed/wing.db') as db:
                    assert db.execute('select version from dns where name=?',('version-barrier',)).fetchone()[0]==version+1
                checks.append('real NaiLink API increments DNS profile version for identical text; no database writes by AdZero')
                alias_dns='bind: "tcp+udp://192.168.50.1:5355"\n'+dns_text
                query('query($raw:String!){parsedDns(raw:$raw){string}}',{'raw':alias_dns})
                query('query($raw:String!){parsedRouting(raw:$raw){string}}',{'raw':'sip(192.168.50.1/24) && dip(192.168.50.1) && dport(53) -> must_direct\nfallback: direct'})
                query('mutation($id:ID!,$dns:String!){updateDns(id:$id,dns:$dns){id}}',{'id':created,'dns':alias_dns})
                stored=query('{dnss{id dns{string}}}')['dnss']
                assert next(row for row in stored if row['id']==created)['dns']['string']==alias_dns
                query('mutation($id:ID!,$dns:String!){updateDns(id:$id,dns:$dns){id}}',{'id':created,'dns':dns_text})
                checks.append('native alias listener and narrow must_direct syntax accepted; DNS API roundtrip preserves bind without starting the core')
                before = snapshot()
                original = config.read_bytes()
                put('etc/daed/luci-session.json', json.dumps({'username': 'luci', 'secret': 'intentionally-stale-test-key'}), 0o600)
                value = adapter()
                assert value.returncode != 0 and 'GraphQL authorization denied' in value.stderr and 'intentionally-stale' not in value.stderr
                checks.append('stale local identity reproduces access denied without key exposure')
                value = adapter('check')
                assert value.returncode != 0 and config.read_bytes() == original and not (base / 'etc/adzero/adapter.json').exists()
                checks.append('authorization preflight rejects before adapter ownership or config writes')
                value = run('/bin/sh','/usr/libexec/adzero/ctl','enable')
                assert value.returncode != 0 and 'GraphQL authorization denied' in value.stderr, value.stderr
                assert not (base/'tmp/backend-was-called').exists() and not (base/'etc/adzero/managed').exists()
                assert config.read_bytes()==original and "option enabled '0'" in backend_config.read_text()
                checks.append('production ctl refuses before backend startup, configuration or ownership changes')
                process.terminate()
                process.wait(timeout=8)
                processes.remove(process)
                prepare()  # The official helper runs with the service stopped.
                process = start()
                value = adapter()
                assert value.returncode == 0 and json.loads(value.stdout)['authorized']
                assert snapshot() == before, 'official identity preparation changed existing database rows'
                checks.append('official stopped-service prepare restores access and preserves all database rows')
                put('tmp/service-data.json',json.dumps({'daed':{'instances':{'main':{'running':True,'pid':process.pid}}}}))
                stat=Path(f'/proc/{process.pid}/stat').read_text()
                start_ticks=stat.rsplit(') ',1)[1].split()[19]
                put(f'proc/{process.pid}/stat',stat)
                put('proc/sys/kernel/random/boot_id','test-boot-id\n')
                identity={'boot':'test-boot-id','pid':process.pid,'start':start_ticks}
                recovery={'schema':1,'plugin':'nailink','stage':'restarting','files':[],'flags':[],
                          'nailink_reload_unconfirmed':True,'nailink_run_identity':identity}
                record=put('etc/adzero/adapter.json',json.dumps(recovery),0o600)
                value=adapter('remove')
                assert value.returncode!=0 and 'no second mutation submitted' in value.stderr,value.stderr
                assert json.loads(record.read_text())==recovery
                checks.append('same procd process identity blocks a second mutation after ambiguous reload')
                # Same PID with different start ticks must count as a new daemon.
                fields=stat.rsplit(') ',1)[1].split();fields[19]=str(int(start_ticks)+1)
                put(f'proc/{process.pid}/stat',stat.rsplit(') ',1)[0]+') '+' '.join(fields)+'\n')
                put('usr/libexec/daed/luci-proxy.real',gateway,0o755)
                put('usr/libexec/daed/luci-proxy', '''#!/bin/sh
umask 077
request=$(mktemp /tmp/gateway-fixture.XXXXXX)
trap 'rm -f "$request"' EXIT
cat > "$request"
if grep -q 'run(dry:false)' "$request"; then
    printf 'Status: 200 OK\\nContent-Type: application/json\\n\\n{"data":{"run":1}}'
else
    /usr/libexec/daed/luci-proxy.real < "$request"
fi
''',0o755)
                value=adapter('remove')
                assert value.returncode==0 and not record.exists(),value.stderr
                checks.append('new process start plus real authorized API allows explicit recovery; reload ack fixture')
                # Protocol-only fixtures exercise production response handling;
                # they do not replace the real JWT/backend checks above.
                put('usr/libexec/daed/luci-proxy', """#!/bin/sh
if [ "$PATH_INFO" = /health/ready ]; then
 if [ -f /tmp/core-switching ]; then
  printf 'Status: 503 Unavailable\\r\\n\\r\\n%s' '{"core":"dae","phase":"core_switching","ready":false}'
 else
  printf 'Status: 200 OK\\r\\n\\r\\n%s' '{"core":"dae","phase":"ready","ready":true,"configured":true,"listening":true,"initialized":true,"dns_ready":true,"routing_ready":true}'
 fi
else
 case "$(cat)" in
  *coreStatus*) printf 'Status: 200 OK\\r\\n\\r\\n%s' '{"data":{"coreStatus":{"current":"dae","phase":"stable","runtime":"ready","api_only":false}}}';;
  *) printf 'Status: 200 OK\\r\\n\\r\\n%s' '{"data":{"general":{"dae":{"running":true,"modified":false}}}}';;
 esac
fi
""", 0o755)
                value = adapter('check')
                assert value.returncode == 0 and value.stdout.strip() == 'nailink', value.stderr
                switch = put('tmp/core-switching', '')
                value = adapter('check')
                assert value.returncode != 0 and '正在切换内核' in value.stderr, value.stderr
                switch.unlink()
                assert not record.exists()
                checks.append('missing persisted running uses live readiness; HTTP 503 transition blocks configuration')
                for status, expected in [(401, 'local session denied'), (403, 'local session denied'),
                                         (502, 'completion unconfirmed'), (503, 'not ready'), (413, 'too large')]:
                    put('usr/libexec/daed/luci-proxy', f"#!/bin/sh\nprintf 'Status: {status} Fixture\\nContent-Type: text/plain\\n\\nprivate-response-marker'\n", 0o755)
                    value = adapter()
                    assert value.returncode != 0 and f'HTTP {status}' in value.stderr and expected in value.stderr
                    assert 'private-response-marker' not in value.stderr
                    checks.append(f'HTTP {status} classified without private response')
                put('usr/libexec/daed/luci-proxy', """#!/bin/sh
printf '%s' 'Status: 200 OK
Content-Type: application/json

{"errors":[{"message":"private-response-marker"}],"data":null}'
""", 0o755)
                value = adapter()
                assert value.returncode != 0 and 'private-response-marker' not in value.stderr
                checks.append('unknown GraphQL error remains private')
                put('usr/libexec/daed/luci-proxy', '#!/bin/sh\nsleep 8\n',0o755)
                started=time.monotonic();value=adapter();elapsed=time.monotonic()-started
                assert value.returncode!=0 and elapsed<7,(elapsed,value.stderr)
                checks.append('unresponsive read-only CGI is bounded to five seconds rather than the 30-second gateway deadline')
                process.terminate(); process.wait(timeout=8); processes.remove(process)
                upstream={'dns':'upstream { local: "udp://223.5.5.5:53" }\nrouting { request { fallback: local } }','calls':[],'modified':False,'complete':False}
                class SlowAPI(BaseHTTPRequestHandler):
                    def log_message(self,*_): pass
                    def do_GET(self):
                        assert self.path=='/health/ready'
                        raw=json.dumps({k:upstream['complete'] for k in ['ready','configured','listening','initialized','dns_ready','routing_ready']}).encode()
                        self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                    def do_POST(self):
                        request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                        query=request['query']; variables=request.get('variables',{})
                        if 'general{' in query: operation='read'; data={'general':{'dae':{'running':True,'modified':upstream['modified']}}}
                        elif 'dnss(' in query: operation='read_dns'; data={'dnss':[{'id':'dns','dns':{'string':upstream['dns']}}]}
                        elif 'updateDns' in query: operation='write_dns'; upstream['dns']=variables['dns']; upstream['modified']=True;data={'updateDns':{'id':'dns'}}
                        elif 'run(dry:false)' in query: operation='reload'; data={'run':1}
                        else: operation='validate_dns'; data={'parsedDns':{'string':''}}
                        upstream['calls'].append(operation)
                        if operation=='reload': time.sleep(31.5);upstream['modified']=False;upstream['complete']=True
                        raw=json.dumps({'data':data}).encode()
                        try:
                            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                        except (BrokenPipeError,ConnectionResetError): pass
                with ThreadingHTTPServer(('127.0.0.1',port),SlowAPI) as server:
                    threading.Thread(target=server.serve_forever,daemon=True).start()
                    put('usr/libexec/daed/luci-proxy',gateway,0o755)
                    put('etc/config/adzero',"config adzero 'main'\n option enabled '1'\n option routing_enabled '0'\n")
                    put('var/run/adblock-fast/dnsmasq.servers','server=/ads.example/\n')
                    try:
                        value=run('/usr/bin/ucode','-S','/usr/libexec/adzero/adapter.uc','--','apply',timeout=50)
                        assert value.returncode==0,value.stderr
                        state=json.loads(record.read_text())
                        assert not state.get('nailink_reload_unconfirmed') and state['stage']=='configured_unverified',state
                        assert upstream['calls'].count('reload')==1 and upstream['calls'].count('write_dns')==1,upstream['calls']
                        checks.append('actual 30-second CGI timeout followed by native-shaped version and dataplane confirmation succeeds with one reload and no rollback')
                        # DAT-only changes must create a fresh version barrier:
                        # an old running=true/modified=false would be insufficient.
                        put('var/run/adblock-fast/dnsmasq.servers','server=/new-ads.example/\n')
                        upstream['complete']=False
                        value=run('/usr/bin/ucode','-S','/usr/libexec/adzero/adapter.uc','--','apply',timeout=50)
                        assert value.returncode==0,value.stderr
                        assert upstream['calls'].count('reload')==2 and upstream['calls'].count('write_dns')==2,upstream['calls']
                        checks.append('DAT-only update advances DNS profile version before a single reload; stale ready state is not accepted')
                    finally: server.shutdown()
            finally:
                for process in reversed(processes):
                    process.terminate()
                    process.wait(timeout=8)
    report = {'source_sha256': source_hash(ROOT / 'luci-app-adzero'), 'nailink_package_sha256': PACKAGE_SHA256,
              'nailink_gateway_sha256': hashlib.sha256(gateway).hexdigest(), 'checks': checks,
              'runtime': 'actual NaiLink 0.6.30 CGI + API-only daemon and actual OpenWrt rpcd/ubusd/ucode',
              'proxy_kernel_and_router': 'not_run', 'existing_database_rows_preserved': True,
              'timeout_fixture': 'real CGI 30-second deadline, delayed upstream/version/readiness and procd inventory fixtures; no claim of actual proxy kernel reload completion'}
    (ROOT / 'artifacts/runtime/nailink-gateway-result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, help='Optional locally cached checksum-pinned NaiLink IPK')
    main(parser.parse_args().package)
