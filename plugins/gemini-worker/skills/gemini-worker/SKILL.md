---
name: gemini-worker
description: 把任务交给 Google Gemini（经本机 Antigravity CLI 的无界面模式，走用户自己 Google 账号的额度，不用 API key）。主要用途是论文文字润色：内容写定后，语言润色交给 Gemini，脚本自动核对数字、引用、交叉引用、公式有没有被改，再由 Claude 逐段审核采纳；也用来读 PDF 和图片、做独立审核和第二意见。Gemini 全程只读。当用户说「交给 Gemini」「让 Gemini 润色 / 看看 / 读 / 审」「Gemini 第二意见」，或要求「检查 Gemini 调用环境」时使用。
---

# 调用 Gemini

## 路径约定

- `<skill>`：本 skill 的目录，即加载本文件时给出的 Base directory。
- `<py>`：Python 3.9 以上（脚本只用标准库，不用装包）。按顺序找，用第一个能用的：
  1. 配置文件 `~/.gemini-worker/config.json` 里的 `"python"`（有这个文件就先读它）；
  2. `python3`、`python`、`py -3`：`--version` 打印出 3.9 以上的版本才算。Windows 上的 `python` 常是 WindowsApps 里的空壳，没有输出或退出码不为 0 就跳过；
  3. 装了 uv 的，用 `uv run --no-project python`。

  都没有：请用户自己装 Python 3.9+ 或 uv，装好后重开 Claude Code。不要替用户下载安装。找到能用的 Python 后，告诉用户可以把路径写进配置文件的 `"python"`。
- `G` 指 `<py> -X utf8 "<skill>/scripts/gemini.py"`。路径带空格要加引号。

## 分工和原则

- 内容（数据、论证、结论）由用户和 Claude 定。Gemini 只做三件事：润色语言、读 PDF 和图片、给独立的第二意见。
- Gemini 的输出不是事实。润色稿要过脚本核对和 Claude 逐段审核；审核意见逐条回到原文核对；读图读出的数，对照原图核实后才能用。
- Gemini 指出的内容问题，由 Claude 判断、和用户商量后自己改，不交回 Gemini 改。
- **隐私**：发给 Gemini 的内容会传到 Google。合作者未发表的稿件和数据、别人的保密材料，先问用户再发。

## 0. 检查环境（第一次用，或报错时）

    G doctor

- **`agy: NOT FOUND`**：请用户**在自己的终端里**安装 Antigravity CLI（Google 已用它取代 Gemini CLI），装好后重开 Claude Code。不要替用户运行安装脚本。
  - Windows（PowerShell）：`irm https://antigravity.google/cli/install.ps1 | iex`
  - macOS / Linux：`curl -fsSL https://antigravity.google/cli/install.sh | bash`

  Windows 上的 Claude 桌面版是 MSIX 应用，从它里面运行的安装程序会把文件写进虚拟化的 AppData，用户自己的终端看不到，所以一定让用户自己装。装在别处的，设环境变量 `GEMINI_AGY_EXE`，或在配置文件里写 `"agy"`。
- **not signed in（退出码 3）**：请用户在自己的终端里运行 `agy`，选用 Google 账号登录，在浏览器里完成授权；页面给出授权码时粘贴到那个终端里，**不要发到对话里**。Claude 不能代为登录，也不能输入授权码。没登录时无界面调用会自己弹出浏览器登录页，所以脚本每次先用 `agy models` 预检，没登录就直接退出。
- 都正常后真调一次：`G doctor --live`，回复 `GEMINI_OK` 就能用。

额度跟着用户的 Google 账号走（免费账号、Google AI Pro、Ultra 各不相同，按时段刷新）。脚本禁止在额度用完后花付费的 AI credits（`useG1Credits: false`）。同一时间最多 2 个任务（`max_parallel`）；agy 的启动由脚本加锁排队（两个 agy 在同一秒启动可能卡死在 “Waiting for migrations”）。

## 模型

- 默认 `gemini-4-neon-xhigh`（Gemini 4 Neon 思考最深的一档）。账号用不了这个模型时，脚本自动换成账号列表里的第一个 Gemini 模型，并打印提示。`G models` 列出这个账号能用的全部模型。
- 指定模型用 `--model <名字>`。要快、要省额度，用 `gemini-4-neon-high` 或 `-medium`。
- 只接受 `gemini-` 开头的模型名：Antigravity 也提供 Claude、GPT 等模型，用了就不是 Gemini 的意见；Orchestrator 的执行模型可能不是 Gemini，也不许用。

## 只读，两层保险

- 每个任务在任务目录里放一份自定义 agent（`.agents/agents/`），用 `--agent` 选中，把工具限定死：润色用 `gw-polisher`，没有任何工具；审核用 `gw-reader`，只有读文件；加 `--web` 时用 `gw-reader-web`，再加搜索和读网页。
- 再用 `--settings` 加一份只对本次生效的拒绝规则（不改用户自己的 agy 设置）：不能写文件、运行命令、用 MCP 和浏览器。
- 跑完脚本核对用过的工具，出现名单外的会警告（摘要里的 UNEXPECTED tools）。

**读文件只能按路径读**：`gw-reader` 的读文件工具能直接看图片，读 PDF 时同时看到页面图像和文字层。但它不能列目录、不能搜索（agy 1.5 的自定义 agent 只放出读文件这一个工具），所以要读的文件都用 `--attach` 或在任务说明里写绝对路径。

## 润色流程

1. **准备输入**：`.tex`、`.md` 或 `.txt`（UTF-8），一次一节或几节。整篇 LaTeX 也可以，脚本只发 `\begin{document}` 之后的正文，导言区原样保留。Word 稿：先把要润色的段落导出成纯文本（段与段之间空一行），润色、核实后再写回 Word；有引用域、交叉引用域的，写回时保留这些域。
2. **补充说明**（可选）：复制 `<skill>/templates/POLISH_NOTES.md` 填写，写目标期刊、必须保留的术语、已经定下的说法。配置文件里的写作规则（默认是自带的 `rules/ai-tells.md`）脚本会自动附上（`--rules default`）。
3. **运行**：

       G polish --input <文件> --name <英文短名> [--notes <补充说明.md>]

   文字长的（几千词以上）放到后台跑，等完成通知。脚本按段落切成每份约 12000 字符（`--max-chars`），在同一个 Gemini 会话里逐份润色，前后术语一致。
4. **核实（必须做）**，都在任务目录里：
   - `check.md`：数字、引用、交叉引用、数学内容、LaTeX 环境任何一项“不通过”，按“逐段”表找到对应段落，退回原文或手工改正。
   - `diff.md`：逐段看改动（`[-删去-]{+加上+}`）。意思变了、论断强弱变了、术语换了、加了或删了内容的，退回原文。
   - `check.md` 末尾的“AI 腔正则：润色后新增的命中”：改掉。
   - `notes.md`：Gemini 提的内容问题，由 Claude 判断、自己改。
5. **写回稿件**：只用核实过的段落。写回后再跑一次核对：`G check --original <原稿> --polished <采用稿> --diff`。
6. **汇报**：采用了哪些段落、退回了哪些及原因、Gemini 提的内容问题和处理办法、用量、任务目录。

## 审核、读 PDF 和图片流程

1. **写任务说明**：复制 `<skill>/templates/REVIEW_BRIEF.md` 填写，放在临时目录。Gemini 看不到对话，背景写全；不写期望答案。
2. **运行**：

       G review --brief <说明.md> --name <英文短名> --project <材料所在目录> [--attach <PDF或图片>]... [--add-dir <目录>] [--web]

   `--project` 和 `--add-dir` 是 Gemini 可以读的目录，`--attach` 的文件所在目录会自动加进去。它不能列目录，要读的文件一个个写明。项目里的 AGENTS.md、GEMINI.md 不会自动加载，需要的背景写进任务说明。要查网页才加 `--web`。
3. **核实**：读 `r1_response.md`，每条意见回到它给的出处核对，再决定采纳与否。摘要里的 “tools used” 和 “agy stderr notes” 说明它读了什么、哪些操作被拒。
4. **追问**：`G continue --job <任务目录> --message <追问.md>`，Gemini 记得之前的对话。

## 脚本参数

- `polish`：`--input`（必填）、`--name`（必填，英文字母、数字和 `.-_`）、`--notes`、`--rules default|none|<文件>`、`--lang auto|en|zh`、`--max-chars`（默认 12000）、`--model`、`--effort low|medium|high|xhigh|max`、`--timeout-min`（默认 60）
- `review`：`--brief`（必填）、`--name`（必填）、`--project`（默认当前目录）、`--attach`（可重复）、`--add-dir`（可重复，或用分号隔开）、`--web`、`--model`、`--effort`、`--timeout-min`
- `continue`：`--job`、`--message`（必填），`--model`、`--effort`、`--timeout-min`
- `check`：`--original`、`--polished`（必填），`--out`、`--diff`
- `doctor [--live]`：查 Python、配置、agy、版本、模型列表；`--live` 真调一次。`models`：列出可用模型。
- 退出码：0 正常；1 Gemini 出错、超时、输出格式不对，或润色后内容核对不通过；2 参数错；3 没装 agy 或没登录；5 同时运行的任务太多。

## 配置文件（可选）

`~/.gemini-worker/config.json`（放在别处用环境变量 `GEMINI_WORKER_CONFIG` 指定），所有键都可以省略，示例见 `<skill>/config.example.json`：

| 键 | 作用 | 默认 |
|---|---|---|
| `python` | 运行脚本用的 Python（给 Claude 看，脚本自己不读） | 见“路径约定” |
| `agy` | agy 的完整路径 | PATH、`~/.local/bin`、`%LOCALAPPDATA%\agy\bin` |
| `jobs_dir` | 任务记录放哪 | `~/.gemini-worker/jobs` |
| `model` | 默认模型 | `gemini-4-neon-xhigh` |
| `author` | 一句话介绍作者，写进给 Gemini 的说明（例如“土木工程方向的研究者”） | “科研人员” |
| `reply_language` | 审核意见和润色说明用什么语言 | 审核：和任务说明一致；润色说明：中文 |
| `rules` | 润色时附给 Gemini 的写作规则：列表，每项是文件路径、`{"title": …, "path": …}`，或 `"builtin"`（自带的 `rules/ai-tells.md`） | `["builtin"]` |
| `tells` | 核对时查“新增 AI 腔”用的正则文件（格式同 `rules/ai-tells.md` 末尾的两个代码块），或 `"builtin"` | `"builtin"` |
| `max_parallel` | 同时运行的任务数上限 | 2 |

环境变量 `GEMINI_WORKER_MODEL`、`GEMINI_WORKER_JOBS`、`GEMINI_MAX_PARALLEL`、`GEMINI_AGY_EXE` 优先于配置文件。

## 任务目录

默认在 `~/.gemini-worker/jobs/<日期>_<时间>_<name>/`：

```
job.json             设置、模型、会话 ID、各轮状态、用量、用过的工具、被拒的操作
settings.json        这次调用的只读设置
.agents/agents/      这次调用的自定义 agent（限定工具）
brief.md             审核任务说明的副本（review）
input.*              润色原文的副本（polish）
polished.*           润色稿（没拿到合格结果的部分保留原文）
check.md / diff.md   内容核对 / 逐段改动（polish）
notes.md             Gemini 的修改说明和内容提醒（polish）
rN_prompt*.md        第 N 轮发给 Gemini 的内容
rN_response*.md      第 N 轮 Gemini 的回复
rN_events.jsonl      agy 的事件流（每一步工具调用）
rN_stderr.log        agy 的诊断输出（被拒的操作、额度提示）
```
