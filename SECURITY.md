# Security Policy

## Supported Versions

| Version | Supported |
| --- | --- |
| 1.x | Supported |

## Reporting a Vulnerability

Please do not report security issues through public GitHub Issues.

Use GitHub Security Advisories or contact the maintainer through the public GitHub profile. Include the affected version, deployment mode, reproduction steps, expected impact, and any suggested mitigation.

## Deployment Security Checklist

WeChat Bridge can send messages through your WeChat account. Treat the HTTP API as a privileged control plane.

- Set `API_TOKEN` before exposing the service outside localhost or a trusted LAN.
- Prefer HTTPS through a reverse proxy when the service is reachable from other networks.
- Avoid placing tokens in URLs. `Authorization: Bearer <TOKEN>` is preferred over `?token=<TOKEN>` because URLs are commonly stored in logs, browser history, and proxy analytics.
- Do not expose `/data` or backup archives. They contain login credentials, message history, contacts, and runtime configuration.
- Restrict access with firewall rules, reverse-proxy authentication, IP allowlists, VPN, or Cloudflare Access when deployed on the public internet.
- Keep `docker-compose.yml` minimal for public deployments. Put personal mounts, private network addresses, AI CLI credentials, and proxy settings in an untracked override file.
- Rotate `API_TOKEN` immediately if it appears in logs, screenshots, chat history, shell history, or a committed file.
- Back up `/data` securely before upgrading, and validate that backups are not publicly downloadable.
- Review plugins before enabling them. Plugins can receive message content and may call external services.

## Current Code Behavior

- Browser login cookies are signed and set with `HttpOnly` and `SameSite=Strict`.
- API token checks use constant-time comparison to reduce timing side channels.
- Unexpected server errors are logged server-side and returned to clients as a generic `Internal server error`.
- Query-string token auth remains supported for simple integrations, but header auth is recommended for network-facing deployments.
