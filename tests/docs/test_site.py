"""Public handbook integrity and deployment boundary checks (standard library)."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SITE = REPO / 'docs/site'
spec = importlib.util.spec_from_file_location('docs_build', SITE / 'build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class Handbook(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        builder.build(self.output)

    def test_version_and_release_match_installer(self):
        config = json.loads((SITE / 'site.json').read_text(encoding='utf-8'))
        makefile = (REPO / 'luci-app-adzero/Makefile').read_text(encoding='utf-8')
        self.assertIn('PKG_VERSION:=' + config['version'], makefile)
        self.assertEqual(config['release'], 'v' + config['version'])
        self.assertIn('/download/' + config['release'], (REPO / 'scripts/install.sh').read_text(encoding='utf-8'))
        body = (self.output / 'installation.html').read_text(encoding='utf-8')
        self.assertIn('https://raw.githubusercontent.com/slobys/AdZero/main/scripts/install-auto.sh', body)
        self.assertIn('/releases/tag/' + config['release'], body)
        self.assertNotIn('$release', body)

    def test_only_public_assets_and_navigation_are_published(self):
        pages = list(self.output.glob('*.html'))
        self.assertEqual(len(pages), 13)
        self.assertEqual([p.name for p in (self.output / 'assets').iterdir()], ['adzero-logo.png'])
        approved=(REPO / 'docs/images/adzero-logo.png').read_bytes()
        self.assertEqual((self.output / 'assets/adzero-logo.png').read_bytes(),approved)
        self.assertEqual((REPO / 'luci-app-adzero/htdocs/luci-static/resources/view/adzero/logo.png').read_bytes(),approved)
        for file in pages:
            body = file.read_text(encoding='utf-8')
            self.assertNotIn('NaiLink 文档', body)
            self.assertNotIn('基于 daed', body)
            self.assertNotRegex(body, r'<script[^>]+src="https?://')
            self.assertNotRegex(body, r'<link[^>]+rel="stylesheet"[^>]+href="https?://')
            self.assertIn('跳转到正文', body)
        workflow = (REPO / '.github/workflows/docs.yml').read_text(encoding='utf-8')
        self.assertIn('path: .build/docs-site', workflow)
        self.assertNotIn('path: .\n', workflow)

    def test_readme_stays_short_and_install_commands_match_handbook(self):
        readme = (REPO / 'README.md').read_text(encoding='utf-8')
        self.assertLessEqual(len(readme.splitlines()), 90, 'README should be beginner-friendly, not an exhaustive handbook')
        install = (self.output / 'installation.html').read_text(encoding='utf-8')
        self.assertIn('scripts/install-auto.sh', readme)
        self.assertIn('scripts/install-auto.sh', install)
        self.assertEqual(readme.count('```sh'), 1, 'README must show exactly one installation command')
        self.assertEqual(install.count('<pre><code>'), 1, 'Online installation page must show one installation command')
        self.assertNotIn('install-arm64-beta.sh -o', readme)
        self.assertNotIn('install-istoreos-beta.sh -o', readme)
        self.assertIn('--detect', install)
        self.assertIn('--check', install)
        for tag in ('v0.21.5-arm64-beta.2', 'v0.21.5-istoreos-beta.3'):
            self.assertIn(tag, install)
        self.assertIn('https://slobys.github.io/AdZero/compatibility.html', readme)
        self.assertNotIn('iStoreOS 进度（尚未发布）', readme)

    def test_public_firmware_table_and_search_are_current(self):
        matrix = json.loads((REPO / 'docs/compatibility-matrix.json').read_text(encoding='utf-8'))
        self.assertEqual(len(matrix['sdk_verified']), 5)
        self.assertEqual(len(matrix['arm64_candidates']), 6)
        page = (self.output / 'compatibility.html').read_text(encoding='utf-8')
        for row in matrix['sdk_verified'] + matrix['arm64_candidates']:
            with self.subTest(firmware=row['firmware'], target=row['target']):
                self.assertIn(row['release'], page)
                self.assertIn(row['target'], page)
        for target in ('x86/64', 'armsr/armv8', 'rockchip/armv8', 'mediatek/filogic'):
            self.assertIn(target, page)
        self.assertIn('修订号 2026102312', page)
        self.assertIn('aarch64_cortex-a53', page)
        for release_tag in ('v0.21.5', 'v0.21.5-arm64-beta.2',
                            'v0.21.5-istoreos-beta.3'):
            self.assertIn(release_tag, page)
        search = json.loads((self.output / 'search-index.json').read_text(encoding='utf-8'))
        self.assertTrue(any(item['url'].startswith('compatibility.html')
                            and ('iStoreOS' in item['text'] or 'Rockchip' in item['text'])
                            for item in search))
        self.assertIn('href="compatibility.html">支持的固件',
                      (self.output / 'index.html').read_text(encoding='utf-8'))
        self.assertIn('installation.html#install',
                      (self.output / 'maintenance.html').read_text(encoding='utf-8'))
        self.assertIn('compatibility.html',
                      (self.output / 'troubleshooting.html').read_text(encoding='utf-8'))

    def test_istoreos_24108_exact_native_ipk_beta_publicly_documented(self):
        page = (self.output / 'compatibility.html').read_text(encoding='utf-8')
        readme = (REPO / 'README.md').read_text(encoding='utf-8')
        auto = (REPO / 'scripts/install-auto.sh').read_text(encoding='utf-8')
        self.assertIn('id="istoreos-24108"', page)
        self.assertIn('iStoreOS 24.10.8', page)
        self.assertIn('修订号 2026073111', page)
        self.assertIn('公开 Beta.1，待实机验收', page)
        self.assertNotIn('适配中，暂未开放安装', page)
        self.assertIn('原生 OPKG/IPK', page)
        self.assertIn('完整文件清单', page)
        self.assertIn('v0.21.5-istoreos-24108-beta.1', page)
        self.assertIn('安装包', (self.output / 'installation.html').read_text(encoding='utf-8'))
        self.assertIn('id="istoreos-24108"', (self.output / 'installation.html').read_text(encoding='utf-8'))
        self.assertIn('iStoreOS **24.10.8** 已开放独立', readme)
        self.assertIn('istoreos:24.10.8:', auto)
        self.assertIn('2026073111', auto)
        self.assertIn('PIN_ISTOREOS24108', auto)
        self.assertIn('d30c309e650442708dcfda552fa4f62e1530916e93ac2d9341f25db5503a2766', auto)
        self.assertLess(page.index('iStoreOS 公测：3 组'), page.index('id="istoreos-24108"'))
        self.assertIn('v0.21.5-istoreos-24108-beta.1', (self.output / 'compatibility.html').read_text(encoding='utf-8'))
        self.assertEqual((self.output / 'installation.html').read_text().count('<pre><code>'),1)

    def test_device_acceptance_tracks_both_public_istoreos_beta_channels(self):
        acceptance = (REPO / 'docs/DEVICE_ACCEPTANCE.md').read_text(encoding='utf-8')
        arm64 = (REPO / 'docs/ARM64.md').read_text(encoding='utf-8')
        compatibility = (REPO / 'docs/COMPATIBILITY.md').read_text(encoding='utf-8')
        self.assertIn('iStoreOS 25.12.6 ARM64 公测验收', acceptance)
        self.assertIn('iStoreOS 24.10.8 原生 IPK Beta.1 验收', acceptance)
        self.assertIn('统一入口默认只打开中文菜单', acceptance)
        self.assertNotIn('当前只能运行 [只读预检]', acceptance)
        self.assertIn('24.10.8-2026073111 / armsr/armv8', arm64)
        self.assertIn('24.10.8-2026073111 / rockchip/armv8', arm64)
        for document in (arm64, compatibility):
            self.assertIn('RELEASE_ISTOREOS_24108_BETA_0.21.5.md', document)
        self.assertIn('physical_device_acceptance=not_run', acceptance)

    def test_istoreos_24108_x86_qemu_install_evidence_is_guest_only(self):
        evidence_dir = REPO / 'docs/evidence/istoreos-24.10.8'
        guest = json.loads((evidence_dir / 'qemu-x86-beta1-offline-install-uninstall.json').read_text(encoding='utf-8'))
        published = json.loads((evidence_dir / 'public-beta-0.21.5-native-build-assets.json').read_text(encoding='utf-8'))
        self.assertEqual(guest['release_tag'], published['release_tag'])
        self.assertEqual(guest['revision'], published['revision'])
        self.assertEqual(guest['profile'], 'x86_64')
        self.assertEqual(guest['published_archive_sha256'], published['profiles']['x86_64']['archive_sha256'])
        self.assertEqual(guest['published_installer_sha256'], published['installer']['sha256'])
        self.assertEqual(guest['original_factory_raw_image_sha256'], '431470911d0aac490c14e744c8213c057819f1c1ac72bc66aa428bf4f8315170')
        for observed in ('real_published_installer_readonly_check',
                         'real_published_installer_install',
                         'real_published_installer_uninstall'):
            self.assertIn('passed', guest['guest_checks'][observed])
        self.assertTrue(guest['guest_checks']['post_install_managed_dns_state_absent'])
        self.assertTrue(guest['guest_checks']['post_uninstall_beta_marker_absent'])
        self.assertEqual(guest['diagnostics']['warning_count'], 2)
        self.assertFalse(guest['diagnostics']['warning_root_cause_verified'])
        self.assertEqual(guest['arm64_guest_lifecycle_test'], 'not_run')
        self.assertEqual(guest['physical_device_acceptance'], 'not_run')
        self.assertFalse(guest['stable_sdk_verified_changed'])
        self.assertTrue(guest['environment']['host_timeout_terminated_vm_after_checks'])
        self.assertFalse(guest['environment']['guest_reboot_tested'])
        doc = (REPO / 'docs/ISTOREOS_24_10.md').read_text(encoding='utf-8')
        self.assertIn('qemu-x86-beta1-offline-install-uninstall.json', doc)
        self.assertIn('Command failed: Not found', doc)

    def test_broken_link_and_anchor_are_rejected(self):
        file = self.output / 'index.html'
        original = file.read_text(encoding='utf-8')
        for broken in ['missing.html', 'rules.html#missing', '../README.md']:
            file.write_text(original + f'<a href="{broken}">bad</a>', encoding='utf-8')
            with self.subTest(link=broken), self.assertRaises(ValueError):
                builder.validate(self.output)

    def test_duplicate_headings_and_invalid_search_are_rejected(self):
        with self.assertRaises(ValueError):
            doc = builder.Document()
            doc.feed('<h2 id="same">A</h2><h2 id="same">B</h2>')
        (self.output / 'search-index.json').write_text('[{"url":"rules.html#missing"}]', encoding='utf-8')
        with self.assertRaises(ValueError):
            builder.validate(self.output)

    def test_chinese_search_and_404_subpath(self):
        index = json.loads((self.output / 'search-index.json').read_text(encoding='utf-8'))
        self.assertTrue(any('临时放行' in entry['text'] and entry['url'].startswith('access-control.html') for entry in index))
        body = (self.output / '404.html').read_text(encoding='utf-8')
        self.assertIn('<base href="https://slobys.github.io/AdZero/">', body)
        self.assertIn('页面未找到 · AdZero', body)
        self.assertNotIn('<aside class="toc"', body)


if __name__ == '__main__':
    unittest.main()
