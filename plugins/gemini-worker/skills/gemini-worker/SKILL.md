---
name: gemini-worker
description: Hand work to Google Gemini through the local Antigravity CLI in headless mode, on the user's own Google account (no API key). Main use is polishing the wording of a paper once its content is settled - a script checks that numbers, citations, cross-references and math are unchanged, then Claude reviews every paragraph before anything is used. Also reads PDFs and images and gives independent reviews and second opinions. Gemini stays read-only. Use when the user says "hand this to Gemini", "have Gemini polish / look at / read / review", "Gemini second opinion" or "check the Gemini setup"; in Chinese 「交给 Gemini」「让 Gemini 润色 / 看看 / 读 / 审」「Gemini 第二意见」「检查 Gemini 调用环境」.
---

# Calling Gemini

## Conventions

- `<skill>`: this skill's directory, i.e. the Base directory given when this file is loaded.
- `<py>`: Python 3.9 or newer (the script uses the standard library only). Use the first one that works:
  1. `"python"` in the config file `~/.gemini-worker/config.json` (read the file first if it exists);
  2. `python3`, `python`, `py -3`: only if `--version` prints 3.9 or newer. On Windows, `python` is often the empty WindowsApps stub; skip it if it prints nothing or exits non-zero;
  3. with uv installed: `uv run --no-project python`.

  If none works, ask the user to install Python 3.9+ or uv and restart Claude Code. Do not download or install anything for them. Once a working Python is found, tell the user they can put its path in `"python"` in the config file.
- `G` means `<py> -X utf8 "<skill>/scripts/gemini.py"`. Quote paths that contain spaces.

## Division of labour and principles

- The content (data, arguments, conclusions) belongs to the user and Claude. Gemini does three things only: polish wording, read PDFs and images, and give independent second opinions.
- Gemini's output is not fact. Polished text goes through the script's check and Claude's paragraph-by-paragraph review; every review point is checked against the original; values read from figures are verified against the figure before use.
- Content problems Gemini points out are judged by Claude, discussed with the user, and fixed by Claude, not sent back to Gemini.
- **Privacy**: whatever is sent to Gemini goes to Google. Ask the user before sending collaborators' unpublished manuscripts or data, or anyone else's confidential material.
- Talk to the user in their language, and write briefs for Gemini in the user's language too.

## 0. Check the setup (first use, or after an error)

    G doctor

- **`agy: NOT FOUND`**: ask the user to install the Antigravity CLI (Google's replacement for the Gemini CLI) **in their own terminal**, then restart Claude Code. Never run the installer for them.
  - Windows (PowerShell): `irm https://antigravity.google/cli/install.ps1 | iex`
  - macOS / Linux: `curl -fsSL https://antigravity.google/cli/install.sh | bash`

  On Windows the Claude desktop app is an MSIX app: installers started from inside it write into a virtualized AppData folder that the user's own terminal cannot see, which is another reason the user installs it. If agy lives elsewhere, set the environment variable `GEMINI_AGY_EXE` or `"agy"` in the config file.
- **not signed in (exit code 3)**: ask the user to run `agy` in their own terminal, sign in with their Google account and finish the authorization in the browser. If the page shows an authorization code, it goes into that terminal, **never into the chat**. Claude cannot sign in for the user or type the code. An unauthenticated headless call would open a browser login by itself, so the script always runs `agy models` first and stops if the user is not signed in.
- When both are fine, make one real call: `G doctor --live`. A reply of `GEMINI_OK` means it works.

Quota follows the user's Google account (free accounts, Google AI Pro and Ultra differ; it refreshes over time). The script never spends paid AI credits after the plan quota runs out (`useG1Credits: false`). At most 2 jobs run at once (`max_parallel`); the script queues agy start-ups behind a lock, because two agy processes starting in the same second can hang at "Waiting for migrations".

## Models

- The default is `gemini-4-neon-xhigh` (Gemini 4 Neon at its deepest thinking level). If the account lacks it, the script switches to the first Gemini model in the account's list and prints a note. `G models` lists every model the account offers.
- Choose a model with `--model <slug>`. For speed or to save quota, use `gemini-4-neon-high` or `-medium`.
- Only slugs starting with `gemini-` are accepted. Antigravity also offers Claude, GPT and other models, whose answers would not be Gemini's; the Orchestrator may execute with a non-Gemini model and is refused too.

## Read-only, two layers

- Each job writes a custom agent into its job folder (`.agents/agents/`) and selects it with `--agent`, which fixes the tool set: `gw-polisher` (polishing) has no tools at all; `gw-reader` (reviews) can only read files; with `--web`, `gw-reader-web` adds search and web reading.
- A per-run `--settings` file adds deny rules without changing the user's own agy settings: no file writes, shell commands, MCP or browser.
- After each run the script checks which tools were used and warns about anything outside the list ("UNEXPECTED tools" in the summary).

**Files are read by path only.** The reader's file tool shows images directly and, for PDFs, both the page images and the text layer. It cannot list folders or search (custom agents in agy 1.5 expose only the file-reading tool), so name every file with `--attach` or give absolute paths in the brief.

## Polishing

1. **Input**: `.tex`, `.md` or `.txt` (UTF-8), one or a few sections at a time. A whole LaTeX document works too: only the body after `\begin{document}` is sent, and the preamble is kept as it is. Word documents: export the paragraphs to plain text first (a blank line between paragraphs), then write the verified result back into Word, keeping any citation and cross-reference fields.
2. **Notes** (optional): copy `<skill>/templates/POLISH_NOTES.md` and fill in the target journal, terms that must not change, and wording that is already settled. The writing rules from the config (by default the bundled `rules/ai-tells.md`) are attached automatically (`--rules default`).
3. **Run**:

       G polish --input <file> --name <short-ascii-name> [--notes <notes.md>]

   Run long texts (several thousand words) in the background and wait for the completion notice. The script splits the text by paragraph into parts of about 12000 characters (`--max-chars`) and polishes them one after another in a single Gemini conversation, so terminology stays consistent.
4. **Verify (mandatory)**, all in the job folder:
   - `check.md`: if numbers, citations, cross-references, math or LaTeX environments fail, find the paragraph in the per-paragraph list and revert it or fix it by hand.
   - `diff.md`: read every change (`[-removed-]{+added+}`). Revert paragraphs whose meaning, strength of claim or terminology changed, or where content was added or dropped.
   - The AI-tells section at the end of `check.md` lists phrases that are new in the polished text: fix them.
   - `notes.md`: content problems Gemini raised. Claude judges them and fixes them itself.
5. **Write back**: use only verified paragraphs. Then run the check again: `G check --original <original> --polished <adopted text> --diff`.
6. **Report**: which paragraphs were adopted, which were reverted and why, the content problems Gemini raised and how they were handled, usage, and the job folder.

## Reviews, reading PDFs and images

1. **Brief**: copy `<skill>/templates/REVIEW_BRIEF.md`, fill it in and keep it in a temp folder. Gemini cannot see the conversation, so give the full background, and do not state the answers you expect.
2. **Run**:

       G review --brief <brief.md> --name <short-ascii-name> --project <material folder> [--attach <PDF or image>]... [--add-dir <folder>] [--web]

   `--project` and `--add-dir` are folders Gemini may read; the folders of `--attach` files are added automatically. It cannot list folders, so name every file. AGENTS.md or GEMINI.md files in the project are not loaded; put the background Gemini needs in the brief. Add `--web` only when web lookups are needed.
3. **Verify**: read `r1_response.md` and check each point against the source it cites before accepting it. "tools used" and "agy stderr notes" in the summary show what it read and what was refused.
4. **Follow-up**: `G continue --job <job folder> --message <follow-up.md>`. Gemini remembers the conversation.

## Script options

- `polish`: `--input` (required), `--name` (required; letters, digits and `.-_`), `--notes`, `--rules default|none|<file>`, `--lang auto|en|zh`, `--max-chars` (default 12000), `--model`, `--effort low|medium|high|xhigh|max`, `--timeout-min` (default 60)
- `review`: `--brief` (required), `--name` (required), `--project` (default: the current folder), `--attach` (repeatable), `--add-dir` (repeatable, or separated by semicolons), `--web`, `--model`, `--effort`, `--timeout-min`
- `continue`: `--job` and `--message` (required), `--model`, `--effort`, `--timeout-min`
- `check`: `--original` and `--polished` (required), `--out`, `--diff`
- `doctor [--live]`: Python, config, agy, version and model list; `--live` makes one real call. `models`: list the available models.
- Exit codes: 0 ok; 1 Gemini error, timeout, bad output format, or a failed content check after polishing; 2 bad arguments; 3 agy missing or not signed in; 5 too many jobs running.

## Config file (optional)

`~/.gemini-worker/config.json`, or wherever the environment variable `GEMINI_WORKER_CONFIG` points. Every key is optional; see `<skill>/config.example.json`.

| Key | Purpose | Default |
|---|---|---|
| `python` | Python used to run the script (read by Claude; the script ignores it) | see Conventions |
| `agy` | Full path to agy | PATH, `~/.local/bin`, `%LOCALAPPDATA%\agy\bin` |
| `jobs_dir` | Where job folders go | `~/.gemini-worker/jobs` |
| `model` | Default model | `gemini-4-neon-xhigh` |
| `author` | One line about the author, inserted into the instructions for Gemini (e.g. "a researcher in civil engineering") | 科研人员 ("a researcher") |
| `reply_language` | Language of review reports and polishing notes | reviews: the brief's language; notes: the text's language |
| `rules` | Writing rules attached when polishing: a list of file paths, `{"title": …, "path": …}` objects, or `"builtin"` (the bundled `rules/ai-tells.md`) | `["builtin"]` |
| `tells` | Regex file for flagging new AI-sounding phrases (format: the two code blocks at the end of `rules/ai-tells.md`), or `"builtin"` | `"builtin"` |
| `max_parallel` | Maximum number of jobs at once | 2 |

The environment variables `GEMINI_WORKER_MODEL`, `GEMINI_WORKER_JOBS`, `GEMINI_MAX_PARALLEL` and `GEMINI_AGY_EXE` override the config file.

## Job folder

By default `~/.gemini-worker/jobs/<date>_<time>_<name>/`:

```
job.json             settings, model, conversation id, status per round, usage, tools used, refused actions
settings.json        the read-only settings for this call
.agents/agents/      the custom agent for this call (fixed tool set)
brief.md             copy of the review brief (review)
input.*              copy of the text to polish (polish)
polished.*           polished text (parts without a valid result keep the original)
check.md / diff.md   content check / changes paragraph by paragraph (polish)
notes.md             Gemini's notes on its edits and its content warnings (polish)
rN_prompt*.md        what was sent to Gemini in round N
rN_response*.md      Gemini's reply in round N
rN_events.jsonl      agy's event stream (every tool call)
rN_stderr.log        agy's diagnostics (refused actions, quota messages)
```

The instructions sent to Gemini and the check reports are written in Chinese. This does not affect English texts; `reply_language` sets the language of Gemini's notes and reviews.
