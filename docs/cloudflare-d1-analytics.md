# Cloudflare D1 匿名统计部署

WeChat Bridge 的匿名使用统计和下载计数使用 Cloudflare D1。Workers KV 仅用于缓存 GitHub 最新版本响应，避免匿名统计消耗 KV 免费套餐每天 1,000 次写入额度。

## 1. 创建数据库

在 Cloudflare Dashboard 中打开 **Storage & Databases → D1 SQL Database**，创建数据库：

```text
wechat-bridge-analytics
```

打开数据库的 **Console**，执行 [`migrations/0001_d1_analytics.sql`](../migrations/0001_d1_analytics.sql) 中的 SQL。

也可以使用 Wrangler：

```bash
npx wrangler d1 create wechat-bridge-analytics
npx wrangler d1 execute wechat-bridge-analytics --remote --file=migrations/0001_d1_analytics.sql
```

## 2. 绑定数据库

打开部署 `wb.yuuou.qzz.io` 的 Worker，进入 **Settings → Bindings → Add binding → D1 database**：

- Variable name：`ANALYTICS_DB`
- D1 database：`wechat-bridge-analytics`

原有 KV 绑定 `COUNTER` 可以保留，但现在只用于 `github:latest_release` 缓存。

## 3. 配置统计访问令牌

在 Worker 的 **Settings → Variables and Secrets** 中配置 Secret：

```text
STATS_TOKEN=<随机生成的高强度令牌>
```

读取统计时优先使用请求头，避免令牌出现在 URL、浏览器历史和代理日志中：

```bash
curl -H "Authorization: Bearer $STATS_TOKEN" https://wb.yuuou.qzz.io/stats
```

## 4. 配置数据清理

在 Worker 的 **Triggers → Cron Triggers** 中添加每日触发器，例如：

```cron
17 2 * * *
```

Worker 的 `scheduled` 处理器会删除 180 天以前的心跳、事件、兼容遥测和请求计数。D1 不支持 KV 的 `expirationTtl`，因此 Cron Trigger 是保留周期的一部分。

## 5. 部署和验证

1. 先创建并初始化 D1，再绑定 `ANALYTICS_DB`。
2. 将 [`docs/assets/cf-worker-dl-proxy.js`](assets/cf-worker-dl-proxy.js) 部署到 Worker。
3. 启动一个启用了匿名统计的 WeChat Bridge 实例。
4. 在 D1 Console 中检查：

   ```sql
   SELECT * FROM telemetry_daily ORDER BY created_at DESC LIMIT 5;
   SELECT * FROM telemetry_events ORDER BY created_at DESC LIMIT 10;
   SELECT * FROM daily_counters ORDER BY day DESC, counter_type;
   ```

5. 请求 `/stats`，确认 `daily`、`active_instances` 和 `weekly_active_instances` 有数据。

如果 D1 未绑定或写入失败，`POST /telemetry` 会返回 `503`，客户端不会把首次观测、安装成功或升级事件误标记为已送达。版本检查和下载本身仍会继续工作，只是对应计数不会写入。

## 旧 KV 数据

部署后不再向 KV 写入匿名统计或请求计数。旧的 `raw:*`、`v2:*`、`ts:*`、`ping:*` 和 `dl:*` Key 不需要立即删除，可以等待原有 TTL 自然过期。切换后的 `/stats` 只读取 D1，因此不会重复计算 KV 和 D1 数据。
