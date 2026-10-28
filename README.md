<p align="center">
  <img src="docs/images/adzero-logo.png" alt="AdZero Logo" width="104" height="104">
</p>
<h1 align="center">AdZero</h1>
<p align="center">软路由上的广告过滤与 DNS 分流插件</p>
<p align="center">
  <a href="https://github.com/slobys/AdZero/releases">下载安装包</a> ·
  <a href="https://slobys.github.io/AdZero/">在线文档</a> ·
  <a href="https://www.youtube.com/@naiyou">YouTube 频道</a> ·
  <a href="https://t.me/naiyous">Telegram 群组</a> ·
  <a href="https://github.com/slobys/AdZero/issues">反馈问题</a>
</p>

AdZero 直接集成在 OpenWrt 的 LuCI 管理页面中，无需额外登录。添加想用的规则、按需调整 DNS，修改完成后统一应用。

日常使用和设置请看 [在线使用文档](https://slobys.github.io/AdZero/)，包括安装、规则、拦截与放行、DNS、代理切换和常见问题；支持中文搜索、命令复制与手机阅读。

## 能做什么

- **广告过滤**：内置常用规则库，也能添加自己的 HTTPS 规则订阅。
- **批量应用**：先添加、编辑或开关多项规则，再一次应用，减少重复加载。
- **拦截统计**：每秒刷新局域网可观察的请求数与「DNS请求拦截占比」，说明它不代表网页广告拦截率；可一键清零请求数和拦截数，从同一时刻重新计算。
- **DNS 应答耗时**：分开显示正常、拦截应答的平均耗时，以及慢应答和 15 秒内未观察到应答的数量；只统计当前接入期间采集到的样本。
- **最近拦截**：按需开启 5 分钟的内存记录，查看域名、次数并检查误杀。
- **效果检测**：复制页面提供的查询命令，在电脑执行后自动核对拦截与正常解析。
- **访问控制**：集中查询域名、查看最近拦截，搜索和批量管理自定义放行／拦截名单；改完统一应用。
- **命中来源**：查询拦截来自哪个已应用列表；来源快照缺失时明确提示，不猜测。
- **临时放行**：支持 10 分钟或 1 小时，到期由路由器尝试恢复，重启后失效。
- **DNS 增强**：可选的 CNAME 过滤和按设备附加规则；上游测试与同组备用集中在概览的 DNS 设置。具体路径和限制见 [增强功能说明](docs/DNS_ENHANCEMENTS.md)。
- **临时暂停**：网站或 App 异常时，暂停几分钟做对比，到期尝试自动恢复。
- **可编辑 DNS**：国内、国外 DNS 都有默认地址，可以自行添加、删除和调整顺序。
- **可选自动分流**：有受支持代理时，国内直连、国外走当前代理；无代理时使用国内 DNS 和本地直连。默认关闭。

提供 NaiLink、PassWall、OpenClash、Nikki 的 DNS 适配，并支持 SSR Plus+ 原生域名广告过滤。使用 adblock-fast 处理广告规则，无需另外安装 AdGuard Home 或 SmartDNS。

## 安装与升级

v0.21.4 新增 LuCI 在线更新、中文菜单安装与固件 SDK 兼容判断改进。详见 [v0.21.4 更新说明](docs/RELEASE_0.21.4.md)。v0.21.3 的 NaiLink／PassWall DNS 并发修复继续保留。

当前为 **v0.21.5 测试版（Pre-release）**，DNS 地址、并发、严格模式、上游测试和自动备用集中在运行概览。PassWall 不支持的组合会在修改网络前预检，并显示具体原因。升级保留原来的单一模式，严格模式默认关闭。严格模式覆盖默认 IPv4 LAN 的明文 DNS 路径，并阻断该 LAN 的 IPv6 DNS 和 853 端口；浏览器 DoH 需另行管理。详见 [DNS 设置与范围](docs/DNS_SETTINGS.md)。

### 中文菜单式安装（推荐）

以 root 登录路由器 SSH，运行下面**一条固定命令**。地址不包含版本号，不需要每次修改命令；菜单会根据已安装状态自动选择安装或升级，并使用发行版内固定的 SDK 校验清单选包：

```sh
curl -fL --proto '=https' --proto-redir '=https' https://raw.githubusercontent.com/slobys/AdZero/main/scripts/install.sh -o /tmp/adzero-install.sh && sh /tmp/adzero-install.sh --menu
```

菜单包含「一键安装/自动升级」「安装但不自动开启」「只读环境与安装包校验」「安全卸载」「退出」。**不会要求手填 OpenWrt 版本、目标架构、IPK/APK 文件名或 GitHub 发布版本**；默认使用仓库安装器当前固定的已发布版本，维护者发布新版时更新该默认来源。下载后也可以再次运行 `sh /tmp/adzero-install.sh --menu` 打开菜单。

安装/升级仍会严格检查固件完整版本、target、软件包架构和 SHA-256；不在兼容矩阵内的设备会停止，不会强装。环境检查只下载并核对安装包，不改变路由器服务。普通用户推荐菜单；已有自动化脚本仍可用 `--install`、`--upgrade`、`--no-enable` 和 `--local-dir` 进行非交互操作。

安装后打开 **服务 → AdZero**。升级后如果页面未更新，按 `Ctrl + F5` 刷新。

安装器会检查机型与软件包、备份配置并安装依赖。也可以在 [GitHub Releases](https://github.com/slobys/AdZero/releases) 下载已发布且匹配固件的发行包。详细操作见 [安装与恢复](docs/INSTALLATION.md)。

### LuCI 页面内在线更新（无需 SSH）

安装**包含此功能的新版本**后，版本号会显示在左上角 AdZero Logo 旁。点击版本号即可检查 GitHub 官方发布版本；有匹配的新版本时显示目标版本、兼容性结果和「立即更新」，再次确认后启动后台升级任务。升级完成后刷新页面核对版本。网页打开时也会静默检查一次，GitHub 不可用不会阻断现有广告过滤。

- 更新检查使用 GitHub Releases API，包括已发布测试版但不包含草稿；仅接受 vX.Y.Z 标签和具有 GitHub SHA-256 digest 的官方 install.sh 资产。
- 更新前验证发布安装脚本 SHA-256，并调用其只读预检核对固件 ID、完整版本、target、包格式、架构及安装包 SHA-256。**没有匹配 SDK 构建的固件仍会拒绝升级。**
- 只有第二次点击「确认升级」才会启动后台任务。已校验的许可 15 分钟有效、针对特定版本且一次性使用；并发任务被锁定，失败不会自动重复安装。
- 后台沿用安装器原有的依赖预检、配置备份、恢复记录和安装锁。升级可能短暂中断 DNS/代理；若未确认成功，保留恢复记录并手动核对。
- 这不是 OpenWrt 固件升级或刷机，只升级 AdZero 软件包。
- **注意：已发布的 v0.21.3 安装包尚不包含在线更新按钮。** 需要先通过 SSH 菜单升级到 v0.21.4（正式构建发布后）或更高版本，此后才可在 LuCI 内点击更新。合并源码不会自动替换旧 Release。

## 日常怎么用

1. **选规则**：在「过滤规则」添加需要的列表，也可以粘贴自己的订阅地址。
2. **按需改设置**：修改 DNS 或分流开关，选择会进入「待应用」。每组可选单一或并发；严格模式按需启用。在同一入口测试已应用地址，单一模式可启用同组备用切换。
3. **统一应用**：改完后点击「应用并重新加载」。有草稿时「重载已应用规则」不可用，先应用或撤销草稿；「更新订阅」获取新规则，「手动校验」只检查本地已下载规则。

「访问控制」集中管理最近拦截和自定义名单；用法及范围见 [统计与访问控制](docs/ACCESS_CONTROL.md)。

保护总开关、临时暂停为即时操作；访问控制中的放行／拦截先加入待应用，改完统一生效。应用规则或网络设置时，可能短暂重载 DNS 或当前代理；具体耗时取决于设备与规则规模。

## 兼容范围

以下组合已有对应官方 SDK 构建包：

| 固件 | 设备目标 | 软件包 |
| --- | --- | --- |
| OpenWrt 24.10.8 | x86/64 | IPK |
| OpenWrt 25.12.5 | x86/64 | APK |
| OpenWrt 25.12.5 | rockchip/armv8 | APK |
| ImmortalWrt 24.10.6 | x86/64 | IPK（独立厂商 SDK 编译通过） |
| ImmortalWrt 25.12.2 | x86/64 | APK（独立厂商 SDK 编译通过） |

以上五种完整固件/目标组合均已具备独立 SDK 构建证据，真实路由器的网络与广告过滤效果仍待验收。ImmortalWrt 的其他版本、iStoreOS、其他架构按 [兼容矩阵](docs/COMPATIBILITY.md) 逐步推进；安装器仍会拒绝发行清单中没有的组合。

**iStoreOS 进度（尚未发布）：**原厂 iStoreOS 25.12.6、修订 `2026102312`、x86_64 的专用 APK 已从原厂 Buildroot 编译，并在隔离 KVM 虚拟机完成离线模拟与实际安装；六个原厂依赖均能满足，原有完整版 `tcpdump` 得以保留，安装后 AdZero 仍默认关闭。详见 [虚拟机验证证据](docs/evidence/istoreos-25.12.6/native-apk-virtual-machine-acceptance.json)。这并非实机 LAN、NaiLink 或 DNS/回滚验收，**iStoreOS 尚未列入公开一键安装包**。本次新字节不是已发布的 v0.21.5 Release 资产。

**安装器兼容性修正（v0.21.5）**：不再因 `DISTRIB_ID` 不是 `OpenWrt` 就直接拒绝，改为匹配发布清单中的**固件名称 + 完整版本 + 设备 target + IPK/APK 格式**，随后核对包架构和 SHA-256。ImmortalWrt 增加两个 x86_64 的厂商 SDK 专属构建组合；其他 ImmortalWrt 版本及尚无专用 SDK 包的 iStoreOS，仍不允许强行跨固件安装，但会明确显示检测到的系统信息和已支持组合。详见 [安装常见问题](docs/INSTALLATION.md)。

NaiLink、PassWall、OpenClash、Nikki 的不同版本需分别验收。SSR Plus+ 使用原生屏蔽列表，保留节点和分流设置，需关闭 AdZero 自动分流；PassWall 2 和 HomeProxy 目前仅识别。同时开启多个代理或遇到未知代理时，不会自动接管。概览中的「代理接入」可展开查看：区分实际内核、已保存开关和当前接入，不把识别到插件当作已经适配。

PassWall 保留原有 dnsmasq 或 ChinaDNS-NG 分流引擎，沿原生屏蔽列表接入；SSR Plus+ 也使用原生接口。会核对运行进程、配置加载路径与屏蔽规则样本，发现 DNS 子进程回收信号阻塞时单独提示。未加载时有限重试，内容未变且运行配置正常时不重复重启代理；这些检查不能代替终端效果检测。

切换时先停止旧代理、启动新代理，再点击「重新检测」。NaiLink 的管理服务和代理内核分别判断；说明见 [切换代理](docs/PROXY_SWITCHING.md)。

PassWall 默认 IPv4 LAN DNS 接管模式下，会每五秒检查旧 UDP DNS 会话；入口稳定后只处理仍走旧入口的连接，不清空全网连接。需要小型 `conntrack` 依赖；自定义 ACL、未知规则或多代理状态会跳过。已缓存的错误解析不会被路由器远程清除，IPv6、TCP 异常和 nftset 报错需分别验收。

## 卸载

先关闭保护并恢复 AdZero 管理的设置，成功后卸载。按路由器的包管理器选择一条：

```sh
# APK 固件（如 OpenWrt 25.12.5）
/usr/libexec/adzero/ctl disable && apk del luci-app-adzero

# OPKG 固件（如 OpenWrt 24.10.8）
/usr/libexec/adzero/ctl disable && opkg remove luci-app-adzero
```

如果恢复失败或提示操作锁占用，先保留错误信息，不要强制卸载或手动删除恢复记录。

## 使用前了解

DNS 过滤主要拦截广告和追踪域名，无法保证去除同域视频广告或网页空白。设备使用自己的加密 DNS、外部 DNS 或绕过路由器时，过滤可能不生效。

界面显示「已开启」表示配置已应用，实际过滤效果还需要在手机或电脑上确认。

「效果检测」通过后会显示对应设备。统计保存在内存中，重启路由器后清零；不代表网页广告个数，不保存完整查询日志。范围与操作见 [统计与检测说明](docs/OBSERVATION.md)。

[规则管理](docs/RULE_SOURCES.md) · [DNS 设置](docs/DNS_SETTINGS.md) · [AdGuard 参考与优化方向](docs/ADGUARD_REVIEW.md) · [验证记录](docs/LOCAL_VALIDATION.md) · [开发与构建](docs/PACKAGING.md) · [Logo 与设计说明](docs/BRAND.md)

源码采用 [MIT 许可证](LICENSE)。adblock-fast、代理插件与规则源遵循各自许可。
