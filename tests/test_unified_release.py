"""Portable package metadata and native dependency provenance boundaries."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import build_unified_release as release


class UnifiedRelease(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.out=self.root/'out';self.out.mkdir()

    @staticmethod
    def tar(entries):
        buffer=io.BytesIO()
        with tarfile.open(fileobj=buffer,mode='w:gz') as t:
            for name,data in entries.items():
                m=tarfile.TarInfo(name);m.size=len(data);t.addfile(m,io.BytesIO(data))
        return buffer.getvalue()

    def origin(self,changed_digest=False,duplicate=False,target='x86/64',arch='x86_64'):
        packages=[];files={}
        for name in sorted(release.DEPENDENCIES):
            filename=name+'-1-r1.apk';data=name.encode()
            packages.append({'name':name,'file':filename,'sha256':hashlib.sha256(data).hexdigest()})
            files['packages/'+filename]=data
        # A historical AdZero application must never enter the dependency tar.
        packages.append({'name':'luci-app-adzero','file':'old.apk','sha256':'0'*64})
        files['packages/old.apk']=b'do not distribute'
        if duplicate:packages.append(packages[0])
        meta={'firmware':'istoreos','release':'25.12.6','revision':'2026102312','target':target,'arch':arch,'packages':packages}
        files['manifest.json']=json.dumps(meta).encode()
        p=self.root/'origin.tar.gz';p.write_bytes(self.tar(files))
        return {p.name:'0'*64 if changed_digest else hashlib.sha256(p.read_bytes()).hexdigest()}

    def test_exact_native_bytes_repacked_without_historical_application(self):
        origins=self.origin()
        with mock.patch.object(release,'DEPENDENCY_ORIGINS',origins):
            report=release.dependency_bundle(self.root,self.out)
        self.assertFalse(report[0]['application_reused'])
        with tarfile.open(self.out/'adzero-dependencies.tar.gz') as t:
            self.assertEqual(len(t.getmembers()),7)
            self.assertFalse(any('old.apk' in m.name or 'luci-app-adzero' in m.name for m in t.getmembers()))
        row=(self.out/'dependencies.tsv').read_text().strip().split('\t')
        self.assertEqual(row[2:8],['istoreos','25.12.6','2026102312','x86/64','x86_64','apk'])

    def test_verified_rockchip_revision_reuses_one_native_profile(self):
        origins = self.origin(target='rockchip/armv8', arch='aarch64_generic')
        with mock.patch.object(release, 'DEPENDENCY_ORIGINS', origins):
            report = release.dependency_bundle(self.root, self.out)
        rows = [line.split('\t') for line in (self.out/'dependencies.tsv').read_text().splitlines()]
        self.assertEqual([row[4] for row in rows], ['2026102312', '2026103017'])
        self.assertEqual(rows[0][5:], rows[1][5:])
        self.assertEqual(report[0]['identity'][2], '2026102312')
        self.assertEqual(report[0]['verified_additional_revisions'], ('2026103017',))
        with tarfile.open(self.out/'adzero-dependencies.tar.gz') as archive:
            self.assertEqual(len(archive.getmembers()), 7)

    def test_changed_origin_fails_before_repacking(self):
        with mock.patch.object(release,'DEPENDENCY_ORIGINS',self.origin(changed_digest=True)):
            with self.assertRaisesRegex(ValueError,'checksum'):release.dependency_bundle(self.root,self.out)

    def test_duplicate_native_dependency_is_not_accepted(self):
        with mock.patch.object(release,'DEPENDENCY_ORIGINS',self.origin(duplicate=True)):
            with self.assertRaisesRegex(ValueError,'duplicate'):release.dependency_bundle(self.root,self.out)

    def test_ipk_cpu_specific_metadata_cannot_be_relabelled_portable(self):
        p=self.root/'app.ipk'
        for arch in ['all','x86_64']:
            control=f'Package: luci-app-adzero\nVersion: {release.version()}\nArchitecture: {arch}\n'.encode()
            p.write_bytes(self.tar({'control.tar.gz':self.tar({'control':control})}))
            if arch=='all':release.portable_metadata(p,None)
            else:
                with self.assertRaisesRegex(ValueError,'portable'):release.portable_metadata(p,None)

    def test_apk_version_and_architecture_must_match_delivery_claim(self):
        for arch in ['noarch','aarch64_generic']:
            text=f'info:\n  name: luci-app-adzero\n  version: {release.version()}\n  arch: {arch}\n'
            with mock.patch.object(release.subprocess,'check_output',return_value=text):
                if arch=='noarch':release.portable_metadata(self.root/'app.apk','apk')
                else:
                    with self.assertRaisesRegex(ValueError,'portable'):release.portable_metadata(self.root/'app.apk','apk')
