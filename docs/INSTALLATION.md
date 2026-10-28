# 安装与恢复

本页面向 v0.21.5 测试版及后续版本的中文菜单与 LuCI 在线更新。两组新增 ImmortalWrt 版本已有独立 SDK 构建和对应发布包；实际网络和代理共存验收仍待真实路由器验证。先查看 [兼容矩阵](COMPATIBILITY.md)；菜单不要求用户手填版本或包名，但仍只选择与固件完整版本、设备目标和包管理器匹配的已验证安装包。

## 在线中文菜单安装与升级（推荐）

以 root 登录路由器 SSH，确保有 `curl`，执行**一条固定命令**（没有需要手动替换的版本号）：

```sh
curl -fL --proto '=https' --proto-redir '=https' https://raw.githubusercontent.com/slobys/AdZero/main/scripts/install.sh -o /tmp/adzero-install.sh && sh /tmp/adzero-install.sh --menu
```

安装器核对固件版本、设备目标、包架构与 SHA-256，备份配置、安装依赖，然后尝试启用保护。未匹配的固件或架构会停止安装，不要绕过检查跨版本装包。依赖从固件原有的软件源安装，不升级整套固件。

**下一版开发中的 tcpdump 兼容修复：**原厂 iStoreOS 25.12.6 已有完整版 `tcpdump`，公开 v0.21.5 的 `tcpdump-mini` 强制依赖会令 APK 计划替换完整版。后续版本改为通用 `tcpdump` 依赖，原厂 `tcpdump-mini` 也声明提供该功能，因此已有任一版本都能满足依赖，无需替换，并能继续保持 DNS 采样功能。**此修复尚未正式发布，不要将现有 v0.21.5 当作已修复版本。**

菜单选项：**1** 自动安装或升级（首次安装自动识别）、**2** 安装/升级但不自动开启保护、**3** 只读环境与安装包 SHA-256 校验、**4** 安全卸载、**0** 退出。按提示选择即可，无须输入 OpenWrt 版本、架构、IPK/APK 或发行版本。默认发行源由维护者维护；用户始终使用相同固定 URL 下载菜单。

`--check` 只执行严格平台和包 SHA-256 检查，不调用软件包安装、DNS 修改或旧版恢复；检查需要下载发行目录和安装包。**未通过 SDK 兼容性检查时仍拒绝跨固件安装。**

**ImmortalWrt x86/64：** v0.21.5 的 SDK 矩阵扩展到 ImmortalWrt 24.10.6（IPK）与 25.12.2（APK），保持固件 ID/版本/target/包管理器严格匹配，不把全部 ImmortalWrt 当作通用兼容。iStoreOS 暂未编译专用安装包，仍须等待其原生 Buildroot / ABI 验证，不要改写固件 ID 绕过检查。

安装后打开「服务 → AdZero」。如果只想安装而不自动开启，直接选菜单 **2**。需要供自动化使用、不要显示菜单时，可显式运行：

```sh
sh /tmp/adzero-install.sh --install --no-enable
```

### LuCI 页面内在线更新（无需 SSH）

旧版 v0.21.3 尚未包含在线更新代码；首次升级到带有此功能的 v0.21.4 或更新版本时仍须使用 SSH 菜单。仅 SDK 构建和 GitHub Release 发布完成之后，才可在页面内操作。

安装**包含此功能的新版本**后，版本号会显示在左上角 AdZero Logo 旁。点击版本号即可检查 GitHub 官方发布版本；有匹配的新版本时显示目标版本、兼容性结果和「立即更新」，再次确认后启动后台升级任务。升级完成后刷新页面核对版本。网页打开时也会静默检查一次，GitHub 不可用不会阻断现有广告过滤。

- 更新检查使用 GitHub Releases API，包括已发布测试版但不包含草稿；仅接受 vX.Y.Z 标签和具有 GitHub SHA-256 digest 的官方 install.sh 资产。
- 更新前验证发布安装脚本 SHA-256，并调用其只读预检核对固件 ID、完整版本、target、包格式、架构及安装包 SHA-256。**没有匹配 SDK 构建的固件仍会拒绝升级。**
- 只有第二次点击「确认升级」才会启动后台任务。已校验的许可 15 分钟有效、针对特定版本且一次性使用；并发任务被锁定，失败不会自动重复安装。
- 后台沿用安装器原有的依赖预检、配置备份、恢复记录和安装锁。升级可能短暂中断 DNS/代理；若未确认成功，保留恢复记录并手动核对。
- 这不是 OpenWrt 固件升级或刷机，只升级 AdZero 软件包。
- **注意：目前公开的 v0.21.3 安装包尚不包含在线更新按钮。** 必须先通过安装菜单升级到首个包含此功能的正式发行包；以后才可在 LuCI 内点击更新。合并源码不会自动替换既有 Release 的文件。

## 升级

**已有 AdZero 仍使用上面相同的菜单命令，选择 1 即可自动升级**，不用填写版本。需要保留非交互式脚本调用时，先下载菜单安装器，再显式运行：

```sh
sh /tmp/adzero-install.sh --upgrade
```

升级器先校验安装包，暂停后台调度并等待当前任务完成，然后准备软件包依赖，通过后才备份配置和恢复记录、调用旧版关闭与恢复，最后安装并重新开启。个人规则选择、放行名单和 DNS 选择会保留。

APK 固件优先用已安装依赖做本地模拟检查；通过后仅安装已校验的本地包，不访问软件源、不升级其他包。缺少依赖或无法通过离线预检时，才刷新固件软件源并模拟检查；这些步骤失败不会关闭旧版保护。OPKG 的软件源刷新也在关闭旧版之前完成。软件包实际写入阶段若失败，安装器保留备份并报告错误，不声称自动回滚成功；需先核对已安装版本和状态，再恢复保护。

v0.15.4 起为旧 DNS 会话交接增加 `conntrack` 依赖。APK 固件可先执行 `apk add conntrack`，确认成功后再升级；这样也避免因新增依赖触发无关第三方源的全量刷新。此步骤失败时先保留当前可用的保护与代理，不用卸载或关闭服务。

v0.15.6 增加小型 `coreutils-timeout`，保证 PassWall 启动既有期限又不会继承被阻塞的子进程回收信号。安装器会准备依赖；缺依赖且软件源不可用时，停在关闭旧保护之前。无需为此卸载插件或重启路由器。

等待旧任务默认最多 180 秒，每 15 秒反馈。安装器不会为升级终止当前任务或删除操作锁；等待超时或恢复失败会停止升级并保留记录。普通退出或 `Ctrl+C` 会尝试恢复本次暂停的调度器。

当前网络需要先保持原状时，可加 `--no-enable`：`sh /tmp/adzero-install.sh --upgrade --no-enable`。安装器仍先关闭并恢复旧版 AdZero，但安装后不会主动开启保护。

升级完成后刷新 LuCI；资源仍旧时按 `Ctrl + F5`。

## 下载发行包安装

**注意：已发布的 v0.21.3 离线归档仍含旧版安装器，不支持 `--menu`。** 菜单功能合并并发布之后，新归档会直接带有菜单安装器。使用这种新归档时，在路由器上解压并进入包含 `install.sh` 的目录：

```sh
sh install.sh --menu --local-dir "$(pwd)"
```

如果下载的是 [v0.21.3 离线发行包](https://github.com/slobys/AdZero/releases/tag/v0.21.3)，但菜单源码已合并到主分支，可先使用上面的固定命令下载新版菜单脚本，进入 v0.21.3 解压后的发行目录，再运行 `sh /tmp/adzero-install.sh --menu --local-dir "$(pwd)"`；不要直接让 v0.21.3 自带的旧脚本处理 `--menu`。

菜单会自动识别已有安装并选择升级，不用追加版本号。高级自动化仍可使用 `sh install.sh --local-dir "$(pwd)" --upgrade`。发行归档未捆绑全部依赖，安装依赖仍可能需要联网。

当前 APK 是开发者未签名测试包。安装器只对已通过 SHA-256 核对的本地 APK 使用 `--allow-untrusted`；文件校验用于检测损坏，下载来源的可信度依赖 GitHub HTTPS。

## 常见情况

### 为什么菜单提示当前固件不支持？

菜单**不会因为名称接近就将 iStoreOS、ImmortalWrt 或其他 OpenWrt 版本当成某个官方 SDK 已验证目标**。选项 3 会给出当前固件、设备目标、包架构或 SHA-256 不匹配的具体错误，不会改变现有 DNS 或代理。当前公开发行的 SDK 组合见 [兼容矩阵](COMPATIBILITY.md)；缺少对应 SDK 包时，请提供经过脱敏的 `/etc/openwrt_release`、`opkg print-architecture` 或 `apk --print-arch` 以申请适配，不要修改系统的固件 ID 来绕过安全检查。

### 提示“此发行版尚未通过该固件 SDK 验证”

旧安装脚本在读取 /etc/openwrt_release 后，只接受 DISTRIB_ID=OpenWrt，导致 ImmortalWrt、iStoreOS 等固件还没来得及与实际安装包矩阵比对就被直接拒绝。新源码已改为以发行包内的 releases.tsv 为准：会识别发行版、完整版本、target、包格式，并选择该组合对应的唯一 SDK 包，再核对实际包架构和 SHA-256。

**这不等于所有衍生固件现已支持。** 截至此版，公开 SDK 通过的包仍只有 README 中列出的三个 OpenWrt 组合。其他固件或版本如果没有经过对应 SDK 构建，仍会被安全拒绝，但报错将显示检测到的系统信息以及当前发行包支持的组合。不要通过把 /etc/openwrt_release 中的 iStoreOS/ImmortalWrt 改成 OpenWrt 来绕过检查；即使 CPU 相同，固件依赖、ABI 与网络接管逻辑也可能不同。

提交适配需求时，可在路由器 SSH 中只读执行：

~~~sh
cat /etc/openwrt_release
uname -m
opkg print-architecture 2>/dev/null || apk --print-arch
~~~

发送输出前检查并遮盖不愿公开的设备信息；不要贴订阅、令牌或节点凭据。开发者需为该固件使用其匹配 SDK 构建并验收，明确记录到兼容矩阵后，才能在发行包中增加对应条目。**仅合并源码修复不会自动让旧版 v0.21.3 的在线安装命令改变**，需重新发布包含新版 install.sh 与对应 releases.tsv 的发行版本。

### v0.15.0 升级提示“固件软件源更新失败”

旧安装器在关闭保护后执行全量 `apk update`，PassWall 等无关第三方源临时不可用也会使升级中断。若日志停在该提示、未出现 `apk add`，新包尚未安装；用 `apk list --installed luci-app-adzero` 和 `/usr/libexec/adzero/ctl status` 核对。

请使用上面的菜单选择「1）一键安装/自动升级」或显式的 `--upgrade`，无需卸载、修改软件源或忽略依赖。若暂不升级，可在确认当前代理可用后执行 `/usr/libexec/adzero/ctl enable` 恢复旧版保护，这会重新加载过滤和当前代理配置。安装阶段真正失败时不要仅凭旧版本号推断所有文件均未变化。

### v0.12.0 提示“没有唯一匹配此固件版本的 SDK 安装包”

v0.12.0 的发布清单由 Windows 生成，CRLF 行尾使旧安装器无法匹配最后一列包格式，三个已支持组合均可能受影响。请使用上面的**中文菜单升级选项**或显式 `--upgrade`；当前清单固定为 LF，安装器兼容 CRLF，固件、target、架构、唯一条目和 SHA-256 检查仍保留。

该错误发生在升级暂停调度、关闭旧版和安装软件包之前，这一次匹配失败没有改动已有配置，无需先卸载。

### 等待操作锁超时

可能有任务仍在运行，也可能保留了异常中断的锁。先读取：

```sh
ls -la /tmp/adzero.lock
cat /tmp/adzero.lock/owner 2>/dev/null
ps w | grep -E '[a]dzero/(ctl|watch|adapter|backend|policy|sources|job)'
```

保留输出供排查，不直接删除锁或恢复记录。安装器不能加速已经运行的旧版任务。

### NaiLink 授权或重载未确认

先只读检查：

```sh
/usr/libexec/adzero/adapter nailink-status
/usr/libexec/adzero/ctl status
```

授权被拒绝、重载超时与配置未恢复是不同情况，处理方法见 [NaiLink 重载确认与恢复](NAILINK_RELOAD_CONFIRMATION.md)。不要通过删除 `adapter.json` 跳过恢复。

### 提示缺少推荐工具

adblock-fast 可能提示缺少 `gawk`、GNU `grep` / `sed`、`coreutils-sort`。这是后端推荐项；不能单凭这条提示判断安装或过滤失败，应结合具体错误和运行状态检查。

## 卸载

使用 APK：

```sh
/usr/libexec/adzero/ctl disable && apk del luci-app-adzero
```

使用 OPKG：

```sh
/usr/libexec/adzero/ctl disable && opkg remove luci-app-adzero
```

先恢复 AdZero 管理的设置，成功后才执行卸载。关闭或卸载不会把个人订阅选择重置成安装默认值。恢复失败时保留记录，先检查错误，不强制卸载。

## 备份与隐私

安装器备份位于 `/etc/adzero-backups/`。`/etc/adzero/adapter.json` 和升级备份中的 `state.tar.gz` 可能包含原有 DNS 或代理配置，保留在路由器本地，不要公开上传。提交问题时提供必要状态和固定前缀错误，隐藏密钥、令牌与订阅地址。
