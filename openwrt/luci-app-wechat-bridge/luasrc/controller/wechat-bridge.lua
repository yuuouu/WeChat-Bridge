module("luci.controller.wechat-bridge", package.seeall)

function index()
	entry({"admin", "services", "wechat-bridge"}, cbi("wechat-bridge"), _("WeChat Bridge"), 60).dependent = true
end
