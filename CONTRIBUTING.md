# 贡献指南

先说声谢谢。这个项目的目标是**保持零依赖、只跑本机、默认克制**。任何改动都请守住这三条。

## 环境

- Windows 10 / 11
- Python 3.10+（用了 `dict[str, ...]` 和 `str | None` 这类语法）
- **不需要 `pip install` 任何东西**——这是硬性约束，请不要引入第三方依赖

## 跑起来

```powershell
python wordbook.py            # 会弹黑窗口 + 浏览器
python wordbook.py --version  # 看版本
```

## 跑测试

```powershell
python -m unittest discover -s tests -v
```

测试是纯离线的：不查词典、不起网络。新增功能请顺带补测试，CI 会在 Windows 上跑 3.10 / 3.11 / 3.12 三档。

## 代码约定

- 只用标准库。新增依赖的 PR 会被拒。
- 涉及用户数据的改动（写词表、撤销、导出）必须**宁可失败也不误删**。
- 前端不拼 `innerHTML`，词典内容一律 `textContent`。
- 中文注释可以，变量名用英文。
- 提交前请自查：
  - `python -m unittest discover -s tests -v` 全绿
  - 不要提交 `词表.md`、`config.json`、`port.txt`、`__pycache__/`

## 提交信息

```
<类型>: <一句话>

feat: 支持自定义模板占位符带空格
fix: 撤销前校验文件未被改动，避免误删手动追加的词
docs: 补充 port.txt 说明
```

类型用 `feat` / `fix` / `docs` / `refactor` / `test` / `chore`。

## PR

- 说清楚「改了什么 / 为什么 / 怎么验的」
- 涉及界面改动的，附一张截图
- 涉及词典解析的，附一个真实返回片段（脱敏）

## 不要做的事

- 不要打包或分发词典数据（项目只做实时查询）
- 不要提高对上游接口的请求频率，不要绕过限流
- 不要引入遥测、统计、外部上报
## 构建 Windows 安装包

在 Windows 上准备 x64 Python 和 Inno Setup 7.1.1，在仓库根目录运行：

```powershell
python -m pip install pyinstaller==6.22.3
.\packaging\build.ps1 -Iscc 'C:\path\to\ISCC.exe'
```

安装包输出到 `build/installer/Wordbook-Setup-x64.exe`。GitHub Actions 的 **Windows installer** 工作流可手动构建供测试的安装包。推送与程序版本一致的标签后，它会将安装包附到 Release 草稿，审核后可先作为预发行版发布。
