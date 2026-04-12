# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x     | ✅ 受支持            |

## Reporting a Vulnerability

如果你发现了安全漏洞，请 **不要** 通过公开的 Issue 提交。

### 报告方式

1. 发送邮件到维护者（通过 GitHub profile 获取联系方式）
2. 或使用 [GitHub Security Advisories](https://github.com/yuuouu/wechat-bridge/security/advisories) 私密报告

### 报告内容

请包含以下信息：

- 漏洞描述
- 复现步骤
- 影响范围评估
- 修复建议（如果有）

### 响应时间

- **确认收到**: 48 小时内
- **初步评估**: 5 个工作日内
- **修复发布**: 视严重程度而定，严重漏洞将尽快发布补丁

## 安全最佳实践

使用 WeChat Bridge 时，请注意以下安全事项：

1. **务必设置 `API_TOKEN`**：暴露到网络时，未设置 Token 意味着任何人都能通过你的微信发消息
2. **不要暴露到公网**：建议仅在内网使用，如需公网访问请配合反向代理 + HTTPS
3. **定期更新**：关注版本更新，及时应用安全补丁
4. **数据安全**：`/data` 目录包含登录凭证和消息记录，请确保适当的文件权限
