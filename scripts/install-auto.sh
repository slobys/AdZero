#!/bin/sh
# AdZero unified entrypoint. Read-only exact firmware routing first; download
# only the selected *SHA-256 pinned* installer, then open its existing menu.
# Never install, upgrade, uninstall or enable DNS merely by running this file.
set -eu
set -f
umask 077
LC_ALL=C
export LC_ALL

REGULAR_URL='https://raw.githubusercontent.com/slobys/AdZero/eecd25ff3d7911530fdea1dfded5f3a399aee777/scripts/install.sh'
ARM64_URL='https://github.com/slobys/AdZero/releases/download/v0.21.5-arm64-beta.2/install-arm64-beta.sh'
ISTOREOS_URL='https://github.com/slobys/AdZero/releases/download/v0.21.5-istoreos-beta.3/install-istoreos-beta.sh'
ISTOREOS24108_URL='https://github.com/slobys/AdZero/releases/download/v0.21.5-istoreos-24108-beta.1/install-istoreos-24108-beta.sh'
PIN_REGULAR='85b1a1b80bfc5c7b05bfd220ebd368739053f2b5bcf0cfb0b973014d5565c333'
PIN_ARM64='77f6ff5e6cccaca1895d6d24092ea89e80f8f0a90bc399af4fa8569c093900ed'
PIN_ISTOREOS='120920acb4f7ed2e2e48d3537bea671da927bf09ca4150a7c9afaed00133dfcd'
PIN_ISTOREOS24108='d30c309e650442708dcfda552fa4f62e1530916e93ac2d9341f25db5503a2766'

fail() {
    printf 'AdZero 安装助手：%s\n' "$*" >&2
    exit 1
}
info() { printf 'AdZero 安装助手：%s\n' "$*"; }
usage() {
    printf '%s\n' '用法：sh install-auto.sh [--menu | --detect | --check]'
    printf '%s\n' '默认打开匹配固件的中文安装菜单；--detect 只识别系统、不联网；--check 仅运行原安装器预检。'
}
MODE=menu
if [ "$#" -gt 1 ]; then fail '仅接受一个操作参数，拒绝混合执行。'; fi
if [ "$#" -eq 1 ]; then
    case "$1" in
        --menu) MODE=menu ;;
        --detect) MODE=detect ;;
        --check) MODE=check ;;
        --help|-h) usage; exit 0 ;;
        *) fail '只支持 --menu、--detect、--check、--help；不提供静默自动安装或启用操作。' ;;
    esac
fi

# Used by isolated tests only; never allow accidental live-root redirection.
ROOT=${ADZERO_AUTO_TEST_ROOT:-}
if [ -n "$ROOT" ] && [ "${ADZERO_AUTO_TEST_MODE:-}" != 1 ]; then
    fail '不允许重定向真实安装器的根目录。'
fi
[ -r "$ROOT/etc/openwrt_release" ] && [ ! -L "$ROOT/etc/openwrt_release" ] ||
    fail '未找到有效的 /etc/openwrt_release，不能识别固件。'
release_value() {
    awk -v k="$1" 'index($0,k "=")==1 {
        v=substr($0,length(k)+2)
        if(v ~ /^".*"$/ || v ~ /^\047.*\047$/) v=substr(v,2,length(v)-2)
        print v; exit
    }' "$ROOT/etc/openwrt_release"
}
FW_LABEL=$(release_value DISTRIB_ID)
VERSION=$(release_value DISTRIB_RELEASE)
TARGET=$(release_value DISTRIB_TARGET)
ARCH=$(release_value DISTRIB_ARCH)
REVISION=$(release_value DISTRIB_REVISION)

case "$FW_LABEL" in ''|*[!a-zA-Z0-9._+-]*) fail '固件 DISTRIB_ID 缺失或不合法。' ;; esac
case "$VERSION" in ''|*[!a-zA-Z0-9._+-]*) fail '固件完整版本缺失或不合法。' ;; esac
case "$TARGET" in ''|*[!a-zA-Z0-9._/-]*) fail '固件 target 缺失或不合法。' ;; esac
case "$ARCH" in ''|*[!a-zA-Z0-9._+-]*) fail '固件软件包架构缺失或不合法。' ;; esac
case "$REVISION" in *[!a-zA-Z0-9._+-]*) fail '固件 revision 不合法。' ;; esac
# Explicit ASCII alphabet avoids older BusyBox tr character-class bugs.
FW=$(printf '%s' "$FW_LABEL" | tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz')

CHANNEL=
PKG_FORMAT=
case "$FW:$VERSION:$TARGET:$ARCH" in
    openwrt:24.10.8:x86/64:x86_64|\
    immortalwrt:24.10.6:x86/64:x86_64)
        CHANNEL=regular; PKG_FORMAT=opkg ;;
    openwrt:25.12.5:x86/64:x86_64|\
    openwrt:25.12.5:rockchip/armv8:aarch64_generic|\
    immortalwrt:25.12.2:x86/64:x86_64)
        CHANNEL=regular; PKG_FORMAT=apk ;;
    openwrt:25.12.6:rockchip/armv8:aarch64_generic|\
    openwrt:25.12.6:mediatek/filogic:aarch64_cortex-a53|\
    immortalwrt:25.12.2:rockchip/armv8:aarch64_generic|\
    immortalwrt:25.12.2:mediatek/filogic:aarch64_cortex-a53)
        CHANNEL=arm64; PKG_FORMAT=apk ;;
    istoreos:24.10.8:x86/64:x86_64|\
    istoreos:24.10.8:armsr/armv8:aarch64_generic|\
    istoreos:24.10.8:rockchip/armv8:aarch64_generic)
        [ "$REVISION" = 2026073111 ] ||
            fail "iStoreOS 24.10.8 测试版仅支持修订 2026073111；当前为 ${REVISION:-未知}。"
        CHANNEL=istoreos24108; PKG_FORMAT=opkg ;;
    istoreos:25.12.6:x86/64:x86_64|\
    istoreos:25.12.6:armsr/armv8:aarch64_generic|\
    istoreos:25.12.6:rockchip/armv8:aarch64_generic)
        [ "$REVISION" = 2026102312 ] ||
            fail "iStoreOS Beta 仅支持修订号 2026102312；当前为 ${REVISION:-未知}，拒绝安装。"
        CHANNEL=istoreos; PKG_FORMAT=apk ;;
    *)
        fail "尚未开放此精确组合：$FW_LABEL $VERSION / $TARGET / $ARCH。请查看 https://slobys.github.io/AdZero/compatibility.html。没有修改设备。" ;;
esac

# Catch package-manager confusion before network access; the downstream
# installer performs further version, dependency and ownership checks.
if [ "$PKG_FORMAT" = apk ]; then
    # The active APK database can be relocated with overlayfs, and a previous
    # firmware may have left an inactive OPKG status file behind. Consult the
    # native APK installed inventory rather than assuming a fixed DB pathname.
    command -v apk >/dev/null 2>&1 ||
        fail '当前固件需要 APK，但未找到 apk 命令。'
    ACTUAL=$(apk --print-arch) || fail '无法读取 APK 实际 CPU 架构。'
    case "$ARCH:$ACTUAL" in
        x86_64:x86_64|\
        aarch64_generic:aarch64|aarch64_generic:aarch64_generic|\
        aarch64_cortex-a53:aarch64|aarch64_cortex-a53:aarch64_cortex-a53) ;;
        *) fail "实际 APK 架构 $ACTUAL 与固件声明的 $ARCH 不匹配。" ;;
    esac
    # Read-only proof that this *running* firmware has an APK-managed system.
    apk --root "${ROOT:-/}" --arch "$ARCH" info -e base-files luci-base rpcd >/dev/null 2>&1 ||
        fail 'APK 已安装包查询失败：请检查 apk info -e base-files luci-base rpcd；拒绝跨包管理器安装。'
else
    [ -r "$ROOT/usr/lib/opkg/status" ] &&
        [ ! -e "$ROOT/lib/apk/db/installed" ] &&
        command -v opkg >/dev/null 2>&1 ||
        fail '目标需要 OPKG，当前包管理器或已安装数据库不匹配。'
fi
if { [ "$CHANNEL" = istoreos ] || [ "$CHANNEL" = istoreos24108 ]; } &&
   [ "$TARGET" = rockchip/armv8 ]; then
    BOARD_FILE="$ROOT/tmp/sysinfo/board_name"
    [ -r "$BOARD_FILE" ] && [ ! -L "$BOARD_FILE" ] ||
        fail 'iStoreOS R6XS 需要读取官方设备板型，无法确认，拒绝安装。'
    BOARD=
    IFS= read -r BOARD < "$BOARD_FILE" || :
    case "$BOARD" in
        fastrhino,r6xs|lunzn,fastrhino-r66s|lunzn,fastrhino-r68s|fastrhino,r66s|fastrhino,r68s) ;;
        *) fail '当前 Rockchip 板型不在官方 R6XS 兼容名单中，拒绝试装。' ;;
    esac
fi

case "$CHANNEL" in
    regular)
        DESCRIPTION='OpenWrt / ImmortalWrt 常规安装（v0.21.5 发行渠道）'
        URL=$REGULAR_URL
        EXPECTED_SHA=$PIN_REGULAR ;;
    arm64)
        DESCRIPTION='OpenWrt / ImmortalWrt ARM64 公测（v0.21.5-arm64-beta.2）'
        URL=$ARM64_URL
        EXPECTED_SHA=$PIN_ARM64 ;;
    istoreos)
        DESCRIPTION='iStoreOS 公测（v0.21.5-istoreos-beta.3）'
        URL=$ISTOREOS_URL
        EXPECTED_SHA=$PIN_ISTOREOS ;;
    istoreos24108)
        DESCRIPTION='iStoreOS 24.10.8 OPKG 公测（v0.21.5-istoreos-24108-beta.1）'
        URL=$ISTOREOS24108_URL
        EXPECTED_SHA=$PIN_ISTOREOS24108 ;;
esac
printf '%s\n' '================ AdZero 自动安装入口 ================'
printf '已识别：%s %s\n设备目标：%s\n包架构：%s\n' "$FW_LABEL" "$VERSION" "$TARGET" "$ARCH"
if [ -n "$REVISION" ]; then
    printf '固件修订：%s\n' "$REVISION"
fi
if [ "$FW" = istoreos ] && [ "$TARGET" = rockchip/armv8 ]; then
    printf '已确认板型：%s\n' "$BOARD"
fi
printf '自动选择：%s\n' "$DESCRIPTION"
if [ "$CHANNEL" != regular ]; then
    info '这是独立测试渠道，尚未完成所有真实设备验收；首次建议安装但不启用过滤。'
fi
if [ "$MODE" = detect ]; then
    info '仅完成本地识别；没有联网、下载安装包或修改 DNS/代理。'
    exit 0
fi

command -v curl >/dev/null 2>&1 || fail '下载安装器需要 curl。'
command -v sha256sum >/dev/null 2>&1 || fail '校验安装器需要 sha256sum。'
case "$EXPECTED_SHA" in ''|*[!0-9a-f]*) fail '固定 SHA-256 不合法。' ;; esac
[ "${#EXPECTED_SHA}" -eq 64 ] || fail '固定 SHA-256 长度不正确。'

TMP=$(mktemp -d /tmp/adzero-auto.XXXXXXXX) || fail '无法创建安全临时目录。'
trap 'rm -rf "$TMP"' 0
trap 'exit 130' 1 2 3 15
info '正在安全下载已识别固件的安装菜单，并校验 SHA-256…'
curl --fail --location --proto '=https' --proto-redir '=https' \
    --connect-timeout 15 --max-time 120 \
    --output "$TMP/install-selected.sh" "$URL" ||
    fail 'HTTPS 下载失败；当前设备未安装或修改软件。'
[ -f "$TMP/install-selected.sh" ] && [ ! -L "$TMP/install-selected.sh" ] ||
    fail '下载结果不是普通文件，拒绝执行。'
size=$(wc -c < "$TMP/install-selected.sh" | awk '{print $1}')
case "$size" in ''|*[!0-9]*) fail '安装器大小不合法。' ;; esac
[ "$size" -ge 256 ] && [ "$size" -lt 262144 ] ||
    fail '安装器文件长度异常，拒绝执行。'
SUM=$(sha256sum "$TMP/install-selected.sh") || fail '无法计算安装器 SHA-256。'
[ "${SUM%% *}" = "$EXPECTED_SHA" ] ||
    fail '下载文件 SHA-256 与公开发行记录不一致，已拒绝执行。'
sh -n "$TMP/install-selected.sh" >/dev/null 2>&1 ||
    fail '下载脚本语法校验失败，拒绝执行。'
info '下载完整性已确认。现在进入对应固件的安装菜单；本助手本身不会安装。'
if [ "$MODE" = check ]; then
    sh "$TMP/install-selected.sh" --check
else
    sh "$TMP/install-selected.sh" --menu
fi
