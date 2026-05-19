---
service: wechat-bridge
date: 26-5-19
severity: medium
rounds: 2
resolved: true
root_cause: 容器退出为 Exit 137，导致宿主机 5200 端口没有服务监听
---

# 26-5-19 容器 Exit 137 导致 WebUI 不可连接恢复

## 现象

访问 `http://100.90.186.98:5200/` 失败，本机 `curl` 返回 `Couldn't connect to server`，HTTP 状态为 `000`。

## 诊断过程

只读检查确认容器不是页面慢，而是已经退出：

```bash
curl -sS -m 8 http://100.90.186.98:5200/
docker ps -a --filter name=wechat-bridge
docker inspect wechat-bridge
docker logs --tail 80 wechat-bridge
```

关键结果：

- `wechat-bridge Exited (137)`，端口映射仍配置为 `0.0.0.0:5200->5200/tcp`。
- 日志最后出现 `收到退出信号，正在关闭...`、`消息轮询循环已停止`、`WeChatBridge 已停止`。
- 未发现应用启动阶段的 Python 异常。

## 根因

`wechat-bridge` 容器被退出信号终止并停留在 `Exited (137)` 状态，导致 5200 端口不可访问。

## 解决方案

用户回复 `确认执行` 后执行低风险恢复：

```bash
docker start wechat-bridge
```

本次没有删除、重建容器，也没有修改网络配置或数据卷。

## 验证

容器状态恢复：

```text
State=running ExitCode=0 RestartCount=0 Health=healthy
wechat-bridge Up 56 seconds (healthy)
```

HTTP 验证：

```text
GET /                  HTTP 200, size=71273
GET /api/status        HTTP 200, logged_in=true, poll_running=true, version=1.2.0
```

浏览器验证：

- 页面标题：`WeChat Bridge`
- 页面可见状态：`已连接`
- 页面内容：`服务启动，等待收发消息...`

## 经验教训

遇到 Web UI 不可连接时，先区分三类问题：端口不可达、容器未运行、应用内部错误。本次属于容器退出，直接 `docker start` 即可恢复；如果短时间内再次 Exit 137，再继续排查内存、宿主机 OOM 和外部停止信号来源。

## 相关链接

- [[_overview]]
- [[services/wechatpush/_overview|WechatPush 概览]]
