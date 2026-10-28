# AdZero v0.21.5 构建、安装与发行

公开安装只使用真实 SDK 输出，不使用 build_ipk.py 的离线预览。

## 安装菜单与发行一致性

推荐的用户入口不再包含版本号：从 `https://raw.githubusercontent.com/slobys/AdZero/main/scripts/install.sh` 下载脚本后运行 `sh /tmp/adzero-install.sh --menu`。该入口的默认 `BASE_URL` 由维护者指向当前**已发布且具备 SDK 包的发行标签**；用户无需选择版本或手动判断 IPK/APK。菜单只是交互包装，安装和升级仍调用原有的 SHA-256 核对与备份恢复流程。

`build_release.py` 必须将同一版本的 `install.sh` 和 `releases.tsv` 一并放进发行归档，并在 `INSTALL.txt` 指引用户执行 `sh install.sh --menu --local-dir "$(pwd)"`。菜单的“环境检查”使用 `--check`，只下载和校验匹配安装包、不运行软件包安装或后端恢复。无交互任务可以显式传 `--install`、`--upgrade`、`--no-enable`；不带参数默认进入菜单，避免在自动化/输入结束时无意触发安装。

不要把主分支尚未发布的菜单脚本与不同版本的 SDK 构建产物混合宣称为一个已验收发行版；新菜单上线仍需经 release 的原始字节校验和真实设备升级/卸载测试。


新安装器不再内置发行版白名单；公开发行包仍必须由 `scripts/build_release.py` 校验。其允许的完整组合现在来自 `docs/compatibility-matrix.json` 的 `sdk_verified` 列表（`sdk_build=passed`），构建报告也必须具有匹配的源码哈希和实际包 SHA-256。新增 ImmortalWrt/iStoreOS 时，必须先用该厂商匹配 SDK 构建，通过验证后再增加兼容矩阵行。未验证的候选条目不会生成可安装的发布清单。

| 官方 SDK | target | 格式 | 当前结果 |
| --- | --- | --- | --- |
| OpenWrt 24.10.8 / GCC 13.3 | x86/64 | IPK | 实际构建通过 |
| OpenWrt 25.12.5 / GCC 14.3 | x86/64 | APK | 实际构建通过 |
| OpenWrt 25.12.5 / GCC 14.3 | rockchip/armv8 | APK | 实际构建通过 |

SDK 的 SHA-256 固定在 scripts/fetch_sdk.py，来源为对应 [官方版本目录](https://downloads.openwrt.org/releases/)。构建保留 SDK 自带 feeds.conf.default；不能用另一固件的 feeds 代替。

## 在线更新的 SDK / Release 发布要求

本项目新增三个安装文件：

- /usr/libexec/adzero/update：只对官方发行源执行版本检查和一次性升级。
- /usr/libexec/adzero/update-check.uc：解析 GitHub Releases API 的数字版本与 install.sh 的 SHA-256 digest。
- /usr/share/adzero/version：必须与 Makefile 的 PKG_VERSION 及 LuCI 页头显示的版本保持一致。

有新版本时，仅从官方 HTTPS 下载已发布的 install.sh，按 GitHub asset digest 验证 SHA-256。脚本必须支持只读 --check，在真实执行 --upgrade 之前完成固件完整版本、target、包格式、架构、SDK 安装包哈希验证。更新许可 15 分钟有效、只针对用户确认的特定版本，且一次性使用；不允许因重试自动重复升级。

**新版本的 GitHub Release 必须包含新版 install.sh、releases.tsv、真实 SDK 构建产物和已核对的包 SHA-256。** 不修改旧标签资产；现有 v0.21.4 已有在线更新功能。新增 ImmortalWrt 的 v0.21.5 必须完成本版本 SDK 编译和发布校验后，才供用户在 UI 中检查更新。

## 第三方发行版独立 SDK 构建

ImmortalWrt x86/64 的官方独立 SDK 现在已固定精确下载地址与 SHA-256，新增 vendor-sdk.yml GitHub Actions 可执行真实厂商 SDK 编译，而不是复用 OpenWrt IPK/APK：

~~~sh
python3 scripts/fetch_sdk.py --firmware immortalwrt --release 24.10.6 --target x86/64 --directory "$HOME/vendor-sdks" --cache "$HOME/vendor-cache"
python3 scripts/fetch_sdk.py --firmware immortalwrt --release 25.12.2 --target x86/64 --directory "$HOME/vendor-sdks" --cache "$HOME/vendor-cache"
~~~

24.10.6 使用 IPK，25.12.2 使用 APK。GitHub CI 已用两个独立的 ImmortalWrt 官方 SDK、各自 feeds 完成 v0.21.4 代码的实际封装和 LuCI 验证；证据见 docs/evidence/v0.21.5/vendor-sdk-builds.json。公开的 v0.21.5 已经使用对应源码重新编译并校验了五项完整发行包，且通过了公开资产下载、SHA-256 与 10 组隔离安装测试；即使 SDK 通过，也不意味着已在真实设备上验证过滤和回滚。

iStoreOS 25.12.6-2026102312 x86/64 的官方 commit/config/feeds/版本索引可运行以下只读核验：

~~~sh
python3 scripts/probe_istoreos.py
~~~

此命令**不会构建 SDK、安装任何插件或修改路由器**；它只证明官方固件的源码提交和 feeds 可追溯。下一步可以准备完全锁定的 iStoreOS 源码及 feeds：

~~~sh
mkdir -p "$HOME/istoreos-builds"
python3 scripts/prepare_istoreos_source.py --directory "$HOME/istoreos-builds/istoreos-25.12.6-2026102312"
~~~

源码准备程序现会显式获取并脱离分支检出官方 buildinfo 公布的历史提交 283d02639c740f8ae6e9425b48be8c61f152c6b8（不依赖厂商分支日后不变化），并使用官方 feeds.buildinfo 中的不可变提交 SHA。它不会覆盖已有目录、运行固件安装或冒充 SDK 验证。之后仍需原生 Buildroot 工具链和 SDK 编译，再核对固件 ABI、依赖和安装测试。缺少匹配该厂商构建的 SDK/ABI 验证之前，不发布 iStoreOS 二进制包，不跨平台借用普通 OpenWrt SDK。

**面向将来 iStoreOS SDK 的严格发行清单：** 当前其他固件仍使用 `sha256 / file / firmware / release / target / arch / format` 七列，iStoreOS 必须有第八列 `revision`，取固件内 `DISTRIB_REVISION` 的十位数字修订号。构建矩阵里的 iStoreOS SDK 条目必须明确固定相同修订号；构建报告、发行清单和路由器检测有任何差异即拒绝安装。此功能是预先加固，不解除当前 iStoreOS 尚无可发布包的限制。

## Linux / WSL 复现

需要 make、gcc/g++、ncurses 开发头文件、git、gawk、rsync、file、unzip、zstd、Python 3 和 Node。SDK 放在 Linux 文件系统，避免 Windows 挂载目录的大小写与权限差异。

~~~sh
python3 scripts/fetch_sdk.py --release 24.10.8 --directory "$HOME/adzero-sdks" --cache "$HOME/adzero-sdk-cache"
~~~

进入返回的 SDK 目录，准备它自己的 feed：

~~~sh
./scripts/feeds update base packages luci
./scripts/feeds install luci-base adblock-fast rpcd-mod-file rpcd-mod-ucode ca-bundle curl ucode-mod-fs ucode-mod-uci ucode-mod-ubus
~~~

回到本项目，使用 fetch 输出的 sdk 路径和 sha256：

~~~sh
python3 scripts/build_sdk.py --sdk /absolute/path/to/sdk \
  --firmware openwrt --release 24.10.8 --target x86/64 \
  --format ipk --sdk-sha256 ac4a0405d2eea821b06f93c14ba13ffa90ad0457648903df7dde02570027ab21 \
  --output dist/v0.8.1/openwrt-24.10.8
~~~

25.12.5 使用对应 SDK、--format apk 和 SHA-256：

~~~text
0c8df0151a1e88feb7c03d694d61f6a18d51872815b7c811d76e2b77504d5e9c
~~~

入口写入 .config 的包选项并 make defconfig，先构建 luci-base 的宿主工具，再编译 AdZero。OpenWrt 的 scripts/config 是 Kconfig 源码目录，不是可执行配置 CLI。显式构建宿主工具解决了 25.12.5 首次构建缺少 po2lmo 的问题。

构建器只同步自己曾复制且未被手动改动的包目录；其他已有源码不覆盖。源文件被删除时要求新 SDK，以避免旧文件残留。构建前后校验源码哈希，检测实际 target、架构及包格式；没有真实输出就失败。

Rockchip 使用 --target rockchip/armv8 下载独立 SDK，SHA-256 为：

~~~text
59194a023968398af64bfa7d8bc3eac322641f6dc9cdbade28a4d9dd41866eba
~~~

构建时指定 --target rockchip/armv8 --format apk。官方 SDK 的 CONFIG_TARGET_ARCH_PACKAGES=aarch64_generic、CONFIG_ARCH=aarch64，与 APK --print-arch 的 CPU 名分别记录，不混为同一个字段。源码/格式检查和发布清单仍以实际 SDK target/ABI 为准。

## 检查真实包

~~~sh
python3 scripts/check_sdk_packages.py dist/v0.8.1/openwrt-24.10.8-x86/luci-app-adzero_0.8.1-r1_all.ipk
python3 scripts/check_sdk_packages.py dist/v0.8.1/openwrt-25.12.5-x86/luci-app-adzero-0.8.1-r1.apk --apk-tool /absolute/sdk/staging_dir/host/bin/apk
~~~

检查 25 个源文件（含界面 CSS 和图标许可）的存在、运行代码内容和权限（含 backend/backend.uc 和 probe/probe.uc），并对 SDK 实际压缩后的 LuCI JS 执行语法及 91 项交互断言。CSS 保留原文，避免旧 SDK 压缩工具破坏自定义属性。仅解包，不执行安装钩子。官方 IPK 使用 gzip tar 外层；APK 使用真正的 apk-tools 读取，不能改扩展名转换。

脚本 ctl、probe、adapter、backend、watch、policy 和 init.d/adzero 为 0755；ucode 源码、规则库、配置和前端资源为 0644。SDK 另外生成自身的包管理元数据与生命周期脚本。

## 发行目录

~~~sh
python3 scripts/build_release.py \
  --manifest dist/v0.8.1/openwrt-24.10.8-x86/build-manifest.json \
  --manifest dist/v0.8.1/openwrt-25.12.5-x86/build-manifest.json \
  --manifest dist/v0.8.1/openwrt-25.12.5-rockchip/build-manifest.json
~~~

发行目录包含 install.sh、三个实际 SDK 包、releases.tsv 和 build-provenance.json。它拒绝不属于当前源码哈希的构建。GitHub Release v0.8.1 标记为 prerelease；一键命令固定到同一个版本，避免安装脚本与包混用。

安装器先检查平台和 SHA-256，保存 /etc/config 备份，再使用固件的软件源补齐依赖。APK 自有包尚未签名，仅该本地文件通过 --allow-untrusted 安装；没有关闭 HTTPS 验证，也不使用强制覆盖或强制架构。安装失败时不自动删除已有包或整个配置目录。

不能把源码 all/noarch 视为跨固件保证。其他厂商、24.10 的其他补丁版本和 CPU 架构，须先补齐对应 SDK 和设备证据，才能写入安装清单。配置包的 SDK 成功仍不等于设备生命周期和终端 DNS 验收成功。

## v0.12.1 发布文件检查

`build_release.py` 对 `releases.tsv`、`install.sh`、`build-provenance.json` 和 `INSTALL.txt` 显式写 UTF-8 / LF，打包前直接检查文件字节、唯一目标条目及实际包 SHA-256。不能只用 `read_text()` 检查，因为它会自动隐藏 Windows 回车。

Windows 上执行 `python -m unittest discover -s tests -p test_release.py -v`，检查实际行尾和压缩包内容。对应 CI 另设 Windows job。

发行目录生成后，在 Linux / WSL 执行：

~~~sh
python3 scripts/check_release_delivery.py dist/v0.12.1/release
~~~

该检查使用最终发行的安装脚本、清单和真实 SDK 包字节，分别执行三个目标的 LF / CRLF 安装路径，核对选择、校验、备份、旧版恢复和包管理器调用顺序。包管理器与服务均为隔离替身，不向开发机或真实路由安装软件。设置 `ADZERO_TEST_SHELL` 可选择校验和固定的 OpenWrt BusyBox ash。

发布后还应重新下载文件，检查校验和及清单行尾，并对下载回来的发行目录执行同样检查。

## v0.15.1 软件源故障与本地 APK 升级

安装器先用 `apk add --simulate --no-network --repositories-file /dev/null` 检查本地包与已安装依赖，通过后才关闭旧版，再用相同的本地限制安装。若不能离线准备，软件源刷新和在线模拟必须在关闭之前通过；不会因无关第三方源失败而先停止保护。不修改系统的软件源列表或跳过依赖检查。

除 `tests/test_installer.py` 的失败注入、信号和调度器检查外，可用官方 SDK 的主机 APK 工具执行真实数据库与依赖求解测试：

~~~sh
python3 scripts/check_apk_installer.py --apk /absolute/path/to/sdk/staging_dir/host/bin/apk --output artifacts/apk-installer-result.json
~~~

它在临时根中生成并安装合成包，检查依赖已满足时的本地版本升级、缺失依赖及版本不满足时保留旧状态；不可用源仅绑定回环地址。控制器为替身，不操作主机软件包数据库、真实 DNS 或路由器；不能代替设备升级验收。

## 开发预览

python3 scripts/build_ipk.py 仍能生成可重复的离线 OPKG 归档，供测试路径和权限。它不进入公开安装清单。正式发行来源与测试证据见 [LOCAL_VALIDATION.md](LOCAL_VALIDATION.md)。


v0.8.1 增加 policy/policy.uc/policy-lib.uc 与 rpcd 的 ucode 插件，明确依赖 rpcd-mod-ucode；不开放任意命令或环境变量接口。个人名单由标准 UCI 配置文件保留。--upgrade 的配置与恢复备份覆盖暂停/放行事务，旧版控制器先关闭成功才安装新版。

规则管理新增 sources/sources.uc/source-lib.uc 和短任务启动器 job，明确依赖 curl 用于严格 HTTPS 预检。规则变更有持久事务记录；恢复失败时升级停止，不绕过旧控制器。
