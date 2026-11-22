# gemini-worker：在 Claude Code 里调用 Gemini 润色论文、读 PDF、给第二意见

在 Claude Code 里说「这一节交给 Gemini 润色」「让 Gemini 看看这个 PDF」，Claude 就会通过你电脑上的 [Antigravity CLI](https://antigravity.google/docs/cli/install)（命令 `agy`，Google 用它取代了 Gemini CLI）把任务交给 Gemini。用的是你自己 Google 账号的额度，不需要 API key。

适合这样分工：论文内容由你和 Claude 写定，文字润色交给 Gemini；需要第二双眼睛时，请 Gemini 独立读材料、挑问题。

## 能做什么

- **润色论文文字**：Gemini 只改语言。润色完，脚本自动核对数字、`\cite` 和 `[12]` 这类引用、`\ref` 和 `\label`、图表公式编号、数学内容、LaTeX 环境有没有被改动，逐段列出改动，并查出润色后新冒出来的 AI 腔（delve、crucial、值得注意的是……）。Claude 再逐段审核，只采用核实过的段落。
- **读 PDF 和图片**：读图表里的数、读论文的某几页。Gemini 读 PDF 时，页面图像和文字层都能看到。
- **独立审核、第二意见**：让 Gemini 按任务说明核查稿件、数据或代码。每条意见都要给出处，Claude 再逐条回到原文核对。
- 英文、中文稿件都行，`.tex`、`.md`、`.txt` 都可以（Word 稿由 Claude 先把段落导出来）。

## Gemini 全程只读

- 每次调用都给 Gemini 一个限定了工具的自定义 agent：润色时它没有任何工具；审核时只能读文件，不能列目录、不能搜索，要读的文件由 Claude 逐个给出路径；你同意查网页时才加上搜索和读网页。
- 另加一份只对本次调用生效的拒绝规则：不能写文件、不能运行命令、不能用 MCP 和浏览器。你自己的 agy 设置不会被改动。
- 额度用完后不会花付费的 AI credits。
- 只用 Gemini 模型。Antigravity 也提供别家的模型，脚本一律拒绝。

## 需要什么

- **Claude Code**（桌面版的 Code 标签页或命令行都可以）
- **Antigravity CLI**，并用你的 Google 账号登录过。能用哪些模型、额度多少，取决于你的账号和订阅
- **Python 3.9 以上**，或者 [uv](https://docs.astral.sh/uv/)。脚本只用标准库，不用装任何包
- **Windows 10 / 11**：作者实测过。脚本也照顾了 macOS 和 Linux，但没有实测

## 安装

### 第一步：装 Antigravity CLI 并登录（在你自己的终端里做）

Windows（PowerShell）：

```powershell
irm https://antigravity.google/cli/install.ps1 | iex
```

macOS / Linux：

```bash
curl -fsSL https://antigravity.google/cli/install.sh | bash
```

装好后在同一个终端里运行 `agy`，按提示用 Google 账号登录：在浏览器里授权，页面给出授权码时粘贴回这个终端。看到对话界面就说明登录好了，输入 `/exit` 退出。

> 用 Windows 上的 Claude 桌面版时，请一定在自己的 PowerShell 里装，不要让 Claude 代装：桌面版是 MSIX 应用，从它里面运行的安装程序会把文件写进虚拟化的 AppData，你自己的终端里找不到。

### 第二步：装这个 skill

三种方式任选一种。

**方式一：从 GitHub 安装（推荐，之后能收到更新）**。在 Claude Code 的对话框里依次输入：

```
/plugin marketplace add Fu-Yiheng/gemini-worker
/plugin install gemini-worker@gemini-worker
```

- 第一条报错的话，改用完整地址：`/plugin marketplace add https://github.com/Fu-Yiheng/gemini-worker.git`
- 装完重启 Claude Code，或者输入 `/reload-plugins`
- 以后更新：输入 `/plugin marketplace update gemini-worker`，然后在 `/plugin` 的 Installed 页里点更新

**方式二：从本地文件夹安装**。在仓库首页点绿色的 **Code** → **Download ZIP**，解压到一个不会删掉的位置，例如 `D:\tools\gemini-worker`（这个文件夹里直接就有 `.claude-plugin` 和 `plugins`），然后输入：

```
/plugin marketplace add D:/tools/gemini-worker
/plugin install gemini-worker@gemini-worker
```

**方式三：直接复制 skill**（最简单，但不能自动更新）。把 `plugins/gemini-worker/skills/gemini-worker` 整个文件夹复制到 `~/.claude/skills/`（Windows 是 `C:\Users\<你的用户名>\.claude\skills\`），重启 Claude Code。

### 第三步：检查环境

在 Claude Code 里说：

> 检查 Gemini 调用环境

Claude 会找到能用的 Python、确认 agy 已经登录，再真调一次 Gemini，回复 `GEMINI_OK` 就成了。Claude Code 运行命令前可能会请你确认，照常允许即可。

## 怎么用

| 你想要 | 这样说 |
|---|---|
| 润色一节 | 「把 paper/intro.tex 交给 Gemini 润色，目标期刊是 JWEIA」 |
| 润色中文稿 | 「让 Gemini 润色 D:\稿件\第三章.md」 |
| 读图表里的数 | 「让 Gemini 读 fig5.png，把每条曲线的峰值读出来」 |
| 读 PDF | 「让 Gemini 看 D:\papers\xxx.pdf 第 4–6 页，摘出试验工况表」 |
| 第二意见 | 「让 Gemini 审一下 results.md 里的结论有没有数据支撑」 |
| 追问 | 「再问 Gemini：第 3 条意见的依据在哪一页？」 |

从插件装的（方式一、二），也可以直接输入 `/gemini-worker:gemini-worker`，再说要做什么。

润色结果不会直接改你的稿子。每个任务一个文件夹（默认在 `~/.gemini-worker/jobs/`），里面有：

- `polished.*`：润色稿
- `check.md`：内容核对表，哪一项、哪一段有出入
- `diff.md`：逐段改动，`[-删去-]{+加上+}`
- `notes.md`：Gemini 的修改说明，以及它看出来、但按规定没有动手改的内容问题

Claude 逐段看完，告诉你哪些采用、哪些退回，你同意后再写回稿件。

## 个人设置（可选）

个人设置写在 `~/.gemini-worker/config.json`，所有项都可以省略。例如：

```json
{
  "author": "土木工程方向的研究者",
  "reply_language": "中文",
  "rules": ["builtin", {"title": "我的写作规则", "path": "~/Documents/writing/my-rules.md"}]
}
```

| 键 | 作用 | 默认 |
|---|---|---|
| `author` | 一句话介绍你，写进给 Gemini 的说明 | 科研人员 |
| `reply_language` | 审核意见和润色说明用什么语言 | 审核跟任务说明一致，润色说明用中文 |
| `model` | 默认模型 | `gemini-4-neon-xhigh`；账号用不了时自动换成能用的 Gemini 模型 |
| `rules` | 润色时附给 Gemini 的写作规则文件；`"builtin"` 是自带的[通用 AI 腔清单](plugins/gemini-worker/skills/gemini-worker/rules/ai-tells.md) | `["builtin"]` |
| `tells` | 查“新增 AI 腔”用的正则文件，格式见自带清单末尾 | 自带清单 |
| `jobs_dir` | 任务记录放在哪 | `~/.gemini-worker/jobs` |
| `python`、`agy` | Python 和 agy 的路径，自动找不到时才需要 | 自动查找 |
| `max_parallel` | 同时运行的任务数 | 2 |

完整说明见 [SKILL.md](plugins/gemini-worker/skills/gemini-worker/SKILL.md)，示例见 [config.example.json](plugins/gemini-worker/skills/gemini-worker/config.example.json)。

## 模型和额度

- 默认用 Gemini 4 Neon 的 xhigh 档（思考最深的一档）。想快一些、省额度，跟 Claude 说「用 high」。
- 额度跟着你的 Google 账号走，按时段刷新。用完时脚本报错退出，不会花付费的 credits。
- 想看账号能用哪些模型，跟 Claude 说「列一下 Gemini 能用的模型」。

## 隐私

- 交给 Gemini 的文字和文件会发送到 Google，按你 Google 账号的条款处理；Claude 读过的内容会发送给 Anthropic，跟平时一样。
- 合作者未发表的稿件、别人的保密材料，Claude 会先问你再发。

## 不经过 Claude，直接在命令行里用

在本仓库的文件夹里：

```
python plugins/gemini-worker/skills/gemini-worker/scripts/gemini.py doctor --live
python plugins/gemini-worker/skills/gemini-worker/scripts/gemini.py polish --input intro.tex --name intro
python plugins/gemini-worker/skills/gemini-worker/scripts/gemini.py review --brief brief.md --name fig5 --attach fig5.png
```

`gemini.py <子命令> -h` 看全部参数。

## 已知限制

- 审核时 Gemini 只能按路径读文件，不能列目录、不能搜索（agy 1.5 的自定义 agent 只放出读文件这一个工具）。
- 实测环境：Windows 11、agy 1.5.14、Gemini 4 Neon。macOS 和 Linux 没有测过。
- agy 会在后台自动更新；以后它的参数或输出格式变了，脚本可能要跟着改。

---

## In English

**gemini-worker** is a Claude Code plugin that lets Claude hand work to Google Gemini through the Antigravity CLI (`agy`) in headless mode, using your own Google account instead of an API key.

- **Polishing**: Gemini edits the wording of a paper (LaTeX, Markdown or plain text; English or Chinese). A script then checks that numbers, citations, cross-references, math and LaTeX environments are unchanged, writes a per-paragraph diff, and flags newly added AI-sounding phrases. Claude reviews every paragraph before anything goes back into your manuscript.
- **Reading and second opinions**: Gemini reads PDFs and images and reviews material against a written brief, citing a source for every point.
- **Read-only**: a custom agent limits Gemini's tools (none for polishing, file reading only for reviews), and per-run deny rules block file writes, shell commands, MCP and the browser. Only Gemini models are allowed, and paid AI credits are never used.

Install agy and sign in from your own terminal (`irm https://antigravity.google/cli/install.ps1 | iex` on Windows, `curl -fsSL https://antigravity.google/cli/install.sh | bash` on macOS/Linux, then run `agy` once). Then, in Claude Code:

```
/plugin marketplace add Fu-Yiheng/gemini-worker
/plugin install gemini-worker@gemini-worker
```

and ask Claude to "check the Gemini setup". It needs Python 3.9+ (standard library only) or uv. Tested on Windows 11 with agy 1.5.14. Prompts and reports are in Chinese; set `"reply_language": "English"` in `~/.gemini-worker/config.json` to get Gemini's notes and reviews in English.
