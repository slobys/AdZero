#!/bin/sh
# Backward-compatible entrypoint. Every supported firmware runs ONE installer.
set -eu
set -f
umask 077
INSTALL_URL=https://github.com/slobys/AdZero/releases/download/v0.23.4/install.sh
INSTALL_SHA256=9f9ee5dff2d021bc79dbf14e4b3e7207c72c8ca200239193c5d87c20a88df82c
fail() { printf 'AdZero: %s\n' "$*" >&2; exit 1; }
if [ "${1:-}" = --detect ]; then
    [ "$#" = 1 ] || fail '--detect 不接受其他选项。'
    ROOT=
    if [ -n "${ADZERO_AUTO_TEST_ROOT:-}" ]; then
        [ "${ADZERO_AUTO_TEST_MODE:-}" = 1 ] || fail '测试根目录只能在显式测试模式使用。'
        ROOT=$ADZERO_AUTO_TEST_ROOT
    fi
    [ -r "$ROOT/etc/openwrt_release" ] || fail '找不到固件信息。'
    awk -F= '$1=="DISTRIB_ID" || $1=="DISTRIB_RELEASE" || $1=="DISTRIB_TARGET" || $1=="DISTRIB_ARCH" {print}' "$ROOT/etc/openwrt_release"
    printf '%s\n' 'AdZero: 三个固件使用同一正式版本；--check 才会检查实际安装条件。'
    exit 0
fi
for option in "$@"; do
    case "$option" in --menu|--check|--install|--upgrade|--no-enable) ;;
        *) fail '用法：install-auto.sh [--menu | --check | --detect | --install | --upgrade] [--no-enable]' ;;
    esac
done
command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 || fail '缺少 HTTPS 下载工具 curl / wget。'
command -v sha256sum >/dev/null 2>&1 || fail '缺少 sha256sum。'
TMP=$(mktemp -d "${TMPDIR:-/tmp}/adzero-entry.XXXXXX") || fail '无法创建临时目录。'
cleanup() { rm -f "$TMP/install.sh"; rmdir "$TMP" 2>/dev/null || :; }
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
if command -v curl >/dev/null 2>&1; then
    curl --fail --location --proto '=https' --proto-redir '=https' --connect-timeout 15 --max-time 180 "$INSTALL_URL" -o "$TMP/install.sh" || fail '正式安装器下载失败；没有修改系统。'
else
    wget -O "$TMP/install.sh" "$INSTALL_URL" || fail '正式安装器下载失败；没有修改系统。'
fi
actual=$(sha256sum "$TMP/install.sh")
[ "${actual%% *}" = "$INSTALL_SHA256" ] || fail '安装器 SHA-256 不匹配；没有执行下载内容。'
sh -n "$TMP/install.sh" || fail '安装器语法检查失败。'
sh "$TMP/install.sh" "$@"
