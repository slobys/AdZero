include $(TOPDIR)/rules.mk

PKG_VERSION:=0.23.4
PKG_RELEASE:=1
PKG_LICENSE:=MIT

LUCI_TITLE:=AdZero - zero-configuration ad blocking
LUCI_DESCRIPTION:=One-click adblock-fast control with cautious proxy/DNS compatibility diagnostics
# Depend on the shared tcpdump provider: tcpdump-mini PROVIDES tcpdump.
# Existing full tcpdump OR tcpdump-mini satisfies it without forced replacement.
LUCI_DEPENDS:=+luci-base +adblock-fast +curl +rpcd-mod-file +rpcd-mod-ucode +ca-bundle +ucode +ucode-mod-fs +ucode-mod-uci +ucode-mod-ubus +ucode-mod-uloop +ucode-mod-socket +tcpdump +conntrack +coreutils-timeout
LUCI_PKGARCH:=all
# Keep scoped CSS custom properties intact across older SDK CSS minifiers.
LUCI_MINIFY_CSS:=0

# Ensure uninstall cannot silently orphan an AdBlock-Fast instance we own.
# During upgrades, retain the existing owner marker for the new package.
define Package/luci-app-adzero/prerm
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] && exit 0
case "$$1" in upgrade|failed-upgrade) exit 0 ;; esac
if [ -f /etc/adzero/managed ]; then
	/usr/libexec/adzero/ctl disable || {
		echo "AdZero: cannot safely restore the managed backend; aborting removal." >&2
		exit 1
	}
fi
exit 0
endef
include $(TOPDIR)/feeds/luci/luci.mk

# call BuildPackage - OpenWrt
