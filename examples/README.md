# Examples

本目录只保留通用、脱敏、可直接学习的插件示例。个人内网地址、私有服务命令、交易/资产/家庭自动化脚本不应提交到这里。

## 示例列表

| 文件 | 说明 |
| --- | --- |
| `webhook_receiver.py` | 无状态命令插件，包含 `/天气 城市` 和 `/echo 文本` 示例 |
| `session_notes.py` | 有状态会话插件，演示 `/note` 开始、普通消息收集、`/exit` 结束 |
| `forwarder.py` | 入站消息转发插件，通过 `FORWARD_URLS` 指定一个或多个目标 URL |
| `bridge_code_agent.py` | 通过微信远程驱动 Gemini / Claude Code / Codex 等 AI CLI |

## 使用方式

默认启动时，WeChat Bridge 只扫描 `examples/` 中声明了 `PLUGIN_CLASS` 的 Python 文件。

自定义插件可以直接放在 `examples/`：

```text
examples/my_plugin.py
examples/my_plugin/
├── plugin.json
└── plugin.py
```

一键脚本升级采用覆盖式更新：上游新增文件会添加，同名文件会覆盖，用户自己放在 `examples/` 中且不同名的旧文件不会被删除。Git 部署遵循 `git pull` 自身规则。

## 天气数据源

`webhook_receiver.py` 的天气命令为 `/天气 城市`，默认城市是紫金。默认 `WEATHER_PROVIDER=auto`，未配置第三方密钥时使用 Open-Meteo，clone 后无需申请 API 即可运行；配置 QWeather 凭证后会自动切换到 QWeather，以获得更适合中国境内城市的实时数据和分钟级降水能力。

可选配置：

```bash
# 默认 auto：有 QWeather 凭证则用 QWeather，否则用 Open-Meteo
WEATHER_PROVIDER=auto

# 强制使用 Open-Meteo，零密钥
WEATHER_PROVIDER=open-meteo

# 强制使用 QWeather，需要配置以下任一凭证
WEATHER_PROVIDER=qweather
```

QWeather 增强模式至少配置以下一种鉴权方式：

```bash
QWEATHER_API_KEY=your_api_key
# 或
QWEATHER_JWT=your_jwt
```

如果你使用和风天气控制台分配的专属 API Host，可以额外配置：

```bash
QWEATHER_API_HOST=abcxyz.qweatherapi.com
QWEATHER_GEO_API_HOST=geoapi.qweather.com
```

## 插件安全

插件可以读取消息内容并调用外部服务。公开仓库中的示例必须使用占位配置或环境变量，不要写入真实 token、内网地址、微信 ID、个人路径或生产业务逻辑。
