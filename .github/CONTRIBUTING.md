# Contributing to WeChat Bridge

感谢你有兴趣为 WeChat Bridge 做贡献！以下是参与本项目的指南。

## 🚀 快速开始

### 开发环境搭建

```bash
# 1. Fork 并克隆仓库
git clone https://github.com/<your-username>/wechat-bridge.git
cd wechat-bridge

# 2. 安装 Python 依赖
pip install -r app/requirements.txt

# 3. 启动开发容器
docker compose up -d --build

# 4. 查看日志
docker logs -f wechat-bridge
```

### 项目结构

```
wechat-bridge/
├── app/                  # 核心应用代码
│   ├── main.py           # 入口
│   ├── bridge.py         # 消息桥接逻辑
│   ├── ilink.py          # iLink Bot API 封装
│   ├── web.py            # HTTP API + Web UI
│   ├── ai_chat.py        # AI 对话模块
│   ├── config.py         # 配置管理
│   ├── db.py             # SQLite 持久化
│   ├── media.py          # 媒体文件处理
│   └── requirements.txt  # Python 依赖
├── ag_monitor/           # 独立监控模块（与核心解耦）
├── Dockerfile
├── docker-compose.yml
└── install.sh            # 一键安装脚本
```

## 📋 贡献流程

### 1. 找到要做的事

- 查看 [Issues](https://github.com/yuuouu/wechat-bridge/issues) 列表
- 标有 `good first issue` 的适合新手
- 如果要做大的改动，请先开 Issue 讨论

### 2. 提交代码

1. 从 `main` 分支创建功能分支：`git checkout -b feat/my-feature`
2. 编写代码并确保测试通过
3. 提交时使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式：
   - `feat: 新增 RSS 订阅推送插件`
   - `fix: 修复图片解密失败的问题`
   - `docs: 更新 API 文档`
   - `refactor: 重构配置加载逻辑`
4. 推送并创建 Pull Request

### 3. 代码规范

- Python 代码遵循 PEP 8
- 函数和类必须有 docstring
- 新功能请同步更新 README 中的相关说明
- 不要提交包含个人信息（IP 地址、Token、微信 ID 等）的代码

## 🐛 报告 Bug

请通过 [Issue](https://github.com/yuuouu/wechat-bridge/issues/new) 提交，包含：

1. 问题描述
2. 复现步骤
3. 期望行为 vs 实际行为
4. 环境信息（Docker 版本、系统架构等）
5. 相关日志（`docker logs wechat-bridge`）

## 💡 功能建议

欢迎通过 Issue 提出功能建议，请描述：

1. 你想解决什么问题
2. 建议的实现思路
3. 是否愿意自己实现

## 📄 License

贡献的代码将采用与项目相同的 [MIT License](LICENSE)。
