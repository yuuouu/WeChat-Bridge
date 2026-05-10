include $(TOPDIR)/rules.mk

PKG_VERSION:=1.2.0
PKG_RELEASE:=1

META_TITLE:=WeChat Bridge
META_DESCRIPTION:=把微信 Bot 变成可编程的 HTTP 消息通道，支持 Web 管理、扫码登录、API 推送和 Webhook。
META_AUTHOR:=yuuouu
META_TAGS:=net service
META_DEPENDS:=+luci-app-wechat-bridge +docker-deps
META_LUCI_ENTRY:=/cgi-bin/luci/admin/services/wechat-bridge
META_WEBSITE:=https://github.com/yuuouu/wechat-bridge
META_TUTORIAL:=https://github.com/yuuouu/wechat-bridge/blob/main/docs/istoreos.md
META_AUTOCONF:=path enable
META_UCI:=wechat-bridge
META_ARCH:=x86_64 aarch64

include ../../meta.mk
