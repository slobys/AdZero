"""Real APK 3 dependency downloads through the universal installer."""

import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import unittest

import test_installer

APK = os.environ.get('ADZERO_APK_TOOL')


@unittest.skipUnless(APK, 'requires the official SDK host apk-tools')
class NativeDependencyFetchTest(unittest.TestCase):
    def setUp(self):
        self.fixture = test_installer.InstallerTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.apk = str(Path(APK).resolve())
        self.repository = self.fixture.base / 'repository'
        self.repository.mkdir()
        self.arch = self.native('--print-arch').stdout.strip()
        self.fixture.write('etc/openwrt_release',
                           "DISTRIB_ID='ImmortalWrt'\nDISTRIB_RELEASE='25.12.1'\n"
                           "DISTRIB_TARGET='x86/64'\nDISTRIB_ARCH='x86_64'\n")
        self.fixture.write('etc/apk/repositories.d/test.list',
                           str(self.repository / 'packages.adb') + '\n')
        base = [self.package(name) for name in ('base-files', 'luci-base', 'rpcd', 'ucode')]
        old = self.package('luci-app-adzero', '0.1.0-r1')
        self.native('--root', self.fixture.root, '--arch', self.arch,
                    'add', '--initdb', '--no-network', '--repositories-file', '/dev/null',
                    *base, old)
        self.dependency = self.package('adblock-fast', '1.2.4-r4', 'ucode')
        self.native('mkndx', '--output', self.repository / 'packages.adb', self.dependency)
        new = self.package('luci-app-adzero', '0.2.0-r1', 'adblock-fast')
        destination = self.fixture.release / new.name
        destination.write_bytes(new.read_bytes())
        (self.fixture.release / 'releases.tsv').write_text(
            f'{hashlib.sha256(new.read_bytes()).hexdigest()}\t{new.name}\topenwrt-family'
            '\tany\tany\tnoarch\tapk\n')
        self.fixture.upgrade_fixture()
        manager = self.fixture.bin / 'apk'
        manager.write_text(
            '#!/bin/sh\nset -eu\n'
            'case " $* " in\n'
            ' *" fetch "*) echo fetch >> "$MOCK_INSTALL_LOG";;\n'
            ' *" add "*) set -- "$@" --usermode; case " $* " in *" --simulate "*) ;;\n'
            ' *) echo commit-apk >> "$MOCK_INSTALL_LOG";; esac;;\nesac\n'
            'exec ' + shlex.quote(self.apk) + ' --allow-untrusted "$@"\n')
        manager.chmod(0o755)

    def native(self, *args, success=True):
        if 'add' in args:
            args = (*args, '--usermode')
        result = subprocess.run([self.apk, '--allow-untrusted', *map(str, args)],
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def package(self, name, version='1-r1', dependency=None):
        package = self.repository / f'{name}-{version}.apk'
        args = ['mkpkg', '--info', 'name:' + name, '--info', 'version:' + version,
                '--info', 'arch:noarch', '--output', package]
        if dependency:
            args += ['--info', 'depends:' + dependency]
        self.native(*args)
        return package

    def test_downloads_missing_dependency_with_installed_native_dependency(self):
        # Reproduce the 3.0.5 query failure independently of shell mocks.
        download = self.fixture.base / 'downloads'
        download.mkdir()
        self.native('--root', self.fixture.root, '--arch', self.arch,
                    'fetch', '--output', download, 'adblock-fast=1.2.4-r4', success=False)
        self.fixture.run_installer('--upgrade')
        self.assertEqual(self.fixture.log.read_text().splitlines(),
                         ['fetch', 'ctl disable', 'commit-apk'])
        self.native('--root', self.fixture.root, 'info', '-e', 'luci-app-adzero=0.2.0-r1')
        self.native('--root', self.fixture.root, 'info', '-e', 'adblock-fast=1.2.4-r4')
        self.native('--root', self.fixture.root, 'info', '-e', 'ucode=1-r1')

    def test_unavailable_download_keeps_installed_app_and_recovery(self):
        self.dependency.unlink()
        database = self.fixture.root / 'lib/apk/db/installed'
        before = database.read_bytes()
        result = self.fixture.run_installer('--upgrade', success=False)
        self.assertIn('adblock-fast', result.stderr)
        self.assertEqual(database.read_bytes(), before)
        self.assertEqual(self.fixture.log.read_text().splitlines(), ['fetch'])
        self.assertTrue((self.fixture.root / 'etc/adzero/adapter.json').exists())

    def test_two_digit_install_plan_downloads_every_missing_package(self):
        extras = [self.package('adzero-fetch-extra-' + str(i)) for i in range(10)]
        self.native('mkndx', '--output', self.repository / 'packages.adb',
                    self.dependency, *extras)
        dependencies = 'adblock-fast ' + ' '.join('adzero-fetch-extra-' + str(i) for i in range(10))
        new = self.package('luci-app-adzero', '0.3.0-r1', dependencies)
        (self.fixture.release / new.name).write_bytes(new.read_bytes())
        (self.fixture.release / 'releases.tsv').write_text(
            f'{hashlib.sha256(new.read_bytes()).hexdigest()}\t{new.name}\topenwrt-family'
            '\tany\tany\tnoarch\tapk\n')
        self.fixture.run_installer('--upgrade')
        operations = self.fixture.log.read_text().splitlines()
        self.assertEqual(operations, ['fetch'] * 11 + ['ctl disable', 'commit-apk'])
        self.native('--root', self.fixture.root, 'info', '-e', 'luci-app-adzero=0.3.0-r1')
        for i in range(10):
            self.native('--root', self.fixture.root, 'info', '-e', 'adzero-fetch-extra-' + str(i))
