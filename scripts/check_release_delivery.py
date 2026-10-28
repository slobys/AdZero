#!/usr/bin/env python3
"""Exercise delivered catalogs/packages with isolated installer/PM fixtures.

Run in Linux/WSL. ADZERO_TEST_SHELL may select the pinned OpenWrt ash.
No real packages, router services or network settings are modified.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from test_installer import InstallerTest
from build_release import validate_release


def check(directory):
    if os.name != 'posix':
        raise ValueError('Run the isolated installer lifecycle in Linux/WSL')
    directory = directory.resolve()
    validate_release(directory)
    shell = shlex.split(os.environ.get('ADZERO_TEST_SHELL', 'sh'))
    # Require the entire verified release matrix for distribution packages,
    # including vendor-specific builds. Tests of single-row release catalogs
    # deliberately use build_release.validate_release() directly instead.
    from build_release import SUPPORTED_BUILDS, SUPPORTED_REVISIONS
    catalog_rows = (directory / 'releases.tsv').read_text().splitlines()
    available = {
        (parts[2], parts[3], parts[4], parts[6])
        for line in catalog_rows if not line.startswith('#')
        for parts in [line.split('\t')] if len(parts) in (7, 8)
    }
    if available != set(SUPPORTED_BUILDS):
        raise ValueError(
            f'Release must contain all exact SDK-verified identities; '
            f'missing={set(SUPPORTED_BUILDS)-available}'
        )
    # Maintain explicit per-platform acceptance. A future iStoreOS SDK row
    # requires a pinned original firmware revision as well as its own package.
    platforms = [
        ('openwrt', '24.10.8', 'x86/64', 'x86_64', 'ipk', '24.10.8-x86_64.ipk', None),
        ('openwrt', '25.12.5', 'x86/64', 'x86_64', 'apk', '25.12.5-x86_64.apk', None),
        ('openwrt', '25.12.5', 'rockchip/armv8', 'aarch64_generic', 'apk', 'rockchip-armv8-aarch64_generic.apk', None),
        ('immortalwrt', '24.10.6', 'x86/64', 'x86_64', 'ipk', 'immortalwrt-24.10.6-x86_64.ipk', None),
        ('immortalwrt', '25.12.2', 'x86/64', 'x86_64', 'apk', 'immortalwrt-25.12.2-x86_64.apk', None),
    ]
    istore = ('istoreos', '25.12.6', 'x86/64', 'apk')
    if istore in SUPPORTED_BUILDS:
        if SUPPORTED_BUILDS[istore] != 'x86_64' or SUPPORTED_REVISIONS.get(istore) != '2026102312':
            raise ValueError('Unknown iStoreOS build revision or ABI; refuse delivery')
        platforms.append(
            ('istoreos', '25.12.6', 'x86/64', 'x86_64', 'apk',
             'istoreos-25.12.6-x86_64.apk', '2026102312')
        )
    if len(platforms) != len(SUPPORTED_BUILDS):
        raise ValueError('The exact SDK-verified platform matrix lacks delivery tests')
    results = []
    for firmware, version, target, arch, fmt, expected_suffix, revision in platforms:
        for ending in ('LF', 'CRLF'):
            kind = f'{firmware}-{version}-{target}-{fmt}'
            fixture = InstallerTest()
            fixture.setUp()
            try:
                if fmt == 'apk':
                    fixture.arm_apk(arch='aarch64' if target == 'rockchip/armv8' else 'x86_64')
                original_id = {
                    'openwrt': 'OpenWrt', 'immortalwrt': 'ImmortalWrt',
                    'istoreos': 'iStoreOS',
                }[firmware]
                fixture.write('etc/openwrt_release',
                              f"DISTRIB_ID='{original_id}'\nDISTRIB_RELEASE='{version}'\n"
                              f"DISTRIB_TARGET='{target}'\nDISTRIB_ARCH='{arch}'\n"
                              + (f"DISTRIB_REVISION='{revision}'\n" if revision else ''))
                delivered = fixture.base / 'delivered'
                shutil.copytree(directory, delivered)
                if ending == 'CRLF':
                    path = delivered / 'releases.tsv'
                    path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
                fixture.upgrade_fixture()
                result = subprocess.run(shell + [
                    str(delivered / 'install.sh'), '--local-dir', str(delivered),
                    '--upgrade', '--no-enable',
                ], env=fixture.env, text=True, capture_output=True, timeout=20)
                if result.returncode:
                    raise AssertionError(kind + ' ' + ending + ': ' + result.stdout + result.stderr)
                assert expected_suffix in result.stdout, result.stdout
                expected = (['opkg update', 'ctl disable', 'opkg install']
                            if fmt == 'ipk' else
                            ['apk check-local', 'ctl disable', 'apk add-local'])
                assert fixture.log.read_text().splitlines() == expected
                assert list((fixture.root / 'etc/adzero-backups').glob('*/state.tar.gz'))
                results.append({'target': kind, 'catalog_ending': ending,
                                'selection_checksum_backup_restore_install': 'passed'})
            finally:
                fixture.doCleanups()
    return {'checks': len(results), 'cases': results,
            'scope': 'actual delivered installer, catalog and SDK package bytes; mock PM/service operations in private roots; not a real router installation'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = check(args.directory)
    text = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.write_text(text, encoding='utf-8', newline='\n')
    print(text, end='')
