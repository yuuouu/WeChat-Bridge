# Plugin Development

WeChat Bridge 插件用于把微信消息接入外部工具、命令和自动化流程。插件运行在 Bridge 进程内，可以直接通过 `send_reply()` 回写微信。

## 加载路径

启动时只扫描：

- `examples/`

支持两种形式：

- 目录下的单文件插件：`my_plugin.py`
- 带 manifest 的插件目录：`examples/my-plugin/plugin.json`

一键脚本升级采用覆盖式更新：上游新增文件会添加，同名文件会覆盖，用户自己放在 `examples/` 中且不同名的旧文件不会被删除。Git 部署遵循 `git pull` 自身规则。

## 最小插件

```python
from plugin_base import Plugin


class EchoPlugin(Plugin):
    name = "echo"
    description = "回显文本"
    commands = ["/echo"]

    def handle(self, payload):
        args = payload.get("args", "")
        self.send_reply(payload["from_user"], args or "(empty)")


PLUGIN_CLASS = EchoPlugin
```

## 生命周期与配置

插件可以按需覆盖：

- `configure(config)`：读取 `plugin.json` 的 `config`
- `on_start()`：插件启动
- `on_stop()`：插件停止
- `on_message(event)`：收到所有入站消息
- `handle(payload)`：收到已路由给本插件的命令
- `has_session(user_id)`：声明是否持有某个用户的会话
- `on_error(exc, context)`：统一处理插件异常

`handle(payload)` 常用字段：

| 字段 | 说明 |
| --- | --- |
| `from_user` | 发送者 user_id |
| `from_name` | 联系人名称 |
| `text` | 原始文本 |
| `command` | 命令名，例如 `/weather` |
| `args` | 命令参数 |
| `bot_id` | 当前 Bot 标识 |

## Manifest 示例

```json
{
  "enabled": true,
  "entry": "plugin.py",
  "config": {
    "api_base": "https://example.com"
  }
}
```

插件内读取：

```python
def configure(self, config):
    super().configure(config)
    self.api_base = self.config.get("api_base", "")
```

## 错误处理

插件启动、停止和命令执行异常会被捕获并写入服务端日志。命令插件异常不会把 Python 异常原文回传给微信用户，用户只会收到通用失败提示。

## 安全要求

- 不要在插件文件中硬编码 token、密码、内网地址或个人路径。
- 需要调用外部服务时，优先使用环境变量或 `plugin.json` 配置。
- 对公网插件接口设置鉴权。
- 对长耗时任务使用线程或队列，避免阻塞消息处理。
