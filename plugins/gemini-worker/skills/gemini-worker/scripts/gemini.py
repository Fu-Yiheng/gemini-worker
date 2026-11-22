#!/usr/bin/env python
"""gemini-worker: call Google Gemini through the Antigravity CLI (agy) in headless mode.

Runs on the user's own Google account (agy signed in with Google), not an API key.

Subcommands
  doctor [--live]                  find agy, show version, config and models; --live makes one tiny real call
  models                           list the model slugs this account can use
  review   --brief F --name N      read-only task: independent review, second opinion, read PDF/images
  polish   --input F --name N      polish the wording of a text file, then check that content is unchanged
  continue --job DIR --message F   follow-up turn in the same Gemini conversation
  check    --original F --polished F   re-run the content check (after editing a polished file)

Optional config file: ~/.gemini-worker/config.json (or $GEMINI_WORKER_CONFIG); see config.example.json.
Run with:  python -X utf8 gemini.py <subcommand> ...   (Python 3.9+, standard library only)
Exit codes: 0 ok; 1 Gemini error / timeout / bad output / content check failed; 2 bad arguments;
            3 agy missing or not signed in; 5 too many jobs running.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
PROMPTS = SKILL_DIR / "prompts"
BUNDLED_TELLS = SKILL_DIR / "rules" / "ai-tells.md"
BUILTIN_MODEL = "gemini-4-neon-xhigh"
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,60}$")
IS_WIN = os.name == "nt"


# ----------------------------------------------------------------------------- small helpers

def die(msg: str, code: int = 2) -> None:
    print(f"gemini-worker: {msg}", file=sys.stderr)
    sys.exit(code)


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def read_text(path: str | Path) -> str:
    p = Path(path)
    if not p.is_file():
        die(f"file not found: {p}")
    data = p.read_bytes()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


# ----------------------------------------------------------------------------- configuration

def config_path() -> Path:
    env = os.environ.get("GEMINI_WORKER_CONFIG")
    return Path(env).expanduser() if env else Path.home() / ".gemini-worker" / "config.json"


def load_config() -> dict:
    p = config_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(read_text(p))
    except json.JSONDecodeError as e:
        die(f"config file is not valid JSON: {p} ({e})")
    if not isinstance(data, dict):
        die(f"config file must hold a JSON object: {p}")
    return data


CFG = load_config()
DEFAULT_MODEL = os.environ.get("GEMINI_WORKER_MODEL") or CFG.get("model") or BUILTIN_MODEL
try:
    MAX_PARALLEL = int(os.environ.get("GEMINI_MAX_PARALLEL") or CFG.get("max_parallel") or 2)
except ValueError:
    MAX_PARALLEL = 2


def cfg_path(key: str) -> Path | None:
    v = CFG.get(key)
    return Path(str(v)).expanduser() if v else None


def author() -> str:
    return str(CFG.get("author") or "科研人员")


def fill(template: str, **kw: str) -> str:
    """Replace {KEY} placeholders; str.format would trip over the braces in LaTeX examples."""
    for k, v in kw.items():
        template = template.replace("{" + k + "}", v)
    return template


# ----------------------------------------------------------------------------- agy, jobs, processes

INSTALL_HELP = ("agy (Antigravity CLI) not found. Ask the user to install it in their own terminal and "
                "then restart Claude Code. Windows PowerShell: "
                "irm https://antigravity.google/cli/install.ps1 | iex   macOS/Linux: "
                "curl -fsSL https://antigravity.google/cli/install.sh | bash   "
                "If agy is installed elsewhere, set GEMINI_AGY_EXE or \"agy\" in the config file.")

SIGNIN_HELP = ("agy is not signed in. Ask the user to run 'agy' in their own terminal and sign in "
               "with their Google account (any authorization code goes into that terminal, not the chat).")


def find_agy() -> str | None:
    exe = "agy.exe" if IS_WIN else "agy"
    cands: list[Path] = []
    if os.environ.get("GEMINI_AGY_EXE"):
        cands.append(Path(os.environ["GEMINI_AGY_EXE"]).expanduser())
    if cfg_path("agy"):
        cands.append(cfg_path("agy"))
    w = shutil.which("agy")
    if w:
        cands.append(Path(w))
    cands.append(Path.home() / ".local" / "bin" / exe)
    if IS_WIN and os.environ.get("LOCALAPPDATA"):
        cands.append(Path(os.environ["LOCALAPPDATA"]) / "agy" / "bin" / exe)
    for c in cands:
        if c.is_file():
            return str(c)
    return None


def need_agy() -> str:
    agy = find_agy()
    if not agy:
        die(INSTALL_HELP, 3)
    return agy


def agy_version(agy: str) -> str:
    try:
        r = subprocess.run([agy, "--version"], capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr) else "?"
    except Exception as e:  # noqa: BLE001
        return f"? ({e})"


def jobs_root() -> Path:
    if os.environ.get("GEMINI_WORKER_JOBS"):
        return Path(os.environ["GEMINI_WORKER_JOBS"]).expanduser()
    return cfg_path("jobs_dir") or Path.home() / ".gemini-worker" / "jobs"


def pid_alive(pid: int) -> bool:
    if not pid:
        return False
    if IS_WIN:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def running_jobs() -> list[Path]:
    root = jobs_root()
    out = []
    if not root.is_dir():
        return out
    for jf in root.glob("*/job.json"):
        try:
            j = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if j.get("status") == "running" and pid_alive(j.get("pid", 0)):
            out.append(jf.parent)
    return out


def check_model(model: str) -> str:
    m = model.strip()
    if not m.lower().startswith("gemini-") or "orchestrator" in m.lower():
        die(f"model '{m}' refused: only Gemini model slugs (gemini-...) are allowed, and not the "
            "Orchestrator (its executor can be a non-Gemini model). Run 'models' to list slugs.")
    return m


class StartLock:
    """Let only one agy process start at a time.

    Two agy processes started in the same second can hang at 'Waiting for migrations to
    complete' (seen 2026-11-22 with agy 1.5.14). The lock is held until the new process has
    started (init event, or the quick command finished), then released.
    """

    def __init__(self, wait_s: float = 300.0):
        self.path = jobs_root() / ".agy_start.lock"
        self.wait_s = wait_s
        self.held = False

    def acquire(self) -> "StartLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()} {time.time():.0f}".encode())
                os.close(fd)
                self.held = True
                return self
            except FileExistsError:
                try:
                    pid_s, ts_s = self.path.read_text().split()
                    stale = (not pid_alive(int(pid_s))) or time.time() - float(ts_s) > 180
                except (OSError, ValueError):
                    stale = True
                if stale:
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if time.time() - t0 > self.wait_s:
                    die("timed out waiting for another gemini-worker job to start agy", 5)
                time.sleep(1.0)

    def release(self) -> None:
        if self.held:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self.held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


def popen_kw() -> dict:
    """No console window on Windows; elsewhere a new session, so the whole tree can be killed."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WIN else {"start_new_session": True}


def kill_tree(pid: int) -> None:
    if IS_WIN:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def run_quick(args: list[str], timeout: float) -> tuple[int | None, str, str]:
    """Run a short agy command; on timeout kill the whole process tree and return rc None."""
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace", **popen_kw())
    try:
        out, err = p.communicate(timeout=timeout)
        return p.returncode, out or "", err or ""
    except subprocess.TimeoutExpired:
        kill_tree(p.pid)
        try:
            out, err = p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        return None, out or "", err or ""


def list_models(agy: str) -> tuple[int, list[str], str]:
    """'agy models' fails fast when not signed in, without starting a browser login."""
    for attempt in (1, 2):
        with StartLock():
            rc, out, err = run_quick([agy, "models"], timeout=90)
        if rc is not None:
            break
        print(f"note: 'agy models' hung (attempt {attempt}), killed it", file=sys.stderr)
    if rc is None:
        return 1, [], "agy models timed out twice"
    slugs = [ln.split()[0] for ln in out.splitlines() if re.match(r"^[a-z0-9][a-z0-9.\-]*\s", ln)]
    return rc, slugs, out + err


def is_signin_error(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in ("sign in", "signed in", "authentication", "log in"))


def preflight(agy: str, model: str, explicit: bool) -> str:
    """Never start a headless run unless signed in: an unauthenticated run opens a browser login.

    Returns the model to use. A default model this account lacks is replaced by the account's first
    Gemini model (with a note); a model the caller asked for explicitly is an error instead.
    """
    rc, slugs, out = list_models(agy)
    if rc != 0 or not slugs:
        if is_signin_error(out):
            die(SIGNIN_HELP, 3)
        die("'agy models' failed: " + out.strip()[:600], 1)
    if model in slugs:
        return model
    gem = [s for s in slugs if s.startswith("gemini-") and "orchestrator" not in s]
    if explicit or not gem:
        die(f"model {model} is not available to this account. Gemini models: "
            f"{', '.join(gem) or 'none'}")
    print(f"note: default model {model} is not available to this account; using {gem[0]}. "
          "Choose another with --model or \"model\" in the config file.", file=sys.stderr)
    return gem[0]


def new_job(name: str, mode: str) -> Path:
    if not NAME_RE.match(name):
        die("--name may only use letters, digits and . _ - (max 60 chars)")
    busy = running_jobs()
    if len(busy) >= MAX_PARALLEL:
        die(f"{len(busy)} Gemini jobs already running (limit {MAX_PARALLEL}): "
            + ", ".join(str(b) for b in busy), 5)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    jobs_root().mkdir(parents=True, exist_ok=True)
    for k in range(1, 100):
        job = jobs_root() / (f"{stamp}_{name}" if k == 1 else f"{stamp}_{name}_{k}")
        try:
            job.mkdir(exist_ok=False)
            return job
        except FileExistsError:
            continue
    die("could not create a job directory")


def load_job(job: Path) -> dict:
    jf = job / "job.json"
    if not jf.is_file():
        die(f"not a job directory (no job.json): {job}")
    return json.loads(jf.read_text(encoding="utf-8"))


def save_job(job: Path, data: dict) -> None:
    write_text(job / "job.json", json.dumps(data, ensure_ascii=False, indent=2))


def build_settings(mode: str, web: bool = False) -> dict:
    """Per-run settings passed with --settings (never saved to the user's settings.json).

    Both modes are read-only: no file writes, no shell commands, no MCP, no browser.
    polish also denies reading files and the web: the text is in the prompt.
    """
    deny = ["write_file(*)", "command(*)", "mcp(*)", "execute_url(*)"]
    allow: list[str] = []
    if mode == "polish":
        deny += ["read_file(*)", "read_url(*)"]
    elif web:
        allow.append("read_url(*)")
    else:
        deny.append("read_url(*)")
    return {
        "agentMode": "",                     # default mode: anything not allowed is soft-denied headless
        "toolPermission": "request-review",
        "artifactReviewPolicy": "always-proceed",
        "allowNonWorkspaceAccess": False,
        "disableSlashCommands": True,
        "useG1Credits": False,               # never spend paid AI credits after the plan quota
        "permissions": {"allow": allow, "deny": deny, "ask": []},
    }


# Custom agents restrict the tool set itself (tested with agy 1.5.14): with `tools: []` the model only
# sees the internal manage_task; with `view_file` it gets a read_file tool. list_dir, grep_search and
# find_by_name are accepted but not exposed, and the SDK names (list_directory, ...) are rejected.
AGENT_TOOLS = {
    "gw-polisher": [],
    "gw-reader": ["view_file"],
    "gw-reader-web": ["view_file", "search_web", "read_url_content"],
}
EXPECTED_TOOLS = {
    "gw-polisher": {"manage_task"},
    "gw-reader": {"read_file", "view_file", "manage_task"},
    "gw-reader-web": {"read_file", "view_file", "manage_task", "search_web", "read_url_content"},
}
AGENT_PROMPT = {
    "gw-polisher": ("You are a text editor working for gemini-worker. You have no tools and never try "
                    "to use any. Follow the user's instructions exactly and answer directly in text."),
    "gw-reader": ("You are a careful read-only reviewer working for gemini-worker. You can only read "
                  "files. Never try to write files, run commands or create artifacts. Put your whole "
                  "answer in your reply."),
}
AGENT_PROMPT["gw-reader-web"] = AGENT_PROMPT["gw-reader"] + " You may also search and read the web."


def write_agent(job_dir: Path, name: str) -> str:
    """Workspace agent in <job>/.agents/agents/, selected with --agent; job_dir is the cwd."""
    tools = AGENT_TOOLS[name]
    tools_yaml = "tools: []" if not tools else "tools:\n" + "\n".join(f"  - {t}" for t in tools)
    text = (f"---\nname: {name}\ndescription: gemini-worker {name} (restricted tool set)\n"
            f"{tools_yaml}\nmainAgent: true\nsubagent: false\n---\n\n# System Prompt\n"
            f"{AGENT_PROMPT[name]}\n")
    write_text(job_dir / ".agents" / "agents" / f"{name}.md", text)
    return name


# ----------------------------------------------------------------------------- talking to agy

class AgyStream:
    """One agy process in stream-json mode: send user messages, collect one result per turn."""

    def __init__(self, agy: str, extra_args: list[str], cwd: Path, events_path: Path,
                 stderr_path: Path):
        self.cmd = [agy, *extra_args, "--input-format", "stream-json",
                    "--output-format", "stream-json"]
        self.stderr_f = open(stderr_path, "a", encoding="utf-8", errors="replace")
        self.events_f = open(events_path, "a", encoding="utf-8")
        self.p = subprocess.Popen(self.cmd, cwd=str(cwd), stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=self.stderr_f, text=True,
                                  encoding="utf-8", errors="replace", bufsize=1, **popen_kw())
        self.q: queue.Queue = queue.Queue()
        self.init: dict | None = None
        self.tools_used: list[str] = []
        self.tool_errors: list[str] = []
        self.started = threading.Event()   # set at the first event (init) or at EOF
        self.t = threading.Thread(target=self._reader, daemon=True)
        self.t.start()

    def _reader(self) -> None:
        assert self.p.stdout is not None
        for line in self.p.stdout:
            self.events_f.write(line if line.endswith("\n") else line + "\n")
            self.events_f.flush()
            s = line.strip()
            if not s:
                continue
            try:
                ev = json.loads(s)
            except json.JSONDecodeError:
                ev = {"event": "_raw", "text": s}
            self.started.set()
            self.q.put(ev)
        self.started.set()
        self.q.put(None)

    def send(self, text: str) -> None:
        msg = {"event": "user", "message": {"content": text}}
        assert self.p.stdin is not None
        self.p.stdin.write(json.dumps(msg, ensure_ascii=True) + "\n")
        self.p.stdin.flush()

    def wait_result(self, deadline: float) -> dict:
        while True:
            left = deadline - time.time()
            if left <= 0:
                return {"status": "TIMEOUT", "response": "", "error": "time limit reached"}
            try:
                ev = self.q.get(timeout=min(left, 5.0))
            except queue.Empty:
                continue
            if ev is None:
                return {"status": "EOF", "response": "",
                        "error": "agy exited before returning a result (see stderr log)"}
            kind = ev.get("event")
            if kind == "init":
                self.init = ev
            elif kind == "result":
                return ev.get("result", {}) or {}
            elif kind == "step_update":
                su = ev.get("step_update", {}) or {}
                if su.get("step_type") == "tool" and su.get("state") == "DONE":
                    name = su.get("tool_name") or (su.get("tool_info") or {}).get("name") or "?"
                    self.tools_used.append(name)
                    err = (su.get("tool_info") or {}).get("error")
                    if err:
                        self.tool_errors.append(f"{name}: {err.get('type', '')} {err.get('message', '')}".strip())

    def close(self, grace: float = 60.0) -> int | None:
        try:
            if self.p.stdin:
                self.p.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.p.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            self.kill()
        self.t.join(timeout=5)
        self.events_f.close()
        self.stderr_f.close()
        return self.p.returncode

    def kill(self) -> None:
        kill_tree(self.p.pid)
        try:
            self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def agy_args(job: dict) -> list[str]:
    args = ["--settings", job["settings_file"], "--model", job["model"]]
    if job.get("agent"):
        args += ["--agent", job["agent"]]
    if job.get("effort"):
        args += ["--effort", job["effort"]]
    for d in job.get("add_dirs", []):
        args += ["--add-dir", d]
    if job.get("conversation_id"):
        args += ["--conversation", job["conversation_id"]]
    return args


def stderr_notes(path: Path, start: int = 0) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[start:]
    except FileNotFoundError:
        return []
    keep = []
    for line in text.splitlines():
        low = line.lower()
        if any(k in low for k in ("denied", "permission", "not allowed", "authentication",
                                  "sign in", "signed in", "error", "warning", "quota", "limit")):
            keep.append(line.strip())
    return keep[:40]


def auth_problem(lines: list[str]) -> bool:
    return any(("authentication" in l.lower() or "not signed in" in l.lower()
                or "sign in" in l.lower()) for l in lines)


def run_turns(agy: str, job_dir: Path, job: dict, round_no: int, messages: list[str],
              validate=None, timeout_min: float = 60.0) -> list[dict]:
    """Send messages one turn at a time in one agy process. validate(resp) -> retry message or None."""
    events = job_dir / f"r{round_no}_events.jsonl"
    errlog = job_dir / f"r{round_no}_stderr.log"
    start_err = errlog.stat().st_size if errlog.exists() else 0
    cwd = Path(job["cwd"])
    with StartLock():
        s = AgyStream(agy, agy_args(job), cwd, events, errlog)
        s.started.wait(timeout=90)   # the init event arrives after start-up, before any turn
    job.update(status="running", pid=os.getpid(), agy_pid=s.p.pid)
    save_job(job_dir, job)
    deadline = time.time() + timeout_min * 60
    results: list[dict] = []
    try:
        for i, msg in enumerate(messages, 1):
            write_text(job_dir / f"r{round_no}_prompt{'' if len(messages) == 1 else f'_{i:02d}'}.md", msg)
            s.send(msg)
            res = s.wait_result(deadline)
            tries = 0
            while validate and res.get("status") == "SUCCESS" and tries < 1:
                retry = validate(res.get("response", ""))
                if not retry:
                    break
                tries += 1
                s.send(retry)
                res = s.wait_result(deadline)
            res["_part"] = i
            results.append(res)
            if res.get("conversation_id"):
                job["conversation_id"] = res["conversation_id"]
            if res.get("status") != "SUCCESS":
                break
    finally:
        if results and results[-1].get("status") == "TIMEOUT":
            s.kill()
        rc = s.close()
    init = (s.init or {}).get("init", {}) if s.init else {}
    if s.init and s.init.get("conversation_id") and not job.get("conversation_id"):
        job["conversation_id"] = s.init["conversation_id"]
    notes = stderr_notes(errlog, start_err)
    last = results[-1] if results else {"status": "EOF", "error": "no result"}
    expected = EXPECTED_TOOLS.get(job.get("agent") or "", set())
    unexpected = sorted(set(s.tools_used) - expected) if job.get("agent") else []
    rnd = {
        "round": round_no,
        "ended": now(),
        "status": last.get("status"),
        "error": last.get("error"),
        "exit_code": rc,
        "parts": len(results),
        "agent": init.get("agent"),
        "model": init.get("model"),
        "permission_mode": init.get("permission_mode"),
        "tools_used": s.tools_used,
        "unexpected_tools": unexpected,
        "tool_errors": s.tool_errors,
        "stderr_notes": notes,
        "usage": last.get("usage"),
        "duration_seconds": last.get("duration_seconds"),
        "events_file": str(events),
        "stderr_file": str(errlog),
    }
    job.setdefault("rounds", []).append(rnd)
    ok = bool(results) and all(r.get("status") == "SUCCESS" for r in results) \
        and len(results) == len(messages)
    job["status"] = "ok" if ok else "error"
    job.pop("pid", None)
    job.pop("agy_pid", None)
    save_job(job_dir, job)
    if init.get("permission_mode") == "always-proceed":
        print("WARNING: agy ran with permission_mode=always-proceed; the read-only settings were "
              "not applied. Check the job before trusting it.", file=sys.stderr)
    if job.get("agent") and init and init.get("agent") != job["agent"]:
        print(f"WARNING: requested agent {job['agent']} but agy reported {init.get('agent')}; "
              "the tool restriction may not have applied.", file=sys.stderr)
    if unexpected:
        print("WARNING: Gemini used tools outside the expected set: " + ", ".join(unexpected)
              + ". Look at the events file before trusting the result.", file=sys.stderr)
    if init.get("model") and init.get("model") != job["model"]:
        print(f"WARNING: requested model {job['model']} but agy reported {init.get('model')}",
              file=sys.stderr)
    return results


def summary_line(job: dict) -> str:
    r = job["rounds"][-1]
    u = r.get("usage") or {}
    return (f"gemini round {r['round']}: {r['status']} | model {r.get('model') or job['model']}"
            f"{' effort ' + job['effort'] if job.get('effort') else ''} | "
            f"tokens in {u.get('input_tokens', '?')} out {u.get('output_tokens', '?')} "
            f"thinking {u.get('thinking_tokens', '?')} | {r.get('duration_seconds') or '?'} s")


def print_round_tail(job: dict) -> None:
    r = job["rounds"][-1]
    if r.get("error"):
        print(f"error: {r['error']}")
    if r.get("tools_used"):
        print("tools used: " + ", ".join(f"{k} x{v}" for k, v in Counter(r["tools_used"]).items()))
    if r.get("unexpected_tools"):
        print("UNEXPECTED tools: " + ", ".join(r["unexpected_tools"]))
    if r.get("tool_errors"):
        print("tool errors (first 5):")
        for t in r["tool_errors"][:5]:
            print("  " + t)
    if r.get("stderr_notes"):
        print("agy stderr notes (first 10):")
        for t in r["stderr_notes"][:10]:
            print("  " + t)


# ----------------------------------------------------------------------------- review

REVIEW_FOOTER = ("\n\n---\n请把完整答复直接写在回复正文里。不要写文件、不要生成 artifact、"
                 "不要运行命令；读不了的文件在答复里说明。")


def split_dirs(values: list[str] | None) -> list[str]:
    out = []
    for v in values or []:
        out += [x for x in v.split(";") if x.strip()]
    return out


def under(path: Path, roots: list[Path]) -> bool:
    for r in roots:
        try:
            path.relative_to(r)
            return True
        except ValueError:
            continue
    return False


def cmd_review(a) -> int:
    agy = need_agy()
    model = check_model(a.model or DEFAULT_MODEL)
    brief = read_text(a.brief)
    project = Path(a.project).resolve() if a.project else Path.cwd().resolve()
    if not project.is_dir():
        die(f"--project is not a directory: {project}")
    add_dirs = [Path(d).resolve() for d in split_dirs(a.add_dir)]
    for d in add_dirs:
        if not d.is_dir():
            die(f"--add-dir is not a directory: {d}")
    attach = []
    for f in a.attach or []:
        p = Path(f).resolve()
        if not p.exists():
            die(f"--attach not found: {p}")
        attach.append(p)
        parent = p if p.is_dir() else p.parent
        if not under(parent, [project, *add_dirs]):
            add_dirs.append(parent)
    model = preflight(agy, model, explicit=a.model is not None)
    job_dir = new_job(a.name, "review")
    settings = build_settings("review", web=a.web)
    write_text(job_dir / "settings.json", json.dumps(settings, indent=2))
    shutil.copyfile(a.brief, job_dir / "brief.md")
    agent = write_agent(job_dir, "gw-reader-web" if a.web else "gw-reader")
    # The job dir is the cwd (it holds the agent definition); the project and the attachment
    # folders are added to the workspace so Gemini may read them.
    dirs = []
    for d in [project, *add_dirs]:
        if str(d) not in dirs:
            dirs.append(str(d))
    role = fill(read_text(PROMPTS / "review_role.md"), AUTHOR=author(),
                REPLY_LANG=str(CFG.get("reply_language") or "任务说明所用的语言"))
    msg = role.strip() + "\n\n---\n\n# 任务说明\n\n" + brief.strip()
    msg += f"\n\n# 可读的目录\n" + "\n".join(f"- {d}" for d in dirs)
    if attach:
        msg += "\n\n# 需要读的文件（绝对路径）\n" + "\n".join(f"- {p}" for p in attach)
    msg += ("\n\n你只有读文件的工具，不能列目录、不能搜索；要读的文件都按上面给出的绝对路径读。"
            + REVIEW_FOOTER)
    job = {
        "name": a.name, "mode": "review", "created": now(), "cwd": str(job_dir),
        "project": str(project), "add_dirs": dirs, "attachments": [str(p) for p in attach],
        "agent": agent, "model": model, "effort": a.effort, "web": a.web,
        "settings_file": str(job_dir / "settings.json"), "agy": agy,
        "agy_version": agy_version(agy), "status": "new",
    }
    save_job(job_dir, job)
    results = run_turns(agy, job_dir, job, 1, [msg], timeout_min=a.timeout_min)
    resp = results[-1].get("response", "") if results else ""
    write_text(job_dir / "r1_response.md", resp)
    print(f"job: {job_dir}")
    print(summary_line(job))
    print_round_tail(job)
    if auth_problem(job["rounds"][-1]["stderr_notes"]) and job["status"] != "ok":
        print(SIGNIN_HELP)
        return 3
    print(f"response: {job_dir / 'r1_response.md'}")
    if job["status"] == "ok":
        print("----- response -----")
        print(resp.strip())
    return 0 if job["status"] == "ok" else 1


# ----------------------------------------------------------------------------- polish

POLISHED_RE = re.compile(r"<<<POLISHED[ \t]*\r?\n(.*?)\r?\n?POLISHED>>>", re.S)
NOTES_RE = re.compile(r"<<<NOTES[ \t]*\r?\n(.*?)\r?\n?NOTES>>>", re.S)
PARA_SPLIT = re.compile(r"\n[ \t]*\n")


def detect_kind(path: str) -> str:
    ext = Path(path).suffix.lower()
    return {".tex": "latex", ".md": "markdown", ".markdown": "markdown"}.get(ext, "text")


def detect_lang(text: str) -> str:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return "zh" if cjk > latin * 0.3 else "en"


def split_tex(text: str) -> tuple[str, str, str]:
    """Return (head, body, tail); only the document body is sent when there is a preamble."""
    b = re.search(r"\\begin\{document\}[^\n]*\n", text)
    e = text.rfind("\\end{document}")
    if b and e > b.end():
        return text[:b.end()], text[b.end():e], text[e:]
    return "", text, ""


def paragraphs(text: str) -> list[str]:
    return [p for p in PARA_SPLIT.split(text.strip("\n")) if p.strip()]


def make_chunks(text: str, max_chars: int) -> list[str]:
    paras = paragraphs(text)
    chunks, cur, size = [], [], 0
    for p in paras:
        if cur and size + len(p) > max_chars:
            chunks.append("\n\n".join(cur))
            cur, size = [], 0
        cur.append(p)
        size += len(p) + 2
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


BUILTIN_RULE = ("AI 腔清单（ai-tells.md）", BUNDLED_TELLS)


def default_rules() -> list[tuple[str, Path]]:
    """Rule files attached to the polish prompt: the config's "rules", else the bundled list.

    Each entry is a path, {"title": ..., "path": ...}, or "builtin" for the bundled rules/ai-tells.md.
    """
    items = CFG.get("rules")
    if items is None:
        return [BUILTIN_RULE]
    if isinstance(items, (str, dict)):
        items = [items]
    out = []
    for it in items:
        if it == "builtin":
            out.append(BUILTIN_RULE)
        elif isinstance(it, dict):
            p = Path(str(it.get("path", ""))).expanduser()
            out.append((str(it.get("title") or f"写作规则（{p.name}）"), p))
        else:
            p = Path(str(it)).expanduser()
            out.append((f"写作规则（{p.name}）", p))
    return out


def load_rules(spec: str) -> list[tuple[str, str]]:
    if spec == "none":
        return []
    if spec == "default":
        out = []
        for title, p in default_rules():
            if p.is_file():
                out.append((title, read_text(p)))
            else:
                print(f"note: rules file not found, skipped: {p}", file=sys.stderr)
        return out
    return [(f"写作规则（{Path(spec).name}）", read_text(spec))]


def strip_fence(s: str) -> str:
    m = re.match(r"^\s*```[A-Za-z]*\s*\n(.*)\n\s*```\s*$", s, re.S)
    return m.group(1) if m else s


def parse_polish(resp: str) -> tuple[str | None, str]:
    m = POLISHED_RE.search(resp or "")
    n = NOTES_RE.search(resp or "")
    pol = strip_fence(m.group(1)).strip("\n") if m else None
    notes = n.group(1).strip() if n else ""
    return (pol if pol and pol.strip() else None), notes


def cmd_polish(a) -> int:
    agy = need_agy()
    model = check_model(a.model or DEFAULT_MODEL)
    src = Path(a.input).resolve()
    text = read_text(src)
    kind = detect_kind(str(src))
    head, body, tail = split_tex(text) if kind == "latex" else ("", text, "")
    lang = a.lang if a.lang != "auto" else detect_lang(body)
    chunks = make_chunks(body, a.max_chars)
    if not chunks:
        die("input has no text to polish")
    rules = load_rules(a.rules)
    notes_txt = read_text(a.notes).strip() if a.notes else ""
    model = preflight(agy, model, explicit=a.model is not None)
    job_dir = new_job(a.name, "polish")
    settings = build_settings("polish")
    write_text(job_dir / "settings.json", json.dumps(settings, indent=2))
    agent = write_agent(job_dir, "gw-polisher")
    shutil.copyfile(src, job_dir / f"input{src.suffix}")
    if a.notes:
        shutil.copyfile(a.notes, job_dir / "notes_from_claude.md")
    kind_zh = {"latex": "LaTeX 源文件", "markdown": "Markdown 文本", "text": "纯文本"}[kind]
    lang_zh = {"en": "英文", "zh": "中文"}[lang]
    instr = fill(read_text(PROMPTS / "polish.md"), KIND=kind_zh, LANG=lang_zh, AUTHOR=author(),
                 NOTES_LANG=str(CFG.get("reply_language") or "中文"))
    first = instr.strip()
    if notes_txt:
        first += "\n\n## 本次任务的补充说明（来自 Claude）\n\n" + notes_txt
    for title, body_rules in rules:
        first += f"\n\n## {title}\n\n" + body_rules.strip()
    n = len(chunks)
    msgs = []
    for i, c in enumerate(chunks, 1):
        block = (f"\n\n## 待润色文字（第 {i}/{n} 部分）\n\n<<<INPUT\n{c}\nINPUT>>>\n\n"
                 "只输出 <<<POLISHED … POLISHED>>> 和 <<<NOTES … NOTES>>> 两个块。")
        msgs.append((first + block) if i == 1 else
                    (f"继续润色下一部分，要求和输出格式与第 1 部分相同。{block}"))
    job = {
        "name": a.name, "mode": "polish", "created": now(), "cwd": str(job_dir), "add_dirs": [],
        "input": str(src), "kind": kind, "lang": lang, "parts": n, "max_chars": a.max_chars,
        "rules": a.rules, "rule_files": [str(p) for _, p in default_rules()] if a.rules == "default"
        else ([] if a.rules == "none" else [a.rules]),
        "agent": agent, "model": model, "effort": a.effort,
        "settings_file": str(job_dir / "settings.json"), "agy": agy,
        "agy_version": agy_version(agy), "status": "new",
    }
    save_job(job_dir, job)

    def validate(resp: str):
        pol, _ = parse_polish(resp)
        if pol is None:
            return ("你上一条回复没有按要求给出 <<<POLISHED … POLISHED>>> 和 <<<NOTES … NOTES>>> "
                    "两个块。请对同一部分重新输出，块外不要写任何东西，不要调用工具。")
        return None

    results = run_turns(agy, job_dir, job, 1, msgs, validate=validate, timeout_min=a.timeout_min)
    out_parts, note_parts, bad = [], [], []
    for i, c in enumerate(chunks, 1):
        res = results[i - 1] if i - 1 < len(results) else None
        write_text(job_dir / f"r1_response_{i:02d}.md", (res or {}).get("response", ""))
        pol, nt = parse_polish((res or {}).get("response", "")) if res else (None, "")
        if res is None or res.get("status") != "SUCCESS" or pol is None:
            bad.append(i)
            out_parts.append(c)  # keep the original text for parts that failed
            note_parts.append(f"### 第 {i}/{n} 部分\n\n（没有拿到合格的润色结果，保留原文）")
        else:
            out_parts.append(pol)
            note_parts.append(f"### 第 {i}/{n} 部分\n\n{nt or '（无）'}")
    polished_body = "\n\n".join(out_parts)
    if kind == "latex" and head:
        polished = head + polished_body.strip("\n") + "\n\n" + tail
    else:
        polished = polished_body.rstrip("\n") + "\n"
    out_file = job_dir / f"polished{src.suffix or '.txt'}"
    write_text(out_file, polished)
    write_text(job_dir / "notes.md", "# Gemini 的修改说明\n\n" + "\n\n".join(note_parts) + "\n")
    report, fails = check_report(body, polished_body, kind, src.name, out_file.name)
    write_text(job_dir / "check.md", report)
    write_text(job_dir / "diff.md", diff_report(body, polished_body))
    if bad:
        job["status"] = "error"
        job["failed_parts"] = bad
    job["check_failures"] = fails
    save_job(job_dir, job)
    print(f"job: {job_dir}")
    print(summary_line(job))
    print_round_tail(job)
    if auth_problem(job["rounds"][-1]["stderr_notes"]) and job["status"] != "ok":
        print(SIGNIN_HELP)
        return 3
    print(f"parts: {n}; failed parts (original kept): {bad or 'none'}")
    print(f"content check: {'PASS' if not fails else 'FAIL -> ' + '; '.join(fails)}")
    print(f"polished: {out_file}")
    print(f"check:    {job_dir / 'check.md'}")
    print(f"diff:     {job_dir / 'diff.md'}")
    print(f"notes:    {job_dir / 'notes.md'}")
    return 0 if job["status"] == "ok" and not fails else 1


# ----------------------------------------------------------------------------- content check

NUM_RE = re.compile(r"(?<![A-Za-z0-9_.])([-\u2212]?)(\d+(?:[.,]\d+)*(?:[eE][-+\u2212]?\d+)?)")
IDENT_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]+\d[A-Za-z0-9_]*")
CITE_RE = re.compile(r"\\(?:cite|citep|citet|citealp|citeauthor|citeyear|parencite|textcite|"
                     r"autocite|footcite)\*?(?:\[[^\]]*\])*\{([^}]*)\}")
REF_RE = re.compile(r"\\(?:ref|eqref|autoref|cref|Cref|pageref|nameref)\*?\{([^}]*)\}")
LABEL_RE = re.compile(r"\\label\{([^}]*)\}")
ENV_RE = re.compile(r"\\(begin|end)\{([^}]*)\}")
BRACKET_CITE_RE = re.compile(r"\[(\d+(?:\s*[,\u2013\u2014-]\s*\d+)*)\]")
MD_CITE_RE = re.compile(r"\[@[^\]]+\]")
XREF_RE = re.compile(r"\b(Fig(?:ure)?s?|Tables?|Eqs?|Equations?|Sections?|Sec)\.?(?:~|\s)*\(?(\d+(?:\.\d+)*)"
                     r"|([图表式])\s*[（(]?(\d+(?:\.\d+)*)|第\s*(\d+(?:\.\d+)*)\s*[章节]")
MATH_RES = [
    re.compile(r"\$\$(.+?)\$\$", re.S),
    re.compile(r"(?<![\\$])\$(?!\$)(.+?)(?<!\\)\$", re.S),
    re.compile(r"\\\((.+?)\\\)", re.S),
    re.compile(r"\\\[(.+?)\\\]", re.S),
    re.compile(r"\\begin\{(equation|align|gather|multline|eqnarray|displaymath)(\*?)\}(.+?)"
               r"\\end\{\1\2\}", re.S),
]


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def math_segments(text: str) -> list[str]:
    segs = []
    for rx in MATH_RES:
        for m in rx.finditer(text):
            segs.append(norm_ws(m.group(m.lastindex or 0)))
    return segs


def features(text: str) -> dict[str, Counter]:
    nums = Counter(("-" if s else "") + n for s, n in NUM_RE.findall(text))
    xrefs = Counter()
    for m in XREF_RE.finditer(text):
        if m.group(1):
            k = m.group(1).lower()
            k = ("fig" if k.startswith("fig") else "table" if k.startswith("table")
                 else "eq" if k.startswith("eq") else "sec")
            xrefs[f"{k} {m.group(2)}"] += 1
        elif m.group(3):
            xrefs[f"{m.group(3)} {m.group(4)}"] += 1
        else:
            xrefs[f"第{m.group(5)}章节"] += 1
    return {
        "数字": nums,
        "带数字的符号（H1、A4、S52 等）": Counter(IDENT_RE.findall(text)),
        "LaTeX 引用 \\cite": Counter(k.strip() for g in CITE_RE.findall(text) for k in g.split(",")),
        "LaTeX 交叉引用 \\ref": Counter(REF_RE.findall(text)),
        "LaTeX \\label": Counter(LABEL_RE.findall(text)),
        "LaTeX 环境": Counter(f"{a}{{{b}}}" for a, b in ENV_RE.findall(text)),
        "方括号引用 [n]": Counter(re.sub(r"\s|[\u2013\u2014]", lambda x: "-" if x.group() in "\u2013\u2014" else "", g)
                              for g in BRACKET_CITE_RE.findall(text)),
        "Markdown 引用 [@key]": Counter(MD_CITE_RE.findall(text)),
        "图表公式章节编号": xrefs,
        "数学内容": Counter(math_segments(text)),
    }


def counter_diff(a: Counter, b: Counter) -> tuple[list[str], list[str]]:
    missing = sorted((a - b).elements())
    added = sorted((b - a).elements())
    return missing, added


def tells_file() -> Path:
    if CFG.get("tells") == "builtin":
        return BUNDLED_TELLS
    return cfg_path("tells") or BUNDLED_TELLS


def load_tells() -> dict[str, re.Pattern]:
    """Regexes from the two code blocks after 'EN（加 `-i`）：' and 'ZH：' (see rules/ai-tells.md)."""
    p = tells_file()
    out: dict[str, re.Pattern] = {}
    if not p.is_file():
        return out
    t = read_text(p)
    for key, label, flags in (("EN", r"EN（加 `-i`）：", re.I), ("ZH", r"ZH：", 0)):
        m = re.search(re.escape(label) + r"\s*```[^\n]*\n(.*?)\n```", t, re.S) if key == "EN" \
            else re.search(r"\nZH：\s*```[^\n]*\n(.*?)\n```", t, re.S)
        if m:
            try:
                out[key] = re.compile(m.group(1).strip(), flags)
            except re.error as e:
                print(f"note: tells regex {key} not usable in Python: {e}", file=sys.stderr)
    return out


def tells_hits(text: str, pats: dict[str, re.Pattern]) -> Counter:
    c = Counter()
    for key, rx in pats.items():
        for m in rx.finditer(text):
            c[f"{key}: {m.group(0).strip().lower() if key == 'EN' else m.group(0).strip()}"] += 1
    return c


def size_of(text: str) -> int:
    return len(re.findall(r"[A-Za-z]+", text)) + len(re.findall(r"[\u4e00-\u9fff]", text))


def check_report(orig: str, new: str, kind: str, orig_name: str, new_name: str) -> tuple[str, list[str]]:
    fo, fn = features(orig), features(new)
    lines = ["# 润色内容核对（脚本自动）", "",
             f"原文：{orig_name}　润色后：{new_name}", "",
             "数字、引用、交叉引用、数学内容、LaTeX 环境任何一项有出入，判为不通过，"
             "对应位置要逐条看；“带数字的符号”和“图表公式章节编号”有出入也要看。", "",
             "| 项目 | 结果 | 原文 | 润色后 | 少了 | 多了 |", "|---|---|---|---|---|---|"]
    fails = []
    for k in fo:
        missing, added = counter_diff(fo[k], fn[k])
        ok = not missing and not added
        if not ok:
            fails.append(k)
        def show(xs):
            s = ", ".join(xs[:12]) + (f" …（共 {len(xs)} 个）" if len(xs) > 12 else "")
            return s.replace("|", "\\|") or "—"
        lines.append(f"| {k} | {'通过' if ok else '**不通过**'} | {sum(fo[k].values())} | "
                     f"{sum(fn[k].values())} | {show(missing)} | {show(added)} |")
    po, pn = paragraphs(orig), paragraphs(new)
    so, sn = size_of(orig), size_of(new)
    ratio = (sn / so) if so else 1.0
    lines += ["", f"- 段落数：原文 {len(po)}，润色后 {len(pn)}"
              + ("" if len(po) == len(pn) else "（**段落数变了，逐段核对时注意对齐**）"),
              f"- 篇幅（英文词数加汉字数）：原文 {so}，润色后 {sn}，比值 {ratio:.2f}"
              + ("" if 0.8 <= ratio <= 1.2 else "（**变化超过 20%，要看是不是删了或加了内容**）")]
    if len(po) == len(pn):
        rows = []
        for i, (a, b) in enumerate(zip(po, pn), 1):
            fa, fb = features(a), features(b)
            bad = [k for k in fa if counter_diff(fa[k], fb[k]) != ([], [])]
            if bad:
                rows.append(f"- 第 {i} 段：{'、'.join(bad)}")
        lines += ["", "## 逐段：有出入的段落", ""] + (rows or ["- 无"])
    pats = load_tells()
    if pats:
        new_hits = tells_hits(new, pats) - tells_hits(orig, pats)
        lines += ["", f"## AI 腔正则：润色后新增的命中（{tells_file().name}）", ""]
        lines += [f"- {h} ×{c}" for h, c in new_hits.most_common()] or ["- 无"]
    return "\n".join(lines) + "\n", fails


TOKEN_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]|\s+|[A-Za-z0-9_\\]+|.", re.S)


def word_diff(a: str, b: str) -> str:
    ta, tb = TOKEN_RE.findall(a), TOKEN_RE.findall(b)
    sm = difflib.SequenceMatcher(a=ta, b=tb, autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            out.append("".join(ta[i1:i2]))
            continue
        if i2 > i1:
            out.append("[-" + "".join(ta[i1:i2]) + "-]")
        if j2 > j1:
            out.append("{+" + "".join(tb[j1:j2]) + "+}")
    return "".join(out)


def diff_report(orig: str, new: str) -> str:
    po, pn = paragraphs(orig), paragraphs(new)
    head = ["# 逐段改动（[-删去-]{+加上+}）", ""]
    if len(po) != len(pn):
        head += [f"段落数不同（原文 {len(po)}，润色后 {len(pn)}），下面按全文比较。", "",
                 "```text", word_diff(orig, new), "```", ""]
        return "\n".join(head)
    same = 0
    body = []
    for i, (a, b) in enumerate(zip(po, pn), 1):
        if a == b:
            same += 1
            continue
        body += [f"## 第 {i} 段", "", "```text", word_diff(a, b), "```", ""]
    head += [f"共 {len(po)} 段，未改动 {same} 段。", ""]
    return "\n".join(head + body)


def cmd_check(a) -> int:
    o = read_text(a.original)
    n = read_text(a.polished)
    kind = detect_kind(a.original)
    if kind == "latex":
        o, n = split_tex(o)[1], split_tex(n)[1]
    report, fails = check_report(o, n, kind, Path(a.original).name, Path(a.polished).name)
    out = Path(a.out) if a.out else Path(a.polished).with_name(Path(a.polished).stem + "_check.md")
    write_text(out, report)
    if a.diff:
        write_text(out.with_name(out.stem.replace("_check", "") + "_diff.md"), diff_report(o, n))
    print(f"content check: {'PASS' if not fails else 'FAIL -> ' + '; '.join(fails)}")
    print(f"report: {out}")
    return 0 if not fails else 1


# ----------------------------------------------------------------------------- continue

def cmd_continue(a) -> int:
    agy = need_agy()
    job_dir = Path(a.job).resolve()
    job = load_job(job_dir)
    if not job.get("conversation_id"):
        die("this job has no conversation id to continue")
    if job.get("status") == "running" and pid_alive(job.get("pid", 0)):
        die("this job is still running", 5)
    if len(running_jobs()) >= MAX_PARALLEL:
        die(f"{MAX_PARALLEL} Gemini jobs already running", 5)
    if a.model:
        job["model"] = check_model(a.model)
    if a.effort:
        job["effort"] = a.effort
    msg = read_text(a.message).strip()
    if job["mode"] == "review":
        msg += REVIEW_FOOTER
    job["model"] = preflight(agy, job["model"], explicit=bool(a.model))
    n = len(job.get("rounds", [])) + 1
    results = run_turns(agy, job_dir, job, n, [msg], timeout_min=a.timeout_min)
    resp = results[-1].get("response", "") if results else ""
    write_text(job_dir / f"r{n}_response.md", resp)
    print(f"job: {job_dir}")
    print(summary_line(job))
    print_round_tail(job)
    print(f"response: {job_dir / f'r{n}_response.md'}")
    if job["status"] == "ok":
        print("----- response -----")
        print(resp.strip())
    return 0 if job["status"] == "ok" else 1


# ----------------------------------------------------------------------------- doctor / models

def cmd_models(a) -> int:
    agy = need_agy()
    rc, slugs, out = list_models(agy)
    print(out.rstrip())
    if rc != 0 or not slugs:
        if is_signin_error(out):
            print(SIGNIN_HELP)
            return 3
        return 1
    return 0


def cmd_doctor(a) -> int:
    cp = config_path()
    print(f"python:        {sys.executable} ({sys.version.split()[0]})")
    print(f"config:        {cp} ({'found' if cp.is_file() else 'not found, using defaults'})")
    agy = find_agy()
    print(f"agy:           {agy or 'NOT FOUND'}")
    if not agy:
        print(INSTALL_HELP)
        return 3
    print(f"version:       {agy_version(agy)}")
    print(f"jobs root:     {jobs_root()}")
    print(f"default model: {DEFAULT_MODEL}")
    for _, p in default_rules():
        print(f"polish rules:  {p} ({'found' if p.is_file() else 'MISSING'})")
    tf = tells_file()
    print(f"tells regex:   {tf} ({'found' if tf.is_file() else 'MISSING'})")
    print(f"running jobs:  {len(running_jobs())} (limit {MAX_PARALLEL})")
    print("--- agy models ---")
    rc = cmd_models(a)
    if rc != 0:
        return rc
    if not a.live:
        return 0
    print("--- live call ---")
    model = preflight(agy, check_model(a.model or DEFAULT_MODEL), explicit=a.model is not None)
    job_dir = new_job("doctor-live", "polish")
    write_text(job_dir / "settings.json", json.dumps(build_settings("polish"), indent=2))
    job = {"name": "doctor-live", "mode": "doctor", "created": now(), "cwd": str(job_dir),
           "add_dirs": [], "agent": write_agent(job_dir, "gw-polisher"),
           "model": model, "effort": a.effort,
           "settings_file": str(job_dir / "settings.json"), "agy": agy,
           "agy_version": agy_version(agy), "status": "new"}
    save_job(job_dir, job)
    res = run_turns(agy, job_dir, job, 1, ["Reply with exactly the word GEMINI_OK and nothing else. "
                                            "Do not use any tools."], timeout_min=5)
    r = job["rounds"][-1]
    resp = (res[-1].get("response", "") if res else "").strip()
    print(summary_line(job))
    print(f"response: {resp!r}")
    print(f"agent: {r.get('agent')} | permission_mode: {r.get('permission_mode')} | "
          f"model reported: {r.get('model')}")
    print_round_tail(job)
    print(f"job: {job_dir}")
    return 0 if job["status"] == "ok" and "GEMINI_OK" in resp else 1


# ----------------------------------------------------------------------------- CLI

def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="gemini.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--model", help=f"Gemini model slug (default {DEFAULT_MODEL})")
        p.add_argument("--effort", choices=EFFORTS,
                       help="reasoning effort, if the model slug does not already fix it")
        p.add_argument("--timeout-min", type=float, default=60.0)

    p = sub.add_parser("doctor")
    p.add_argument("--live", action="store_true", help="also make one tiny real call")
    p.add_argument("--model", help=f"Gemini model slug (default {DEFAULT_MODEL})")
    p.add_argument("--effort", choices=EFFORTS)
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("models")
    p.set_defaults(fn=cmd_models)

    p = sub.add_parser("review")
    p.add_argument("--brief", required=True, help="task description (.md, UTF-8)")
    p.add_argument("--name", required=True)
    p.add_argument("--project", help="directory Gemini may read (default: cwd)")
    p.add_argument("--add-dir", action="append", help="extra readable directory; repeat or use ;")
    p.add_argument("--attach", action="append", help="file Gemini must read (PDF, image, ...); repeatable")
    p.add_argument("--web", action="store_true", help="allow web search and reading web pages")
    common(p)
    p.set_defaults(fn=cmd_review)

    p = sub.add_parser("polish")
    p.add_argument("--input", required=True, help=".tex / .md / .txt to polish (UTF-8)")
    p.add_argument("--name", required=True)
    p.add_argument("--notes", help="extra instructions for this text (journal, terms to keep, ...)")
    p.add_argument("--rules", default="default",
                   help="default = the config's \"rules\" (else the bundled rules/ai-tells.md); "
                        "none; or one file")
    p.add_argument("--lang", choices=["auto", "en", "zh"], default="auto")
    p.add_argument("--max-chars", type=int, default=12000, help="characters per turn")
    common(p)
    p.set_defaults(fn=cmd_polish)

    p = sub.add_parser("continue")
    p.add_argument("--job", required=True)
    p.add_argument("--message", required=True, help="follow-up message (.md)")
    p.add_argument("--model")
    p.add_argument("--effort", choices=EFFORTS)
    p.add_argument("--timeout-min", type=float, default=60.0)
    p.set_defaults(fn=cmd_continue)

    p = sub.add_parser("check")
    p.add_argument("--original", required=True)
    p.add_argument("--polished", required=True)
    p.add_argument("--out")
    p.add_argument("--diff", action="store_true", help="also write a word diff next to the report")
    p.set_defaults(fn=cmd_check)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
