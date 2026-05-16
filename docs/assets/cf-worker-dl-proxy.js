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
 *   - 仅记录每日请求次数（ping:YYYY-MM-DD → N）
 *   - 不记录 IP 地址、User-Agent、请求参数等任何可识别信息
 *   - 当客户端启用 TELEMETRY_ENABLED=1 时，额外接收匿名技术指标
 *     （版本号、操作系统、部署方式等），仍不含任何个人或聊天信息
 *   - 所有统计数据 180 天后自动过期
 *   - 源代码完全公开，欢迎审计
 *
 * 部署步骤：
 *   1. CF Dashboard → Workers 和 Pages → 创建 Worker
 *   2. 粘贴本代码 → 保存并部署
 *   3. 设置 → 绑定 → KV 命名空间 → 变量名 COUNTER → 选或创建命名空间
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

    // ── /stats ── 统计面板（需 ?token=yuu）
    if (path === '/stats') {
      if (url.searchParams.get('token') !== 'yuu') {
        return new Response('Forbidden', { status: 403 });
      }
      return handleStats(env);
    }

    // ── /install.sh ── 安装脚本代理
    if (path === '/install.sh') {
      await bump(env, 'dl:install:sh');
      const resp = await fetch(`${GITHUB_RAW}/scripts/install.sh`, {
        headers: { 'User-Agent': 'WB-Proxy' },
      });
      if (!resp.ok) return new Response('fetch failed', { status: resp.status });
      let script = await resp.text();
      script = script.replace(
        /https:\/\/raw\.githubusercontent\.com\/yuuouu\/WeChat-Bridge\/main\/scripts\/install\.sh/g,
        url.origin + '/install.sh'
      );
      script = script.replace(
        `https://github.com/${REPO}/archive/refs/heads/main.tar.gz`,
        url.origin + '/archive/main.tar.gz'
      );
      return new Response(script, {
        headers: {
          'Content-Type': 'text/plain; charset=utf-8',
          'Cache-Control': 'public, max-age=300',
          'Access-Control-Allow-Origin': '*',
        },
      });
    }

    // ── /install.ps1 ── Windows 安装脚本代理
    if (path === '/install.ps1') {
      await bump(env, 'dl:install:ps1');
      const resp = await fetch(`${GITHUB_RAW}/scripts/install.ps1`, {
        headers: { 'User-Agent': 'WB-Proxy' },
      });
      if (!resp.ok) return new Response('fetch failed', { status: resp.status });
      let script = await resp.text();
      script = script.replace(
        /https:\/\/raw\.githubusercontent\.com\/yuuouu\/WeChat-Bridge\/main\/scripts\/install\.ps1/g,
        url.origin + '/install.ps1'
      );
      script = script.replace(
        `https://github.com/${REPO}/archive/refs/heads/main.zip`,
        url.origin + '/archive/main.zip'
      );
      return new Response(script, {
        headers: {
          'Content-Type': 'text/plain; charset=utf-8',
          'Cache-Control': 'public, max-age=300',
          'Access-Control-Allow-Origin': '*',
        },
      });
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
};

// ── 匿名遥测处理 ──
// 仅接受白名单字段，丢弃一切未知数据
const ALLOWED_FIELDS = ['v', 'prev_v', 'os', 'arch', 'py', 'mode', 'features'];

async function handleTelemetry(request, env) {
  if (!env.COUNTER) return new Response('ok', { headers: { 'Access-Control-Allow-Origin': '*' } });
  try {
    const data = await request.json();
    const today = new Date().toISOString().slice(0, 10);

    // 只处理白名单字段，按维度聚合计数
    for (const field of ALLOWED_FIELDS) {
      const val = data[field];
      if (!val) continue;
      // features 是数组，其他是字符串
      const values = Array.isArray(val) ? val : [String(val)];
      for (const v of values) {
        // 防注入：只允许字母数字和少量安全字符
        const safe = String(v).replace(/[^a-zA-Z0-9._\-\/]/g, '').slice(0, 30);
        if (!safe) continue;
        const key = `t:${field}:${safe}:${today}`;
        const cur = parseInt(await env.COUNTER.get(key) || '0');
        await env.COUNTER.put(key, String(cur + 1), { expirationTtl: 180 * 86400 });
      }
    }
  } catch (e) {}
  return new Response('ok', { headers: { 'Access-Control-Allow-Origin': '*' } });
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
// 只写日维度，total 在 /stats 里从 daily 加总，节省 50% KV 写操作
async function bump(env, prefix) {
  if (!env.COUNTER) return;
  try {
    const today = new Date().toISOString().slice(0, 10);
    const key = `${prefix}:${today}`;
    const val = parseInt(await env.COUNTER.get(key) || '0');
    await env.COUNTER.put(key, String(val + 1), { expirationTtl: 180 * 86400 });
  } catch (e) {}
}

// ── 统计面板 ──
async function handleStats(env) {
  if (!env.COUNTER) {
    return new Response(JSON.stringify({ error: 'KV not bound' }), {
      status: 500, headers: { 'Content-Type': 'application/json' },
    });
  }

  const daily = {};
  for (let i = 0; i < 7; i++) {
    const d = new Date(); d.setDate(d.getDate() - i);
    const ds = d.toISOString().slice(0, 10);
    daily[ds] = {
      version_check: parseInt(await env.COUNTER.get(`ping:${ds}`) || '0'),
      install_sh: parseInt(await env.COUNTER.get(`dl:install:sh:${ds}`) || '0'),
      install_ps1: parseInt(await env.COUNTER.get(`dl:install:ps1:${ds}`) || '0'),
      archive: parseInt(await env.COUNTER.get(`dl:archive:${ds}`) || '0'),
    };
  }

  // 读取 telemetry 维度分布（累计，键自动 180 天过期）
  const telemetry = {};
  try {
    const { keys } = await env.COUNTER.list({ prefix: 't:' });
    for (const { name } of keys) {
      const parts = name.split(':');
      if (parts.length < 4) continue;
      const field = parts[1];
      const value = parts[2];
      const count = parseInt(await env.COUNTER.get(name) || '0');
      if (!telemetry[field]) telemetry[field] = {};
      telemetry[field][value] = (telemetry[field][value] || 0) + count;
    }
  } catch (e) {}

  const days = Object.values(daily);
  return new Response(JSON.stringify({
    total_7d: {
      version_check: days.reduce((s, d) => s + d.version_check, 0),
      install_sh:    days.reduce((s, d) => s + d.install_sh, 0),
      install_ps1:   days.reduce((s, d) => s + d.install_ps1, 0),
      archive:       days.reduce((s, d) => s + d.archive, 0),
    },
    daily,
    telemetry,
  }, null, 2), { headers: { 'Content-Type': 'application/json' } });
}
