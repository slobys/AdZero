#!/usr/bin/env python3
"""Check installer status parsing with pinned native OPKG, without installs."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
import tempfile

from check_openwrt_runtime import ROOT, SHA256, URL


def check():
    archive = ROOT / 'artifacts/runtime/openwrt-24.10.8-x86-64-rootfs.tar.gz'
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise ValueError('Pinned official OPKG rootfs differs; run check_openwrt_runtime.py first')
    installer = (ROOT / 'scripts/install.sh').read_text()
    functions = installer[installer.index('opkg_native() {'):installer.index('detect_manager() {')]
    report = []
    with tempfile.TemporaryDirectory(prefix='adzero-native-opkg-status-') as d:
        home = Path(d);program = home/'program';system = home/'root';commands = home/'bin'
        for p in (program, commands, system/'usr/lib/opkg', system/'var/lock'):
            p.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as image:
            files = {m.name.lstrip('./'): m for m in image.getmembers()}
            for name in ('bin/opkg', 'bin/busybox', 'lib/libc.so', 'lib/libubox.so.20240329', 'lib/libgcc_s.so.1'):
                item = files[name]
                if not item.isfile():
                    raise ValueError('Unexpected native OPKG library member')
                target = program/Path(name).name;target.write_bytes(image.extractfile(item).read());target.chmod(0o755)
            original = image.extractfile(files['usr/lib/opkg/status']).read()
        conf = home/'opkg.conf';conf.write_text('dest root /\narch all 100\narch x86_64 200\n')
        binary = [str(program/'libc.so'), '--library-path', str(program), str(program/'opkg'), '-f', str(conf)]
        wrapper = commands/'opkg';wrapper.write_text('#!/bin/sh\nexec '+shlex.join(binary)+' "$@"\n');wrapper.chmod(0o755)
        script = home/'installed.sh';script.write_text('set -eu\nPM=opkg\n'+functions+'\npackage_installed "$1"\n')
        db = system/'usr/lib/opkg/status';db.write_bytes(original)
        env = {**os.environ, 'ROOT':str(system), 'PATH':str(commands)+':'+os.environ['PATH']}
        shell = [str(program/'libc.so'),'--library-path',str(program),str(program/'busybox'),'ash']
        awk = commands/'awk';awk.write_text('#!/bin/sh\nexec '+shlex.join(shell[:-1]+['awk'])+' "$@"\n');awk.chmod(0o755)
        records = re.split(rb'\n\s*\n', original)
        available = {re.search(rb'^Package: (\S+)', r, re.M)[1].decode():r for r in records if re.search(rb'^Package: (\S+)', r, re.M)}
        for name in ('base-files','luci-base','rpcd'):
            if name not in available:
                continue  # The generic official rootfs need not include LuCI.
            before = db.read_bytes()
            result = subprocess.run(shell+[str(script),name],env=env,capture_output=True,timeout=10)
            if result.returncode or db.read_bytes()!=before:
                raise AssertionError('Original installed record rejected or modified: '+name)
            status = re.search(rb'^Status: (.+)$',available[name],re.M)[1].decode()
            report.append({'source':'original_rootfs','package':name,'status':status,'accepted':True,
                           'old_ok_only_check_accepts':bool(re.fullmatch(r'.* ok installed',status))})
        for status,expected in [('install ok installed',True),('install user installed',True),('install hold installed',True),('install user,hold installed',True),('install ok unpacked',False),('deinstall ok config-files',False)]:
            db.write_text('Package: base-files\nVersion: 1\nArchitecture: all\nStatus: '+status+'\n\n')
            before = db.read_bytes()
            result = subprocess.run(shell+[str(script),'base-files'],env=env,capture_output=True,timeout=10)
            if (result.returncode==0)!=expected or db.read_bytes()!=before:
                raise AssertionError('Native OPKG status mismatch: '+status)
            report.append({'source':'isolated_status_variant','status':status,'accepted':expected})
    return {'native_opkg_rootfs':URL,'rootfs_sha256':SHA256,'checks':report,
            'package_db_unchanged':True,'package_installations':0,'router_touched':False,
            'friend_istoreos_24_10_7_device_acceptance':'not_run'}


if __name__ == '__main__':
    result=check()
    (ROOT/'artifacts/native-opkg-inventory.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
