include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-wechat-bridge
PKG_VERSION:=1.1.0
PKG_RELEASE:=1
PKG_MAINTAINER:=yuuouu

LUCI_TITLE:=LuCI support for WeChat Bridge
LUCI_PKGARCH:=all
WECHAT_BRIDGE_DOCKER_DEPENDS?=
LUCI_DEPENDS:=+luci-lib-taskd $(WECHAT_BRIDGE_DOCKER_DEPENDS)

define Package/luci-app-wechat-bridge/prerm
#!/bin/sh
/usr/libexec/istorec/wechat-bridge.sh rm >/dev/null 2>&1 || true
exit 0
endef

include $(TOPDIR)/feeds/luci/luci.mk

# call BuildPackage - OpenWrt buildroot signature
