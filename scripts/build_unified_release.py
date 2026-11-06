#!/usr/bin/env python3
"""One stable release: two portable SDK app packages and native vendor deps.

No router mutation or publication. Historical dependency bytes retain pinned
provenance; historical AdZero application packages are never reused.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile

from build_sdk import ROOT, PACKAGE, source_hash
from build_ipk import version
from check_sdk_packages import inspect

DEPENDENCIES = {
    'adblock-fast', 'conntrack', 'coreutils-timeout',
    'libnetfilter-cthelper0', 'libnetfilter-cttimeout1', 'libnetfilter-queue1',
}
# Immutable public assets, verified against GitHub's SHA-256 asset digests.
DEPENDENCY_ORIGINS = {
    'adzero-istoreos-armsr-beta.tar.gz': '8ef0bd41993bc51e3cc186d4f690f5f004fec2d07730f18907c79c7a419b02d7',
    'adzero-istoreos-rockchip-r6xs-beta.tar.gz': '5a8145b2ed4d72c6049a230bdb790ae2394d7b499f72f0b9dd948a95925791aa',
    'adzero-istoreos-x86_64-beta.tar.gz': 'bc2a8bd284feda18ef99c8dd80d4472f0e4349b202489f91bd57e60cf3124a76',
    'adzero-istoreos-24108-armsr-beta.tar.gz': 'fbe63b3abdbf86de73c1980b801394b3387861a218caed199c74d514d863100f',
    'adzero-istoreos-24108-r6xs-beta.tar.gz': '3231de521d4df73607d340cde81d714aab51d9582e6f6c851680e191fa8915de',
    'adzero-istoreos-24108-x86_64-beta.tar.gz': '10fe47d0c83ce0151d87d19550173665931fb7d0dbb743a9f6a18fcb153b3e44',
}
# Both original R6S and R6XS APK databases accept this unchanged closure.
# See docs/evidence/istoreos-25.12.6/rockchip-2026103017-dependencies.json.
VERIFIED_DEPENDENCY_REVISIONS = {
    ('istoreos', '25.12.6', '2026102312', 'rockchip/armv8', 'aarch64_generic', 'apk'): ('2026103017',),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_text(path, text):
    path.write_bytes(text.encode('utf-8'))


def portable_metadata(package, apk_tool):
    if package.suffix == '.ipk':
        with tarfile.open(package) as outer:
            member = next(m for m in outer.getmembers() if m.name.lstrip('./') == 'control.tar.gz')
            data = outer.extractfile(member).read()
        with tarfile.open(fileobj=io.BytesIO(data)) as inner:
            control = next(m for m in inner.getmembers() if m.name.lstrip('./') == 'control')
            text = inner.extractfile(control).read().decode()
        expected = {'Package':'luci-app-adzero', 'Version':version(), 'Architecture':'all'}
        for field, value in expected.items():
            if re.findall(r'^' + field + r': (.+)$', text, re.M) != [value]:
                raise ValueError('IPK name/version/portable architecture differs')
    else:
        text = subprocess.check_output([str(apk_tool), 'adbdump', str(package.resolve())], text=True)
        for field, value in {'name':'luci-app-adzero', 'version':version(), 'arch':'noarch'}.items():
            if re.findall(r'^  ' + field + r': (.+)$', text, re.M) != [value]:
                raise ValueError('APK name/version/portable architecture differs')


def dependency_bundle(directory, output):
    files, rows, origins, identities = {}, [], [], set()
    for name, expected in DEPENDENCY_ORIGINS.items():
        path = directory / name
        if path.is_symlink() or digest(path.read_bytes()) != expected:
            raise ValueError(f'Native dependency origin checksum differs: {name}')
        with tarfile.open(path) as archive:
            members = {m.name: m for m in archive.getmembers()}
            if len(members) != len(archive.getmembers()):
                raise ValueError('Duplicate origin archive entries')
            meta = json.load(archive.extractfile('manifest.json'))
            fmt = 'ipk' if 'factory_feed_dependencies' in meta else 'apk'
            packages = meta.get('factory_feed_dependencies', meta.get('packages'))
            identity = (meta['firmware'], meta['release'], meta['revision'],
                        meta['target'], meta['arch'], fmt)
            if identity in identities or identity[0] != 'istoreos':
                raise ValueError('Duplicate or unexpected dependency platform')
            identities.add(identity)
            prefix = '-'.join(identity).replace('/', '-')
            if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._+-]*', prefix):
                raise ValueError('Invalid dependency profile')
            selected, package_rows = set(), []
            required = DEPENDENCIES | ({'ucode-mod-socket'} if fmt == 'ipk' else set())
            for package in packages:
                package_name = package['name']
                if package_name == 'luci-app-adzero':
                    continue
                filename = package['file']
                if (package_name not in required or package_name in selected or
                        not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._+~-]*\.' + fmt, filename)):
                    raise ValueError('Unexpected, duplicate or unsafe native dependency')
                member = members['packages/' + filename]
                if not member.isfile() or member.size > 16 * 1024 * 1024:
                    raise ValueError('Invalid native dependency member')
                data = archive.extractfile(member).read()
                if digest(data) != package['sha256']:
                    raise ValueError('Native dependency inner checksum differs')
                selected.add(package_name)
                files[prefix + '/packages/' + filename] = data
                package_rows.append(f'{digest(data)}\t{filename}\t{package_name}\n')
            if selected != required:
                raise ValueError('Incomplete native dependency closure')
            files[prefix + '/packages.tsv'] = ''.join(package_rows).encode()
            rows.append((identity, prefix))
            extra_revisions = VERIFIED_DEPENDENCY_REVISIONS.get(identity, ())
            for revision in extra_revisions:
                compatible = (*identity[:2], revision, *identity[3:])
                if compatible in identities:
                    raise ValueError('Duplicate dependency platform')
                identities.add(compatible)
                rows.append((compatible, prefix))
            origins.append({'asset': name, 'sha256': expected, 'identity': identity,
                            'verified_additional_revisions': extra_revisions,
                            'application_reused': False, 'dependencies': sorted(selected)})
    target = output / 'adzero-dependencies.tar.gz'
    with target.open('wb') as destination:
        with gzip.GzipFile(filename='', fileobj=destination, mode='wb', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as archive:
                for name, data in sorted(files.items()):
                    info = tarfile.TarInfo(name)
                    info.size, info.mode, info.mtime = len(data), 0o644, 0
                    archive.addfile(info, io.BytesIO(data))
    sha = digest(target.read_bytes())
    write_text(output / 'dependencies.tsv', ''.join(
        '\t'.join((sha, target.name, *identity, prefix)) + '\n'
        for identity, prefix in sorted(rows)))
    return origins


def build(manifests, dependencies, output, apk_tool):
    output.mkdir(parents=True, exist_ok=True)
    current, reports, rows, formats = source_hash(PACKAGE), [], [], set()
    for path in manifests:
        report = json.loads(path.read_text(encoding='utf-8'))
        fmt = report.get('package_format')
        if (fmt not in ('ipk', 'apk') or fmt in formats or
                report.get('sdk_build') != 'passed' or
                report.get('vendor_sdk_origin_verified') is not True or
                report.get('source_sha256') != current):
            raise ValueError('Need one genuine SDK build of each format from current sources')
        package = path.parent / report['package']
        if package.is_symlink() or digest(package.read_bytes()) != report['sha256']:
            raise ValueError('SDK application package digest differs')
        # Includes payload bytes, modes, minified UI equivalence and rejects
        # architecture-specific code sneaking into our portable application.
        inspect(package, apk_tool)
        portable_metadata(package, apk_tool)
        from build_ipk import payload
        if any(data.startswith(b'\x7fELF') for data, _ in payload().values()):
            raise ValueError('Native application code requires architecture-specific packages')
        filename = f'luci-app-adzero_{version()}_' + ('all.ipk' if fmt == 'ipk' else 'noarch.apk')
        shutil.copyfile(package, output / filename)
        rows.append('\t'.join((report['sha256'], filename, 'openwrt-family',
                               'any', 'any', 'all' if fmt == 'ipk' else 'noarch', fmt)) + '\n')
        reports.append(report)
        formats.add(fmt)
    if formats != {'ipk', 'apk'}:
        raise ValueError('Both IPK and APK SDK builds are required')
    origins = dependency_bundle(dependencies, output)
    write_text(output / 'releases.tsv', ''.join(sorted(rows)))
    write_text(output / 'install.sh', (ROOT / 'scripts/install.sh').read_text(encoding='utf-8'))
    write_text(output / 'INSTALL.txt', 'AdZero ' + version() + '\n'
               'OpenWrt / ImmortalWrt / iStoreOS: same installer and version.\n'
               'sh install.sh --menu\n'
               'Offline: sh install.sh --local-dir . --menu\n'
               'Fresh installation leaves protection off; upgrade preserves its prior switch.\n')
    write_text(output / 'build-provenance.json', json.dumps({
        'version': version(), 'source_sha256': current, 'channel': 'stable',
        'application_builds': reports, 'native_dependency_origins': origins,
        'device_acceptance': 'not_run_for_this_release',
    }, ensure_ascii=False, indent=2) + '\n')
    write_text(output / 'SHA256SUMS', ''.join(
        digest(p.read_bytes()) + '  ' + p.name + '\n'
        for p in sorted(output.iterdir()) if p.is_file() and p.name != 'SHA256SUMS'))
    return {'version': version(), 'source_sha256': current, 'formats': sorted(formats),
            'dependency_profiles': len(origins), 'output': str(output)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', action='append', required=True, type=Path)
    parser.add_argument('--dependencies', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--apk-tool', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.manifest, args.dependencies, args.output, args.apk_tool), indent=2))
