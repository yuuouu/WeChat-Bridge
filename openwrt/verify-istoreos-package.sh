#!/bin/sh

set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OPENWRT_DIR="$ROOT_DIR/openwrt"
LUCI_DIR="$OPENWRT_DIR/luci-app-wechat-bridge"
META_DIR="$OPENWRT_DIR/app-meta-wechat-bridge"
SDK_DIR="${1:-${SDK_DIR:-}}"
REQUIRE_SDK="${REQUIRE_SDK:-0}"
BUILD_IN_SDK="${BUILD_IN_SDK:-0}"
WECHAT_BRIDGE_DOCKER_DEPENDS="${WECHAT_BRIDGE_DOCKER_DEPENDS:-}"
OPENWRT_APP_META_DIR="${OPENWRT_APP_META_DIR:-}"
REQUIRE_APP_META="${REQUIRE_APP_META:-0}"

fail() {
	echo "FAIL: $*" >&2
	exit 1
}

note() {
	echo "==> $*"
}

require_file() {
	[ -f "$1" ] || fail "missing file: $1"
}

require_grep() {
	pattern="$1"
	file="$2"
	grep -Eq -- "$pattern" "$file" || fail "missing pattern in $file: $pattern"
}

reject_grep() {
	pattern="$1"
	path="$2"
	if grep -R -E -- "$pattern" "$path" >/dev/null 2>&1; then
		fail "forbidden pattern found under $path: $pattern"
	fi
}

note "checking planned file list"
require_file "$LUCI_DIR/Makefile"
require_file "$LUCI_DIR/luasrc/controller/wechat-bridge.lua"
require_file "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_file "$LUCI_DIR/root/etc/config/wechat-bridge"
require_file "$LUCI_DIR/root/etc/uci-defaults/99-wechat-bridge"
require_file "$LUCI_DIR/root/usr/libexec/istorec/wechat-bridge.sh"
require_file "$LUCI_DIR/po/zh-cn/wechat-bridge.po"
require_file "$META_DIR/Makefile"
require_file "$META_DIR/config.sh"
require_file "$META_DIR/logo.png"
require_file "$ROOT_DIR/docs/istoreos.md"
require_file "$OPENWRT_DIR/README.md"
require_file "$OPENWRT_DIR/VERIFICATION.md"
require_file "$OPENWRT_DIR/AUDIT.md"
require_file "$ROOT_DIR/.github/workflows/docker-publish.yml"
if [ -d "$ROOT_DIR/plan" ] && find "$ROOT_DIR/plan" -mindepth 1 \( -name 'luci-app-*' -o -name 'app-meta-*' -o -name 'istorec' \) | grep . >/dev/null 2>&1; then
	fail "formal packaging outputs must not be placed under plan/"
fi
if find "$OPENWRT_DIR" -type f \( -name '.DS_Store' -o -name '*~' -o -name '*.bak' -o -name '*.tmp' -o -name '*.swp' \) | grep . >/dev/null 2>&1; then
	fail "unexpected temporary or editor file found under openwrt/"
fi
if find "$OPENWRT_DIR" "$ROOT_DIR/docs/istoreos.md" -type f \( -name '*.md' -o -name '*.sh' -o -name '*.lua' -o -name 'Makefile' -o -name '*.po' -o -name 'wechat-bridge' \) -print0 |
	xargs -0 grep -Il "$(printf '\r')" | grep . >/dev/null 2>&1; then
	fail "CRLF line endings found in packaging text files"
fi

note "checking shell syntax"
sh -n "$LUCI_DIR/root/usr/libexec/istorec/wechat-bridge.sh"
sh -n "$LUCI_DIR/root/etc/uci-defaults/99-wechat-bridge"
sh -n "$META_DIR/config.sh"

note "checking executable entrypoints"
[ -x "$LUCI_DIR/root/usr/libexec/istorec/wechat-bridge.sh" ] || fail "istorec script is not executable"
[ -x "$LUCI_DIR/root/etc/uci-defaults/99-wechat-bridge" ] || fail "uci-defaults script is not executable"
[ -x "$META_DIR/config.sh" ] || fail "app-meta config.sh is not executable"

note "checking Lua syntax if luac is available"
if command -v luac >/dev/null 2>&1; then
	luac -p "$LUCI_DIR/luasrc/controller/wechat-bridge.lua" "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
else
	echo "SKIP: luac not found"
fi

note "checking luci package metadata"
require_grep 'include \$\(TOPDIR\)/rules\.mk' "$LUCI_DIR/Makefile"
require_grep 'include \$\(TOPDIR\)/feeds/luci/luci\.mk' "$LUCI_DIR/Makefile"
require_grep '^LUCI_PKGARCH:=all$' "$LUCI_DIR/Makefile"
require_grep '^LUCI_DEPENDS:=.*\+luci-lib-taskd' "$LUCI_DIR/Makefile"
require_grep '^WECHAT_BRIDGE_DOCKER_DEPENDS\?=$' "$LUCI_DIR/Makefile"
require_grep '/usr/libexec/istorec/wechat-bridge\.sh rm' "$LUCI_DIR/Makefile"

note "checking UCI section and route"
require_grep "^config wechat-bridge 'config'$" "$LUCI_DIR/root/etc/config/wechat-bridge"
require_grep 'entry\(\{"admin", "services", "wechat-bridge"\}, cbi\("wechat-bridge"\), _\("WeChat Bridge"\)' "$LUCI_DIR/luasrc/controller/wechat-bridge.lua"
require_grep '^META_LUCI_ENTRY:=/cgi-bin/luci/admin/services/wechat-bridge$' "$META_DIR/Makefile"
require_grep 'function m\.on_after_save\(self\)' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 'self\.uci:commit\("wechat-bridge"\)' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 'pending_action = "install"' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 'pending_action = "start"' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 'pending_action = "stop"' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 'pending_action = "restart"' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 'pending_action = "upgrade"' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 's:option\(TextValue, "_logs", translate\("Recent Logs"\)\)' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 'return sys\.exec\(script \.\. " logs 2>/dev/null"\)' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 'status = s:option\(DummyValue, "_status", translate\("Container Status"\)\)' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 'script \.\. " status 2>/dev/null"' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 'script \.\. " port 2>/dev/null"' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 'Open Web Panel' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 's:option\(Flag, "enabled", translate\("Enable"\)\)' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 's:option\(Value, "port", translate\("Web Port"\)\)' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 'port\.datatype = "port"' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 's:option\(Value, "config_path", translate\("Data Directory"\)\)' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 's:option\(Value, "image_name", translate\("Image Name"\)\)' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 'image_name\.default = "ghcr\.io/yuuouu/wechat-bridge"' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 's:option\(Value, "image_tag", translate\("Image Tag"\)\)' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 'image_tag\.default = "latest"' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 's:option\(Value, "api_token", translate\("API Token"\)\)' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 'api_token\.password = true' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 's:option\(ListValue, "markdown_mode", translate\("Markdown Mode"\)\)' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 'markdown_mode:value\("markdown", translate\("Markdown"\)\)' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 'markdown_mode:value\("normalize", translate\("Normalize"\)\)' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"
require_grep 'markdown_mode:value\("plain", translate\("Plain Text"\)\)' "$LUCI_DIR/luasrc/model/cbi/wechat-bridge.lua"

note "checking LuCI translations"
for msgid in \
	"WeChat Bridge" \
	"Settings" \
	"Container Status" \
	"Open Web Panel" \
	"Enable" \
	"Web Port" \
	"Data Directory" \
	"Image Name" \
	"Image Tag" \
	"API Token" \
	"Markdown Mode" \
	"Install / Rebuild Container" \
	"Start" \
	"Stop" \
	"Restart" \
	"Upgrade Image" \
	"Last Action Output" \
	"Recent Logs"; do
	require_grep "^msgid \"${msgid}\"$" "$LUCI_DIR/po/zh-cn/wechat-bridge.po"
done

note "checking app-meta fields"
require_grep 'REGISTRY: ghcr\.io' "$ROOT_DIR/.github/workflows/docker-publish.yml"
require_grep 'IMAGE_NAME: \$\{\{ github\.repository \}\}' "$ROOT_DIR/.github/workflows/docker-publish.yml"
require_grep 'platforms: linux/amd64,linux/arm64' "$ROOT_DIR/.github/workflows/docker-publish.yml"
require_grep 'include \$\(TOPDIR\)/rules\.mk' "$META_DIR/Makefile"
require_grep '^include ../../meta\.mk$' "$META_DIR/Makefile"
require_grep '^PKG_VERSION:=1\.1\.0$' "$META_DIR/Makefile"
APP_VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT_DIR/app/version.py")
META_VERSION=$(sed -n 's/^PKG_VERSION:=//p' "$META_DIR/Makefile")
LUCI_VERSION=$(sed -n 's/^PKG_VERSION:=//p' "$LUCI_DIR/Makefile")
[ "$APP_VERSION" = "$META_VERSION" ] || fail "app-meta PKG_VERSION does not match app/version.py"
[ "$APP_VERSION" = "$LUCI_VERSION" ] || fail "luci-app PKG_VERSION does not match app/version.py"
require_grep '^PKG_RELEASE:=1$' "$META_DIR/Makefile"
require_grep '^META_TITLE:=WeChat Bridge$' "$META_DIR/Makefile"
require_grep '^META_DESCRIPTION:=把微信 Bot 变成可编程的 HTTP 消息通道，支持 Web 管理、扫码登录、API 推送和 Webhook。$' "$META_DIR/Makefile"
require_grep '^META_AUTHOR:=yuuouu$' "$META_DIR/Makefile"
require_grep '^META_TAGS:=net service$' "$META_DIR/Makefile"
require_grep '^META_DEPENDS:=\+luci-app-wechat-bridge \+docker-deps$' "$META_DIR/Makefile"
require_grep '^META_WEBSITE:=https://github.com/yuuouu/wechat-bridge$' "$META_DIR/Makefile"
require_grep '^META_TUTORIAL:=https://github.com/yuuouu/wechat-bridge/blob/main/docs/istoreos.md$' "$META_DIR/Makefile"
require_grep '^META_AUTOCONF:=path enable$' "$META_DIR/Makefile"
require_grep '^META_UCI:=wechat-bridge$' "$META_DIR/Makefile"
require_grep '^META_ARCH:=x86_64 aarch64$' "$META_DIR/Makefile"
require_grep '\[ -z "\$ISTORE_DONT_START" \] && \[ "\$want_enable" = "1" \]' "$META_DIR/config.sh"
require_grep '/usr/libexec/istorec/wechat-bridge\.sh install' "$META_DIR/config.sh"

note "checking required documentation sections"
require_grep '^## 安装$' "$ROOT_DIR/docs/istoreos.md"
require_grep '^## 首次打开 Web UI$' "$ROOT_DIR/docs/istoreos.md"
require_grep '^## API Token 与调用示例$' "$ROOT_DIR/docs/istoreos.md"
require_grep '^## 青龙通知接入$' "$ROOT_DIR/docs/istoreos.md"
require_grep '^## iStoreOS / OpenWrt 通知接入$' "$ROOT_DIR/docs/istoreos.md"
require_grep '^## 管理与升级$' "$ROOT_DIR/docs/istoreos.md"
require_grep '^## 卸载$' "$ROOT_DIR/docs/istoreos.md"
require_grep '^## 安全提示$' "$ROOT_DIR/docs/istoreos.md"
require_grep 'uci get wechat-bridge\.config\.api_token' "$ROOT_DIR/docs/istoreos.md"
require_grep 'Authorization: Bearer YOUR_TOKEN' "$ROOT_DIR/docs/istoreos.md"
require_grep '/api/status' "$ROOT_DIR/docs/istoreos.md"
require_grep '/api/send\?token=YOUR_TOKEN' "$ROOT_DIR/docs/istoreos.md"
require_grep 'webhookMethod' "$ROOT_DIR/docs/istoreos.md"
require_grep 'luci-app-wechatpush' "$ROOT_DIR/docs/istoreos.md"
require_grep '/usr/libexec/istorec/wechat-bridge\.sh upgrade' "$ROOT_DIR/docs/istoreos.md"
require_grep 'API_TOKEN' "$ROOT_DIR/docs/istoreos.md"
require_grep 'HTTPS' "$ROOT_DIR/docs/istoreos.md"
require_grep '^## Docker 依赖确认$' "$OPENWRT_DIR/README.md"
require_grep '^## 编译 luci-app IPK$' "$OPENWRT_DIR/README.md"
require_grep '^## 测试机安装验证$' "$OPENWRT_DIR/README.md"
require_grep '^## 上游提交顺序$' "$OPENWRT_DIR/README.md"
require_grep 'scripts/feeds search docker' "$OPENWRT_DIR/README.md"
require_grep 'scripts/feeds search dockerd' "$OPENWRT_DIR/README.md"
require_grep 'scripts/feeds search docker-ce' "$OPENWRT_DIR/README.md"
require_grep "WECHAT_BRIDGE_DOCKER_DEPENDS='\\+dockerd \\+docker'" "$OPENWRT_DIR/README.md"
require_grep 'BUILD_IN_SDK=1' "$OPENWRT_DIR/README.md"
require_grep 'OPENWRT_APP_META_DIR=/path/to/openwrt-app-meta' "$OPENWRT_DIR/README.md"
require_grep 'linkease/istore-repo' "$OPENWRT_DIR/README.md"
require_grep 'linkease/openwrt-app-meta' "$OPENWRT_DIR/README.md"
require_grep '^## Strict SDK check$' "$OPENWRT_DIR/VERIFICATION.md"
require_grep '^## Device checks$' "$OPENWRT_DIR/VERIFICATION.md"
require_grep '^## Completion status$' "$OPENWRT_DIR/AUDIT.md"
require_grep '^## Prompt-to-artifact checklist$' "$OPENWRT_DIR/AUDIT.md"

note "checking app API surface if python3 is available"
if command -v python3 >/dev/null 2>&1; then
	python3 - "$ROOT_DIR" <<'PY'
import json
import sys
import types
from pathlib import Path

root = Path(sys.argv[1]).resolve()
app_root = root / "app"
if str(root) not in sys.path:
    sys.path.insert(0, str(root))
if str(app_root) not in sys.path:
    sys.path.insert(0, str(app_root))

from tests.crypto_stub import install_crypto_stub

install_crypto_stub()
sys.modules.setdefault("qrcode", types.ModuleType("qrcode"))

from tests.test_webapp_server import _FakeBridge, _FakeClient
from webapp import api_handlers
from webapp.context import WebAppContext
from webapp.server import BridgeHandler


class DirectHandler:
    def __init__(self, ctx, *, path="/", headers=None):
        self.ctx = ctx
        self.path = path
        self.headers = headers or {}
        self.status = None
        self.payload = None

    def _get_context(self):
        return self.ctx

    def _json_response(self, payload, status=200):
        self.status = status
        self.payload = payload

    def _check_api_token(self):
        return BridgeHandler._check_api_token(self)


ctx = WebAppContext(client=_FakeClient(logged_in=True), bridge=_FakeBridge(), api_token="secret-token")

status_handler = DirectHandler(ctx)
api_handlers.handle_status(status_handler, ctx, {})
assert status_handler.status == 200
assert status_handler.payload["logged_in"] is True
assert status_handler.payload["bot_id"] == "bot-test"
assert "version" in status_handler.payload

unauth_handler = DirectHandler(ctx, path="/api/send")
api_handlers.handle_send_post(unauth_handler, ctx, {}, json.dumps({"to": "Alice", "text": "hello"}).encode())
assert unauth_handler.status == 401
assert "Unauthorized" in unauth_handler.payload["error"]

auth_handler = DirectHandler(ctx, path="/api/send", headers={"Authorization": "Bearer secret-token"})
api_handlers.handle_send_post(auth_handler, ctx, {}, json.dumps({"to": "Alice", "text": "hello"}).encode())
assert auth_handler.status == 200
assert auth_handler.payload["ok"] is True
assert ctx.bridge.sent == [("Alice", "hello", "api", "")]

query_handler = DirectHandler(ctx, path="/api/send?token=secret-token")
api_handlers.handle_send_get(query_handler, ctx, {"to": ["Alice"], "text": ["via query"]})
assert query_handler.status == 200
assert query_handler.payload["ok"] is True
assert ctx.bridge.sent[-1] == ("Alice", "via query", "api", "")
PY
else
	echo "SKIP: python3 not found"
fi

note "checking locked-out implementation patterns"
reject_grep 'luci-lib-docker|zoneinfo-asia|lsblk|@main\[0\]|system\.@system\[0\]\.timezone' "$LUCI_DIR"
reject_grep 'luci-lib-docker|zoneinfo-asia|lsblk|@main\[0\]|system\.@system\[0\]\.timezone' "$META_DIR"
if find "$LUCI_DIR" -name '*.json' | grep . >/dev/null 2>&1; then
	fail "unexpected JSON file under luci package"
fi
if find "$LUCI_DIR" -path '*/htdocs/luci-static/resources/view/*' -type f | grep . >/dev/null 2>&1; then
	fail "unexpected JS LuCI view under luci package"
fi

note "checking istorec command surface"
SCRIPT="$LUCI_DIR/root/usr/libexec/istorec/wechat-bridge.sh"
require_grep 'install\|upgrade\)' "$SCRIPT"
require_grep 'start\)' "$SCRIPT"
require_grep 'stop\)' "$SCRIPT"
require_grep 'restart\)' "$SCRIPT"
require_grep 'status\)' "$SCRIPT"
require_grep 'port\)' "$SCRIPT"
require_grep 'logs\)' "$SCRIPT"
require_grep 'rm\)' "$SCRIPT"
require_grep 'docker pull "\$\{image_name\}:\$\{image_tag\}"' "$SCRIPT"
require_grep '--restart=unless-stopped' "$SCRIPT"
require_grep '-p "\$\{port\}:5200"' "$SCRIPT"
require_grep '-v "\$\{config_path\}:/data"' "$SCRIPT"
require_grep 'PORT=5200' "$SCRIPT"
require_grep 'TOKEN_FILE=/data/token\.json' "$SCRIPT"
require_grep 'DATA_DIR=/data' "$SCRIPT"
require_grep 'AI_CONFIG_FILE=/data/ai_config\.json' "$SCRIPT"
require_grep 'NO_BROWSER=1' "$SCRIPT"
require_grep 'API_TOKEN=\$\{api_token\}' "$SCRIPT"
require_grep 'MARKDOWN_MODE=\$\{markdown_mode\}' "$SCRIPT"
require_grep 'system\.@system\[0\]\.zonename' "$SCRIPT"
require_grep 'docker logs --tail 100 "\$CONTAINER"' "$SCRIPT"

note "checking logo metadata"
if command -v file >/dev/null 2>&1; then
	file "$META_DIR/logo.png" | grep -Eq 'PNG image data, 128 x 128' || fail "logo.png is not a 128x128 PNG"
fi
logo_size=$(wc -c < "$META_DIR/logo.png" | tr -d ' ')
[ "$logo_size" -le 32768 ] || fail "logo.png is too large for app metadata"

note "running local fake uci/docker behavior checks"
TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/wechat-bridge-verify.XXXXXX")
trap 'rm -rf "$TMP_DIR"' EXIT
STATE="$TMP_DIR/uci.state"
DOCKER_LOG="$TMP_DIR/docker.log"
mkdir -p "$TMP_DIR/bin"

cat > "$TMP_DIR/bin/uci" <<'UCI'
#!/bin/sh
state="${UCI_STATE:?}"
[ "$1" = "-q" ] && shift
cmd="$1"; shift
case "$cmd" in
	get)
		[ -f "$state" ] || exit 1
		awk -F= -v key="$1" '$1 == key {print substr($0, length(key) + 2); found=1} END {exit found ? 0 : 1}' "$state"
		;;
	set)
		kv="$1"; key=${kv%%=*}; value=${kv#*=}; touch "$state"
		awk -F= -v key="$key" '$1 != key {print}' "$state" > "$state.tmp"
		printf '%s=%s\n' "$key" "$value" >> "$state.tmp"
		mv "$state.tmp" "$state"
		;;
	commit)
		printf 'commit:%s\n' "$1" >> "$state"
		;;
	*) exit 1 ;;
esac
UCI
chmod +x "$TMP_DIR/bin/uci"

cat > "$TMP_DIR/bin/docker" <<'DOCKER'
#!/bin/sh
log="${DOCKER_LOG:?}"
case "$1" in
	inspect)
		[ "${DOCKER_CASE:-none}" = "none" ] && exit 1
		[ "${2:-}" = "-f" ] && { [ "${DOCKER_CASE:-none}" = "running" ] && echo true || echo false; }
		exit 0
		;;
	logs)
		i=1; while [ "$i" -le 150 ]; do echo "line-$i"; i=$((i + 1)); done
		;;
	pull|rm|run|start|stop|restart)
		printf '%s' "$1" >> "$log"; shift
		for arg in "$@"; do printf ' [%s]' "$arg" >> "$log"; done
		printf '\n' >> "$log"
		;;
	*) exit 1 ;;
esac
DOCKER
chmod +x "$TMP_DIR/bin/docker"

PATH="$TMP_DIR/bin:$PATH"
export PATH UCI_STATE="$STATE" DOCKER_LOG

note "checking uci-defaults behavior"
: > "$STATE"
sh "$LUCI_DIR/root/etc/uci-defaults/99-wechat-bridge"
grep -F 'wechat-bridge.config=wechat-bridge' "$STATE" >/dev/null || fail "uci-defaults did not create named section"
grep -F 'wechat-bridge.config.enabled=0' "$STATE" >/dev/null || fail "uci-defaults did not default enabled=0"
grep -F 'wechat-bridge.config.port=5200' "$STATE" >/dev/null || fail "uci-defaults did not default port=5200"
grep -F 'wechat-bridge.config.image_name=ghcr.io/yuuouu/wechat-bridge' "$STATE" >/dev/null || fail "uci-defaults did not default image_name"
grep -F 'wechat-bridge.config.image_tag=latest' "$STATE" >/dev/null || fail "uci-defaults did not default image_tag"
grep -F 'wechat-bridge.config.markdown_mode=markdown' "$STATE" >/dev/null || fail "uci-defaults did not default markdown_mode"
if grep -F 'wechat-bridge.config.config_path=' "$STATE" >/dev/null; then
	fail "uci-defaults must not write config_path"
fi
awk -F= '$1 == "wechat-bridge.config.api_token" && $2 ~ /^[0-9a-f]{32}$/ {found=1} END {exit found ? 0 : 1}' "$STATE" || fail "uci-defaults token is not 32 hex"

: > "$STATE"
ISTORE_CONF_DIR=/mnt/istore ISTORE_DONT_START=1 sh "$META_DIR/config.sh" path enable
grep -F 'wechat-bridge.config.config_path=/mnt/istore/WeChatBridge' "$STATE" >/dev/null || fail "config.sh did not set config_path"
grep -F 'wechat-bridge.config.enabled=1' "$STATE" >/dev/null || fail "config.sh did not enable"
awk -F= '$1 == "wechat-bridge.config.api_token" && $2 ~ /^[0-9a-f]{32}$/ {found=1} END {exit found ? 0 : 1}' "$STATE" || fail "config.sh token is not 32 hex"

cat > "$STATE" <<EOF_STATE
wechat-bridge.config=wechat-bridge
wechat-bridge.config.api_token=keep-existing-token
wechat-bridge.config.port=7777
wechat-bridge.config.image_name=custom/image
wechat-bridge.config.image_tag=v9
wechat-bridge.config.markdown_mode=plain
EOF_STATE
ISTORE_CONF_DIR=/mnt/istore ISTORE_DONT_START=1 sh "$META_DIR/config.sh" path enable
grep -F 'wechat-bridge.config.api_token=keep-existing-token' "$STATE" >/dev/null || fail "config.sh overwrote existing api_token"
grep -F 'wechat-bridge.config.port=7777' "$STATE" >/dev/null || fail "config.sh overwrote existing port"
grep -F 'wechat-bridge.config.image_name=custom/image' "$STATE" >/dev/null || fail "config.sh overwrote existing image_name"
grep -F 'wechat-bridge.config.image_tag=v9' "$STATE" >/dev/null || fail "config.sh overwrote existing image_tag"
grep -F 'wechat-bridge.config.markdown_mode=plain' "$STATE" >/dev/null || fail "config.sh overwrote existing markdown_mode"

: > "$STATE"
ISTORE_CONF_DIR=/mnt/istore ISTORE_DONT_START=1 sh "$META_DIR/config.sh" path
grep -F 'wechat-bridge.config.config_path=/mnt/istore/WeChatBridge' "$STATE" >/dev/null || fail "config.sh path did not set config_path"
if grep -F 'wechat-bridge.config.enabled=1' "$STATE" >/dev/null; then
	fail "config.sh path-only enabled unexpectedly"
fi

: > "$STATE"
env -u ISTORE_CONF_DIR ISTORE_DONT_START=1 sh "$META_DIR/config.sh" enable
grep -F 'wechat-bridge.config.enabled=1' "$STATE" >/dev/null || fail "config.sh enable did not enable"
if grep -F 'wechat-bridge.config.config_path=' "$STATE" >/dev/null; then
	fail "config.sh enable-only wrote config_path unexpectedly"
fi

: > "$STATE"
ISTORE_CONF_DIR=/mnt/istore ISTORE_DONT_START=1 sh "$META_DIR/config.sh" enable_path path_enable
if grep -E 'wechat-bridge\.config\.(config_path|enabled)=' "$STATE" >/dev/null; then
	fail "config.sh matched enable_path/path_enable unexpectedly"
fi
if env -u ISTORE_CONF_DIR ISTORE_DONT_START=1 sh "$META_DIR/config.sh" path >/dev/null 2>"$TMP_DIR/config-path-missing.err"; then
	fail "config.sh path succeeded without ISTORE_CONF_DIR"
fi
grep -F 'ISTORE_CONF_DIR is required for path autoconf' "$TMP_DIR/config-path-missing.err" >/dev/null || fail "config.sh path did not report missing ISTORE_CONF_DIR"

cat > "$STATE" <<EOF_STATE
wechat-bridge.config.port=5300
wechat-bridge.config.config_path=$TMP_DIR/data
wechat-bridge.config.image_name=example/image
wechat-bridge.config.image_tag=1.2.3
wechat-bridge.config.api_token=abc123
wechat-bridge.config.markdown_mode=normalize
system.@system[0].zonename=Asia/Shanghai
EOF_STATE

[ "$(DOCKER_CASE=none sh "$SCRIPT" status)" = "not-installed" ] || fail "status not-installed failed"
[ "$(DOCKER_CASE=stopped sh "$SCRIPT" status)" = "stopped" ] || fail "status stopped failed"
[ "$(DOCKER_CASE=running sh "$SCRIPT" status)" = "running" ] || fail "status running failed"
[ "$(sh "$SCRIPT" port)" = "5300" ] || fail "port did not read UCI"
[ -z "$(DOCKER_CASE=none sh "$SCRIPT" logs)" ] || fail "logs should be empty when container is missing"
if DOCKER_CASE=none sh "$SCRIPT" start >/dev/null 2>"$TMP_DIR/start-missing.err"; then
	fail "start succeeded when container is missing"
fi
grep -F 'not-installed' "$TMP_DIR/start-missing.err" >/dev/null || fail "start did not report missing container"
if DOCKER_CASE=none sh "$SCRIPT" restart >/dev/null 2>"$TMP_DIR/restart-missing.err"; then
	fail "restart succeeded when container is missing"
fi
grep -F 'not-installed' "$TMP_DIR/restart-missing.err" >/dev/null || fail "restart did not report missing container"
[ "$(DOCKER_CASE=none sh "$SCRIPT" stop)" = "not-installed" ] || fail "stop did not report missing container"

: > "$DOCKER_LOG"
DOCKER_CASE=stopped sh "$SCRIPT" start >/dev/null
DOCKER_CASE=running sh "$SCRIPT" stop >/dev/null
DOCKER_CASE=running sh "$SCRIPT" restart >/dev/null
DOCKER_CASE=none sh "$SCRIPT" rm >/dev/null
grep -F 'start [wechat-bridge]' "$DOCKER_LOG" >/dev/null || fail "start did not call docker start"
grep -F 'stop [wechat-bridge]' "$DOCKER_LOG" >/dev/null || fail "stop did not call docker stop"
grep -F 'restart [wechat-bridge]' "$DOCKER_LOG" >/dev/null || fail "restart did not call docker restart"
grep -F 'rm [-f] [wechat-bridge]' "$DOCKER_LOG" >/dev/null || fail "rm did not call docker rm -f"

: > "$STATE"
[ "$(sh "$SCRIPT" port)" = "5200" ] || fail "port did not default to 5200"
if DOCKER_CASE=none sh "$SCRIPT" install >/dev/null 2>"$TMP_DIR/install-empty.err"; then
	fail "install succeeded with empty config_path"
fi
grep -F 'config_path is empty' "$TMP_DIR/install-empty.err" >/dev/null || fail "install did not report empty config_path"

: > "$DOCKER_LOG"
cat > "$STATE" <<EOF_STATE
wechat-bridge.config.port=5300
wechat-bridge.config.config_path=$TMP_DIR/data
wechat-bridge.config.image_name=example/image
wechat-bridge.config.image_tag=1.2.3
wechat-bridge.config.api_token=abc123
wechat-bridge.config.markdown_mode=normalize
system.@system[0].zonename=Asia/Shanghai
EOF_STATE
DOCKER_CASE=running sh "$SCRIPT" install >/dev/null
[ -d "$TMP_DIR/data" ] || fail "install did not create data directory"
grep -F 'pull [example/image:1.2.3]' "$DOCKER_LOG" >/dev/null || fail "docker pull did not use configured image"
grep -F 'rm [-f] [wechat-bridge]' "$DOCKER_LOG" >/dev/null || fail "install did not remove old container"
grep -F '[--name] [wechat-bridge]' "$DOCKER_LOG" >/dev/null || fail "docker run missing container name"
grep -F '[--restart=unless-stopped]' "$DOCKER_LOG" >/dev/null || fail "docker run missing restart policy"
grep -F '[-p] [5300:5200]' "$DOCKER_LOG" >/dev/null || fail "docker run missing port mapping"
grep -F "[-v] [$TMP_DIR/data:/data]" "$DOCKER_LOG" >/dev/null || fail "docker run missing data mount"
grep -F '[-e] [PORT=5200]' "$DOCKER_LOG" >/dev/null || fail "docker run missing PORT env"
grep -F '[-e] [TOKEN_FILE=/data/token.json]' "$DOCKER_LOG" >/dev/null || fail "docker run missing TOKEN_FILE env"
grep -F '[-e] [DATA_DIR=/data]' "$DOCKER_LOG" >/dev/null || fail "docker run missing DATA_DIR env"
grep -F '[-e] [AI_CONFIG_FILE=/data/ai_config.json]' "$DOCKER_LOG" >/dev/null || fail "docker run missing AI_CONFIG_FILE env"
grep -F '[-e] [NO_BROWSER=1]' "$DOCKER_LOG" >/dev/null || fail "docker run missing NO_BROWSER env"
grep -F '[-e] [API_TOKEN=abc123]' "$DOCKER_LOG" >/dev/null || fail "docker run missing API_TOKEN env"
grep -F '[-e] [MARKDOWN_MODE=normalize]' "$DOCKER_LOG" >/dev/null || fail "docker run missing MARKDOWN_MODE env"
grep -F '[-e] [TZ=Asia/Shanghai]' "$DOCKER_LOG" >/dev/null || fail "docker run missing zonename TZ"
grep -F '[example/image:1.2.3]' "$DOCKER_LOG" >/dev/null || fail "docker run did not use configured image"

: > "$DOCKER_LOG"
cat > "$STATE" <<EOF_STATE
wechat-bridge.config.port=5300
wechat-bridge.config.config_path=$TMP_DIR/data-utc
wechat-bridge.config.image_name=example/image
wechat-bridge.config.image_tag=1.2.3
wechat-bridge.config.api_token=abc123
wechat-bridge.config.markdown_mode=normalize
EOF_STATE
DOCKER_CASE=running sh "$SCRIPT" upgrade >/dev/null
grep -F '[-e] [TZ=UTC]' "$DOCKER_LOG" >/dev/null || fail "docker run missing UTC fallback TZ"

DOCKER_CASE=running sh "$SCRIPT" logs > "$TMP_DIR/logs"
[ "$(wc -l < "$TMP_DIR/logs" | tr -d ' ')" = "100" ] || fail "logs did not cap to 100 lines"

if [ -n "$SDK_DIR" ]; then
	note "checking target SDK feeds in $SDK_DIR"
	[ -x "$SDK_DIR/scripts/feeds" ] || fail "SDK scripts/feeds is missing or not executable"
	( cd "$SDK_DIR" && ./scripts/feeds search docker && ./scripts/feeds search dockerd && ./scripts/feeds search docker-ce )
	if [ -z "$WECHAT_BRIDGE_DOCKER_DEPENDS" ]; then
		fail "WECHAT_BRIDGE_DOCKER_DEPENDS must be set to target SDK Docker daemon/CLI packages"
	fi
	if [ "$BUILD_IN_SDK" = "1" ]; then
		note "building luci-app-wechat-bridge in target SDK"
		SDK_PACKAGE_DIR="$SDK_DIR/package/wechat-bridge"
		mkdir -p "$SDK_PACKAGE_DIR"
		rm -rf "$SDK_PACKAGE_DIR/luci-app-wechat-bridge"
		cp -R "$LUCI_DIR" "$SDK_PACKAGE_DIR/"
		( cd "$SDK_DIR" && make package/luci-app-wechat-bridge/clean V=s WECHAT_BRIDGE_DOCKER_DEPENDS="$WECHAT_BRIDGE_DOCKER_DEPENDS" && make package/luci-app-wechat-bridge/compile V=s WECHAT_BRIDGE_DOCKER_DEPENDS="$WECHAT_BRIDGE_DOCKER_DEPENDS" )
	else
		echo "SKIP: SDK package compile; set BUILD_IN_SDK=1 to build luci-app-wechat-bridge"
	fi
elif [ "$REQUIRE_SDK" = "1" ]; then
	fail "SDK feeds/package compile checks require SDK path; pass argv[1] or SDK_DIR=..."
else
	echo "SKIP: SDK feeds/package compile checks; pass SDK path as argv[1] or SDK_DIR=..."
fi

if [ -n "$OPENWRT_APP_META_DIR" ]; then
	note "checking upstream openwrt-app-meta docker-deps in $OPENWRT_APP_META_DIR"
	[ -f "$OPENWRT_APP_META_DIR/dummy/Makefile" ] || fail "openwrt-app-meta dummy/Makefile not found"
	grep -Eq 'docker-deps|Package/docker-deps' "$OPENWRT_APP_META_DIR/dummy/Makefile" || fail "docker-deps not found in openwrt-app-meta dummy/Makefile"
elif [ "$REQUIRE_APP_META" = "1" ]; then
	fail "openwrt-app-meta docker-deps check requires OPENWRT_APP_META_DIR=..."
else
	echo "SKIP: upstream docker-deps check; set OPENWRT_APP_META_DIR=..."
fi

note "local verification completed"
