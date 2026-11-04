#!/usr/bin/env python3
"""Exercise core-only detection with real OpenWrt ubus sessions in a chroot.

The CGI is a controlled response fixture; no real router/proxy is contacted.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import time

from check_openwrt_runtime import ROOT, SHA256


def main():
    archive=ROOT/'artifacts/runtime/openwrt-24.10.8-x86-64-rootfs.tar.gz'
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==SHA256
    with tempfile.TemporaryDirectory(prefix='adzero-proxy-') as tmp:
        base=Path(tmp)
        with tarfile.open(archive) as tar:tar.extractall(base,filter='fully_trusted')
        for name in ('tmp','dev','var/run/ubus','usr/libexec/adzero','usr/libexec/daed'):(base/name).mkdir(parents=True,exist_ok=True)
        os.mknod(base/'dev/null',0o666|0o20000,os.makedev(1,3))
        os.mknod(base/'dev/urandom',0o666|0o20000,os.makedev(1,9))
        (base/'var/run/ubus/ubus.sock').symlink_to('/tmp/ubus.sock')
        shutil.copyfile(ROOT/'luci-app-adzero/root/usr/libexec/adzero/proxy-state.uc',base/'usr/libexec/adzero/proxy-state.uc')
        shutil.copyfile(ROOT/'luci-app-adzero/root/usr/libexec/adzero/probe.uc',base/'usr/libexec/adzero/probe.uc')
        (base/'usr/libexec/adzero/probe-test.uc').write_text("import * as p from './proxy-state.uc'; let s=p.nailink_state(''); print(sprintf('%J\\n',{running:s.running,phase:s.phase??null}));\n")
        gateway=base/'usr/libexec/daed/luci-proxy'
        gateway.write_text('''#!/bin/sh
set -eu
[ "$HTTP_ORIGIN" = http://localhost ]
session=${HTTP_COOKIE#daed_luci_session=}
umask 077
printf '%s' "$session" > /tmp/last-session
access=$(ubus call session access "{\\"ubus_rpc_session\\":\\"$session\\",\\"scope\\":\\"access-group\\",\\"object\\":\\"luci-app-daed-router\\",\\"function\\":\\"write\\"}" | jsonfilter -e '@.access')
[ "$access" = true ]
if [ "$REQUEST_METHOD" = GET ] && [ "$PATH_INFO" = /health/ready ]; then
 printf 'health\\n' >> /tmp/requests
 case "$(cat /tmp/scenario)" in
  native|native_slow|native_object) printf 'Status: 200 OK\\r\\n\\r\\n{"core":"sing-box","phase":"ready","ready":true,"configured":true,"listening":true,"initialized":true,"dns_ready":true,"routing_ready":true}';;
  issue35) printf 'Status: 200 OK\\r\\n\\r\\n{"core":"dae","phase":"ready","ready":true,"configured":true,"listening":true,"initialized":true,"dns_ready":true,"routing_ready":true}';;
  issue35_stopped) printf 'Status: 200 OK\\r\\n\\r\\n{"core":"dae","phase":"inactive","ready":true,"configured":false}';;
  health_ready|health_slow) printf 'Status: 200 OK\\r\\n\\r\\n{"core":"mihomo","phase":"ready","ready":true,"configured":true,"listening":true,"initialized":true,"dns_ready":true,"routing_ready":true}';;
  health_incomplete) printf 'Status: 200 OK\\r\\n\\r\\n{"core":"mihomo","phase":"ready","ready":true}';;
  health_pending) printf 'Status: 503 Unavailable\\r\\n\\r\\n{"core":"mihomo","phase":"restoring","ready":false}';;
  health_restore_failed) printf 'Status: 503 Unavailable\\r\\n\\r\\n{"core":"mihomo","phase":"dns_route_restore_failed","ready":false}';;
  health_inactive) printf 'Status: 200 OK\\r\\n\\r\\n{"core":"mihomo","phase":"inactive","ready":true,"configured":false}';;
  *) printf 'Status: 503 Unavailable\\r\\n\\r\\n{}';;
 esac
 exit 0
fi
[ "$REQUEST_METHOD" = POST ] && [ "$PATH_INFO" = /graphql ]
body=$(cat)
if [ "$body" = '{"query":"{coreStatus}"}' ]; then
 case "$(cat /tmp/scenario)" in
  issue35|issue35_stopped) printf '%s\\r\\n\\r\\n%s' 'Status: 200 OK' '{"data":{"coreStatus":"{\\"mode\\":\\"tproxy\\",\\"current\\":\\"dae\\",\\"phase\\":\\"stable\\",\\"runtime\\":\\"ready\\",\\"api_only\\":false}"}}';;
  native) printf 'Status: 200 OK\\r\\n\\r\\n{"data":{"coreStatus":"{\\"current\\":\\"sing-box\\",\\"runtime\\":\\"ready\\",\\"running\\":true}"}}';;
  native_slow) sleep 2; printf 'Status: 200 OK\\r\\n\\r\\n{"data":{"coreStatus":"{\\"current\\":\\"sing-box\\",\\"runtime\\":\\"ready\\",\\"running\\":true}"}}';;
  native_object) printf 'Status: 200 OK\\r\\n\\r\\n{"data":{"coreStatus":{"current":"sing-box","runtime":"ready","running":true}}}';;
  health_ready|health_incomplete|health_pending|health_restore_failed|health_inactive) printf 'Status: 502 Unavailable\\r\\n\\r\\nPRIVATE-RESPONSE';;
  health_slow) exec sleep 8;;
  switching) printf 'Status: 200 OK\\r\\n\\r\\n{"data":{"coreStatus":"{\\"current\\":\\"mihomo\\",\\"runtime\\":\\"switching\\",\\"running\\":true}"}}';;
  legacy422) printf 'Status: 422 Unprocessable Entity\\r\\n\\r\\n{"errors":[{"message":"Cannot query field coreStatus on type Query"}]}';;
  core_denied) printf 'Status: 403 Forbidden\\r\\n\\r\\nPRIVATE-RESPONSE';;
  *) printf 'Status: 200 OK\\r\\n\\r\\n{"errors":[{"message":"Cannot query field coreStatus"}]}';;
 esac
 exit 0
fi
[ "$body" = '{"query":"{general{dae{running}}}"}' ]
case "$(cat /tmp/scenario)" in
 legacy422) printf 'Status: 200 OK\\r\\n\\r\\n{"data":{"general":{"dae":{"running":true}}}}';;
 true|false) printf 'Status: 200 OK\\r\\nContent-Type: application/json\\r\\n\\r\\n{"data":{"general":{"dae":{"running":%s}}}}' "$(cat /tmp/scenario)";;
 denied) printf 'Status: 403 Forbidden\\r\\n\\r\\nPRIVATE-RESPONSE';;
 errors) printf 'Status: 200 OK\\r\\n\\r\\n{"errors":[{"message":"PRIVATE-RESPONSE"}],"data":null}';;
 invalid) printf 'Status: 200 OK\\r\\n\\r\\n{"data":{"general":{"dae":{"running":"false"}}}}';;
 slow) exec sleep 4;;
 *) printf 'invalid PRIVATE-RESPONSE';;
esac
''')
        gateway.chmod(0o755)
        child={k:v for k,v in os.environ.items() if not k.startswith('ADZERO_')}
        def run(*args):return subprocess.run(['chroot',tmp,*args],env=child,text=True,capture_output=True,timeout=8)
        procs=[]
        try:
            for command in (['/sbin/ubusd','-s','/tmp/ubus.sock'],['/sbin/rpcd','-s','/tmp/ubus.sock']):
                procs.append(subprocess.Popen(['chroot',tmp,*command],env=child,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
                time.sleep(.2)
            rows=[]
            for scenario,expected in [('issue35',True),('issue35_stopped',False),('true',True),('false',False),('native',True),('native_slow',True),('native_object',True),('health_ready',True),('health_slow',True),('health_incomplete',None),('health_pending',None),('health_restore_failed',None),('health_inactive',False),('switching',None),('legacy422',True),('core_denied',None),('denied',None),('errors',None),('invalid',None),('broken',None),('slow',None)]:
                (base/'tmp/scenario').write_text(scenario)
                (base/'tmp/requests').write_text('')
                started=time.monotonic();result=run('/usr/bin/ucode','/usr/libexec/adzero/probe-test.uc');elapsed=time.monotonic()-started
                assert result.returncode==0,result.stderr
                state=json.loads(result.stdout)
                assert state['running'] is expected,(scenario,result.stdout,result.stderr)
                if scenario=='health_restore_failed':assert state['phase']=='unknown', 'DNS recovery failure is not a core transition'
                if scenario=='health_pending':assert state['phase']=='recovering'
                if scenario=='health_inactive':assert state['phase']=='stopped'
                assert 'PRIVATE' not in result.stdout+result.stderr
                assert not list((base/'tmp').glob('adzero-proxy-*'))
                sid=(base/'tmp/last-session').read_text()
                access=run('/bin/ubus','call','session','access',json.dumps({'ubus_rpc_session':sid,'scope':'access-group','object':'luci-app-daed-router','function':'write'}))
                assert access.returncode!=0 or json.loads(access.stdout).get('access') is False
                assert elapsed<7.2,(scenario,elapsed)
                if scenario in ('core_denied','denied','errors','switching'):
                    assert not (base/'tmp/requests').read_text(), 'Explicit denial/transition must not be overridden by readiness'
                rows.append({'scenario':scenario,'running':expected,'seconds':round(elapsed,3),'session_destroyed':True,'temporary_files_removed':True})
            # A healthy HTTP endpoint alone cannot claim an external worker.
            # Exercise readiness recovery through the complete inventory too.
            (base/'etc/config/daed').write_text("config main 'main'\n option enabled '1'\n")
            manager=base/'proc/4567'
            manager.mkdir(parents=True)
            (manager/'comm').write_text('daed\n')
            worker=base/'proc/4568'
            worker.mkdir()
            (base/'tmp/scenario').write_text('health_ready')
            for core,expected in [('mihomo',True),('sing-box',False),(None,False)]:
                if core:
                    (worker/'cmdline').write_bytes(('/usr/bin/'+core+'\0-c\0/var/run/daed/cores/active-fixture/config.json\0').encode())
                else:
                    (worker/'cmdline').unlink()
                result=run('/usr/bin/ucode','-S','/usr/libexec/adzero/probe.uc')
                assert result.returncode==0,result.stderr
                data=json.loads(result.stdout)
                nailink=next(p for p in data['plugins'] if p['id']=='nailink')
                assert nailink['active'] is expected,(core,nailink)
                assert nailink['runtime_evidence']=='health_api'
                assert data['dns']['uncertain_proxies']==([] if expected else ['nailink'])
                assert 'PRIVATE' not in result.stdout+result.stderr
                assert not list((base/'tmp').glob('adzero-proxy-*'))
                rows.append({'scenario':'health_inventory_'+str(core),'running':expected,'owned_worker_required':True})
            output={'runtime':'real OpenWrt ucode/ubus/rpcd; controlled CGI responses','checks':len(rows),'cases':rows,'device_acceptance':'not_run'}
            (ROOT/'artifacts/runtime/proxy-result.json').write_text(json.dumps(output,indent=2)+'\n')
            print(json.dumps(output,indent=2))
        finally:
            for proc in reversed(procs):proc.terminate();proc.wait(timeout=5)


if __name__=='__main__':main()
