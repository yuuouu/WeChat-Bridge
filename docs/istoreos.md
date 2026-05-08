# iStoreOS 安装与使用

WeChat Bridge 的 iStoreOS 版本采用 Docker 管理型打包：LuCI 插件负责配置、拉取镜像、启停容器和日志入口，应用主体运行在 `ghcr.io/yuuouu/wechat-bridge` 容器中。

## 安装

在 iStore 应用商店中安装 `WeChat Bridge`。iStore 自动配置会把数据目录写入：

```text
$ISTORE_CONF_DIR/WeChatBridge
```

首次安装会自动生成 32 位十六进制 `API_TOKEN`。安装完成后进入 `服务` -> `WeChat Bridge`，确认以下配置：

- `启用`：打开后会创建并启动容器。
- `Web 端口`：默认 `5200`。
- `数据目录`：iStore 自动配置为 `WeChatBridge` 数据目录。
- `镜像名`：默认 `ghcr.io/yuuouu/wechat-bridge`。
- `镜像 Tag`：默认 `latest`，需要固定版本时可改为具体 tag。
- `API Token`：Web 面板和 API 调用的访问密钥。
- `Markdown 模式`：默认 `markdown`，通知整理可选 `normalize`，纯文本可选 `plain`。

如果是手动安装 IPK，先安装 Docker 运行环境和 `luci-app-wechat-bridge`，再进入 LuCI 页面填写数据目录并点击 `安装 / 重建容器`。

## 首次打开 Web UI

进入 LuCI 的 `服务` -> `WeChat Bridge`，点击 `打开 Web 面板`。默认地址类似：

```text
http://路由器IP:5200/
```

如果页面要求密码，输入 LuCI 页面中的 `API Token`。进入 Web UI 后按页面提示扫码登录微信 Bot。

健康检查接口不需要鉴权：

```bash
curl http://路由器IP:5200/api/status
```

## API Token 与调用示例

在 LuCI 页面可以查看或修改 `API Token`。也可以在路由器 SSH 中读取：

```sh
uci get wechat-bridge.config.api_token
```

发送文本消息：

```bash
curl -X POST http://路由器IP:5200/api/send \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"to":"好友名称","text":"来自 iStoreOS 的测试消息"}'
```

如果不指定 `to`，系统会发送给通讯录中的第一个联系人：

```bash
curl "http://路由器IP:5200/api/send?token=YOUR_TOKEN&text=任务完成"
```

## 青龙通知接入

在青龙面板进入 `系统设置` -> `通知设置`，选择 `自定义通知`：

- `webhookMethod`：`GET`
- `webhookContentType`：`text/plain`
- `webhookUrl`：

```text
http://路由器IP:5200/api/send?token=YOUR_TOKEN&text=$content&title=$title&markdown=normalize
```

保存后点击测试，微信中应收到青龙测试通知。

## iStoreOS / OpenWrt 通知接入

如果安装了 `luci-app-wechatpush`，进入 `服务` -> `微信推送`，推送模式选择 `自定义推送`，配置示例：

```json
{
  "url": "http://路由器IP:5200/api/send?token=YOUR_TOKEN",
  "data": "@${tempjsonpath}",
  "content_type": "Content-Type: application/json",
  "str_title_start": "",
  "str_title_end": "",
  "str_linefeed": "\\n",
  "str_splitline": "\\n\\n---\\n\\n",
  "str_space": ": ",
  "str_tab": "- ",
  "type": {
    "to": "\"你的微信user_id\"",
    "markdown": "\"normalize\"",
    "text": "\"## ${1}\\n\\n${2}\""
  }
}
```

微信中发送 `/uid` 可获取当前联系人的 `user_id`。

## 管理与升级

LuCI 页面提供安装/重建、启动、停止、重启、升级镜像和日志查看。也可以通过 SSH 执行：

```sh
/usr/libexec/istorec/wechat-bridge.sh status
/usr/libexec/istorec/wechat-bridge.sh logs
/usr/libexec/istorec/wechat-bridge.sh restart
/usr/libexec/istorec/wechat-bridge.sh upgrade
```

升级镜像会执行 `docker pull` 并重建 `wechat-bridge` 容器，数据目录保持不变。

## 卸载

通过 iStore 或 `opkg remove luci-app-wechat-bridge` 卸载时，会删除 `wechat-bridge` 容器，但保留数据目录。需要彻底清理时，再手动删除 LuCI 页面中配置的 `数据目录`。

## 安全提示

- 公网暴露 Web UI 或 `/api/send` 前必须设置 `API_TOKEN`。
- 推荐使用 HTTPS、反向代理鉴权或 VPN，不建议直接把 `5200` 端口裸露到公网。
- 不要把 `API_TOKEN` 写入公开仓库、公开日志或可被其他用户读取的脚本。
- 固定生产环境版本时，把镜像 Tag 从 `latest` 改为明确版本号。
