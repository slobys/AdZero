# AdZero：三种固件 ARM64 适配与验收

> 本页 ARM64 新目标仍是**未完成实体设备认证的候选**；四组 OpenWrt/ImmortalWrt 25.12 ARM64、两组 iStoreOS 25.12 ARM64 及两组 iStoreOS 24.10.8 ARM64 均已通过**各自独立的公开 Beta** 提供自愿试装。常规稳定安装渠道仍只接受 `docs/compatibility-matrix.json` 的 `sdk_verified`，不能将 Beta 视为全系列兼容。

## 当前目标

| 固件 | 固件版本 / target | 进度 |
| --- | --- | --- |
| OpenWrt | 25.12.5 / rockchip/armv8 | v0.21.5 已有对应 SDK APK，真机 LAN 尚未普遍验证 |
| OpenWrt | 25.12.6 / rockchip/armv8 | 官方 SDK 编译、APK/LuCI 检查通过；独立 ARM64 Beta 公开试装，真机待测 |
| OpenWrt | 25.12.6 / mediatek/filogic | 官方 SDK 编译、APK/LuCI 检查通过；独立 ARM64 Beta 公开试装，真机待测 |
| ImmortalWrt | 25.12.2 / rockchip/armv8 | 独立厂商 SDK 编译、APK/LuCI 检查通过；独立 ARM64 Beta 公开试装，真机待测 |
| ImmortalWrt | 25.12.2 / mediatek/filogic | 独立厂商 SDK 编译、APK/LuCI 检查通过；独立 ARM64 Beta 公开试装，真机待测 |
| iStoreOS | 25.12.6-2026102312 / armsr/armv8 | 原生 APK 编译通过；6 依赖闭环、7 包离线模拟通过；独立 iStoreOS Beta 已公开，真机待测 |
| iStoreOS | 25.12.6-2026102312 / rockchip/armv8（R6XS） | 原生 APK 编译通过；6 依赖闭环、7 包离线模拟通过；独立 iStoreOS Beta 已公开，真机待测 |
| iStoreOS | 24.10.8-2026073111 / armsr/armv8 | 原生 OPKG/IPK 和 7 依赖闭环、8 包原厂 OPKG 离线模拟通过；[Beta.1](RELEASE_ISTOREOS_24108_BETA_0.21.5.md) 已公开，真机待测 |
| iStoreOS | 24.10.8-2026073111 / rockchip/armv8（R6XS） | 原生 OPKG/IPK 和 7 依赖闭环、8 包原厂 OPKG 离线模拟通过；[Beta.1](RELEASE_ISTOREOS_24108_BETA_0.21.5.md) 已公开，真机待测 |

四个已通过 CI 的候选包均来自提交 `61e1334`，下载后的 APK SHA-256 与相应 SDK 构建清单一致；详细记录在 [`evidence/arm64-pr10-sdk-builds.json`](evidence/arm64-pr10-sdk-builds.json)。**`ci_passed_unreleased` 仍只表示 SDK 编译与静态检查，不能直接成为稳定 `sdk_verified`**。这些原生 APK 通过 [独立 ARM64 Beta.2 安装渠道](RELEASE_ARM64_BETA_0.21.5_BETA2.md) 公开供自愿测试者使用：4 个固件专用包哈希固定，默认不启用过滤，依赖来自对应官方 HTTPS 源或经限定的中科大镜像（同厂商、精确版本或固件滚动 25.12，且 target/架构必须一致）；不覆盖已发布 v0.21.5，也不会出现在稳定版的普通 LuCI 在线更新中。

Rockchip 的发行包架构常为 `aarch64_generic`，Filogic 为 `aarch64_cortex-a53`。即便 APK 运行环境显示 `aarch64` CPU，**也不能自动推断它与所有 ARM64 包兼容**。完整固件版本、target、包架构及发行版 ID 都要精确匹配。iStoreOS 还必须检查十位 `DISTRIB_REVISION`。

## OpenWrt / ImmortalWrt：分别构建

`scripts/fetch_sdk.py` 从**所属厂商官方目录**下载对应 target SDK，下载归档前后核对官方 sha256sums，记录 `adzero-sdk-origin.json`。 `scripts/build_sdk.py` 在改动 SDK 源码或进行编译之前校验这个来源文件中的厂商、版本、target、绝对路径和固定哈希，不能靠手填 SHA 或相同架构跨厂商构建。

Linux/WSL 示例：

```sh
# OpenWrt 25.12.6，MediaTek Filogic
python3 scripts/fetch_sdk.py --firmware openwrt --release 25.12.6 \
  --target mediatek/filogic --directory "$HOME/adzero-sdks" --cache "$HOME/adzero-cache"

# ImmortalWrt 25.12.2，Rockchip ARMv8
python3 scripts/fetch_sdk.py --firmware immortalwrt --release 25.12.2 \
  --target rockchip/armv8 --directory "$HOME/immortal-sdks" --cache "$HOME/immortal-cache"
```

在返回的 SDK 目录内，用 SDK 自带 `./scripts/feeds update base packages luci`，安装 LuCI、adblock-fast、ucode、tcpdump/conntrack 等所需 feeds；之后参照仓库 `.github/workflows/sdk.yml` 或 `.github/workflows/vendor-sdk.yml` 的完整编译、APK 内容及 LuCI 验证命令。每个目标单独生成 `build-manifest.json` 与 SHA-256。

## iStoreOS：原厂 ARM64 构建路径

官方 25.12.6、修订 2026102312 的原厂来源分别为：

- ARMSR：`https://fw20.koolcenter.com/iStoreOS/armsr/`
- Rockchip R6XS：`https://fw20.koolcenter.com/iStoreOS/r6xs/`

对每个目标的 `commit.buildinfo`、`config.buildinfo`、`feeds.buildinfo`、`feeds.conf`、`version.latest.v2` 各自固定 SHA-256，官方镜像名和压缩镜像哈希也被固定。它们对应的完整源码提交同为 `283d02639c740f8ae6e9425b48be8c61f152c6b8`，**但完整配置与 feeds 不同**，不能混用或直接使用 x86_64 原生包。

只读厂商元数据核对（不修改固件）：

```sh
python3 scripts/probe_istoreos_arm64.py --profile armsr
python3 scripts/probe_istoreos_arm64.py --profile rockchip-r6xs
```

创建全新源码目录（禁止覆盖已有构建）：

```sh
mkdir -p "$HOME/istoreos-builds"
python3 scripts/prepare_istoreos_source.py --profile rockchip-r6xs \
  --directory "$HOME/istoreos-builds/rockchip-r6xs-2026102312"
```

进入所创建的原厂 Buildroot，按照固定的 `feeds.conf` 获取并安装全部 feeds，确认源码提交未漂移，再运行：

```sh
python3 scripts/build_istoreos_native.py \
  --source "$HOME/istoreos-builds/rockchip-r6xs-2026102312" \
  --output "$HOME/istoreos-builds/new-r6xs-candidate" --jobs 4
```

它依次编译原厂 tools、toolchain、Linux target、LuCI host 和 AdZero APK，再校验 vendor 来源、target、feeds、包架构、源码、权限与导出文件 SHA。**即使编译及封装验证成功，仍只生成 `publishable=false` 的候选包**，不能直接写入公开 `releases.tsv`。

### iStoreOS 原厂 ARM64 固件内容核验（2026-10-28）

从官方镜像 SHA-256 固定的 ARMSR 与 R6XS 原始文件中只读提取了 `/etc/openwrt_release` 和 `/lib/apk/db/installed`。两者均为 `iStoreOS 25.12.6`、修订 `2026102312`、包架构 `aarch64_generic`，但实际 target 不同（`armsr/armv8` / `rockchip/armv8`），原有 APK 分别登记 **1,018** / **940** 个包，均包含原版 `tcpdump`、`luci-base`、`rpcd`。参见 [ARMSR 镜像证据](evidence/istoreos-25.12.6/arm64-armsr-original-image.json) 与 [R6XS 镜像证据](evidence/istoreos-25.12.6/arm64-r6xs-original-image.json)。R6XS 下载文件含厂商附加尾部元数据，必须核对原始压缩包哈希并单独校验 gzip 流后提取，不能误判为普通损坏镜像。

可在 Linux 上使用 `python3 scripts/inspect_istoreos_arm64_image.py --profile armsr --image /path/to/official.img` 复查固件 ABI。对**已真正编译成功的同一原厂目标 APK**，使用 `python3 scripts/check_istoreos_dependencies.py --source /path/to/pinned-buildroot --image /path/to/official.img --profile armsr` 做只读递归依赖检查。R6XS 使用 `--profile rockchip-r6xs` 及其独立源码/镜像。**镜像核验本身不能替代 APK 编译或实机网络过滤、安装验收；实际构建和离线解算证据见下文。**

### ARMSR 原生构建与离线软件包验证

iStoreOS ARMSR 的原厂锁定 Buildroot 已实际生成 `luci-app-adzero-0.21.5-r1.apk` 候选，源码/13 feeds/target/ABI 与 SHA 检查、66 个交付文件和 406 项 LuCI 检查均通过。APK SHA-256：`b579f94fe847da6b6a0230fbdb7efa833d39a6bc80c9b6c726e8da7b10dc0f52`。与原版镜像的 1,018 个预装包对照，缺少 6 个原厂依赖，均通过同一 Buildroot 原生 APK 及递归 SHA-256 核对，证据见 [依赖闭环](evidence/istoreos-25.12.6/arm64-armsr-native-closure.json)。

在临时 APK 数据库中使用原厂 apk-tools 执行 `--simulate --no-network`，AdZero 加 6 个依赖的 **7/7 项全部通过**，未移除原系统包，完整版 `tcpdump` 保留。可通过 `scripts/check_istoreos_apk_solver.py --profile armsr --source /path/to/Buildroot --image /path/to/verified.img` 复现；结果见 [离线模拟证据](evidence/istoreos-25.12.6/arm64-armsr-native-offline-solver.json)。**这不是实机安装，候选字节不能覆盖公开 v0.21.5 Release，暂不开放菜单/在线更新安装。**

### R6XS Rockchip 原生构建与离线软件包验证

Rockchip R6XS 固定原厂 **15 组 feeds** 的 Buildroot 也已完整生成原生 APK，target 为 `rockchip/armv8`，架构 `aarch64_generic`，源码、SHA、66 个交付文件与 406 项 LuCI 回归均通过。AdZero APK 字节哈希与 ARMSR 相同（`b579f94fe847da6b6a0230fbdb7efa833d39a6bc80c9b6c726e8da7b10dc0f52`），但**原厂固件及部分依赖包不同**，不能由相同 APK 内容推断跨固件安装包相互兼容。R6XS 原始固件预装 940 个 APK，缺少的 6 个依赖由其**对应原厂源码**构建，[递归依赖记录](evidence/istoreos-25.12.6/arm64-r6xs-native-closure.json)独立锁定 SHA；与 ARMSR 相比，R6XS 的 `adblock-fast` APK SHA 不相同。

原版 R6XS APK 数据库的纯离线模拟使用 `scripts/check_istoreos_apk_solver.py --profile rockchip-r6xs --source /path/to/R6XS-buildroot --image /path/to/R6XS-verified.img`：同样 **7/7 通过**，没有替换原厂完整版 `tcpdump` 或其他已安装软件包，[R6XS 模拟证据](evidence/istoreos-25.12.6/arm64-r6xs-native-offline-solver.json)。**仅离线模拟，并非真实 ARM64 路由器安装、启停、代理共存、DNS、重启或回滚认证；在真机验收前不能加入正式 `sdk_verified` 或公开一键安装清单。**

## ARM64 发行包回归（暂不对外开放）

发行打包工具只认 `sdk_verified` 中已经通过独立 SDK 构建验证的组合。未来一旦经证据审核新增精确 ARM64 组合，`scripts/check_release_delivery.py` 会自动遍历**全部**受信目标，并对每一组发行文件执行 LF/CRLF 两种清单的安装选包、SHA-256、升级备份和服务恢复模拟。测试已覆盖四组未来 OpenWrt/ImmortalWrt ARM64 目标，以及 iStoreOS ARMSR/Rockchip 的十位固件修订号；测试使用模拟包管理器，**不是**实际 ARM64 路由器安装证明，不能据此发布候选包。

## 发布和真机验收门槛

每个目标独立检查：

1. 原厂 SDK 的**实际编译产物**、APK 声明及 ABI，与固件 `/etc/openwrt_release`、软件包管理器架构一致。
2. APK 安装、依赖预检、原有软件保留、AdZero 默认禁用、启停、卸载及异常恢复均可复现。
3. LAN 终端 IPv4/IPv6、DNS 拦截/放行、NaiLink/PassWall DNS 路径、代理共存、重启、升级与回滚实测。
4. 编译版本与发行包、`releases.tsv` 和每个 SDK/包 SHA 一致；经证据复核后才允许新增 `sdk_verified` 行并**发布新版本**。不修改旧 v0.21.5 发布资产。

所有真实设备未完成的检查必须标注为待测，不把源码核验、CI 编译或虚拟机安装冒称设备网络认证。
