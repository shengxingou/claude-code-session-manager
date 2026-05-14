# Claude Code Session Manager

一个面向 macOS 的 Claude Code 会话管理器，提供本地桌面窗口界面，用于浏览 `~/.claude` 下的项目与会话、恢复或分叉历史会话、查看活跃会话，并在 `cmux` 或原生 Terminal.app 中直接启动 Claude Code。

## 功能特性

- 按项目聚合 Claude Code 会话历史
- 显示每个会话的首条消息、时间、消息数量和活跃状态
- 支持继续最近会话、新建会话、恢复会话、分叉会话
- 支持查看所有活跃会话并直接恢复或退出
- 支持跨项目搜索历史对话
- 支持手动添加无历史记录的项目目录
- 支持删除本地会话文件
- 支持 `cmux` 自动执行 Claude Code 命令
- 在未安装 `cmux` 时自动回退到 macOS 原生 Terminal.app
- 支持打包为本地 `.app` 和 `.dmg` 安装文件

## 运行环境

- macOS
- Python 3.13 或兼容版本
- Claude Code CLI 已可在终端中使用
- 可选：`cmux`

## 界面形态

当前版本默认以本地桌面窗口运行，不依赖系统浏览器。

- 默认模式：`pywebview` 本地窗口
- 可选模式：`--browser` 使用系统浏览器打开

## 安装依赖

建议使用虚拟环境：

```bash
python3 -m venv .venv-app
.venv-app/bin/python -m pip install --upgrade pip setuptools wheel
.venv-app/bin/python -m pip install -r requirements.txt
```

## 本地运行

默认以桌面窗口模式启动：

```bash
.venv-app/bin/python session-manager.py
```

如果需要用系统浏览器打开：

```bash
.venv-app/bin/python session-manager.py --browser
```

可选参数：

```bash
.venv-app/bin/python session-manager.py --host 127.0.0.1 --port 5199
```

## 打包为 App / DMG

项目自带打包脚本：

```bash
./build_dmg.sh
```

该脚本会：

- 使用 `PyInstaller --windowed` 生成 `dist/ccSession.app`
- 将 `static/` 资源一并打包进应用
- 生成 `dist/ccSession-installer.dmg`

## 终端启动逻辑

会话启动相关操作通过 `/api/launch` 实现：

- 如果检测到 `cmux` 且当前设置为 `cmux`：
  - 优先使用 `cmux` CLI
  - 若 CLI 不可用，则通过 AppleScript 驱动 `cmux` 窗口并自动输入命令
- 如果未安装 `cmux`，或当前设置为 `terminal`：
  - 自动回退到原生 `Terminal.app`

这意味着：即使用户没有安装 `cmux`，应用仍然可以正常使用，只是会话会在系统终端中启动。

## 恢复会话说明

当前版本 Claude CLI 对恢复历史会话有额外要求，因此本项目在恢复按钮对应的命令中会自动附带一个继续提示词，以保证历史会话可以真正恢复，而不是只打开终端窗口。

## 数据来源

项目主要读取以下本地数据：

- `~/.claude/history.jsonl`
- `~/.claude/projects/`
- `~/.claude/sessions/`
- `~/.claude/custom_projects.json`
- `~/.claude/terminal_config.json`

## 项目结构

```text
.
├── session-manager.py      # Flask 后端 + 本地桌面窗口入口
├── static/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── build_dmg.sh            # 构建 ccSession.app 和 dmg
├── requirements.txt
├── README.md
└── 操作记录.md             # 中文开发与验证记录
```

## 注意事项

- 当前打包产物默认是本地构建版本，未做开发者证书签名和 notarization
- 在其他 Mac 上首次打开 `.app` 或 `.dmg` 时，可能会出现 Gatekeeper 提示
- 本项目假定 Claude Code 的本地数据结构与当前版本兼容；若 Claude CLI 或 `~/.claude` 目录结构未来变化，可能需要同步调整

## 适用场景

- 本地快速查看 Claude Code 历史会话
- 在多个项目间切换 Claude Code 工作上下文
- 用 GUI 方式恢复、分叉或继续会话
- 为习惯桌面应用而非纯终端操作的用户提供会话入口
