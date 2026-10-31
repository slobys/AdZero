"""Generic app selection and dependency safety; real solvers are checked separately."""
from pathlib import Path
import hashlib
import io
import tarfile
import unittest
import test_installer

MANAGER = r'''#!/usr/bin/env python3
import json,os,pathlib,sys
a=sys.argv[1:];manager=pathlib.Path(sys.argv[0]).name
def log(s):
 with open(os.environ['MOCK_INSTALL_LOG'],'a') as f:f.write(s+'\n')
for opt in ['--root','--arch','--offline-root','--lists-dir','--cache']:
 if opt in a:i=a.index(opt);a[i:i+2]=[]
if 'info' in a or 'status' in a:
 name=a[-1]
 if name not in ['base-files','luci-base','rpcd','ucode'] and not (name=='luci-app-adzero' and os.environ.get('EXISTING_APP')=='1'):sys.exit(1)
 if manager=='apk':print(name);sys.exit(0)
 state=json.loads(os.environ.get('OPKG_STATES','{}')).get(name,'install '+os.environ.get('OPKG_FLAG','ok')+' installed')
 if state is None:sys.exit(1)
 reply='Package: '+os.environ.get('OPKG_REPLY_NAME',name)+'\nStatus: '+state
 if os.environ.get('OPKG_DUPLICATE')=='1':reply+='\n\n'+reply
 print(reply);sys.exit(int(os.environ.get('OPKG_QUERY_EXIT','0')))
if 'update' in a:log('refresh');sys.exit(int(os.environ.get('BROKEN_FEED','0')))
if 'fetch' in a or '--download-only' in a:
 log('fetch-before-disable')
 if os.environ.get('DOWNLOAD_FAIL')=='1':sys.exit(1)
 orig=sys.argv;opt='--output' if 'fetch' in a else '--cache'
 dest=pathlib.Path(orig[orig.index(opt)+1]);(dest/('conntrack-1-r1.apk' if manager=='apk' else 'conntrack_1-r1_all.ipk')).write_bytes(b'dep')
 sys.exit(0)
if '--simulate' in a or '--noaction' in a:
 if os.environ.get('OTHER_UCODE_ABI')=='1':print('(1/2) Installing libucode20990101 (1-r1)');sys.exit(0)
 if os.environ.get('UNSAFE_PLAN')=='1':print('Upgrading libc (1-r1 -> 2-r1)');sys.exit(0)
 app=next(pathlib.Path(x) for x in a if x.endswith(('.apk','.ipk')))
 if os.environ.get('MISSING_DEP')=='1' and ('--no-network' in sys.argv or '--lists-dir' in sys.argv) and not list((app.parent/'deps').glob('*')):print('missing conntrack');sys.exit(1)
 print('(1/2) Installing luci-app-adzero (0.23.4-r1)' if manager=='apk' else 'Installing luci-app-adzero (0.23.4-r1) to root...')
 if os.environ.get('MISSING_DEP')=='1':print('(2/2) Installing conntrack (1-r1)')
 sys.exit(0)
if 'add' in a or 'install' in a:
 if os.environ.get('EXPECTED_EXTRA_PACKAGES'):
  assert len([x for x in a if x.endswith(('.apk','.ipk'))])-1==int(os.environ['EXPECTED_EXTRA_PACKAGES']),a
 log('commit-'+manager);sys.exit(0)
sys.exit('unexpected '+repr(a))
'''


class PortableInstaller(unittest.TestCase):
    def setUp(self):
        # Reuse the real safety harness without inheriting/rerunning its tests.
        self.f=test_installer.InstallerTest();self.f.setUp();self.addCleanup(self.f.doCleanups)
        (self.f.release/'dependencies.tsv').write_text('')

    def platform(self,family='OpenWrt',manager='apk',release='25.12.6'):
        f=self.f
        for n in ['apk','opkg']:(f.bin/n).unlink(missing_ok=True)
        f.write('etc/openwrt_release',f"DISTRIB_ID='{family}'\nDISTRIB_RELEASE='{release}'\nDISTRIB_TARGET='x86/64'\nDISTRIB_ARCH='x86_64'\nDISTRIB_REVISION='2026102312'\n")
        p=f.bin/manager;p.write_text(MANAGER);p.chmod(0o755)
        ext='ipk' if manager=='opkg' else 'apk'
        (f.release/('adzero-test.'+ext)).write_bytes((f.release/'adzero-test.ipk').read_bytes())
        (f.release/'releases.tsv').write_text(f'{f.sum}\tadzero-test.{ext}\topenwrt-family\tany\tany\t'+('all' if ext=='ipk' else 'noarch')+'\t'+ext+'\n')

    def events(self,*args,success=True):
        self.f.run_installer(*args,success=success)
        return self.f.log.read_text().splitlines() if self.f.log.exists() else []

    def test_shared_app_all_three_families_both_formats(self):
        for family in ['OpenWrt','ImmortalWrt','iStoreOS']:
            for manager in ['opkg','apk']:
                with self.subTest(family=family,manager=manager):
                    self.f.log.unlink(missing_ok=True);self.platform(family,manager)
                    self.assertEqual(self.events(),['commit-'+manager])

    def test_new_patch_does_not_need_another_application_release(self):
        self.platform('ImmortalWrt',release='25.12.9')
        self.assertEqual(self.events(),['commit-apk'])

    def test_native_opkg_installed_flags_are_not_a_firmware_allowlist(self):
        for family in ('OpenWrt','ImmortalWrt','iStoreOS'):
            for flag in ('ok','user','hold','user,hold'):
                with self.subTest(family=family,flag=flag):
                    self.platform(family,manager='opkg',release='24.10.7');self.f.log.unlink(missing_ok=True)
                    self.f.env['OPKG_FLAG']=flag
                    self.assertEqual(self.events('--check'),[])
                    self.assertFalse((self.f.root/'etc/adzero-backups').exists())

    def test_user_installed_packages_can_complete_fresh_ipk_install(self):
        self.platform('iStoreOS',manager='opkg',release='24.10.7');self.f.env['OPKG_FLAG']='user'
        self.assertEqual(self.events(),['commit-opkg'])

    def test_user_installed_app_upgrade_preserves_the_protection_switch(self):
        self.platform('iStoreOS',manager='opkg',release='24.10.7');f=self.f
        f.env.update(OPKG_FLAG='user',EXISTING_APP='1')
        f.write('etc/adzero/managed','1');f.executable(f.bin/'uci','#!/bin/sh\necho 1\n')
        f.executable(f.root/'usr/libexec/adzero/ctl','#!/bin/sh\necho "ctl $1" >> "$MOCK_INSTALL_LOG"\n[ "$1" != disable ] || rm "$ADZERO_INSTALL_ROOT/etc/adzero/managed"\n')
        self.assertEqual(self.events(),['ctl disable','commit-opkg','ctl enable'])

    def test_incomplete_opkg_package_state_stops_before_install_or_disable(self):
        for state in (None,'install ok unpacked','deinstall ok config-files','unknown ok not-installed','install ok half-configured'):
            with self.subTest(state=state):
                self.platform('iStoreOS',manager='opkg',release='24.10.7')
                self.f.env.update(OPKG_STATES=__import__('json').dumps({'luci-base':state}),EXISTING_APP='1')
                self.f.log.unlink(missing_ok=True)
                result=self.f.run_installer('--upgrade',success=False)
                self.assertIn('luci-base',result.stdout+result.stderr)
                self.assertFalse(self.f.log.exists());self.assertFalse((self.f.root/'etc/adzero-backups').exists())

    def test_failed_wrong_or_ambiguous_opkg_status_is_not_installed(self):
        for options in ({'OPKG_QUERY_EXIT':'1'},{'OPKG_REPLY_NAME':'unrelated'},{'OPKG_DUPLICATE':'1'}):
            with self.subTest(options=options):
                self.platform('iStoreOS',manager='opkg',release='24.10.7')
                for key in ('OPKG_QUERY_EXIT','OPKG_REPLY_NAME','OPKG_DUPLICATE'):self.f.env.pop(key,None)
                self.f.env.update(options)
                self.assertEqual(self.events('--check',success=False),[])

    def test_crlf_catalog_still_selects_same_application(self):
        self.platform();p=self.f.release/'releases.tsv';p.write_bytes(p.read_bytes().replace(b'\n',b'\r\n'))
        self.assertEqual(self.events(),['commit-apk'])

    def test_new_module_cannot_mix_with_another_ucode_engine_abi(self):
        self.platform();self.f.env.update(OTHER_UCODE_ABI='1',EXISTING_APP='1')
        self.assertEqual(self.events('--upgrade',success=False),[])

    def test_native_apk_with_relocated_db_overrides_stale_opkg(self):
        self.platform('iStoreOS');self.f.executable(self.f.bin/'opkg','#!/bin/sh\nexit 1\n')
        self.assertFalse((self.f.root/'lib/apk/db/installed').exists())
        self.assertEqual(self.events(),['commit-apk'])

    def test_check_does_not_install_or_refresh(self):
        self.platform();self.assertEqual(self.events('--check'),[])
        self.assertFalse((self.f.root/'etc/adzero-backups').exists())

    def test_dependency_failure_does_not_disable_old_protection(self):
        for manager in ['apk','opkg']:
            with self.subTest(manager=manager):
                self.f.log.unlink(missing_ok=True);self.platform(manager=manager)
                self.f.env.update(MISSING_DEP='1',DOWNLOAD_FAIL='1',EXISTING_APP='1')
                events=self.events('--upgrade',success=False)
                self.assertNotIn('ctl disable',events)
                self.assertFalse(any(e.startswith('commit') for e in events))

    def test_native_dependency_download_completes_before_old_restore(self):
        for manager in ['apk','opkg']:
            with self.subTest(manager=manager):
                self.f.log.unlink(missing_ok=True);self.platform(manager=manager)
                self.f.env.update(MISSING_DEP='1',EXISTING_APP='1',EXPECTED_EXTRA_PACKAGES='1')
                self.assertEqual(self.events('--upgrade'),['fetch-before-disable','ctl disable','commit-'+manager])

    def test_exact_vendor_supplement_passes_all_missing_files_to_native_manager(self):
        self.platform('iStoreOS');f=self.f
        f.env.update(MISSING_DEP='1',EXPECTED_EXTRA_PACKAGES='6')
        names=['adblock-fast','conntrack','coreutils-timeout','libnetfilter-cthelper0','libnetfilter-cttimeout1','libnetfilter-queue1']
        profile='istoreos-25.12.6-2026102312-x86-64-x86_64-apk';files={};rows=[]
        for name in names:
            filename=name+'-1-r1.apk';data=name.encode();files[profile+'/packages/'+filename]=data
            rows.append(hashlib.sha256(data).hexdigest()+'\t'+filename+'\t'+name+'\n')
        files[profile+'/packages.tsv']=''.join(rows).encode()
        p=f.release/'adzero-dependencies.tar.gz'
        with tarfile.open(p,'w:gz') as t:
            for name,data in files.items():
                m=tarfile.TarInfo(name);m.size=len(data);t.addfile(m,io.BytesIO(data))
        sha=hashlib.sha256(p.read_bytes()).hexdigest()
        (f.release/'dependencies.tsv').write_text(f'{sha}\t{p.name}\tistoreos\t25.12.6\t2026102312\tx86/64\tx86_64\tapk\t{profile}\n')
        self.assertEqual(self.events(),['commit-apk'])

    def test_system_replacement_is_blocked_before_disable(self):
        self.platform();self.f.env.update(UNSAFE_PLAN='1',EXISTING_APP='1')
        self.assertEqual(self.events('--upgrade',success=False),[])

    def test_unrelated_feed_failure_does_not_block_satisfied_local_plan(self):
        self.platform();self.f.env['BROKEN_FEED']='1'
        self.assertEqual(self.events(),['commit-apk'])

    def test_unknown_firmware_is_not_treated_as_openwrt(self):
        self.platform('UnknownFirmware');self.assertEqual(self.events(success=False),[])

    def test_upgrade_preserves_enabled_switch_without_beta_markers(self):
        self.platform('iStoreOS');f=self.f;f.env['EXISTING_APP']='1'
        f.write('etc/adzero/managed','1');f.executable(f.bin/'uci','#!/bin/sh\necho 1\n')
        f.executable(f.root/'usr/libexec/adzero/ctl','#!/bin/sh\necho "ctl $1" >> "$MOCK_INSTALL_LOG"\n[ "$1" != disable ] || rm "$ADZERO_INSTALL_ROOT/etc/adzero/managed"\n')
        self.assertEqual(self.events(),['ctl disable','commit-apk','ctl enable'])
