local http = require "luci.http"
local sys = require "luci.sys"
local util = require "luci.util"

local script = "/usr/libexec/istorec/wechat-bridge.sh"
local pending_action
local last_output

local function run_action(action)
	return util.trim(sys.exec(script .. " " .. action .. " 2>&1"))
end

local function current_port()
	local port = util.trim(sys.exec(script .. " port 2>/dev/null"))
	if port == "" then
		port = "5200"
	end
	return port
end

m = Map("wechat-bridge", translate("WeChat Bridge"))

function m.on_after_save(self)
	if pending_action then
		self.uci:commit("wechat-bridge")
		self.uci:load("wechat-bridge")
		last_output = run_action(pending_action)
	end
end

s = m:section(NamedSection, "config", "wechat-bridge", translate("Settings"))
s.addremove = false
s.anonymous = true

status = s:option(DummyValue, "_status", translate("Container Status"))
status.rawhtml = true
function status.cfgvalue(self, section)
	local state = util.trim(sys.exec(script .. " status 2>/dev/null"))
	if state == "" then
		state = "not-installed"
	end

	local labels = {
		running = translate("Running"),
		stopped = translate("Stopped"),
		["not-installed"] = translate("Not installed")
	}

	local host = http.getenv("HTTP_HOST") or http.getenv("SERVER_NAME") or "router.lan"
	host = host:gsub(":%d+$", "")
	local port = current_port()
	local url = "http://" .. host .. ":" .. port .. "/"

	return string.format(
		'<strong>%s</strong><br /><a href="%s" target="_blank">%s</a>',
		util.pcdata(labels[state] or state),
		util.pcdata(url),
		util.pcdata(translate("Open Web Panel"))
	)
end

enabled = s:option(Flag, "enabled", translate("Enable"))
enabled.default = "0"
enabled.rmempty = false

port = s:option(Value, "port", translate("Web Port"))
port.default = "5200"
port.datatype = "port"
port.rmempty = false

config_path = s:option(Value, "config_path", translate("Data Directory"))
config_path.placeholder = "/mnt/your-disk/istore/WeChatBridge"
config_path.rmempty = false

image_name = s:option(Value, "image_name", translate("Image Name"))
image_name.default = "ghcr.io/yuuouu/wechat-bridge"
image_name.rmempty = false

image_tag = s:option(Value, "image_tag", translate("Image Tag"))
image_tag.default = "latest"
image_tag.rmempty = false

api_token = s:option(Value, "api_token", translate("API Token"))
api_token.password = true
api_token.rmempty = false

markdown_mode = s:option(ListValue, "markdown_mode", translate("Markdown Mode"))
markdown_mode:value("markdown", translate("Markdown"))
markdown_mode:value("normalize", translate("Normalize"))
markdown_mode:value("plain", translate("Plain Text"))
markdown_mode.default = "markdown"
markdown_mode.rmempty = false

webhook_port = s:option(Value, "webhook_port", translate("Webhook Manager Port"))
webhook_port.default = "18082"
webhook_port.datatype = "port"
webhook_port.rmempty = false
webhook_port.description = translate("Port for the internal Webhook Manager / Plugin System")

ql_scripts_path = s:option(Value, "ql_scripts_path", translate("Qinglong Scripts Path"))
ql_scripts_path.placeholder = "/mnt/sda1/qinglong/data/scripts"
ql_scripts_path.rmempty = true
ql_scripts_path.description = translate("Path to Qinglong scripts for plugins to use (Read-only)")

install = s:option(Button, "_install", translate("Install / Rebuild Container"))
install.inputstyle = "apply"
function install.write(self, section)
	pending_action = "install"
end

start = s:option(Button, "_start", translate("Start"))
start.inputstyle = "apply"
function start.write(self, section)
	pending_action = "start"
end

stop = s:option(Button, "_stop", translate("Stop"))
stop.inputstyle = "reset"
function stop.write(self, section)
	pending_action = "stop"
end

restart = s:option(Button, "_restart", translate("Restart"))
restart.inputstyle = "reload"
function restart.write(self, section)
	pending_action = "restart"
end

upgrade = s:option(Button, "_upgrade", translate("Upgrade Image"))
upgrade.inputstyle = "apply"
function upgrade.write(self, section)
	pending_action = "upgrade"
end

output = s:option(DummyValue, "_output", translate("Last Action Output"))
output.rawhtml = true
function output.cfgvalue(self, section)
	if not last_output or last_output == "" then
		return ""
	end
	return "<pre>" .. util.pcdata(last_output) .. "</pre>"
end

logs = s:option(TextValue, "_logs", translate("Recent Logs"))
logs.rows = 16
logs.wrap = "off"
logs.readonly = "readonly"
function logs.cfgvalue(self, section)
	return sys.exec(script .. " logs 2>/dev/null")
end
function logs.write(self, section, value)
end

return m
