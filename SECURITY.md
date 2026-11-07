# 安全策略

## 它在本地做了什么

wordbook 是一个**只监听 `127.0.0.1`** 的本地 HTTP 服务，用浏览器当界面。它不对外网开放，不上报任何数据。

已知的攻击面与现有防护：

| 面 | 防护 |
|---|---|
| 跨站请求（CSRF） | POST 接口校验 `Sec-Fetch-Site` 与 `Origin`，不匹配返回 403 |
| DNS rebinding | `Host` 头必须字面是 `127.0.0.1:<port>` / `localhost:<port>` / `[::1]:<port>`，否则 403 |
| 端口抢占 | 监听端设 `SO_EXCLUSIVEADDRUSE`，第二个实例绑定会失败（Windows） |
| 输入注入 | 单词经 `WORD_RE` 白名单校验；前端全部 `textContent`，不拼 HTML |
| CSV 公式注入 | 导出时对 `= + - @` 开头的单元格加 `'` 前缀 |
| 数据误删 | 撤销前校验「文件长度 + 文件尾部」与写入记录一致，不一致就拒绝 |

## 不在防护范围内的

- **本机其他进程**：同机进程可以直接读写 `词表.md` 和 `%LOCALAPPDATA%\Wordbook\`。这不是本项目能防的。
- **词典上游**：单词会发送到有道 / dictionaryapi.dev。不要在词表里放敏感内容——虽然词表本身不出本机，但**查询的单词**会离开。
- **浏览器扩展**：能读你页面内容的扩展不在防护范围内。

## 报告漏洞

请**不要**开公开 Issue。用 GitHub 的 [Private vulnerability reporting](https://github.com/MSaknussemm/wordbook/security/advisories/new)，
或在 Issue 里只说"有安全问题，请给个联系方式"。

请附上：

- 复现步骤（越具体越好）
- 影响范围（能读到数据？能删数据？只能关服务？）
- 你的 Windows 版本、Python 版本、浏览器版本

一般 72 小时内回复。修好之后会在 Release notes 里致谢（除非你想匿名）。

## 支持范围

只对**最新 Release** 提供安全修复。旧版本请先升级。
