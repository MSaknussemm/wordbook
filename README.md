# 生词本 · Wordbook

> 一个只跑在本机的极简生词本：左边查词，右边两行，点「记下」就追加到桌面的 Markdown。
> A local-first English → Chinese vocabulary notebook for Windows, with zero pip dependencies.

![icon](assets/icon.png)

## 功能

- **左查右显**：输入英文单词或短语，回车；默认只显示「单词 + 词性 + 中文意思」两行
- **双词典源**：优先有道（中文释义），失败或未收录时自动退回 Wiktionary（英文）
- **记录可调**：极简 / 学习卡 / 全量 三档预设；也可逐项勾选字段，或用 `{{word}} {{pos}} {{zh}} ...` 自定义模板
- **不重复**：同一个词只记一次
- **撤销上一条**、**打开词表**、**导出 Anki CSV**（UTF-8 BOM，Excel 不乱码）
- **无黑窗口**：桌面快捷方式 → `pythonw` 后台启动；关掉标签页约 15 秒自动退出
- **端口自适应**：8765 被占会自动顺延，真实端口写进 `%LOCALAPPDATA%\Wordbook\port.txt`，启动器照着打开
- **只监听回环地址**，并对 POST 校验 `Host` / `Origin` / `Sec-Fetch-Site`，挡跨站请求和 DNS rebinding

## 环境

- Windows 10 / 11
- Python 3.9+（安装时保留 `pythonw.exe`；**不需要 pip 安装任何第三方包**）

## 安装

1. 把整个目录放到任意位置（示例：`C:\生词本`）
2. 右键 `启动词本.vbs` → **发送到** → **桌面快捷方式**
3. 想换图标：右键桌面快捷方式 → 属性 → 更改图标 → 选 `生词本.ico`
4. 双击桌面图标

首次启动会自动打开浏览器。词表默认写到 `桌面\词表.md`。

运行时文件：

| 文件 | 作用 |
|---|---|
| `%LOCALAPPDATA%\Wordbook\port.txt` | 当前真实端口 |
| `%LOCALAPPDATA%\Wordbook\config.json` | 记录设置（字段 / 模板） |

## 用法

| 操作 | 说明 |
|---|---|
| 回车 | 查询 |
| `Ctrl + D` | 记下当前词 |
| `↑` / `↓` | 光标不在输入框时，翻「最近查过」 |
| `Esc` | 清空输入框 |
| ⚙ 记录设置 | 选择记录哪些字段 / 自定义模板 |
| 退出程序 | 立即停止后台服务 |

## 目录结构

```
wordbook/
├─ 启动词本.vbs          # 无窗口启动器（桌面快捷方式指向它）
├─ 启动词本(排错用).bat   # 带黑窗口启动，出问题看报错
├─ 停止词本.bat          # 强制关闭后台服务
├─ stop.ps1              # 停止脚本的逻辑本体
├─ wordbook.py           # 后端：查词、解析、写 Markdown、自动退出
├─ index.html            # 前端：左右分栏、查询、记下、设置
├─ 生词本.ico            # 图标
├─ 使用说明.md            # 中文使用说明
└─ assets/icon.png       # README 图标
```

## 它是怎么跑的

```
双击快捷方式
  → 启动词本.vbs 读 %LOCALAPPDATA%\Wordbook\port.txt
  → 没有活着的实例就 pythonw wordbook.py --no-browser
  → 后端把真实端口写回 port.txt
  → 启动器校验 /api/ping 的 app 字段后打开浏览器
  → 页面挂一条 SSE 长连接 + 20 秒心跳
  → 关标签 15 秒 / 心跳停 90 秒 / 启动后 60 秒没人连 → 自动退出
```

只依赖 Python 标准库：`http.server` 提供本地 HTTP 与 SSE，`urllib` 查词典，Markdown 直接追加写入。

## 词典来源与免责声明

- 有道 `dict.youdao.com/jsonapi_s` 是**非官方**公开接口，没有 SLA，可能随时变更或失效；请控制请求频率，仅用于个人学习。
- 英文兜底来自 [dictionaryapi.dev](https://dictionaryapi.dev)（Wiktionary，CC BY-SA）。
- 本项目**不附带任何词典数据**，释义在查询时实时获取。
- 与上述服务无任何关联。

## 隐私

- 服务只监听 `127.0.0.1`，不对局域网/外网开放。
- 查词时，单词会发送到有道 / dictionaryapi.dev；词表内容不会离开本机。
- 词表和设置都是本地纯文本（Markdown / JSON）。

## License

代码以 MIT 许可发布；词典内容版权归各自来源所有。
