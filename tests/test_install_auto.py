"""One pinned stable installer, independent of firmware channels."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class UnifiedEntrypoint(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / 'bin'; self.bin.mkdir()
        self.root = self.base / 'root'; (self.root / 'etc').mkdir(parents=True)
        self.child = self.base / 'child.sh'
        self.child.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$AUTO_RESULT"\n')
        source = (ROOT / 'scripts/install-auto.sh').read_text()
        source = re.sub(r'INSTALL_SHA256=[a-f0-9]{64}', 'INSTALL_SHA256=' + hashlib.sha256(self.child.read_bytes()).hexdigest(), source)
        self.script = self.base / 'entry.sh'; self.script.write_text(source)
        curl = self.bin / 'curl'
        curl.write_text('''#!/usr/bin/env python3
import os,pathlib,sys
a=sys.argv[1:]
assert 'https://github.com/slobys/AdZero/releases/download/v0.23.4/install.sh' in a
pathlib.Path(os.environ['AUTO_FETCH']).write_text('fetched')
pathlib.Path(a[a.index('-o')+1]).write_bytes(pathlib.Path(os.environ['AUTO_CHILD']).read_bytes())
'''); curl.chmod(0o755)
        self.result, self.fetched = self.base/'result', self.base/'fetch'
        self.env = {**os.environ, 'PATH': str(self.bin)+':'+os.environ['PATH'], 'AUTO_CHILD':str(self.child), 'AUTO_RESULT':str(self.result), 'AUTO_FETCH':str(self.fetched), 'ADZERO_AUTO_TEST_ROOT':str(self.root), 'ADZERO_AUTO_TEST_MODE':'1'}

    def run_entry(self, *args):
        return subprocess.run(shlex.split(os.environ.get('ADZERO_TEST_SHELL','sh'))+[str(self.script),*args], env=self.env, text=True, input='', capture_output=True, timeout=10)

    def test_pinned_digest_matches_the_only_current_installer(self):
        source=(ROOT/'scripts/install-auto.sh').read_text()
        self.assertIn('INSTALL_SHA256='+hashlib.sha256((ROOT/'scripts/install.sh').read_bytes()).hexdigest(),source)
        self.assertNotIn('-beta',source)
        self.assertNotIn('PIN_ISTOREOS',source)

    def test_all_three_firmwares_use_same_script(self):
        for family in ['OpenWrt','ImmortalWrt','iStoreOS']:
            (self.root/'etc/openwrt_release').write_text(f"DISTRIB_ID='{family}'\n")
            result=self.run_entry('--check')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(self.result.read_text().strip(),'--check')

    def test_detection_is_local_and_does_not_claim_installability(self):
        (self.root/'etc/openwrt_release').write_text("DISTRIB_ID='iStoreOS'\nDISTRIB_RELEASE='24.10.9'\n")
        result=self.run_entry('--detect')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('24.10.9',result.stdout); self.assertIn('--check',result.stdout)
        self.assertFalse(self.fetched.exists())

    def test_checksum_failure_never_executes_download(self):
        self.child.write_text('#!/bin/sh\ntouch "$AUTO_RESULT"\n')
        result=self.run_entry('--menu')
        self.assertNotEqual(result.returncode,0); self.assertIn('SHA-256',result.stderr)
        self.assertFalse(self.result.exists())

    def test_unapproved_url_or_shell_arguments_never_download(self):
        for arg in ['--base-url','https://invalid/',';touch /tmp/wrong']:
            self.assertNotEqual(self.run_entry(arg).returncode,0)
        self.assertFalse(self.fetched.exists())

    def test_modes_preserve_arguments(self):
        result=self.run_entry('--upgrade','--no-enable')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.result.read_text().splitlines(),['--upgrade','--no-enable'])

    def test_stock_router_can_bootstrap_with_wget_without_curl(self):
        (self.bin/'curl').unlink()
        for name in ['sh','sha256sum','mktemp','rm','rmdir','python3']:
            (self.bin/name).symlink_to(shutil.which(name))
        wget=self.bin/'wget'
        wget.write_text('''#!/usr/bin/env python3
import os,pathlib,sys
a=sys.argv[1:]
pathlib.Path(a[a.index('-O')+1]).write_bytes(pathlib.Path(os.environ['AUTO_CHILD']).read_bytes())
''');wget.chmod(0o755)
        self.env['PATH']=str(self.bin)
        result=self.run_entry('--check')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.result.read_text().strip(),'--check')
