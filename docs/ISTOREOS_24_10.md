# iStoreOS 24.10.8 原生 OPKG/IPK 兼容性

**当前状态：已开放独立 Beta.1 供用户自愿试装，尚未完成真实设备认证。**

只支持原厂 **iStoreOS 24.10.8、修订号 2026073111** 的三个精确目标：

| 平台 | 固件 target | 软件包格式 | 状态 |
| --- | --- | --- | --- |
| x86_64 | `x86/64` | OPKG / IPK | 公开 Beta.1 |
| ARM64 ARMSR | `armsr/armv8` | OPKG / IPK | 公开 Beta.1 |
| ARM64 Rockchip R6XS | `rockchip/armv8` | OPKG / IPK | 公开 Beta.1（须核对原厂板型） |

**不是**所有 iStoreOS 24.10.x 都能装；`24.10.7`、其他修订号与未列出的机型均会拒绝。24.10 使用 OPKG/IPK，不允许复用 25.12 的 APK 或跨固件依赖。

## 使用方法

以 root SSH 登录路由器，运行统一一键安装命令：

```sh
curl -fL --proto '=https' --proto-redir '=https' https://raw.githubusercontent.com/slobys/AdZero/main/scripts/install-auto.sh -o /tmp/adzero-install-auto.sh && sh /tmp/adzero-install-auto.sh --menu
```

它会自动进入 iStoreOS 24.10.8 Beta.1 的中文菜单。首次建议「安装但不启用过滤」，确认原 DNS、代理与 LAN 正常后再开启。使用前请备份并保留本地管理或救援通道。

下载页面：[v0.21.5-istoreos-24108-beta.1](https://github.com/slobys/AdZero/releases/tag/v0.21.5-istoreos-24108-beta.1)。

## 三份原厂固件的实际验证

- 原始官方固件 `fw.koolcenter.com/iStoreOS` 的 gzip SHA-256、RAW SHA-256、SquashFS 内版本、修订号、target、架构和已安装 OPKG 数据库均验证一致。
- x86_64、ARMSR、R6XS 固件原始数据库包含 **1041、1001、922** 个软件包，保留完整版 `tcpdump`。
- 三种原厂 Buildroot 对应的源代码、固定原厂配置以及 **38 条 feeds 精确 Git 提交**经过检查。
- 三份 AdZero 原生 IPK 已通过对应 iStoreOS 24.10 厂商 LuCI Buildroot 打包规则生成，真实包头：`luci-app-adzero_0.21.5-r1_all.ipk`、`Architecture: all`、原厂 OPKG gzip-tar 格式。
- 从固件自身原厂 24.10.8 镜像软件源下载并核验 **7 个尚未预装依赖 IPK/目标**，共 21 个；没有强制替换原厂完整版 `tcpdump`。
- 使用从原厂 x86 镜像提取的真实 OPKG ELF 及原厂 musl 组件，在 x86/ARMSR/R6XS 三套原始 installed 数据库和全部 `.list` 文件快照中，将真正编译出的原生 AdZero IPK 加上 7 个原厂依赖进行 `--noaction` 模拟。三套均通过：各新增 8 个包、0 升级、0 删除，原始数据库 SHA 完全不变。
- 最终发布安装器的 `--check` 已在同样的三个原始固件快照中通过完整离线验包与依赖检查，不修改任何 DNS/代理设置。

原始预备检查见 [旧候选证据](evidence/istoreos-24.10.8/original-image-opkg-dependency-readiness.json)，最终真实 Buildroot IPK、原厂 OPKG 模拟和全部发布 SHA 见 [Beta.1 最终证据](evidence/istoreos-24.10.8/public-beta-0.21.5-native-build-assets.json)。

## 原厂 x86 虚拟机实装与卸载（2026-10-29）

除上述三个目标的原厂 OPKG 离线模拟外，已在**原版 iStoreOS 24.10.8 x86/64** 固件镜像的 QEMU TCG 虚拟机中进行完整软件包操作。原始 RAW 镜像 SHA-256 为 `431470911d0aac490c14e744c8213c057819f1c1ac72bc66aa428bf4f8315170`；系统盘使用可丢弃的 QCOW2 写时复制层，额外只读来源的本地数据盘装载已通过 SHA256SUMS 校验的公开 Beta.1 安装器与 x86 IPK 压缩包。虚拟机**完全断网**，没有接触用户路由器。

- 使用原厂 OPKG，公开安装器 `--check --local-dir` 在虚拟机内通过 `--noaction` 预检，未安装包或更改 DNS。
- 公开安装器 `--install --local-dir` 实际安装并配置 `luci-app-adzero 0.21.5-r1`。虚拟机确认 OPKG 已登记、配置与 Beta 标记存在，未生成 AdZero 管理的 DNS/代理状态；安装器报告**默认关闭过滤**。
- 公开安装器 `--uninstall` 通过，OPKG 不再列出 `luci-app-adzero`，Beta 安装标记已移除。卸载输出出现两条非致命的 `Command failed: Not found`，暂未确认来自何种清理调用，不能把日志视为完全无告警；原厂依赖包的卸载后完整清单未独立比对。

详细的镜像与发布文件 SHA、测试边界、成功标记及警告见 [QEMU x86 实装证据](evidence/istoreos-24.10.8/qemu-x86-beta1-offline-install-uninstall.json)。虚拟机在完成上述检查后由宿主超时终止，**没有验证路由器重启、LAN DNS、启用过滤后的代理或异常恢复**；ARMSR/R6XS 未做等价虚拟机实装，更不能据此标记实体设备通过。稳定 `sdk_verified` 仍不变。

## 仍然需要真实设备试验

虽然有原厂镜像与真实包管理器的隔离验证，尚无实体设备全部验收反馈。请优先用测试路由器验证安装/卸载、开启/关闭后 LAN IPv4/IPv6 DNS、PassWall/NaiLink/其他代理共存、重启、故障恢复。预发行不等于长期稳定认证。

**源码溯源限制：** 原厂 24.10.8 发布时没有提供能证实固件镜像实际构建 Git HEAD 的历史 `commit.buildinfo`。本项目使用当时 iStoreOS 24.10 历史源码并锁定每个原厂 feed Git SHA，但不会声称其历史 Git HEAD 与原厂最终镜像完全一致。打包时只省略无关的 BPF/LLVM 主机工具编译，保留固件架构、LuCI 来源及 OPKG 包格式。

已发布安装资产与稳定版 v0.21.5、iStoreOS 25.12 Beta.3 完全隔离；不把这三种目标直接加入稳定 `sdk_verified`。
