#!/bin/sh

want_path=0
want_enable=0

for flag in "$@"; do
	case "$flag" in
		path) want_path=1 ;;
		enable) want_enable=1 ;;
	esac
done

generate_token() {
	if command -v hexdump >/dev/null 2>&1; then
		hexdump -n 16 -e '16/1 "%02x"' /dev/urandom
	elif command -v od >/dev/null 2>&1; then
		od -An -N16 -tx1 /dev/urandom | tr -d ' \n'
	else
		(date +%s; cat /proc/sys/kernel/random/uuid 2>/dev/null) | md5sum | cut -c1-32
	fi
}

ensure_section() {
	if ! uci -q get wechat-bridge.config >/dev/null 2>&1; then
		uci set wechat-bridge.config='wechat-bridge'
	fi
}

set_default() {
	local name="$1"
	local value="$2"

	if [ -z "$(uci -q get "wechat-bridge.config.${name}")" ]; then
		uci set "wechat-bridge.config.${name}=${value}"
	fi
}

ensure_section

if [ "$want_path" = "1" ]; then
	if [ -z "$ISTORE_CONF_DIR" ]; then
		echo "ISTORE_CONF_DIR is required for path autoconf" >&2
		exit 1
	fi
	uci set "wechat-bridge.config.config_path=${ISTORE_CONF_DIR}/WeChatBridge"
fi

if [ "$want_enable" = "1" ]; then
	uci set "wechat-bridge.config.enabled=1"
fi

set_default port 5200
set_default image_name ghcr.io/yuuouu/wechat-bridge
set_default image_tag latest
set_default markdown_mode markdown

if [ -z "$(uci -q get wechat-bridge.config.api_token)" ]; then
	uci set "wechat-bridge.config.api_token=$(generate_token)"
fi

uci commit wechat-bridge

if [ -z "$ISTORE_DONT_START" ] && [ "$want_enable" = "1" ]; then
	/usr/libexec/istorec/wechat-bridge.sh install
fi
