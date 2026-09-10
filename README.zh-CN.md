[English](README.md) | [Русский](README.ru.md) | 简体中文

<div align="center">
  <p align="center"><img src=".github/cover.png" width="720"
   alt="td-atlas 标志置于波形线场之上。"></p>
  <h1>td-atlas</h1>
  <p>
    把 TouchDesigner 拆成原子的索引、通往运行中实例的实时桥，
    以及对已保存工程的离线读取 —— 通过 MCP 交给 AI 智能体。
  </p>
  <p>
    <a href="#安装">安装</a> ·
    <a href="#作为-mcp-服务器">在智能体里使用</a> ·
    <a href="plugin/skills/touchdesigner/references/tools.md">工具清单</a> ·
    <a href="docs/cli.md">命令行</a> ·
    <a href="docs/troubleshooting.md">排查</a> ·
    <a href="CHANGELOG.md">更新记录</a>
  </p>
  <p>
    <a href="https://github.com/grigabyte/td-atlas/actions/workflows/ci.yml"><img src="https://github.com/grigabyte/td-atlas/actions/workflows/ci.yml/badge.svg" alt="CI：在 macOS 与 Windows 上运行 pytest 和 ruff"></a>
    <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="宿主机需要 Python 3.11 或更新版本">
    <img src="https://img.shields.io/badge/platform-macOS-lightgrey" alt="平台：macOS；Windows 代码路径在 CI 中运行，但 Windows 上的 TouchDesigner 未经验证">
    <a href="LICENSE"><img src="https://img.shields.io/badge/licence-MIT-blue" alt="许可证：MIT"></a>
  </p>
</div>


在 TouchDesigner 里搭建的智能体需要同时具备三样东西：对*这台*机器上算子与
参数的准确认知、对运行中实例的控制且能一步撤销、以及在不打开工程的前提下
读取已保存工程的办法。缺了第一样，它就会猜参数名。缺了第二样，一步失败就
会留下半张网络。缺了第三样，关于既有工程的每个问题都得先把程序跑起来。

td-atlas 建立在两条观察之上：

1. **智能体需要知道的关于 TouchDesigner 的几乎一切，都已经随程序一起
   安装在本机。**
2. **当工作悄无声息地什么都没做时，TouchDesigner 几乎什么都不会报。**

## 它能做什么

- **[原子索引](docs/atom-index.md)** —— 两遍扫描产出一个 SQLite 索引，对生成
  它的那个构建版本是精确的，而不是从描述另一个版本的 wiki 上抓来的。
- **[桥](docs/bridge.md)** —— 一个 Web Server DAT 和一个回调 DAT，由脚本搭建
  而非以 `.tox` 形式分发，因此可读、可 diff，并且就地升级。
- **[TouchDesigner 不会报告的事](docs/health.md)** —— `errors` 覆盖 TouchDesigner
  称之为错误的部分；`td_health` 覆盖它不称之为错误的部分。
- **[调用日志](docs/journal.md)** —— 每次桥调用一行，由宿主机写入，比会话活得更久。
- **[离线读取工程](docs/offline-projects.md)** —— 不启动 TouchDesigner 就能检查、
  搜索和比较一个工程，而且不触碰原始文件。
- **[保存时写出的网络文本](docs/network-text.md)** —— 桥可以在每次保存时把网络
  以文本形式写在 `.toe` 旁边，于是工程在 git 里有了可 diff 的历史。

## 安装

先要有三样东西。

- **TouchDesigner。** 索引是从*你自己*那份程序里构建的，保存的是那份程序报出
  的值，所以没有什么需要下载。
- **宿主机上的 Python 3.11 或更新版本。**
- **一个会说 MCP 的 AI 智能体**，因为调用这些工具的正是它。本项目是针对
  [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) 构建和
  测量的 —— 下面那行 `claude mcp add` 就是它的命令 —— 任何会说
  [Model Context Protocol](https://modelcontextprotocol.io) 的客户端都能拿到
  同一批工具。你和智能体说话，智能体和 TouchDesigner 说话。

平台情况，以及这个连接器可能改变你工程里的哪些东西，见
[兼容性](docs/compatibility.md)。

在终端里，一行：

```bash
curl -fsSL https://grigabyte.github.io/td-atlas/i | sh
```

`/i` 就是本仓库的 `install.sh`，只是名字更短：每次改动它的推送之后，
GitHub Pages 都会从 `main` 重新发布这个文件，所以不存在第二份会落后的副本。
Pages 不响应时，同样的字节可以直接从仓库取：

```bash
curl -fsSL https://raw.githubusercontent.com/grigabyte/td-atlas/main/install.sh | sh
```

[`install.sh`](install.sh) 会找到一个 Python，克隆仓库，在旁边建一个虚拟环境，
安装这个包，构建索引并布置好桥 —— 每条命令在执行前都会先打印出来。它问两个
问题：克隆到哪里、现在是否构建索引；没有终端可问时，两个都取默认值。td-atlas
本身落在两个地方：你指定的那份检出和 `~/.td-atlas`；除此之外，`uv` 或 `pip`
会像对待任何包一样填自己的缓存。不用 sudo，不碰系统目录，你的 shell 启动文件
原样不动。在已有的检出上再跑一次，它会更新那份检出而不是从头开始。它是一个
POSIX shell 脚本，所以 Windows 走下面这段步骤。

<details>
<summary>手动安装，Windows 上也是同一套步骤</summary>

```bash
git clone https://github.com/grigabyte/td-atlas
cd td-atlas
uv venv                     # or: python3 -m venv .venv
uv pip install -e .         # or: .venv/bin/pip install -e .
```

PyPI 上没有这个包，所以 `pip install td-atlas` 和 `uvx td-atlas` 什么都找不到
—— 这份检出*就是*安装本身。然后，在检出目录里：

```bash
.venv/bin/td-atlas build      # offline index, 23–30 s, no TouchDesigner process
.venv/bin/td-atlas install    # stage the bridge, print the bootstrap and MCP lines
```

</details>

下文一律简写作 `td-atlas`。除非虚拟环境已激活，否则请按路径调用 ——
`.venv/bin/td-atlas`，Windows 上是 `.venv\Scripts\td-atlas` —— 因为系统 Python
看不到这个包。

`td-atlas install` 会打印两样需要粘贴的东西。第一样贴进 TouchDesigner 的
textport（Dialogs → Textport and DATs），每个工程贴一次：

```python
exec(open('/Users/you/.td-atlas/bootstrap.py').read())
```

第二样是给你的 MCP 客户端用的 `claude mcp add` 行 —— 见下文。加上
`--write-mcp-json DIR` 还会把同一条记录写入（或并入）`DIR/.mcp.json`。

然后，在 TouchDesigner 已打开、桥已就位的情况下，用只有运行中的实例才知道的
运行期事实把索引补全：

```bash
td-atlas probe
```

接着是 `td-atlas doctor`，这里只有它能告诉你安装到底成没成。它会为每个不是
`ok` 的环节指出修复办法，只要有一环断了就以非零码退出，所以哪怕你是半路进来
的，它也能告诉你走到了哪一步：在什么都还没构建的机器上，它会报
`index : FAIL … fix: td-atlas build` 和
`bridge : warn … fix: td-atlas install`。

## 作为 MCP 服务器

运行 `td-atlas install`，粘贴它打印出的那行 `claude mcp add …` —— 它以绝对路径
指向当前解释器，所以不管 MCP 客户端的工作目录是什么、有没有激活虚拟环境，
它都照常工作。手动接入是这样：

```bash
claude mcp add td-atlas -- /path/to/python -m td_atlas.cli mcp
```

从这里开始，工具归智能体，日常语言归你。下面没有一条是要在终端里敲的命令 ——
那是一个人对智能体说的话，以及它转成的调用：

| 你对智能体说的话 | 它调用什么 |
| --- | --- |
| *「哪个算子能用噪声置换图像？在动手搭之前先给我准确的参数名。」* | `td_search_operators`，然后 `td_operator_schema` |
| *「在我打开的工程里搭一条 noise 接 blur 接 out TOP，再检查没有东西在无声地失效。」* | `td_build` —— 一个撤销块 —— 然后 `td_health` |
| *「看起来什么都没发生。」* | `td_health`，然后对它点名的东西调 `td_flags` |
| *「让我看看现在是什么样子，还有这一秒里的运动。」* | `td_render`，运动则用接触印相表 |
| *「`myproject.toe` 的 `/project1` 里面是什么？TouchDesigner 是关着的。」* | `td_project_read` —— 文件被复制到缓存，在那里读取 |
| *「从我们开始到现在你改了什么？」* | 前后各一次 `td_snapshot`，然后对这两份做 `td_project_diff` —— 组件落在 `~/.td-atlas`，绝不动你自己的文件 |
| *「撤销。」* | `td_undo` —— 一整个 `td_build` 批次是一步 |

41 个工具分三组：**9 个索引**工具离线可用，**23 个实时**工具作用于运行中的实例，
**9 个工程文件**工具从磁盘读写 `.toe`/`.tox`。每一个工具、它的参数和它的用途，
都列在
[`plugin/skills/touchdesigner/references/tools.md`](plugin/skills/touchdesigner/references/tools.md)
里 —— 一份清单，由测试钉在代码上，而不是在这里放第二份会走样的副本。

`td_build` 和 `td_set_params` 在发送前会拿索引校验参数名，所以常见的错误会
以更正的形式回来：

```
- t: is a parameter group, not a settable parameter (try: tx, ty, tz)
- typ: no such parameter (try: type, ty)
- type: 'simplex5d' is not a valid menu entry
        (try: simplex4d, simplex3d, simplex2d, sparse, perlin4d)
- period: -3 is below the clamped minimum 0.0
```

## 文档

| 文档 | 面向谁 |
| --- | --- |
| [`plugin/skills/touchdesigner/SKILL.md`](plugin/skills/touchdesigner/SKILL.md) | *使用*这个连接器的智能体 |
| [`plugin/skills/touchdesigner/references/gotchas.md`](plugin/skills/touchdesigner/references/gotchas.md) | 每一个不报错却害人的陷阱 |
| [`plugin/skills/touchdesigner/references/tools.md`](plugin/skills/touchdesigner/references/tools.md) | 全部 41 个 MCP 工具 |
| [`AGENTS.md`](AGENTS.md) | 向本仓库*提交改动*的智能体 |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 提 PR 之前怎么跑测试和 linter |
| [`CHANGELOG.md`](CHANGELOG.md) | 每个版本改了什么，以及每一次协议变更 |
| [`docs/architecture.md`](docs/architecture.md) | 三层如何拼在一起，以及为什么这么拼 |
| [`docs/cli.md`](docs/cli.md) | 每个 `td-atlas` 子命令和参数，以及各自的前提 |
| [`docs/formats.md`](docs/formats.md) | 逆向出来的 `.toe`/`.tox` 格式，附证据 |
| [`docs/atom-index.md`](docs/atom-index.md) | 构建索引的两遍扫描，以及每个来源给出什么 |
| [`docs/bridge.md`](docs/bridge.md) | 运行在 TouchDesigner 内部的组件，以及它在 `exec` 之外提供了什么 |
| [`docs/health.md`](docs/health.md) | TouchDesigner 不会报告的事，以及 `td_health` 改为打印什么 |
| [`docs/journal.md`](docs/journal.md) | 调用日志：写了什么、谁来写、什么被排除在外 |
| [`docs/offline-projects.md`](docs/offline-projects.md) | 在 TouchDesigner 关闭时读取、搜索和比较 `.toe` |
| [`docs/network-text.md`](docs/network-text.md) | 写在 `.toe` 旁边的可 diff 文本，以及七类已知差异 |
| [`docs/compatibility.md`](docs/compatibility.md) | 构建版本、Python、操作系统，以及它可能改变你工程里的什么 |
| [`docs/skill.md`](docs/skill.md) | 智能体技能，以及把它作为插件安装 |
| [`docs/bundle.md`](docs/bundle.md) | 构建 `.mcpb`，以及发布它意味着什么 |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | 每一个症状、它是什么、该运行什么 |
| [`docs/development.md`](docs/development.md) | 测试套件，以及它守住的那条不变量 |
| [`docs/layout.md`](docs/layout.md) | 仓库里的每个目录，以及各自放着什么 |

## 许可证

MIT —— 见 [LICENSE](LICENSE)。TouchDesigner 是 Derivative Inc. 的产品；
本项目与他们没有关联，也不转发安装目录里的任何内容，它只读取你机器上
已经有的东西。
