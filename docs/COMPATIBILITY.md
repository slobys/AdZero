# 兼容性与证据矩阵
> 最新独立公开试装渠道包括 **OpenWrt/ImmortalWrt ARM64 Beta.2**、**iStoreOS 25.12 Beta.3** 与 **iStoreOS 24.10.8 原生 OPKG Beta.1**，统一 SSH 安装入口见 [README](../README.md)。本页下面标注 2026-10-28 的文字属于历史构建/验收记录，不代表当前没有 Beta 安装渠道；**稳定版 sdk_verified 仍是五组精确目标**。

**当前安装器：** 安装器不再硬编码只接受名为 OpenWrt 的发行版，而是依据发行目录 `releases.tsv` 中的固件 ID、完整版本、target 和包格式匹配唯一包，并核对固件声明架构、包管理器实际架构及 SHA-256。`scripts/build_release.py` 仅认可 `compatibility-matrix.json` 中明确标为 `sdk_build=passed` 的完整条目；候选固件列表不能视为 SDK 验证通过。没有对应 SDK 条目、也未明确列入独立公开 Beta 的 ImmortalWrt/iStoreOS 版本仍不能安装；新目标须按厂商源构建验证，不能混用固件渠道。不要靠修改 `DISTRIB_ID` 或改名安装包来绕过限制。 **独立公开 Beta 试装渠道例外**：iStoreOS 25.12.6 revision 2026102312 可使用 [Beta.3 APK 渠道](RELEASE_ISTOREOS_BETA_0.21.5_BETA3.md)；iStoreOS 24.10.8 revision 2026073111 可使用 [Beta.1 OPKG/IPK 渠道](RELEASE_ISTOREOS_24108_BETA_0.21.5.md)。两条渠道分别只认对应的 x86_64、ARMSR、R6XS 精确目标和同厂商依赖，默认不启用过滤；均未完成全部真机 LAN 验收，不修改稳定版 SDK 白名单。

**OpenWrt/ImmortalWrt ARM64 独立公开 Beta（2026-10-29）**：OpenWrt 25.12.6 和 ImmortalWrt 25.12.2 各自新增 `rockchip/armv8` / `mediatek/filogic` 两种原厂 SDK APK，另通过 [独立 ARM64 Beta.2 一键安装器](RELEASE_ARM64_BETA_0.21.5_BETA2.md) 提供有条件公开试装。完整版本/target/ABI/SHA 精确匹配，缺失依赖仅从所属厂商官方 HTTPS 仓库或中科大同厂商 HTTPS 镜像加载（严格检查完整固件版本、target 和 ABI）；预检若可能替换/升级已安装软件就拒绝，安装后默认不启用 DNS 拦截。**不修改稳定版 sdk_verified 或 releases.tsv，不代表实体路由器已通过 LAN/代理/回滚验收。**

**iStoreOS 24.10.8 / 2026073111 原生 OPKG/IPK 独立公开 Beta.1**：x86_64（`x86/64`）、ARMSR ARM64（`armsr/armv8`）、Rockchip R6XS ARM64（`rockchip/armv8`，原厂板型）各自完成历史厂商 Buildroot 原生 LuCI IPK 构建、7 份目标厂商 IPK 依赖哈希，以及使用原版 OPKG 程序和完整 1041/1001/922 已安装包文件清单的 8 包离线安装模拟，**0 现有包升级/删除**。此 Beta 独立发布，严格限制 revision `2026073111`，绝不混用 25.12 APK；详情见 [iStoreOS 24.10 公测](ISTOREOS_24_10.md)。原厂历史源码精确构建 Git HEAD 未能由 OEM commit.buildinfo 证明；真实设备 DNS、代理、重启和恢复验收仍待完成，不加入稳定 `sdk_verified`。

v0.18.0 使用共享代码和下列 SDK 组合构建，不把旧版本的单设备证据升级为新功能的真机认证。代理识别增加配置归属核对；原生服务统一使用外部期限启动，避免受影响固件的子进程信号继承问题。功能范围、固件构建和实际 LAN 验收分别记录。

| 固件完整版本 | target / 包架构 | 包格式 | SDK | 设备安装 | LAN 过滤 |
| --- | --- | --- | --- | --- | --- |
| OpenWrt 24.10.8 | x86/64 / x86_64 | IPK | 通过 | 待测 | 待测 |
| OpenWrt 25.12.5 | x86/64 / x86_64 | APK | 通过 | 待测 | 待测 |
| OpenWrt 25.12.5 | rockchip/armv8 / aarch64_generic | APK | 通过 | 待测 | 待测 |
| OpenWrt 其他 24.10.x / 25.12.x | 按实际 target | 实际 OPKG/APK | 逐项推进 | 待测 | 待测 |
| ImmortalWrt 24.10.6 | x86/64 / x86_64 | IPK | 独立厂商 SDK 编译与实际包验证通过 | 待测 | 待测 |
| ImmortalWrt 25.12.2 | x86/64 / x86_64 | APK | 独立厂商 SDK 编译与实际包验证通过 | 待测 | 待测 |
| iStoreOS 25.12.6，修订 2026102312 | x86/64 / x86_64 | APK（官方镜像已核实） | 原厂 Buildroot 已生成专用 APK、封装与依赖闭环验证通过；仍为候选 | 待测 | 待测 |

机器可读计划见 [compatibility-matrix.json](compatibility-matrix.json)。x86/64、rockchip/armv8 之后，按 mediatek/filogic、ipq40xx/generic、ramips/mt7621 逐项扩展；i386、MIPS/MIPS64、RISC-V 仍为候选。

**2026-10-28 ARM64 新一轮开发（候选，非正式支持）：**OpenWrt 25.12.6 新增 rockchip/armv8、mediatek/filogic 两组官方 SDK 哈希及 CI 构建；ImmortalWrt 25.12.2 新增相同两个 target 的**独立厂商** SDK 哈希及 CI 构建；iStoreOS 25.12.6-2026102312 预先锁定 ARMSR（13 组 feeds）与 R6XS Rockchip（15 组 feeds）原厂固件配置及镜像 SHA，并扩展原生构建/候选 APK 检查。机器可读的六个待验组合单独记在 `arm64_candidates`，与 `sdk_verified` 完全隔离。**编译或测试证据不足之前，所有新增组合均不能被菜单安装或在线更新选中。**参见 [ARM64 适配进度与门槛](ARM64.md)。

**iStoreOS ARM64 原生构建与离线验收（2026-10-28）：** ARMSR（`armsr/armv8`，13 组原厂 feeds）及 Rockchip R6XS（`rockchip/armv8`，15 组原厂 feeds）两套原厂固定 Buildroot 均已**实际生成原生 APK 候选包**；独立检查官方镜像的 `/etc/openwrt_release`、原版 APK 数据库、候选包 SHA-256、66 个交付文件和 LuCI 内容。两套固件各缺 6 个原厂依赖，已分别由目标原厂构建并完成递归 SHA/ABI 检查；在原厂已安装 APK 数据库的临时副本上，使用对应原厂 apk-tools 执行无网络模拟，均 **7/7 包成功、0 个原系统包移除**，原有完整版 `tcpdump` 保留。详细证据见 [ARMSR 离线解算](evidence/istoreos-25.12.6/arm64-armsr-native-offline-solver.json)、[R6XS 离线解算](evidence/istoreos-25.12.6/arm64-r6xs-native-offline-solver.json) 和 [ARM64 专项记录](ARM64.md)。**仅证明原厂构建与离线模拟，不能替代实体路由器实际安装、DNS、代理共存、重启、升级和恢复验证；仍留在 `arm64_candidates`，不加入公开 `sdk_verified`、菜单安装或在线更新清单。** 两套候选虽然内部版本为 `0.21.5-r1`，但不能覆盖 GitHub 已发布 v0.21.5 的文件。

rockchip/armv8 的官方 profiles 元数据与用户回报均为 OpenWrt 25.12.5 r33051-f5dae5ece4，SDK 的 CONFIG_TARGET_ARCH_PACKAGES 为 aarch64_generic，CONFIG_ARCH 为 aarch64。安装器区分固件包 ABI 与 APK 的 CPU 名，接受该目标上 --print-arch 返回 aarch64 或 aarch64_generic；其他 ABI/CPU 组合仍拒绝。不能把这一别名扩展为任意 ARM64 固件兼容。

2026-10-21 用户反馈：v0.5.3 已在该 Rockchip 设备启用，后端载入 66,425 条域名并完成 NaiLink API 配置同步。这是旧版特定设备的安装/配置证据；上表 v0.18.0 的设备安装与 LAN 项目仍待重新验收，不能直接继承为全部通过。

本体使用 shell、ucode、LuCI JS，未捆绑特定 CPU 的 ELF。依赖、字节序、浮点 ABI、固件 feeds 和包管理器仍按目标分别处理。实际包架构优先于 CPU 位数或产品宣传名；all/noarch 不是跨固件认证。

## ImmortalWrt / iStoreOS 兼容性扩展（2026-10-28）

ImmortalWrt 不复用 OpenWrt 的二进制包。已从 ImmortalWrt 官方下载站核对两个 x86/64 发行目标，以及其独立 SDK SHA-256：

- 24.10.6：OPKG，ImmortalWrt SDK GCC 13.3.0，SHA-256 709bea2ce466b7ad671ce00fdcb1eb69b2b3fa0f0921b12b620725d95cd8c0e5
- 25.12.2：APK，ImmortalWrt SDK GCC 14.3.0，SHA-256 4d4631f134c2f27215a91aa7ef5bf9a01ea96c2450d3b8a5c38048c32462ac60

官方 rootfs 的 /etc/openwrt_release 也已核对：DISTRIB_ID='ImmortalWrt'、完整版本、x86/64 和 x86_64 架构；相应 DISTRIB_REVISION 分别为 r33869-cf234f8de6d5 和 r38135-0c4cd0f9920a。新增 vendor-sdk.yml 已使用各自的官方 SDK、各自 feeds 完成独立编译与封装验证；构建产物 SHA-256、安装文件和 LuCI 交互测试全部通过，证据见 [vendor-sdk-builds.json](evidence/v0.21.5/vendor-sdk-builds.json)。这两个精确目标现列入 sdk_verified，但实际设备启停、DNS、代理和故障回滚仍需验收；不能扩展为全部 ImmortalWrt 版本通用。

iStoreOS 最新 x86_64 25.12.6-2026102312 的官方编译元数据已经核实：源代码 istoreos/istoreos 的 istoreos-25.12 分支提交是 283d02639c740f8ae6e9425b48be8c61f152c6b8。官方另提供对应 config、feeds 和构建信息；CONFIG_VERSION_DIST="iStoreOS"。scripts/probe_istoreos.py 使用 SHA-256 固定并核对这些官方元数据；scripts/prepare_istoreos_source.py 还可从该固定提交准备原生源码与 13 个 SHA 锁定的 feeds，拒绝覆盖用户已有代码。源代码及 13 组 SHA 锁定的 feeds 准备已经实测通过，宿主构建工具链也已成功构建。进一步从官方 SHA-256 核验的完整原始镜像内，直接读取 `/etc/openwrt_release`：`DISTRIB_RELEASE=25.12.6`、`DISTRIB_REVISION=2026102312`、`DISTRIB_TARGET=x86/64`、`DISTRIB_ARCH=x86_64`；同时确认 `/lib/apk/db/installed` 存在。证据见 [istoreos-original-image.json](evidence/v0.21.5/istoreos-original-image.json)。现已使用该精确固件对应的原生 Buildroot 生成 `luci-app-adzero-0.21.5-r1.apk`，完成 66 个封装文件和 406 项 LuCI 交互断言验收，并对官方固件镜像内的 1,065 个已安装包做递归依赖核对：缺失的 7 个依赖均由原厂锁定源码构建的 APK 提供，SHA-256、版本及包架构一致。原始字节与构建来源审计见 [iStoreOS 原生 APK 候选证据](evidence/v0.21.5/istoreos-native-apk-candidate.json)。**这些仅证明构建及离线依赖闭环，不代表已完成固件 SDK/ABI 与真实路由器上的 DNS、代理、重启、卸载及故障回滚验收**。因此继续拒绝通过公开安装器安装 iStoreOS，不能将候选包加入正式 `releases.tsv`。

**隔离虚拟机扩展验证：**在 QEMU/KVM 以临时磁盘快照启动原厂镜像，实际读取的 `DISTRIB_ID=iStoreOS`、版本 `25.12.6`、修订 `2026102312`、内核 `6.12.112` 和 APK 3.0.5 均与构建目标一致。挂载仅用于测试的 ext4 虚拟磁盘，在完全无外网的 `apk add --simulate --no-network` 中，AdZero 原生 APK 加七个厂商依赖均通过解算，但**发现原包会把系统预装的完整 `tcpdump` 替换成 `tcpdump-mini`**。后续源码已改为通用 `tcpdump` 依赖，原有完整版与提供 `tcpdump` 虚拟能力的 mini 均可满足，避免无必要的软件包替换；必须重新编译新源码并在虚拟机及真实设备中验收，才能对外发布 iStoreOS 包。虚拟机内预检不等于实机 LAN、DNS、代理共存验收。

**修复后的隔离虚拟机实装（2026-10-28）：**使用原厂镜像、禁用外网连接的 QEMU/KVM 和临时磁盘快照，将**修复为通用 `tcpdump` 依赖**的 AdZero 原生 APK 与 6 个缺失的厂商 APK 一起作为离线本地包提供。原厂 APK 的 `--simulate --no-network` 返回 0，且没有移除原有完整版 `tcpdump`；随后在相同隔离环境中执行真实 `apk add --no-network --allow-untrusted` 安装也返回 0，确认 AdZero 已登记、配置文件存在、完整版 `tcpdump` 仍存在、AdZero 默认启用状态仍为 `0`。验证记录及全部 APK/来源 SHA-256 见 [原厂 iStoreOS 虚拟机安装验收](evidence/istoreos-25.12.6/native-apk-virtual-machine-acceptance.json)。**注意：此候选包内部仍标注 0.21.5-r1，但与公开 v0.21.5 Release 的实际字节不同，不能覆盖或冒充已发布资产。该验证仍不证明实际路由器上的 LAN、NaiLink、DNS、启动及升级回滚已通过；不得据此自动开放 iStoreOS 一键安装。**

**iStoreOS 固件修订号保护（后续独立安装包）：** `25.12.6-2026102312` 是固件镜像的组合命名，系统真实的 `DISTRIB_RELEASE=25.12.6` 与 `DISTRIB_REVISION=2026102312` 是两个独立字段。安装器现已为未来的 iStoreOS 专属包预留 `releases.tsv` 第八列（必须是 10 位数字），并要求两项完全匹配；旧式七列的 iStoreOS 行也会被拒绝。其他 OpenWrt、ImmortalWrt 发行清单保留七列不变。**这一安全准备不意味着已经发布了 iStoreOS 安装包**。

请保留系统 /etc/openwrt_release 的真实信息，不能把 ImmortalWrt / iStoreOS 伪装成 OpenWrt。所有新固件必须逐一通过 SDK 构建、包格式验证、安装备份恢复、DNS 和代理的真实设备验收。

## 代理范围

| 环境 | 当前处理 | 待补证据 |
| --- | --- | --- |
| 无代理 | 当前解析器的 adblock-fast 输出 | 路由器及终端查询 |
| NaiLink / daed | 本地认证 CGI/API + GeoSite reject | daed API、eBPF、实际 LAN |
| PassWall | 原生 block_host / use_block_list | dnsmasq、ChinaDNS-NG、SmartDNS、ACL |
| OpenClash | 原生覆写钩子 + Meta 内核配置检查 | 不同插件/核心版本、订阅刷新、TUN |
| Nikki | 原生混入 + UCI 数组合并，核对生成配置 | 不同插件/核心版本、重启、TUN |
| PassWall 2 | 识别，拒绝自动接入 | 独立适配实现 |
| SSR Plus+ | 原生 deny.list 广告过滤、运行 DNS 配置检查；保留原生分流，需关闭 AdZero 自动分流 | 不同版本、真实设备和 LAN |
| HomeProxy | 区分 sing-box-c 客户端和服务端/日志实例，仅识别 | 原生 DNS 适配和真实设备 |
| 多代理/未知代理 | 检测并拒绝自动修改 | 查明 DNS 所有者 |

Mihomo 的本机真实 DNS 检查覆盖普通/Fake-IP、UDP/TCP、A/AAAA；不涉及 LAN 或路由器防火墙。dae 验证覆盖真实配置解析和 DAT 加载，不启动 eBPF。配置/UCI 事务测试中的服务重启是隔离替身。

精确 procd 实例与共享核心进程分开报告；不凭 Mihomo/Xray/sing-box 进程名推断插件所有者。界面的终端验证字段保持 false，直到有真实设备证据。

v0.12.3 对 NaiLink 单独查询实际代理内核状态，不再把常驻管理进程等同于代理运行；已确认停止的内核优先于保存的启用标记。无法确认时返回独立原因。其余原生接入仍保持现有范围，切换与恢复说明见 [切换代理](PROXY_SWITCHING.md)。

## 不自动处理的情况

v0.21.3 对 NaiLink / PassWall 的独立 DNS 并发新增实际 TCP 路径预检与接入后的应答校验。NaiLink 国外并发及严格模式要求可用本机 SOCKS5；国内并发还要求普通 TCP 53 上游可达。PassWall 国内有相同传输条件，国外仍要求原生 TCP DNS 与本机 SOCKS5。OpenClash / Nikki 使用原生内核并发，不走该独立进程；不新增 SOCKS 要求。该检查不证明任意版本、终端或公网链路已验收，详见 [DNS 设置](DNS_SETTINGS.md)。

- 同时有多个启用/运行的代理，或无法识别的代理核心。
- Nikki core_only、禁用但已有内容的混入文件。
- Fake-IP whitelist 模式；不擅自改变原模式语义。
- dnsmasq 放行例外与拦截父域名重叠，无法等价导出给代理。
- 用户改动了自有混入/恢复记录，不能安全覆盖。
- AdGuard Home 运行，或 adblock-fast 由外部启用。
- nftset/ipset 等非纯域名输出模式。

## 认证要求

每个组合记录固件完整版本、SDK/包 SHA-256、target、包架构、后端/代理/内核版本和网络拓扑。必须验收启停、规则更新、订阅更新、重启、升级、卸载和失败恢复，以及 IPv4/IPv6、UDP/TCP、LAN DNS、代理内核 DNS、终端 DoH/DoT 和绕过路径。

证据分层为：源码审阅 → 隔离运行/替身 → SDK → 设备安装 → LAN 终端。前一项不能替代后一项。参见 [DNS_ADAPTERS.md](DNS_ADAPTERS.md)、[LOCAL_VALIDATION.md](LOCAL_VALIDATION.md) 和 [DEVICE_ACCEPTANCE.md](DEVICE_ACCEPTANCE.md)。

## v0.9.0 分流能力边界

现有三项目标 SDK 安装矩阵不扩大。四代理均有生产适配器；Mihomo 真实核心隔离路径与 dae 解析验证分别记录，PassWall 当前只有原生参数/事务验证，真机及原生生成器输出待验收。无代理路径要求单个启用 dnsmasq 与支持的 adblock-fast dnsmasq 模式。不会从 CPU 架构推断 LAN、eBPF/TUN 或任意插件版本的支持。详见 SMART_DNS.md。

v0.13.0 扩展 SSR Plus+、HomeProxy 的只读识别，并明确返回 `adapter_supported`。新增识别不是原生适配认证；四个现有适配器不变。共享内核进程仍不用于猜测代理所有者。
