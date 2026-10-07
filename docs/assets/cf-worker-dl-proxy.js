/**
 * WeChat Bridge — Cloudflare Worker（版本检查 + 下载加速 + 匿名统计）
 *
 * 功能：
 *   /              → 版本检查代理（缓存 GitHub API 10 分钟）
 *   /install.sh    → Linux/macOS 安装脚本代理
 *   /install.ps1   → Windows 安装脚本代理
 *   /archive/main.tar.gz → 源码压缩包代理 (tar.gz)
 *   /archive/main.zip    → 源码压缩包代理 (zip, Windows)
 *   /stats         → 统计面板（JSON 格式，最近 7 天）
 *
 * 隐私声明：
 *   - 记录每日版本检查、安装脚本和源码包请求次数
 *   - 不记录 IP 地址、User-Agent、请求参数等任何可识别信息
 *   - 当客户端启用 TELEMETRY_ENABLED=1 时，额外接收匿名技术指标
 *     （轮换的日/周匿名标识、版本号、操作系统、部署方式等）
 *   - 原始随机安装 ID 只保存在客户端，不上传，也不收集个人或聊天信息
 *   - 匿名统计保存在 D1，180 天后由每日 Cron Trigger 清理
 *   - Workers KV 仅用于 GitHub 最新版本的短期缓存
 *   - 源代码完全公开，欢迎审计
 *
 * 部署步骤：
 *   1. CF Dashboard → Workers 和 Pages → 创建 Worker
 *   2. 粘贴本代码 → 保存并部署
 *   3. 设置 → 绑定 → D1 数据库 → 变量名 ANALYTICS_DB
 *   4. 在 D1 Console 执行 migrations/0001_d1_analytics.sql
 *   5. 保留可选 KV 绑定 COUNTER，仅用于版本响应缓存
 *
 * 源码仓库: https://github.com/yuuouu/WeChat-Bridge
 */

const REPO = 'yuuouu/WeChat-Bridge';
const GITHUB_RAW = `https://raw.githubusercontent.com/${REPO}/main`;
const GITHUB_ARCHIVE = `https://github.com/${REPO}/archive/refs/heads/main.tar.gz`;
const GITHUB_ARCHIVE_ZIP = `https://github.com/${REPO}/archive/refs/heads/main.zip`;
const GITHUB_API = `https://api.github.com/repos/${REPO}/releases/latest`;
const CACHE_TTL = 600;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (request.method === 'OPTIONS') {
      return new Response('', {
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET, POST',
        },
      });
    }

    // ── /stats ── 统计面板
    // Token 通过 CF Dashboard → wb Worker → 设置 → 变量和机密 → STATS_TOKEN 配置
    if (path === '/stats') {
      if (!env.STATS_TOKEN) {
        return new Response('STATS_TOKEN is not configured', { status: 503 });
      }
      const authorization = request.headers.get('Authorization') || '';
      const suppliedToken = authorization.startsWith('Bearer ')
        ? authorization.slice(7)
        : url.searchParams.get('token'); // Backward compatibility for existing bookmarks.
      if (suppliedToken !== env.STATS_TOKEN) {
        return new Response('Forbidden', { status: 403 });
      }
      return handleStats(env);
    }

    // ── /install.sh ── 安装脚本代理
    if (path === '/install.sh') {
      return proxyInstallScript(env, url, 'sh');
    }

    // ── /install.ps1 ── Windows 安装脚本代理
    if (path === '/install.ps1') {
      return proxyInstallScript(env, url, 'ps1');
    }

    // ── /archive/main.tar.gz ── 源码包代理（24h 边缘缓存）
    if (path === '/archive/main.tar.gz') {
      return fetchAndCacheArchive(request, env, ctx, GITHUB_ARCHIVE, 'application/gzip', 'wechat-bridge.tar.gz');
    }

    // ── /archive/main.zip ── 源码包代理（24h 边缘缓存）
    if (path === '/archive/main.zip') {
      return fetchAndCacheArchive(request, env, ctx, GITHUB_ARCHIVE_ZIP, 'application/zip', 'wechat-bridge.zip');
    }

    // ── POST /telemetry ── 可选的匿名技术指标上报
    if (path === '/telemetry' && request.method === 'POST') {
      return handleTelemetry(request, env);
    }

    // ── / ── 版本检查（默认路径）
    if (request.method !== 'GET') {
      return new Response('Method Not Allowed', { status: 405 });
    }
    await bump(env, 'ping');

    // 读缓存
    if (env.COUNTER) {
      try {
        const cached = await env.COUNTER.get('github:latest_release');
        if (cached) {
          return new Response(cached, {
            headers: { 'Content-Type': 'application/json', 'X-Cache': 'HIT', 'Access-Control-Allow-Origin': '*' },
          });
        }
      } catch (e) {}
    }

    // 请求 GitHub API
    try {
      const resp = await fetch(GITHUB_API, {
        headers: { 'User-Agent': 'WB-Proxy', 'Accept': 'application/vnd.github.v3+json' },
      });
      if (resp.status === 200) {
        const json = await resp.json();
        const slim = JSON.stringify({ tag_name: json.tag_name, published_at: json.published_at, body: json.body });
        if (env.COUNTER) {
          try { await env.COUNTER.put('github:latest_release', slim, { expirationTtl: CACHE_TTL }); } catch (e) {}
        }
        return new Response(slim, {
          headers: { 'Content-Type': 'application/json', 'X-Cache': 'MISS', 'Access-Control-Allow-Origin': '*' },
        });
      }
      const errBody = await resp.text();
      return new Response(errBody, {
        status: resp.status,
        headers: { 'Content-Type': 'application/json', 'X-Cache': 'MISS', 'Access-Control-Allow-Origin': '*' },
      });
    } catch (e) {
      return new Response(JSON.stringify({ error: e.message }), {
        status: 502,
        headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
      });
    }
  },

  async scheduled(_controller, env, ctx) {
    if (env.ANALYTICS_DB) {
      ctx.waitUntil(cleanupOldData(env));
    }
  },
};

// ── 匿名遥测处理 ──
// 仅接受白名单字段，丢弃一切未知数据。
// v2 心跳以 (day, daily_id) 主键去重，weekly_id 用于精确 WAU。
const V2_DIMENSION_FIELDS = [
  'v', 'os', 'arch', 'py', 'mode', 'uptime_bucket', 'accounts_bucket',
  'plugins_bucket', 'ai_provider', 'webhook_enabled',
];
const V2_EVENTS = new Set(['heartbeat', 'process_start', 'first_seen', 'install_success', 'upgrade']);
const FEATURE_VALUES = new Set(['ai', 'webhook', 'plugins', 'multi_account', 'docker']);
const LEGACY_FIELDS = [
  'v', 'prev_v', 'os', 'arch', 'py', 'mode', 'uptime_days', 'accounts',
  'ai_provider', 'plugins_count', 'webhook_enabled',
];

function safeValue(value, maxLength = 30) {
  return String(value || '').replace(/[^a-zA-Z0-9._+\-]/g, '').slice(0, maxLength);
}

function validId(value) {
  return /^[a-f0-9]{32}$/.test(String(value || ''));
}

function utcDay(offset = 0) {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
}

function utcWeek() {
  const date = new Date();
  const day = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1));
  const week = Math.ceil((((date - yearStart) / 86400000) + 1) / 7);
  return `${date.getUTCFullYear()}-W${String(week).padStart(2, '0')}`;
}

function v2Dimensions(data) {
  const dimensions = {};
  for (const field of V2_DIMENSION_FIELDS) {
    const value = safeValue(data[field]);
    if (value) dimensions[field] = value;
  }
  dimensions.features = Array.isArray(data.features)
    ? [...new Set(data.features.map((value) => safeValue(value)).filter((value) => FEATURE_VALUES.has(value)))]
    : [];
  return dimensions;
}

async function handleTelemetry(request, env) {
  if (!env.ANALYTICS_DB) {
    return jsonResponse({ ok: false, error: 'D1 not bound' }, 503);
  }

  try {
    const contentLength = parseInt(request.headers.get('Content-Length') || '0');
    if (contentLength > 8192) return jsonResponse({ ok: false, error: 'Payload Too Large' }, 413);
    const data = await request.json();

    if (data.schema === 2) {
      const event = safeValue(data.event);
      if (!V2_EVENTS.has(event)) return jsonResponse({ ok: false, error: 'Invalid event' }, 400);
      const day = utcDay();
      const week = utcWeek();
      const dimensions = v2Dimensions(data);

      if (event === 'heartbeat') {
        if (!validId(data.daily_id) || !validId(data.weekly_id)) {
          return jsonResponse({ ok: false, error: 'Invalid anonymous id' }, 400);
        }
        await env.ANALYTICS_DB.prepare(`
          INSERT INTO telemetry_daily (
            day, daily_id, week, weekly_id, version, os, arch, python_version,
            deploy_mode, uptime_bucket, accounts_bucket, plugins_bucket,
            ai_provider, webhook_enabled, features
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT(day, daily_id) DO NOTHING
        `).bind(
          day,
          data.daily_id,
          week,
          data.weekly_id,
          dimensions.v || null,
          dimensions.os || null,
          dimensions.arch || null,
          dimensions.py || null,
          dimensions.mode || null,
          dimensions.uptime_bucket || null,
          dimensions.accounts_bucket || null,
          dimensions.plugins_bucket || null,
          dimensions.ai_provider || null,
          dimensions.webhook_enabled || null,
          JSON.stringify(dimensions.features),
        ).run();
      } else {
        if (!validId(data.event_id)) return jsonResponse({ ok: false, error: 'Invalid event id' }, 400);
        const details = { ...dimensions };
        if (event === 'install_success') details.install_mode = safeValue(data.install_mode);
        if (event === 'upgrade') {
          details.from_v = safeValue(data.from_v);
          details.to_v = safeValue(data.to_v);
        }
        await env.ANALYTICS_DB.prepare(`
          INSERT INTO telemetry_events (event_type, event_id, day, details)
          VALUES (?, ?, ?, ?)
          ON CONFLICT(event_type, event_id) DO NOTHING
        `).bind(event, data.event_id, day, JSON.stringify(details)).run();
      }
      return jsonResponse({ ok: true });
    }

    // 兼容旧客户端：每次上报保存一行，避免每个维度各写一次。
    const legacy = {};
    for (const field of LEGACY_FIELDS) legacy[field] = safeValue(data[field]);
    const features = Array.isArray(data.features)
      ? [...new Set(data.features.map((value) => safeValue(value)).filter((value) => FEATURE_VALUES.has(value)))]
      : [];
    if (!Object.values(legacy).some(Boolean) && features.length === 0) {
      return jsonResponse({ ok: false, error: 'Invalid legacy telemetry' }, 400);
    }
    await env.ANALYTICS_DB.prepare(`
      INSERT INTO legacy_telemetry (
        event_id, day, version, previous_version, os, arch, python_version,
        deploy_mode, uptime_days, accounts, ai_provider, plugins_count,
        webhook_enabled, features
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).bind(
      crypto.randomUUID().replaceAll('-', ''),
      utcDay(),
      legacy.v || null,
      legacy.prev_v || null,
      legacy.os || null,
      legacy.arch || null,
      legacy.py || null,
      legacy.mode || null,
      legacy.uptime_days || null,
      legacy.accounts || null,
      legacy.ai_provider || null,
      legacy.plugins_count || null,
      legacy.webhook_enabled || null,
      JSON.stringify(features),
    ).run();
    return jsonResponse({ ok: true });
  } catch (error) {
    console.error('telemetry persistence failed', error);
    return jsonResponse({ ok: false, error: 'persistence_failed' }, 503);
  }
}

function jsonResponse(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
  });
}

// ── 安装脚本代理（5 分钟上游边缘缓存）──
async function proxyInstallScript(env, url, ext) {
  await bump(env, `dl:install:${ext}`);
  const resp = await fetch(`${GITHUB_RAW}/scripts/install.${ext}`, {
    headers: { 'User-Agent': 'WB-Proxy' },
    cf: { cacheTtl: 300 },
  });
  if (!resp.ok) return new Response('fetch failed', { status: resp.status });
  let script = await resp.text();
  script = script.replaceAll(
    `https://raw.githubusercontent.com/${REPO}/main/scripts/install.${ext}`,
    url.origin + `/install.${ext}`
  );
  const archiveName = ext === 'ps1' ? 'main.zip' : 'main.tar.gz';
  script = script.replaceAll(
    `https://github.com/${REPO}/archive/refs/heads/${archiveName}`,
    url.origin + `/archive/${archiveName}`
  );
  return new Response(script, {
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
      'Cache-Control': 'public, max-age=300',
      'Access-Control-Allow-Origin': '*',
    },
  });
}

// ── 源码包 24h 边缘缓存 ──
async function fetchAndCacheArchive(request, env, ctx, archiveUrl, contentType, filename) {
  const cache = caches.default;
  const cacheKey = new Request(new URL(request.url).toString(), { method: 'GET' });

  // 检查边缘缓存
  const cached = await cache.match(cacheKey);
  if (cached) {
    await bump(env, 'dl:archive');
    const headers = new Headers(cached.headers);
    headers.set('X-Cache', 'HIT');
    return new Response(cached.body, { headers });
  }

  // 缓存未命中，回源 GitHub
  await bump(env, 'dl:archive');
  const resp = await fetch(archiveUrl, {
    headers: { 'User-Agent': 'WB-Proxy' },
    redirect: 'follow',
  });
  if (!resp.ok) return new Response('fetch failed', { status: resp.status });

  const response = new Response(resp.body, {
    headers: {
      'Content-Type': contentType,
      'Content-Disposition': `attachment; filename="${filename}"`,
      'Cache-Control': 'public, max-age=86400',
      'X-Cache': 'MISS',
    },
  });

  // 异步写入边缘缓存，不阻塞响应
  ctx.waitUntil(cache.put(cacheKey, response.clone()));
  return response;
}

// ── 计数器 ──
// D1 的 UPSERT 是原子计数，避免 KV 读-改-写的并发丢数和每日 1,000 次写入限额。
async function bump(env, prefix) {
  if (!env.ANALYTICS_DB) return;
  try {
    await env.ANALYTICS_DB.prepare(`
      INSERT INTO daily_counters (day, counter_type, total)
      VALUES (?, ?, 1)
      ON CONFLICT(day, counter_type)
      DO UPDATE SET total = total + 1
    `).bind(utcDay(), prefix).run();
  } catch (error) {
    console.error('counter persistence failed', prefix, error);
  }
}

function statsDay() {
  return {
    version_check: 0,
    install_sh: 0,
    install_ps1: 0,
    archive: 0,
    active_instances: 0,
    process_starts: 0,
    first_seen: 0,
    install_success: 0,
    upgrades: 0,
    dimensions: {},
  };
}

// ── 统计面板 ──
async function handleStats(env) {
  if (!env.ANALYTICS_DB) {
    return jsonResponse({ error: 'D1 not bound' }, 503);
  }

  const daily = {};
  for (let i = 0; i < 7; i++) {
    daily[utcDay(-i)] = statsDay();
  }

  try {
    const cutoff = utcDay(-6);
    const dimensionQuery = `
      WITH recent AS (SELECT * FROM telemetry_daily WHERE day >= ?)
      SELECT day, 'v' AS field, version AS value, COUNT(*) AS total
        FROM recent WHERE version IS NOT NULL GROUP BY day, version
      UNION ALL
      SELECT day, 'os', os, COUNT(*) FROM recent WHERE os IS NOT NULL GROUP BY day, os
      UNION ALL
      SELECT day, 'arch', arch, COUNT(*) FROM recent WHERE arch IS NOT NULL GROUP BY day, arch
      UNION ALL
      SELECT day, 'py', python_version, COUNT(*) FROM recent
        WHERE python_version IS NOT NULL GROUP BY day, python_version
      UNION ALL
      SELECT day, 'mode', deploy_mode, COUNT(*) FROM recent
        WHERE deploy_mode IS NOT NULL GROUP BY day, deploy_mode
      UNION ALL
      SELECT day, 'uptime_bucket', uptime_bucket, COUNT(*) FROM recent
        WHERE uptime_bucket IS NOT NULL GROUP BY day, uptime_bucket
      UNION ALL
      SELECT day, 'accounts_bucket', accounts_bucket, COUNT(*) FROM recent
        WHERE accounts_bucket IS NOT NULL GROUP BY day, accounts_bucket
      UNION ALL
      SELECT day, 'plugins_bucket', plugins_bucket, COUNT(*) FROM recent
        WHERE plugins_bucket IS NOT NULL GROUP BY day, plugins_bucket
      UNION ALL
      SELECT day, 'ai_provider', ai_provider, COUNT(*) FROM recent
        WHERE ai_provider IS NOT NULL GROUP BY day, ai_provider
      UNION ALL
      SELECT day, 'webhook_enabled', webhook_enabled, COUNT(*) FROM recent
        WHERE webhook_enabled IS NOT NULL GROUP BY day, webhook_enabled
      UNION ALL
      SELECT day, 'features', json_each.value, COUNT(*) FROM recent, json_each(recent.features)
        GROUP BY day, json_each.value
    `;
    const results = await env.ANALYTICS_DB.batch([
      env.ANALYTICS_DB.prepare(`
        SELECT day, counter_type, total FROM daily_counters WHERE day >= ?
      `).bind(cutoff),
      env.ANALYTICS_DB.prepare(`
        SELECT day, COUNT(*) AS total FROM telemetry_daily WHERE day >= ? GROUP BY day
      `).bind(cutoff),
      env.ANALYTICS_DB.prepare(`
        SELECT day, event_type, COUNT(*) AS total FROM telemetry_events
        WHERE day >= ? GROUP BY day, event_type
      `).bind(cutoff),
      env.ANALYTICS_DB.prepare(dimensionQuery).bind(cutoff),
      env.ANALYTICS_DB.prepare(`
        SELECT COUNT(DISTINCT weekly_id) AS total FROM telemetry_daily WHERE week = ?
      `).bind(utcWeek()),
    ]);

    const counterNames = {
      ping: 'version_check',
      'dl:install:sh': 'install_sh',
      'dl:install:ps1': 'install_ps1',
      'dl:archive': 'archive',
    };
    for (const row of results[0].results || []) {
      if (daily[row.day] && counterNames[row.counter_type]) {
        daily[row.day][counterNames[row.counter_type]] = Number(row.total) || 0;
      }
    }
    for (const row of results[1].results || []) {
      if (daily[row.day]) daily[row.day].active_instances = Number(row.total) || 0;
    }
    const eventNames = {
      process_start: 'process_starts',
      first_seen: 'first_seen',
      install_success: 'install_success',
      upgrade: 'upgrades',
    };
    for (const row of results[2].results || []) {
      if (daily[row.day] && eventNames[row.event_type]) {
        daily[row.day][eventNames[row.event_type]] = Number(row.total) || 0;
      }
    }
    for (const row of results[3].results || []) {
      if (!daily[row.day] || !row.field || !row.value) continue;
      if (!daily[row.day].dimensions[row.field]) daily[row.day].dimensions[row.field] = {};
      daily[row.day].dimensions[row.field][row.value] = Number(row.total) || 0;
    }
    const weeklyActiveInstances = Number(results[4].results?.[0]?.total) || 0;
    const telemetry = await readLegacyTelemetry(env);
    return statsResponse(daily, weeklyActiveInstances, telemetry);
  } catch (error) {
    console.error('stats query failed', error);
    return jsonResponse({ error: 'stats_query_failed' }, 503);
  }
}

function statsResponse(daily, weeklyActiveInstances, telemetry) {
  const days = Object.values(daily);
  return new Response(JSON.stringify({
    total_7d: {
      version_check: days.reduce((s, d) => s + d.version_check, 0),
      install_sh:    days.reduce((s, d) => s + d.install_sh, 0),
      install_ps1:   days.reduce((s, d) => s + d.install_ps1, 0),
      archive:       days.reduce((s, d) => s + d.archive, 0),
      active_instance_days: days.reduce((s, d) => s + d.active_instances, 0),
      weekly_active_instances: weeklyActiveInstances,
      process_starts: days.reduce((s, d) => s + d.process_starts, 0),
      first_seen: days.reduce((s, d) => s + d.first_seen, 0),
      install_success: days.reduce((s, d) => s + d.install_success, 0),
      upgrades: days.reduce((s, d) => s + d.upgrades, 0),
    },
    daily,
    telemetry,
  }, null, 2), { headers: { 'Content-Type': 'application/json', 'Cache-Control': 'private, max-age=60' } });
}

async function readLegacyTelemetry(env) {
  const telemetry = {};
  const result = await env.ANALYTICS_DB.prepare(`
    WITH recent AS (SELECT * FROM legacy_telemetry WHERE day >= ?)
    SELECT 'v' AS field, version AS value, COUNT(*) AS total
      FROM recent WHERE version IS NOT NULL GROUP BY version
    UNION ALL
    SELECT 'prev_v', previous_version, COUNT(*) FROM recent
      WHERE previous_version IS NOT NULL GROUP BY previous_version
    UNION ALL
    SELECT 'os', os, COUNT(*) FROM recent WHERE os IS NOT NULL GROUP BY os
    UNION ALL
    SELECT 'arch', arch, COUNT(*) FROM recent WHERE arch IS NOT NULL GROUP BY arch
    UNION ALL
    SELECT 'py', python_version, COUNT(*) FROM recent
      WHERE python_version IS NOT NULL GROUP BY python_version
    UNION ALL
    SELECT 'mode', deploy_mode, COUNT(*) FROM recent
      WHERE deploy_mode IS NOT NULL GROUP BY deploy_mode
    UNION ALL
    SELECT 'uptime_days', uptime_days, COUNT(*) FROM recent
      WHERE uptime_days IS NOT NULL GROUP BY uptime_days
    UNION ALL
    SELECT 'accounts', accounts, COUNT(*) FROM recent
      WHERE accounts IS NOT NULL GROUP BY accounts
    UNION ALL
    SELECT 'ai_provider', ai_provider, COUNT(*) FROM recent
      WHERE ai_provider IS NOT NULL GROUP BY ai_provider
    UNION ALL
    SELECT 'plugins_count', plugins_count, COUNT(*) FROM recent
      WHERE plugins_count IS NOT NULL GROUP BY plugins_count
    UNION ALL
    SELECT 'webhook_enabled', webhook_enabled, COUNT(*) FROM recent
      WHERE webhook_enabled IS NOT NULL GROUP BY webhook_enabled
    UNION ALL
    SELECT 'features', json_each.value, COUNT(*) FROM recent, json_each(recent.features)
      GROUP BY json_each.value
  `).bind(utcDay(-179)).run();
  for (const row of result.results || []) {
    if (!telemetry[row.field]) telemetry[row.field] = {};
    telemetry[row.field][row.value] = Number(row.total) || 0;
  }
  return telemetry;
}

async function cleanupOldData(env) {
  const cutoff = utcDay(-180);
  await env.ANALYTICS_DB.batch([
    env.ANALYTICS_DB.prepare('DELETE FROM telemetry_daily WHERE day < ?').bind(cutoff),
    env.ANALYTICS_DB.prepare('DELETE FROM telemetry_events WHERE day < ?').bind(cutoff),
    env.ANALYTICS_DB.prepare('DELETE FROM legacy_telemetry WHERE day < ?').bind(cutoff),
    env.ANALYTICS_DB.prepare('DELETE FROM daily_counters WHERE day < ?').bind(cutoff),
  ]);
}
