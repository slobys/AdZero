"""Real ucode + controlled CGI/ubus boundary; no router or proxy is contacted."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
UCODE = shlex.split(os.environ.get('ADZERO_UCODE', ''))


def health(core='dae', **changes):
    return dict(core=core, phase='ready', ready=True, configured=True,
                listening=True, initialized=True, dns_ready=True,
                routing_ready=True) | changes


@unittest.skipUnless(UCODE, 'requires real ucode/fs')
class NaiLinkStateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('ADZERO_PROBE_')}
        self.env['NAILINK_TEST_ROOT'] = str(self.root)
        (self.root / 'tmp').mkdir()
        source = (ROOT / 'luci-app-adzero/root/usr/libexec/adzero/proxy-state.uc').read_text()
        # Replace only the session transport boundary; parsing, CGI execution,
        # response validation and cleanup run unchanged in the real interpreter.
        (self.root / 'proxy-state.uc').write_text(source.replace("from 'ubus'", "from './ubus.uc'"))
        (self.root / 'ubus.uc').write_text('''import * as fs from 'fs';
export function connect() { return {
 call: function(object, method, args) {
  let f=fs.open(getenv('NAILINK_TEST_ROOT')+'/sessions','a'); f.write(method+'\\n'); f.close();
  if(method=='create') return {ubus_rpc_session:'0123456789abcdef0123456789abcdef'};
  if(method=='access') return {access:true};
  return {};
 }, disconnect: function() {}
}; };
''')
        (self.root / 'run.uc').write_text("import * as p from './proxy-state.uc'; print(sprintf('%J',p.nailink_state(getenv('NAILINK_TEST_ROOT'))));")
        cgi = self.root / 'usr/libexec/daed/luci-proxy'
        cgi.parent.mkdir(parents=True)
        cgi.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys, time
root=pathlib.Path(os.environ['NAILINK_TEST_ROOT'])
assert os.environ['HTTP_COOKIE']=='daed_luci_session=0123456789abcdef0123456789abcdef'
assert os.environ['HTTP_ORIGIN']=='http://localhost'
payload=sys.stdin.read()
assert len(payload)==int(os.environ['CONTENT_LENGTH'])
path=os.environ['PATH_INFO']
key=json.loads(payload)['query'] if path=='/graphql' else path
with (root/'requests').open('a') as f: f.write(key+'\\n')
response=json.loads((root/'responses.json').read_text())[key]
time.sleep(response.get('delay',0))
print('Status: '+str(response.get('code',200))+' Test\\r\\n\\r')
print(response['raw'] if 'raw' in response else json.dumps(response['body']))
''')
        cgi.chmod(0o755)

    def query(self, status, ready=None, *, encoded=True, code=200, raw=None, errors=None, delay=0):
        data = json.dumps(status) if encoded else status
        response = {'body': {'data': {'coreStatus': data}}}
        if errors:
            response['body']['errors'] = errors
        ready_response = {'code': code, 'body': ready, 'delay': delay}
        if raw is not None:
            ready_response['raw'] = raw
        (self.root / 'responses.json').write_text(json.dumps({
            '{coreStatus}': response, '/health/ready': ready_response,
            '{general{dae{running}}}': {'body': {'data': {'general': {'dae': {'running': True}}}}},
        }))
        for name in ('requests', 'sessions'):
            (self.root / name).write_text('')
        result = subprocess.run(UCODE + ['-L', str(self.root / '*.uc'), str(self.root / 'run.uc')],
                                env=self.env, text=True, capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('PRIVATE', result.stdout + result.stderr)
        self.assertFalse(list((self.root / 'tmp').glob('adzero-proxy-*')))
        self.assertEqual((self.root / 'sessions').read_text().splitlines(), ['create', 'grant', 'access', 'destroy'])
        self.requests = (self.root / 'requests').read_text().splitlines()
        return json.loads(result.stdout)

    def test_issue_35_exact_graphql_payload(self):
        status = {'mode': 'tproxy', 'current': 'dae', 'phase': 'stable', 'runtime': 'ready',
                  'api_only': False, 'memory': {'backend_kib': 122184, 'total_kib': 122184, 'complete': True}}
        result = self.query(status, health())
        self.assertIs(result['running'], True)
        self.assertEqual(result['phase'], 'ready')
        self.assertEqual(result['evidence'], 'health_api')
        self.assertEqual(self.requests, ['{coreStatus}', '/health/ready'])

    def test_ready_requires_live_readiness_for_all_cores_and_optional_running(self):
        for core in ('dae', 'sing-box', 'mihomo'):
            for running in ({}, {'running': None}, {'running': True}):
                for encoded in (True, False):
                    with self.subTest(core=core, running=running, encoded=encoded):
                        status = dict(current=core, phase='rolled_back', runtime='ready', api_only=False) | running
                        self.assertIs(self.query(status, health(core), encoded=encoded)['running'], True)

    def test_ready_manager_with_stopped_core_including_stale_running_flag(self):
        for running in ({}, {'running': None}, {'running': True}):
            result = self.query(dict(current='dae', phase='stable', runtime='ready', api_only=False) | running,
                                health(phase='inactive', configured=False, initialized=False, listening=False,
                                       dns_ready=False, routing_ready=False))
            self.assertIs(result['running'], False)
            self.assertEqual(result['phase'], 'stopped')

    def test_slow_health_authorization_has_same_budget_as_core_status(self):
        status = dict(current='dae',phase='stable',runtime='ready',api_only=False)
        self.assertIs(self.query(status,health(),delay=2)['running'],True)

    def test_unsafe_status_never_uses_health_or_legacy_to_claim_ready(self):
        base = dict(current='dae', phase='stable', runtime='ready', api_only=False)
        cases = [dict(running=v) for v in (False, 'true', 1, [], {})]
        cases += [dict(api_only=v) for v in (True, 'false', 0, [], {})]
        cases += [dict(runtime=v) for v in ('starting', 'switching', 'reloading', 'recovering', 'failed', 'unknown', None)]
        cases += [dict(phase=v) for v in ('switching', 'rollback_failed', 'failed', 'unknown', 1)]
        for changes in cases:
            with self.subTest(changes=changes):
                result = self.query(base | changes, health())
                self.assertIsNone(result['running'])
                self.assertEqual(self.requests, ['{coreStatus}'])

    def test_stopped_idle_and_conflicting_running_flag(self):
        for phase in ('stopped', 'idle'):
            for running, expected in ((False, False), (None, False), (True, None), ('false', None)):
                with self.subTest(phase=phase, running=running):
                    result = self.query(dict(current='dae', runtime=phase, running=running), health())
                    self.assertIs(result['running'], expected)
                    self.assertEqual(self.requests, ['{coreStatus}'])

    def test_health_core_mismatch_and_incomplete_or_unready_responses(self):
        status = dict(current='dae', phase='stable', runtime='ready', api_only=False)
        cases = [health('mihomo'), health('invalid'), None, {}, health(ready='true'),
                 health(phase='inactive', configured=True), health(phase='core_switching', ready=False)]
        for field in ('configured', 'listening', 'initialized', 'dns_ready', 'routing_ready'):
            for value in (False, None, 'true', 1):
                cases.append(health(**{field: value}))
        for value in cases:
            with self.subTest(value=value):
                self.assertIsNone(self.query(status, value)['running'])
        for code in (401, 403, 500):
            self.assertIsNone(self.query(status, health(), code=code)['running'])
        self.assertIsNone(self.query(status, raw='PRIVATE-broken-json')['running'])

    def test_graphql_denial_does_not_use_healthy_endpoint(self):
        status = dict(current='dae', phase='stable', runtime='ready', api_only=False)
        self.assertIsNone(self.query(status, health(), errors=[{'message': 'PRIVATE-denied'}])['running'])
        self.assertEqual(self.requests, ['{coreStatus}'])

    def test_invalid_core_status_cannot_be_overridden_by_health(self):
        for status in ('{PRIVATE-broken', 'null', '[]', 'false', 1, [], {},
                       {'current':'unknown','runtime':'ready','api_only':True}):
            with self.subTest(status=status):
                self.assertIsNone(self.query(status, health(), encoded=False)['running'])
                self.assertEqual(self.requests, ['{coreStatus}'])

    def test_legacy_boolean_fallback_remains_available(self):
        self.assertIs(self.query(None, health(), encoded=False)['running'], True)
        self.assertEqual(self.requests, ['{coreStatus}', '{general{dae{running}}}'])


if __name__ == '__main__':
    unittest.main()
