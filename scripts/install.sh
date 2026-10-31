#!/bin/sh
# AdZero release installer. Only exact SDK-verified matrix rows are installable.
set -eu
set -f
umask 077
BASE_URL=${ADZERO_BASE_URL:-https://github.com/slobys/AdZero/releases/download/v0.23.5}
LOCAL_DIR=
ENABLE=1
ENABLE_EXPLICIT=0
UPGRADE=0
MENU=0
CHECK_ONLY=0
CLI_REQUESTED=0
ROOT=${ADZERO_INSTALL_ROOT:-}
fail() { printf 'AdZero: %s\n' "$*" >&2; exit 1; }
while [ "$#" -gt 0 ]; do
	case "$1" in
		--menu) MENU=1; shift ;;
		--install) CLI_REQUESTED=1; shift ;;
		--check) CHECK_ONLY=1; CLI_REQUESTED=1; shift ;;
		--base-url) [ "$#" -ge 2 ] || fail '缺少 URL'; BASE_URL=$2; CLI_REQUESTED=1; shift 2 ;;
		--local-dir) [ "$#" -ge 2 ] || fail '缺少发行目录'; LOCAL_DIR=$2; CLI_REQUESTED=1; shift 2 ;;
		--no-enable) ENABLE=0; ENABLE_EXPLICIT=1; CLI_REQUESTED=1; shift ;;
		--upgrade) UPGRADE=1; CLI_REQUESTED=1; shift ;;
		*) fail '用法: install.sh [--menu | --install | --check] [--upgrade] [--no-enable] [--local-dir DIR | --base-url https://...]' ;;
	esac
done
if [ "$MENU" = 1 ] && { [ "$CHECK_ONLY" = 1 ] || [ "$UPGRADE" = 1 ] || [ "$ENABLE" = 0 ]; }; then
	fail '菜单模式不能同时使用 --check、--upgrade 或 --no-enable；请在菜单内选择相应操作。'
fi
[ "$(id -u)" = 0 ] || fail '请通过路由器 root SSH 执行。'
[ -r "$ROOT/etc/openwrt_release" ] || fail '没有发现 OpenWrt 固件信息。'
release_value() {
	awk -v key="$1" 'index($0,key "=")==1 { v=substr($0,length(key)+2); if (v ~ /^".*"$/ || v ~ /^\047.*\047$/) v=substr(v,2,length(v)-2); print v; exit }' "$ROOT/etc/openwrt_release"
}

# Script-only application packages are shared by the three distributions.
# Compiled dependency supplements remain exact vendor/ABI/revision matches.
apk_native() {
    if [ -n "$DECLARED_ARCH" ]; then
        apk --root "${ROOT:-/}" --arch "$DECLARED_ARCH" "$@"
    else
        apk --root "${ROOT:-/}" "$@"
    fi
}
opkg_native() {
    if [ -n "$ROOT" ]; then opkg --offline-root "$ROOT" "$@"; else opkg "$@"; fi
}
package_installed() {
    if [ "$PM" = apk ]; then
        apk_native info -e "$1" >/dev/null 2>&1
    else
        # OPKG flags include user/hold, independently of the installed state.
        # Require a successful native query and one exact package record;
        # unpacked/config-files, wrong names and ambiguous output are rejected.
        installed_status=$(opkg_native status "$1" 2>/dev/null) || return 1
        printf '%s\n' "$installed_status" | awk -v wanted="$1" '
          {sub(/\r$/, "", $0)}
          /^Package:/ {packages++; exact=(NF==2 && $2==wanted)}
          /^Status:/ {states++; installed=(NF==4 && $4=="installed")}
          END {exit !(packages==1 && exact && states==1 && installed)}
        '
    fi
}
detect_manager() {
    PM=
    # Native APK inventory is authoritative even with relocated/overlay DBs
    # and leftover OPKG files. Query every required base package separately.
    if command -v apk >/dev/null 2>&1; then
        apk_base=1
        for base_package in base-files luci-base rpcd; do
            [ "$(apk_native info -e "$base_package" 2>/dev/null)" = "$base_package" ] || apk_base=0
        done
        if [ "$apk_base" = 1 ]; then PM=apk; return; fi
    fi
    if command -v opkg >/dev/null 2>&1 && command -v apk >/dev/null 2>&1 &&
       [ -r "$ROOT/usr/lib/opkg/status" ] && [ -r "$ROOT/lib/apk/db/installed" ]; then
        fail '同时发现 OPKG 和 APK 数据库，但原生 APK 清单不可确认，拒绝猜测。'
    fi
    if command -v opkg >/dev/null 2>&1 &&
       { [ -r "$ROOT/usr/lib/opkg/status" ] || [ -r "$ROOT/var/lib/opkg/status" ]; }; then
        PM=opkg; return
    fi
    # Fixed-path fallback is retained only for a sole APK manager. The
    # generic release later requires its native inventory to be readable.
    if command -v apk >/dev/null 2>&1 && [ -r "$ROOT/lib/apk/db/installed" ]; then PM=apk; fi
    [ -n "$PM" ] || fail '无法读取当前软件包管理器；需要带 LuCI 的 OpenWrt、ImmortalWrt 或 iStoreOS。'
}
run_package_plan() (
    set +f
    set -- "$TMP/package.$EXT"
    for candidate in "$TMP"/deps/*.$EXT; do [ ! -f "$candidate" ] || set -- "$@" "$candidate"; done
    if [ "$PM" = apk ]; then
        if [ "$PLAN_ONLINE" = 1 ]; then
            apk_native add --simulate --force-reinstall --allow-untrusted "$@"
        else
            apk_native add --simulate --force-reinstall --no-network --repositories-file /dev/null --allow-untrusted "$@"
        fi
    else
        if [ "$PLAN_ONLINE" = 1 ]; then opkg_native --noaction --force-reinstall install "$@";
        else opkg_native --lists-dir "$TMP/empty-lists" --noaction --force-reinstall install "$@"; fi
    fi
)
safe_package_plan() {
    # Do not replace a working DNS stack, libc, tcpdump, proxy or unrelated
    # package to install a LuCI application. Reinstallation/upgrade is allowed
    # only for AdZero itself. Native package managers still check dependencies.
    awk '
      /[Dd]owngrading/ {bad=1}
      /(^|[[:space:]])(Removing|Purging|Upgrading|Downgrading|Reinstalling)[[:space:]]/ {
        if ($0 !~ /(Removing|Purging|Upgrading|Downgrading|Reinstalling) luci-app-adzero([[:space:]]|$)/) bad=1
        if ($0 ~ /(Removing|Purging) luci-app-adzero([[:space:]]|$)/) bad=1
      }
      END {exit bad}
    ' "$TMP/preflight.log" || fail '依赖求解要求更换已有系统软件包，已停止；未关闭现有保护。请先通过固件的软件包管理器处理依赖冲突。'
    if package_installed ucode && grep -Eq 'Installing libucode[0-9]+[[:space:]]' "$TMP/preflight.log"; then
        fail '新增模块需要另一个 ucode 运行库 ABI，不能与现有引擎混用；请先通过本机软件包管理器统一更新 ucode，当前保护未关闭。'
    fi
}
dependency_supplement() {
    # Optional supplements are never borrowed from another revision or ABI.
    fetch dependencies.tsv "$TMP/dependencies.tsv" || return 1
    supplement=$(awk -F '\t' -v fw="$FIRMWARE" -v ver="$VERSION" -v rev="$DECLARED_REVISION" -v target="$TARGET" -v arch="$DECLARED_ARCH" -v fmt="$EXT" '
      {sub(/\r$/, "", $0)}
      NF==9 && $3==fw && $4==ver && $5==rev && $6==target && $7==arch && $8==fmt {print $1 " " $2 " " $9;n++}
      END {if(n!=1)exit 1}' "$TMP/dependencies.tsv") || return 1
    supplement_sum=${supplement%% *}; supplement_rest=${supplement#* }
    supplement_file=${supplement_rest%% *}; supplement_profile=${supplement_rest#* }
    case "$supplement_sum" in ''|*[!0-9a-f]*) fail '依赖补充包摘要无效。' ;; esac
    [ "${#supplement_sum}" = 64 ] || fail '依赖补充包摘要长度无效。'
    case "$supplement_file" in ''|.*|*[!a-zA-Z0-9._+-]*) fail '依赖补充包文件名无效。' ;; esac
    case "$supplement_profile" in ''|.*|*[!a-zA-Z0-9._+-]*) fail '依赖配置名无效。' ;; esac
    fetch "$supplement_file" "$TMP/dependencies.tar.gz"
    actual=$(sha256sum "$TMP/dependencies.tar.gz"); [ "${actual%% *}" = "$supplement_sum" ] || fail '依赖补充包 SHA-256 不匹配。'
    tar -tzf "$TMP/dependencies.tar.gz" > "$TMP/dependency-paths" || fail '依赖补充包不可读取。'
    awk -v profile="$supplement_profile" '
      $0 ~ /^[a-zA-Z0-9][a-zA-Z0-9._+-]*\/packages.tsv$/ && $0 !~ /\.\./ {
        if(seen[$0]++)bad=1;if($0==profile"/packages.tsv")manifest++;next
      }
      $0 ~ /^[a-zA-Z0-9][a-zA-Z0-9._+-]*\/packages\/[a-zA-Z0-9][a-zA-Z0-9._+~-]*\.(ipk|apk)$/ && $0 !~ /\.\./ {
        if(seen[$0]++)bad=1;next
      }
      {bad=1} END{exit bad || manifest!=1}' "$TMP/dependency-paths" || fail '依赖补充包含不允许的路径。'
    tar -tvzf "$TMP/dependencies.tar.gz" | awk 'substr($1,1,1)!="-" {bad=1} END{exit bad}' || fail '依赖补充包只能包含普通文件。'
    mkdir "$TMP/supplement"
    tar -xzf "$TMP/dependencies.tar.gz" -C "$TMP/supplement" || fail '依赖补充包解压失败。'
    count=0
    while IFS="$(printf '\t')" read -r digest filename name; do
        case "$digest" in ''|*[!0-9a-f]*) fail '依赖文件摘要无效。' ;; esac
        [ "${#digest}" = 64 ] || fail '依赖文件摘要长度无效。'
        case "$filename" in ''|.*|*[!a-zA-Z0-9._+~-]*) fail '依赖文件名无效。' ;; esac
        case "$name" in adblock-fast|conntrack|coreutils-timeout|libnetfilter-cthelper0|libnetfilter-cttimeout1|libnetfilter-queue1|ucode-mod-socket) ;;
            *) fail '依赖清单含不允许的软件包。' ;; esac
        actual=$(sha256sum "$TMP/supplement/$supplement_profile/packages/$filename")
        [ "${actual%% *}" = "$digest" ] || fail '依赖文件 SHA-256 不匹配。'
        [ ! -e "$TMP/seen-$name" ] || fail '依赖清单重复。'
        : > "$TMP/seen-$name"
        count=$((count + 1)); [ "$count" -le 7 ] || fail '依赖清单过大。'
        # Existing native packages are left alone, including full tcpdump.
        if ! package_installed "$name"; then cp "$TMP/supplement/$supplement_profile/packages/$filename" "$TMP/deps/$filename"; fi
    done < "$TMP/supplement/$supplement_profile/packages.tsv"
    required_count=6; [ "$EXT" != ipk ] || required_count=7
    [ "$count" = "$required_count" ] || fail '依赖清单不完整。'
    printf '%s\n' 'AdZero: 已自动准备匹配本机固件的原厂依赖。'
}
prepare_universal_package() {
    mkdir -p "$TMP/deps" "$TMP/empty-lists"
    PLAN_ONLINE=0
    if run_package_plan > "$TMP/preflight.log" 2>&1; then safe_package_plan; APK_LOCAL=1; return; fi
    # Only iStoreOS needs our supplemental packages when its feeds lack them.
    if [ "$FIRMWARE" = istoreos ]; then
        if dependency_supplement; then
            if run_package_plan > "$TMP/preflight.log" 2>&1; then safe_package_plan; APK_LOCAL=1; return; fi
        fi
    fi
    PLAN_ONLINE=1
    if ! run_package_plan > "$TMP/preflight.log" 2>&1; then
        [ "$CHECK_ONLY" != 1 ] || { cat "$TMP/preflight.log" >&2; fail '本机原生软件源尚不能满足依赖；只读检查未刷新软件源，也未修改保护。'; }
        printf '%s\n' 'AdZero: 正在用本机原有软件源准备依赖；不添加其他固件的软件源。'
        if [ "$PM" = apk ]; then apk_native update > "$TMP/repository.log" 2>&1 || :
        else opkg_native update > "$TMP/repository.log" 2>&1 || :; fi
        run_package_plan > "$TMP/preflight.log" 2>&1 || { cat "$TMP/preflight.log" >&2; fail '本机软件源不能满足依赖，未关闭现有保护。'; }
    fi
    safe_package_plan
    if [ "$PM" = apk ]; then
        awk '/^\([0-9]+\/[0-9]+\) Installing / {name=$3;version=$4;gsub(/[()]/,"",version);if(name!="luci-app-adzero")print name "=" version}' "$TMP/preflight.log" > "$TMP/fetch-packages"
        while IFS= read -r spec; do
            case "$spec" in ''|*[!a-zA-Z0-9._+=~:-]*) fail '原生 APK 下载计划不可安全解析。' ;; esac
            apk_native fetch --output "$TMP/deps" "$spec" || fail '依赖下载失败；原保护未关闭。'
        done < "$TMP/fetch-packages"
        PLAN_ONLINE=0
        run_package_plan > "$TMP/preflight.log" 2>&1 || fail '依赖未能完成离线复核；原保护未关闭。'
        safe_package_plan; APK_LOCAL=1
    else
        # opkg downloads the resolved closure without installing or running hooks.
        (cd "$TMP/deps" && opkg_native --download-only --force-reinstall --cache "$TMP/deps" install "$TMP/package.ipk") || fail '依赖下载失败；原保护未关闭。'
        run_package_plan > "$TMP/preflight.log" 2>&1 || fail 'OPKG 依赖预检未完成；原保护未关闭。'
        safe_package_plan
    fi
}
install_universal_package() (
    set +f
    set -- "$TMP/package.$EXT"
    for candidate in "$TMP"/deps/*.$EXT; do [ ! -f "$candidate" ] || set -- "$@" "$candidate"; done
    if [ "$PM" = apk ]; then
        apk_native add --force-reinstall --no-network --repositories-file /dev/null --allow-untrusted "$@"
    else opkg_native --cache "$TMP/deps" --force-reinstall install "$@"; fi
)

# The menu is a front end for this same SDK-verified installer. Never eval a
# release name or execute an unverified downloaded shell script.
menu_release_name() {
	if [ -n "$LOCAL_DIR" ]; then
		printf '%s\n' '本地发行目录'
		return 0
	fi
	case "${BASE_URL##*/}" in
		v[0-9]*) printf '%s\n' "${BASE_URL##*/}" ;;
		*) printf '%s\n' '自定义发行源' ;;
	esac
}
menu_install_state() {
	# 0: installed; 1: absent; 2: partially installed (fail closed).
	config="$ROOT/etc/config/adzero"
	controller="$ROOT/usr/libexec/adzero/ctl"
	[ ! -L "$config" ] && [ ! -L "$controller" ] || return 2
	if [ -f "$config" ] && [ -x "$controller" ]; then return 0; fi
	if [ -e "$config" ] || [ -e "$controller" ]; then return 2; fi
	return 1
}
menu_action() {
	# Keep the original validated install/upgrade code as the sole mutation path.
	# The subprocess handles its own locks, traps, package SHA-256 and rollback.
	if [ -n "$LOCAL_DIR" ]; then
		sh "$0" --local-dir "$LOCAL_DIR" "$@"
	else
		sh "$0" --base-url "$BASE_URL" "$@"
	fi
}
menu_confirm() {
	printf '%s [y/N]: ' "$1"
	IFS= read -r answer || return 1
	case "$answer" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}
menu_install() {
	noenable=$1
	if menu_install_state; then
		mode=--upgrade
	else
		case "$?" in
			1) mode=--install ;;
			*)
				printf '%s\n' 'AdZero: 发现不完整的旧安装，不能安全判断安装/升级。请先修复原有配置和控制程序。'
				return 0 ;;
		esac
	fi
	if [ "$mode" = --upgrade ]; then description=升级; else description=安装; fi
	if [ "$noenable" = 1 ]; then description="$description（完成后不自动开启）"; fi
	if ! menu_confirm "确认$description AdZero（目标来源：$(menu_release_name)）？"; then
		printf '%s\n' 'AdZero: 已取消，未修改系统。'
		return 0
	fi
	printf 'AdZero: 正在%s；将自动选择 IPK 或 APK，并在停止旧保护前检查依赖。\n' "$description"
	if [ "$noenable" = 1 ]; then
		if menu_action "$mode" --no-enable; then
			printf '%s\n' 'AdZero: 操作已完成。'
		else
			printf '%s\n' 'AdZero: 操作失败，请核对上述错误；不要删除恢复记录。'
		fi
	else
		if menu_action "$mode"; then
			printf '%s\n' 'AdZero: 操作已完成。'
		else
			printf '%s\n' 'AdZero: 操作失败，请核对上述错误；不要删除恢复记录。'
		fi
	fi
}
menu_uninstall() (
	if menu_install_state; then
		:
	else
		case "$?" in
			1) printf '%s\n' 'AdZero: 未检测到完整安装。' ;;
			*) printf '%s\n' 'AdZero: 安装状态不完整，拒绝自动卸载，防止遗留 DNS/代理设置。' ;;
		esac
		return 0
	fi
	# Resolve the package manager before touching protection status.
	DECLARED_ARCH=$(release_value DISTRIB_ARCH)
	if ! pm=$(detect_manager; printf '%s' "$PM"); then
		printf '%s\n' 'AdZero: 无法确认包管理器（可能有两个包管理器数据库），未修改当前保护。'
		return 0
	fi
	PM=$pm
	if ! package_installed luci-app-adzero; then
		printf '%s\n' 'AdZero: 原生包管理器未确认插件归属，拒绝卸载；未修改当前保护。'
		return 0
	fi
	printf '%s\n' 'AdZero: 卸载前会先恢复它管理的设置；如果恢复失败，将停止卸载。'
	printf '%s\n' 'AdZero: 保留已安装的依赖，只移除 AdZero 插件。'
	printf '%s' '确认卸载请输入 YES: '
	IFS= read -r confirmation || return 0
	[ "$confirmation" = YES ] || {
		printf '%s\n' 'AdZero: 已取消卸载。'
		return 0
	}
	# Share the installer's existing lock. Do not compete with upgrades or
	# disable while a protection mutation holds the controller lock.
	if [ -e "$ROOT/tmp/adzero.lock" ] || [ -L "$ROOT/tmp/adzero.lock" ]; then
		printf '%s\n' 'AdZero: 当前有保护任务锁，拒绝卸载；请先检查任务和锁所有者。'
		return 0
	fi
	mkdir -p "$ROOT/tmp" || {
		printf '%s\n' 'AdZero: 无法创建锁目录，拒绝卸载。'
		return 0
	}
	if ! mkdir "$ROOT/tmp/adzero-install.lock" 2>/dev/null; then
		printf '%s\n' 'AdZero: 另一个安装或卸载操作正在进行，拒绝并行卸载。'
		return 0
	fi
	remove_tmp=$(mktemp -d "${TMPDIR:-/tmp}/adzero-remove.XXXXXX") || {
		rmdir "$ROOT/tmp/adzero-install.lock" 2>/dev/null || :
		printf '%s\n' 'AdZero: 无法创建卸载预检目录，未关闭保护。'; return 0
	}
	trap 'rm -f "$remove_tmp/plan" "$remove_tmp/keep" "$remove_tmp/preserve-plan"; rmdir "$remove_tmp" 2>/dev/null || :; rmdir "$ROOT/tmp/adzero-install.lock" 2>/dev/null || :' 0
	trap 'exit 130' 1 2 3 15
	if [ "$pm" = apk ]; then
		if ! apk_native del --simulate --no-network --repositories-file /dev/null luci-app-adzero > "$remove_tmp/plan" 2>&1; then
			printf '%s\n' 'AdZero: 原生卸载预检失败，尚未关闭保护。'; return 0
		fi
		# APK garbage-collects automatic dependencies when deleting a world
		# package. Mark existing dependencies as manually retained without
		# changing packages, running hooks or pinning future version updates.
		awk '/^\([0-9]+\/[0-9]+\) Purging / && $3!="luci-app-adzero" {v=$4;gsub(/[()]/,"",v);print $3 "=" v}' "$remove_tmp/plan" > "$remove_tmp/keep"
		if [ -s "$remove_tmp/keep" ]; then
			set --
			while IFS= read -r keep_spec; do
				case "$keep_spec" in ''|*[!a-zA-Z0-9._+=~:-]*) printf '%s\n' 'AdZero: 卸载依赖计划格式无效，未关闭保护。'; return 0 ;; esac
				keep_name=${keep_spec%%=*}
				case "$keep_name" in [a-zA-Z0-9]*) ;; *) printf '%s\n' 'AdZero: 依赖名称无效，未关闭保护。'; return 0 ;; esac
				set -- "$@" "$keep_name"
			done < "$remove_tmp/keep"
			if ! apk_native add --simulate --no-scripts --no-network --repositories-file /dev/null "$@" > "$remove_tmp/preserve-plan" 2>&1 ||
			   grep -Eq '(Installing|Upgrading|Downgrading|Reinstalling|Purging|Removing)[[:space:]]' "$remove_tmp/preserve-plan"; then
				printf '%s\n' 'AdZero: 保留依赖会触发包变更，已停止；尚未关闭保护。'; return 0
			fi
			apk_native add --no-scripts --no-network --repositories-file /dev/null "$@" > "$remove_tmp/preserve-plan" 2>&1 || {
				printf '%s\n' 'AdZero: 未能保留已安装依赖，尚未关闭保护。'; return 0;
			}
			apk_native del --simulate --no-network --repositories-file /dev/null luci-app-adzero > "$remove_tmp/plan" 2>&1 || return 0
		fi
		if ! awk '
		  /^\([0-9]+\/[0-9]+\) Purging / {if($3!="luci-app-adzero")bad=1;else own++}
		  /(Installing|Upgrading|Downgrading|Reinstalling|Removing)[[:space:]]/ {bad=1}
		  END{exit bad || own!=1}' "$remove_tmp/plan"; then
			printf '%s\n' 'AdZero: 卸载仍会更改其他组件，已停止；尚未关闭保护。'; return 0
		fi
	else
		if ! opkg_native --noaction remove luci-app-adzero > "$remove_tmp/plan" 2>&1; then
			printf '%s\n' 'AdZero: OPKG 卸载依赖检查失败，尚未关闭保护。'; return 0
		fi
		if ! awk '/^Removing package /{if($3!="luci-app-adzero")bad=1;else own++}
		  /(Installing|Upgrading|Downgrading|Reinstalling)[[:space:]]/{bad=1}
		  END{exit bad || own!=1}' "$remove_tmp/plan"; then
			printf '%s\n' 'AdZero: OPKG 卸载会更改其他组件，尚未关闭保护。'; return 0
		fi
	fi
	if ! "$ROOT/usr/libexec/adzero/ctl" disable; then
		printf '%s\n' 'AdZero: 关闭及恢复失败，已停止卸载。请保留恢复记录。'
		return 0
	fi
	if [ -e "$ROOT/etc/adzero/managed" ] || [ -e "$ROOT/etc/adzero/adapter.json" ]; then
		printf '%s\n' 'AdZero: 恢复记录仍存在，已停止卸载；没有删除记录或软件包。'
		return 0
	fi
	if [ "$pm" = opkg ]; then
		if opkg_native remove luci-app-adzero; then
			printf '%s\n' 'AdZero: 卸载完成。'
		else
			printf '%s\n' 'AdZero: OPKG 卸载失败；保护已关闭，请核对包状态。'
		fi
	else
		if apk_native del --no-network --repositories-file /dev/null luci-app-adzero; then
			printf '%s\n' 'AdZero: 卸载完成。'
		else
			printf '%s\n' 'AdZero: APK 卸载失败；保护已关闭，请核对包状态。'
		fi
	fi
)
show_menu() {
	while :; do
		printf '\n%s\n' '================ AdZero 安装管理 ================'
		printf '固件：%s  版本：%s\n' "$(release_value DISTRIB_ID)" "$(release_value DISTRIB_RELEASE)"
		printf '目标：%s  包架构：%s\n' "$(release_value DISTRIB_TARGET)" "$(release_value DISTRIB_ARCH)"
		printf '发行来源：%s（自动选择安装包，不用输入版本）\n' "$(menu_release_name)"
		if menu_install_state; then
			printf '%s\n' '插件状态：已安装'
		else
			case "$?" in
				1) printf '%s\n' '插件状态：未安装' ;;
				*) printf '%s\n' '插件状态：不完整，请先检查' ;;
			esac
		fi
		printf '%s\n' '------------------------------------------------'
		printf '%s\n' '  1) 一键安装 / 自动升级（首次不开启，升级保留开关）'
		printf '%s\n' '  2) 安装 / 升级（不自动开启保护）'
		printf '%s\n' '  3) 只读环境与安装包兼容性检查'
		printf '%s\n' '  4) 安全卸载（先恢复 AdZero 管理的配置）'
		printf '%s\n' '  0) 退出'
		printf '%s' '请选择 [0-4]: '
		if ! IFS= read -r selection; then
			printf '\n%s\n' 'AdZero: 输入结束，已退出。'
			return 0
		fi
		case "$selection" in
			1) menu_install 0 ;;
			2) menu_install 1 ;;
			3)
				printf '%s\n' 'AdZero: 下载、核对发行包和依赖；不安装、不停止现有服务、不修改软件源。'
				if menu_action --check; then
					printf '%s\n' 'AdZero: 校验完成。'
				else
					printf '%s\n' 'AdZero: 校验未通过，请查看上方固件、架构或下载错误。'
				fi ;;
			4) menu_uninstall ;;
			0) printf '%s\n' 'AdZero: 已退出。'; return 0 ;;
			*) printf '%s\n' 'AdZero: 请输入 0 至 4。' ;;
		esac
	done
}
# No arguments always means the safe menu, even in a non-TTY shell. Headless
# callers must request --install / --upgrade explicitly; never auto-install on EOF.
if [ "$MENU" = 1 ] || [ "$CLI_REQUESTED" = 0 ]; then
	show_menu
	exit 0
fi
FIRMWARE=$(release_value DISTRIB_ID)
FIRMWARE_LABEL=$(release_value DISTRIB_ID)
VERSION=$(release_value DISTRIB_RELEASE)
TARGET=$(release_value DISTRIB_TARGET)
DECLARED_ARCH=$(release_value DISTRIB_ARCH)
DECLARED_REVISION=$(release_value DISTRIB_REVISION)
# The release catalog, not a hard-coded distro name, is authoritative.
# Normalizing a distro ID does NOT imply another firmware SDK is compatible.
case "$FIRMWARE_LABEL" in ''|*[!a-zA-Z0-9._+-]*) fail '固件 DISTRIB_ID 缺失或无效，请检查 /etc/openwrt_release。' ;; esac
case "$VERSION" in ''|*[!a-zA-Z0-9._+-]*) fail '固件 DISTRIB_RELEASE 缺失或无效，请检查 /etc/openwrt_release。' ;; esac
case "$TARGET" in
	'') fail '固件信息中缺少 DISTRIB_TARGET，无法选择安装包。请提供 /etc/openwrt_release、uname -m 和软件包架构输出。尚未安装或修改网络配置。' ;;
	*[!a-zA-Z0-9._/-]*) fail '固件 DISTRIB_TARGET 包含无效字符，请检查 /etc/openwrt_release。' ;;
esac
case "$DECLARED_ARCH" in *[!a-zA-Z0-9._+-]*) fail '固件 DISTRIB_ARCH 包含无效字符，请检查 /etc/openwrt_release。' ;; esac
case "$DECLARED_REVISION" in *[!a-zA-Z0-9._+-]*) fail '固件 DISTRIB_REVISION 包含无效字符，请检查 /etc/openwrt_release。' ;; esac
# OpenWrt BusyBox tr 1.36 may mis-handle paired character classes: OpenWrt -> OwenWrt.
# Use explicit ASCII alphabets after the UCI ID character whitelist.
FIRMWARE=$(printf '%s' "$FIRMWARE_LABEL" | tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz')
detect_manager
if [ "$PM" = opkg ]; then EXT=ipk; else EXT=apk; fi
command -v sha256sum >/dev/null 2>&1 || fail '缺少 sha256sum。'
TMP=$(mktemp -d "${TMPDIR:-/tmp}/adzero-install.XXXXXX") || fail '无法创建临时目录。'
WATCH="$ROOT/usr/libexec/adzero/watch"
INSTALL_LOCK="$ROOT/tmp/adzero-install.lock"
INSTALL_LOCK_OWNED=0
# Strip the whole comm field, which may contain spaces or parentheses.
# Linux start ticks distinguish a surviving process from a reused PID.
process_snapshot() (
	IFS= read -r proc_stat 2>/dev/null < "/proc/$1/stat" || exit 1
	proc_fields=${proc_stat##*) }
	set -- $proc_fields
	[ "$#" -ge 20 ] || exit 1
	proc_state=$1
	shift 19
	case "$1" in ''|*[!0-9]*) exit 1 ;; esac
	printf '%s %s\n' "$1" "$proc_state"
)
watch_matches() (
	# Only the exact scheduler command and our UID may receive a signal.
	proc_args=$(tr '\000' '\n' 2>/dev/null < "/proc/$1/cmdline") || exit 1
	case "$proc_args" in
		"/bin/sh
$WATCH"|"/bin/ash
$WATCH"|"/bin/busybox
sh
$WATCH"|"/bin/busybox
ash
$WATCH") ;;
		*) exit 1 ;;
	esac
	self_uid=$(awk '/^Uid:/ { print $2, $3, $4, $5 }' /proc/self/status)
	proc_uid=$(awk '/^Uid:/ { print $2, $3, $4, $5 }' "/proc/$1/status" 2>/dev/null) || exit 1
	[ -n "$self_uid" ] && [ "$self_uid" = "$proc_uid" ]
)
resume_watchers() {
	[ -f "$TMP/watchers" ] || return 0
	while read -r watch_pid watch_start; do
		current=$(process_snapshot "$watch_pid") || continue
		[ "${current%% *}" = "$watch_start" ] && watch_matches "$watch_pid" || continue
		kill -CONT "$watch_pid" 2>/dev/null || :
	done < "$TMP/watchers"
}
cleanup() {
	resume_watchers
	[ "$INSTALL_LOCK_OWNED" != 1 ] || rmdir "$INSTALL_LOCK" 2>/dev/null || :
	rm -rf "$TMP/deps" "$TMP/supplement" "$TMP/empty-lists"
	rm -f "$TMP/dependencies.tsv" "$TMP/dependencies.tar.gz" "$TMP/dependency-paths" "$TMP/fetch-packages" "$TMP/repository.log"
	rm -f "$TMP/seen-adblock-fast" "$TMP/seen-conntrack" "$TMP/seen-coreutils-timeout" "$TMP/seen-libnetfilter-cthelper0" "$TMP/seen-libnetfilter-cttimeout1" "$TMP/seen-libnetfilter-queue1" "$TMP/seen-ucode-mod-socket"
	rm -f "$TMP/releases.tsv" "$TMP/package.$EXT" "$TMP/disable.log" "$TMP/watchers" "$TMP/preflight.log"
	rmdir "$TMP" 2>/dev/null || :
}
trap 'cleanup' 0
trap 'exit 130' 1 2 3 15
watch_candidates() (
	set +f
	grep -lF "$WATCH" /proc/[0-9]*/cmdline 2>/dev/null || :
)
pause_watchers() {
	[ -r /proc/self/stat ] || fail '无法核对后台调度进程，已停止升级。'
	: > "$TMP/watchers"
	# Output only numeric /proc paths, never command-line content.
	for proc_file in $(watch_candidates); do
		watch_pid=${proc_file#/proc/}; watch_pid=${watch_pid%/cmdline}
		case "$watch_pid" in ''|*[!0-9]*) continue ;; esac
		before=$(process_snapshot "$watch_pid") || continue
		watch_matches "$watch_pid" || continue
		current=$(process_snapshot "$watch_pid") || continue
		[ "${current%% *}" = "${before%% *}" ] || fail '后台进程已变化，请重试升级。'
		case "${current#* }" in T|t) continue ;; esac
		printf '%s %s\n' "$watch_pid" "${before%% *}" >> "$TMP/watchers"
		kill -STOP "$watch_pid" 2>/dev/null || fail '无法暂停后台调度，已停止升级。'
		after=$(process_snapshot "$watch_pid") || fail '后台进程已退出，请重试升级。'
		[ "${after%% *}" = "${before%% *}" ] && watch_matches "$watch_pid" || fail '后台进程身份已变化，已停止升级。'
		# SIGSTOP is asynchronous. Its children continue their current work.
		stop_checks=0
		while [ "${after#* }" != T ] && [ "${after#* }" != t ]; do
			[ "$stop_checks" -lt 3 ] || fail '后台调度未暂停，已停止升级。'
			sleep 1
			after=$(process_snapshot "$watch_pid") || fail '后台进程已退出，请重试升级。'
			[ "${after%% *}" = "${before%% *}" ] || fail '后台进程身份已变化，已停止升级。'
			stop_checks=$((stop_checks + 1))
		done
	done
	[ ! -s "$TMP/watchers" ] || printf 'AdZero: 已暂停后台调度，正在让旧版任务完成；退出安装器会恢复调度。\n'
}
upgrade_tick() {
	[ "$upgrade_waits" -lt "$upgrade_limit" ] || fail '旧版操作锁持续占用，已停止升级并恢复后台调度；没有删除锁或安装新包。请提供锁和进程信息。'
	[ "$((upgrade_waits % 15))" != 0 ] || printf 'AdZero: 等待旧版任务完成（已等 %s 秒，最多 %s 秒）；不会终止任务或删除锁。\n' "$upgrade_waits" "$upgrade_limit"
	sleep 1
	upgrade_waits=$((upgrade_waits + 1))
}
wait_upgrade_lock() {
	while [ -e "$ROOT/tmp/adzero.lock" ] || [ -L "$ROOT/tmp/adzero.lock" ]; do upgrade_tick; done
}
backup_state() {
	tar -czf "$BACKUP/config.tar.gz" -C "$ROOT/etc" config || fail '配置备份失败。'
	if [ -d "$ROOT/etc/adzero" ]; then
		tar -czf "$BACKUP/state.tar.gz" -C "$ROOT/etc" adzero || fail '恢复记录备份失败。'
	fi
}
fetch() {
	if [ -n "$LOCAL_DIR" ]; then
		cp "$LOCAL_DIR/$1" "$2" || fail '本地发行文件缺失。'
	else
		case "$BASE_URL" in https://*) ;; *) fail '请指定发行目录或 HTTPS 下载地址。' ;; esac
		if command -v curl >/dev/null 2>&1; then
			curl --fail --location --proto '=https' --proto-redir '=https' --connect-timeout 15 --max-time 180 --output "$2" "${BASE_URL%/}/$1" || fail '下载失败；没有更改 DNS 配置。'
		elif command -v wget >/dev/null 2>&1; then
			wget -O "$2" "${BASE_URL%/}/$1" || fail 'HTTPS 下载失败；没有更改 DNS 配置。'
		else fail '缺少 curl 和 wget；也可下载发行文件后使用 --local-dir。'; fi
	fi
}
APK_LOCAL=0
prepare_package() {
	# Resolve against installed packages only. A local APK with satisfied
	# dependencies must not depend on unrelated repository availability.
	if [ "$PM" = apk ]; then
		if apk add --simulate --no-network --repositories-file /dev/null --allow-untrusted "$TMP/package.apk" > "$TMP/preflight.log" 2>&1; then
			APK_LOCAL=1
			printf 'AdZero: 本地安装包依赖检查通过，无需刷新软件源。\n'
		else
			printf 'AdZero: 需要联网准备依赖，先检查固件软件源；尚未关闭旧版保护。\n'
			apk update || fail '固件软件源更新失败，尚未关闭旧版保护。请恢复软件源后重试。'
			apk add --simulate --allow-untrusted "$TMP/package.apk" || fail '安装包或依赖预检失败，尚未关闭旧版保护。'
		fi
	else
		opkg update || fail '固件软件源更新失败，尚未关闭旧版保护。请恢复软件源后重试。'
	fi
}
fetch releases.tsv "$TMP/releases.tsv"
UNIVERSAL=0
case "$FIRMWARE" in openwrt|immortalwrt|istoreos)
    if awk -F '\t' -v ext="$EXT" '{sub(/\r$/,"",$0)} $3=="openwrt-family" && $4=="any" && $5=="any" && $7==ext {found=1} END{exit !found}' "$TMP/releases.tsv"; then UNIVERSAL=1; fi ;;
esac
if [ "$UNIVERSAL" = 1 ]; then
    ROW=$(awk -F '\t' -v ext="$EXT" '
      {sub(/\r$/,"",$0)} NF==7 && $3=="openwrt-family" && $4=="any" && $5=="any" &&
      $7==ext && (($7=="ipk" && $6=="all") || ($7=="apk" && $6=="noarch")) {print $1 " " $2 " " $6;n++}
      END{if(n!=1)exit 1}' "$TMP/releases.tsv") || fail '通用发行清单缺少唯一的 IPK/APK 安装包。'
    for base_package in base-files luci-base rpcd; do
        package_installed "$base_package" || fail "本机原生软件包清单不完整：未确认 $base_package 已完整安装（$PM）。请检查该包的安装状态或包管理器错误。"
    done
    if package_installed luci-app-adzero; then UPGRADE=1; fi
    # An upgrade preserves the previous switch; a fresh installation adds
    # the UI without taking over a working router's DNS automatically.
    if [ "$ENABLE_EXPLICIT" = 0 ]; then
        ENABLE=0
        if [ -f "$ROOT/etc/adzero/managed" ] && command -v uci >/dev/null 2>&1 &&
           [ "$(uci -q -c "$ROOT/etc/config" get adzero.main.enabled 2>/dev/null)" = 1 ]; then ENABLE=1; fi
    fi
else
# A Windows-authored catalog may use CRLF. Remove only the line terminator.
# Match the exact distributor, release, target and package format. Architecture
# comes from the verified catalog row and must match the target package manager.
ROW=$(awk -F '\t' -v fw="$FIRMWARE" -v ver="$VERSION" -v target="$TARGET" -v ext="$EXT" -v rev="$DECLARED_REVISION" '
  { sub(/\r$/, "", $0) }
  (NF==7 || NF==8) && $3==fw && $4==ver && $5==target && $7==ext &&
  (($3!="istoreos" && NF==7) || ($3=="istoreos" && NF==8 && rev!="" && $8==rev)) {
    print $1 " " $2 " " $6; n++
  }
  END { if(n!=1) exit 1 }
' "$TMP/releases.tsv") || {
	# Only the exact iStoreOS revision can select an eight-column catalog row.
	# All existing 7-column OpenWrt/ImmortalWrt rows remain unchanged.
	AVAILABLE=$(awk -F '\t' '
      { sub(/\r$/, "", $0) }
      (NF==7 || NF==8) && $3 ~ /^[a-z0-9._+-]+$/ &&
      $4 ~ /^[a-zA-Z0-9._+-]+$/ && $5 ~ /^[a-zA-Z0-9._\/-]+$/ &&
      $6 ~ /^[a-zA-Z0-9._+-]+$/ && ($7=="ipk" || $7=="apk") {
        if ($3=="istoreos") {
          if (NF!=8 || $8 !~ /^[0-9]+$/ || length($8)!=10) next
        } else if (NF!=7) next
        if (++n<=8) {
          printf "%s%s %s target=%s arch=%s (%s)", n==1?"":"; ",$3,$4,$5,$6,$7
          if (NF==8) printf " revision=%s",$8
        }
      }
      END {if(n>8) printf "; ..."}
    ' "$TMP/releases.tsv")
	fail "未找到唯一匹配的已发布 SDK 安装包：检测到 $FIRMWARE_LABEL $VERSION，revision=${DECLARED_REVISION:-未提供}，target=$TARGET，格式=$EXT。当前发行包支持：${AVAILABLE:-无有效条目}。请勿跨固件强制安装；提供 /etc/openwrt_release 和 opkg print-architecture / apk --print-arch 以便扩展兼容矩阵。尚未停止保护或修改网络配置。"
}
fi
[ "$CHECK_ONLY" = 1 ] || [ "$UPGRADE" = 1 ] || [ ! -e "$ROOT/etc/adzero/adapter.json" ] || fail '请使用 --upgrade 自动准备升级，或先在现有版本中关闭保护；保留原有恢复记录。'
SUM=${ROW%% *}
REST=${ROW#* }
FILE=${REST%% *}
ARCH=${REST#* }
case "$ARCH" in ''|*[!a-zA-Z0-9._+-]*) fail '发行清单中的架构字段无效。' ;; esac
if [ "$UNIVERSAL" != 1 ]; then
[ -z "$DECLARED_ARCH" ] || [ "$DECLARED_ARCH" = "$ARCH" ] || fail "固件声明架构 $DECLARED_ARCH 与所选 SDK 包架构 $ARCH 不一致。"
if [ "$PM" = opkg ]; then
	opkg print-architecture | awk -v arch="$ARCH" '$2==arch {found=1} END {exit !found}' || fail "OPKG 可安装架构中没有 SDK 包要求的 $ARCH，已拒绝安装。"
else
	ACTUAL_APK_ARCH=$(apk --print-arch)
	# Only explicitly enumerated firmware, *exact version*, target and package
	# ABI combinations may use the APK CPU alias aarch64. The catalog must
	# already contain a verified exact row; this is not cross-vendor approval.
	if [ "$ACTUAL_APK_ARCH" != "$ARCH" ]; then
		case "$FIRMWARE:$VERSION:$TARGET:$ARCH:$ACTUAL_APK_ARCH" in
			openwrt:25.12.5:rockchip/armv8:aarch64_generic:aarch64|\
			openwrt:25.12.6:rockchip/armv8:aarch64_generic:aarch64|\
			openwrt:25.12.6:mediatek/filogic:aarch64_cortex-a53:aarch64|\
			immortalwrt:25.12.2:rockchip/armv8:aarch64_generic:aarch64|\
			immortalwrt:25.12.2:mediatek/filogic:aarch64_cortex-a53:aarch64|\
			istoreos:25.12.6:rockchip/armv8:aarch64_generic:aarch64|\
			istoreos:25.12.6:armsr/armv8:aarch64_generic:aarch64) ;;
			*) fail "APK 架构 $ACTUAL_APK_ARCH 与已发布的 $FIRMWARE $VERSION $TARGET SDK 包架构 $ARCH 不匹配。" ;;
		esac
	fi
fi
fi
[ "${#SUM}" = 64 ] || fail '校验清单格式错误。'
case "$SUM" in *[!0-9a-f]*) fail '校验清单格式错误。' ;; esac
case "$FILE" in ''|*[!a-zA-Z0-9._+-]*|.*) fail '不安全的安装包文件名。' ;; esac
fetch "$FILE" "$TMP/package.$EXT"
ACTUAL=$(sha256sum "$TMP/package.$EXT")
[ "${ACTUAL%% *}" = "$SUM" ] || fail 'SHA-256 不匹配，已停止安装。'
if [ "$UNIVERSAL" = 1 ] && [ "$CHECK_ONLY" = 1 ]; then prepare_universal_package; fi
if [ "$CHECK_ONLY" = 1 ]; then
	printf 'AdZero: 环境检测通过：%s %s，target=%s，SDK 架构=%s，包格式=%s。\n' "$FIRMWARE" "$VERSION" "$TARGET" "$ARCH" "$EXT"
	printf 'AdZero: 已选择 %s 并通过 SHA-256 校验；未安装软件包、未操作代理或 DNS。\n' "$FILE"
	exit 0
fi
mkdir -p "$ROOT/tmp"
mkdir "$INSTALL_LOCK" 2>/dev/null || fail '另一项安装正在进行，或安装器锁仍待检查；没有停止现有保护。'
INSTALL_LOCK_OWNED=1
CTL="$ROOT/usr/libexec/adzero/ctl"
upgrade_limit=${ADZERO_UPGRADE_WAIT_SECONDS:-180}
case "$upgrade_limit" in ''|*[!0-9]*) fail '升级等待时限无效。' ;; esac
[ "$upgrade_limit" -ge 1 ] && [ "$upgrade_limit" -le 300 ] || fail '升级等待时限应为 1 至 300 秒。'
upgrade_waits=0
if [ "$UPGRADE" = 1 ] && [ -x "$CTL" ]; then
	pause_watchers
	wait_upgrade_lock
fi
# Repository errors must happen before the OLD controller restores its state.
if [ "$UNIVERSAL" = 1 ]; then prepare_universal_package; else prepare_package; fi
BACKUP="$ROOT/etc/adzero-backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP"
chmod 700 "$ROOT/etc/adzero-backups" "$BACKUP"
backup_state
if [ "$UPGRADE" = 1 ]; then
	# Keep config and recovery state together before asking the OLD controller
	# to restore its changes. Never replace a controller that still owns them.
	if [ -x "$CTL" ]; then
		# Recent controllers understand zero wait. Older ones fail immediately.
		# Retry only known pre-mutation busy responses, never restore failures.
		while ! ADZERO_LOCK_WAIT_SECONDS=0 "$CTL" disable > "$TMP/disable.log" 2>&1; do
			case "$(cat "$TMP/disable.log")" in
				'AdZero: 另一项操作正在进行；若异常断电可检查临时锁。'|'AdZero: 等待操作锁超时；可能仍有操作或遗留锁。请检查进程后重试，勿直接删除锁。') ;;
				*) cat "$TMP/disable.log" >&2; fail '旧版未能完成关闭与恢复，已停止升级；恢复记录保留。' ;;
			esac
			upgrade_tick
			wait_upgrade_lock
			backup_state
		done
		cat "$TMP/disable.log"
	fi
	[ ! -e "$ROOT/etc/adzero/adapter.json" ] && [ ! -e "$ROOT/etc/adzero/managed" ] || fail '旧版仍有未恢复的所有权记录，已停止升级。'
fi
printf 'AdZero: 安装 %s；配置备份：%s\n' "$FILE" "$BACKUP/config.tar.gz"
if [ "$UNIVERSAL" = 1 ]; then
	install_universal_package || fail '本地安装失败；配置备份已保留，请检查软件包状态。'
elif [ "$PM" = opkg ]; then
	opkg install "$TMP/package.ipk" || fail '安装失败，请检查当前固件软件源依赖。'
else
	if [ "$APK_LOCAL" = 1 ]; then
		apk add --no-network --repositories-file /dev/null --allow-untrusted "$TMP/package.apk" || fail '本地安装失败；备份已保留，请核对软件包状态后恢复保护。'
	else
		apk add --allow-untrusted "$TMP/package.apk" || fail '安装失败；备份已保留，请核对软件包与依赖后恢复保护。'
	fi
fi
[ -x "$CTL" ] || fail '安装后缺少 AdZero 控制程序。'
if [ "$ENABLE" = 1 ]; then
	"$CTL" enable || fail '安装完成，但启用未成功；请检查 AdZero 诊断与恢复记录。'
	if [ -x "$ROOT/etc/init.d/adzero" ]; then "$ROOT/etc/init.d/adzero" enable; "$ROOT/etc/init.d/adzero" start; fi
fi
printf 'AdZero: 安装完成，请打开 LuCI「服务 → AdZero」。终端过滤效果需实际 DNS 查询验收。\n'
