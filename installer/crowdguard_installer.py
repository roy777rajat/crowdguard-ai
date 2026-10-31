#!/usr/bin/env python3
"""CrowdGuard AI - guided installer / launcher (Windows, macOS, Linux). Standard library only.

    python crowdguard_installer.py            # guided install (default)
    python crowdguard_installer.py start|stop|status|doctor   # run from the install folder

How it works: steps run one by one with plain, deterministic checks. Nothing in the happy path uses AI.
If a step fails, built-in fixes are tried first. If it still fails and an AI provider was configured, the AI
helper suggests ONE fix from a fixed menu of pre-written actions; you are asked Yes/No before it is applied.
The AI can never run commands of its own. Logs: <install folder>/logs/installer.log
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

REPO_URL = "https://github.com/roy777rajat/crowdguard-ai.git"
ZIP_URL = "https://github.com/roy777rajat/crowdguard-ai/archive/refs/heads/main.zip"
META_URL = "https://api.meta.ai/v1/responses"
META_MODEL = "muse-spark-1.3"
BACKEND_PORT, FRONTEND_PORT = 8000, 5173
IS_WIN = os.name == "nt"
MIN_PY, MIN_NODE = (3, 10), 20

# ------------------------------------------------------------------ console
try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
except Exception:  # pragma: no cover
    pass
if IS_WIN:
    os.system("")  # enables ANSI colours in modern Windows terminals
USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
C = {"r": "31", "g": "32", "y": "33", "b": "34", "m": "35", "c": "36", "w": "97", "d": "90"}


def col(text: str, c: str, bold: bool = False) -> str:
    if not USE_COLOR:
        return text
    return f"\033[{'1;' if bold else ''}{C[c]}m{text}\033[0m"


def out(text: str = "") -> None:
    print(text, flush=True)


def heading(i: int, n: int, title: str, why: str) -> None:
    bar = "#" * i + "-" * (n - i)
    out()
    out(col("=" * 72, "c"))
    out(col(f"  STEP {i} of {n}  [{bar}]  {title}", "c", True))
    out(col(f"  {why}", "d"))
    out(col("=" * 72, "c"))


def info(msg: str) -> None:
    out("  " + col("[ .. ]", "b") + " " + msg)


def ok(msg: str) -> None:
    out("  " + col("[ OK ]", "g", True) + " " + msg)


def warn(msg: str) -> None:
    out("  " + col("[WARN]", "y", True) + " " + msg)


def bad(msg: str) -> None:
    out("  " + col("[FAIL]", "r", True) + " " + msg)


def sub(msg: str) -> None:
    out("         " + col(msg, "d"))


INTERACTIVE = True  # set False by --yes


def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    if not INTERACTIVE:
        return default
    suffix = f" [{default}]" if default and not secret else ""
    try:
        text = (getpass.getpass if secret else input)("  " + col("?", "m", True) + f" {prompt}{suffix}: ")
    except (EOFError, KeyboardInterrupt):
        out()
        return default
    return text.strip() or default


def yesno(prompt: str, default: bool = True) -> bool:
    if not INTERACTIVE:
        return default
    d = "Y/n" if default else "y/N"
    ans = ask(f"{prompt} ({d})", "").lower()
    return default if not ans else ans.startswith("y")


# ------------------------------------------------------------------ log + command runner
LOG: Optional[Path] = None


def log(text: str) -> None:
    if LOG:
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a", encoding="utf-8", errors="replace") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")
        except OSError:
            pass


class StepError(Exception):
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


def run(cmd: List[str], cwd: Optional[Path] = None, label: str = "", timeout: int = 1200,
        env: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
    """Run a command, show a small live status line, keep full output in the log. Returns (code, output)."""
    log("$ " + " ".join(cmd))
    try:
        proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                env={**os.environ, **(env or {})})
    except (OSError, ValueError) as e:
        return 127, f"cannot start {cmd[0]}: {e}"
    lines: List[str] = []
    last = [""]

    def reader() -> None:
        assert proc.stdout
        for line in proc.stdout:
            lines.append(line.rstrip())
            if line.strip():
                last[0] = line.strip()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    start, spin, i = time.time(), "|/-\\", 0
    while t.is_alive():
        t.join(0.3)
        if time.time() - start > timeout:
            proc.kill()
            lines.append(f"TIMEOUT after {timeout}s")
            break
        if USE_COLOR and label:
            msg = f"         {spin[i % 4]} {label} ({int(time.time() - start)}s)  {last[0][:40]}"
            sys.stdout.write("\r" + col(msg[:100].ljust(100), "d"))
            sys.stdout.flush()
            i += 1
    proc.wait()
    if USE_COLOR and label:
        sys.stdout.write("\r" + " " * 100 + "\r")
    text = "\n".join(lines)
    log(text[-4000:])
    return (proc.returncode if proc.returncode is not None else 1), text


# ------------------------------------------------------------------ AI helper (failure diagnosis only)
class AIHelper:
    """Optional. Asks Meta Muse Spark which pre-written fix to try. Sends only a redacted error tail."""

    def __init__(self) -> None:
        self.key = ""
        self.provider = "none"

    @property
    def enabled(self) -> bool:
        return self.provider == "meta" and bool(self.key)

    def redact(self, text: str) -> str:
        home = str(Path.home())
        text = text.replace(home, "~")
        user = getpass.getuser()
        if len(user) > 2:
            text = re.sub(re.escape(user), "<user>", text)
        if self.key:
            text = text.replace(self.key, "<key>")
        text = re.sub(r"(?i)(bearer\s+|key=|token=|password=)\S+", r"\1<redacted>", text)
        text = re.sub(r"\b[A-Za-z0-9_\-]{32,}\b", "<redacted>", text)
        return text

    def _call(self, instructions: str, user: str, tokens: int) -> str:
        body = json.dumps({"model": META_MODEL, "instructions": instructions, "input": user,
                           "max_output_tokens": tokens, "store": False,
                           "reasoning": {"effort": "minimal"}}).encode()
        req = urllib.request.Request(META_URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as r:
            data = json.loads(r.read().decode())
        if data.get("output_text"):
            return str(data["output_text"])
        parts = []
        for item in data.get("output", []):
            if item.get("type") == "message":
                parts += [c.get("text", "") for c in item.get("content", []) if c.get("type") == "output_text"]
        return "".join(parts)

    def validate(self) -> Tuple[bool, str]:
        try:
            self._call("Reply with the single word OK.", "ping", 64)
            return True, "key accepted"
        except urllib.error.HTTPError as e:
            return False, {401: "key rejected (401)", 403: "key not allowed (403)", 402: "billing issue (402)",
                           429: "rate limited (429)"}.get(e.code, f"HTTP {e.code}")
        except Exception as e:  # network etc.
            return False, f"could not reach Meta ({type(e).__name__})"

    def suggest(self, step: str, error: str, actions: Dict[str, str]) -> Optional[Tuple[str, str]]:
        menu = "\n".join(f"- {k}: {v}" for k, v in actions.items())
        instr = ("You help fix a failed step of a software installer. Choose exactly ONE action id from the menu "
                 'that is most likely to fix the error, or "none". Reply with JSON only: '
                 '{"action":"<id or none>","reason":"<one plain sentence for a non-technical user>"}.')
        user = (f"OS: {platform.system()} {platform.release()}\nStep: {step}\n"
                f"Error (redacted, last lines):\n{self.redact(error)[-1800:]}\n\nMenu:\n{menu}")
        try:
            text = self._call(instr, user, 1500)
            m = re.search(r"\{.*\}", text, re.S)
            if not m:
                return None
            obj = json.loads(m.group(0))
            act = str(obj.get("action", "none"))
            return (act, str(obj.get("reason", ""))) if act in actions else None
        except Exception as e:
            log(f"AI helper unavailable: {e}")
            return None


AI = AIHelper()


# ------------------------------------------------------------------ context + helpers
class Ctx:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.root = Path()
        self.py = sys.executable
        self.venv_py = Path()
        self.node_ok = False
        self.summary: Dict[str, str] = {}
        self.steps: Dict[str, "Step"] = {}
        self.env_extra: Dict[str, str] = {}

    def vpy(self) -> str:
        return str(self.venv_py)


def venv_python(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")


def which(name: str) -> Optional[str]:
    return shutil.which(name + (".cmd" if IS_WIN and name in ("npm", "npx") else "")) or shutil.which(name)


def port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def http_get(url: str, timeout: float = 5) -> Tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, str(e)


def http_json(url: str, data: Optional[dict] = None, token: str = "") -> Tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception:
        return 0, {}


def pkg_install(tool: str) -> Tuple[int, str]:
    """Install Node/Git with the platform package manager (asks first; may need admin rights)."""
    names = {"node": {"winget": "OpenJS.NodeJS.LTS", "brew": "node", "apt": "nodejs npm", "dnf": "nodejs npm"},
             "git": {"winget": "Git.Git", "brew": "git", "apt": "git", "dnf": "git"}}[tool]
    if IS_WIN and which("winget"):
        return run(["winget", "install", "-e", "--id", names["winget"], "--accept-package-agreements",
                    "--accept-source-agreements"], label=f"installing {tool} (winget)")
    if platform.system() == "Darwin" and which("brew"):
        return run(["brew", "install", names["brew"]], label=f"installing {tool} (brew)")
    sudo = [] if (hasattr(os, "geteuid") and os.geteuid() == 0) else (["sudo"] if which("sudo") else [])
    if which("apt-get"):
        run(sudo + ["apt-get", "update"], label="apt update")
        return run(sudo + ["apt-get", "install", "-y"] + names["apt"].split(), label=f"installing {tool} (apt)")
    if which("dnf"):
        return run(sudo + ["dnf", "install", "-y"] + names["dnf"].split(), label=f"installing {tool} (dnf)")
    return 1, "no supported package manager found (winget/brew/apt/dnf)"


# ------------------------------------------------------------------ steps
class Step:
    def __init__(self, sid: str, title: str, why: str, fn: Callable[[Ctx], None],
                 actions: Optional[Dict[str, Tuple[str, Callable[[Ctx], None]]]] = None,
                 builtin: Optional[List[Tuple[str, Callable[[Ctx], None]]]] = None, optional: bool = False):
        self.sid, self.title, self.why, self.fn = sid, title, why, fn
        self.actions = actions or {}
        self.builtin = builtin or []
        self.optional = optional


def step_welcome(ctx: Ctx) -> None:
    pass  # handled before the step loop (needs to ask for the AI key first)


def step_location(ctx: Ctx) -> None:
    default = ctx.args.dir or str(Path("C:/CrowdGuard") if IS_WIN else Path.home() / "CrowdGuard")
    chosen = ask("Where should CrowdGuard AI be installed? (press Enter for the default)", default)
    root = Path(chosen).expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise StepError(f"{root} exists and is a file")
    if root.exists() and any(root.iterdir()) and not (root / ".git").exists() \
            and not (root / "backend").exists():
        warn(f"{root} is not empty and is not a CrowdGuard folder.")
        root = root / "crowdguard-ai"
        info(f"Using the sub-folder {root} instead.")
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write_test"
        probe.write_text("x")
        probe.unlink()
    except OSError as e:
        raise StepError(f"Cannot write to {root}: {e}", str(e))
    ctx.root = root
    global LOG
    LOG = root / "logs" / "installer.log"
    log(f"install root {root}; {platform.platform()}; python {sys.version.split()[0]}")
    existing = (root / ".git").exists() or (root / "backend").exists()
    ok(f"Install folder: {root}  ({'existing install - will update' if existing else 'new install'})")
    ctx.summary["Install folder"] = str(root)


def step_prereq(ctx: Ctx) -> None:
    rows: List[Tuple[str, bool, str]] = []
    info("Checking your computer...")
    rows.append(("Operating system", True, f"{platform.system()} {platform.release()} ({platform.machine()})"))
    pyv = sys.version_info
    rows.append(("Python >= %d.%d" % MIN_PY, pyv[:2] >= MIN_PY, f"{sys.version.split()[0]} at {sys.executable}"))
    if pyv[:2] == (3, 10):
        warn("Python 3.10 works for the app, but 3.11+ is recommended (some test tools misbehave on early 3.10 builds).")
    free_gb = shutil.disk_usage(ctx.root).free / 1e9
    rows.append(("Free disk space (need ~1.5 GB)", free_gb >= 1.5, f"{free_gb:.1f} GB free"))
    for name, url in (("Internet: GitHub", "https://github.com"), ("Internet: PyPI", "https://pypi.org/simple/"),
                      ("Internet: npm registry", "https://registry.npmjs.org/")):
        code, _ = http_get(url, 8)
        rows.append((name, code != 0, "reachable" if code else "NOT reachable"))  # any HTTP answer means the network works
    for p in (BACKEND_PORT, FRONTEND_PORT):
        busy = port_busy(p)
        mine = busy and (ctx.root / ".crowdguard" / "pids.json").exists()
        rows.append((f"Port {p} free", (not busy) or mine,
                     "free" if not busy else ("in use by a previous CrowdGuard (will be restarted)" if mine
                                              else "IN USE by another program")))
    node = which("node")
    npm = which("npm")
    nver, node_ok = "not found", False
    if node:
        _, v = run([node, "--version"])
        m = re.match(r"v(\d+)", v.strip())
        nver = v.strip()
        node_ok = bool(m and int(m.group(1)) >= MIN_NODE) and bool(npm)
    rows.append((f"Node.js >= {MIN_NODE} + npm", node_ok, f"{nver} at {node}" if node else "not installed"))
    git = which("git")
    rows.append(("Git (optional; ZIP download is the fallback)", True, git or "not installed - will use ZIP"))

    out()
    for name, good, detail in rows:
        (ok if good else bad)(f"{name:<46} {col(detail, 'd')}")
        log(f"prereq {name}: {good} {detail}")

    fatal = [r for r in rows if not r[1] and not r[0].startswith("Node")]
    if any(r[0].startswith("Python") for r in fatal):
        raise StepError(f"Python {MIN_PY[0]}.{MIN_PY[1]}+ is required. Re-run install.sh / install.ps1, which "
                        "installs it for you.")
    if any("Internet" in r[0] for r in fatal):
        raise StepError("No internet access to a required site. Check your network, VPN or proxy and try again.",
                        "; ".join(f"{r[0]}={r[2]}" for r in fatal))
    if any("disk" in r[0] for r in fatal):
        raise StepError("Not enough free disk space.")
    if any("Port" in r[0] for r in fatal):
        raise StepError("Port 8000 or 5173 is used by another program. Close it and try again.")
    if not node_ok:
        warn("Node.js is needed for the web screens.")
        if yesno("Install Node.js now using your system's package manager? (may ask for your password)"):
            code, txt = pkg_install("node")
            if code != 0:
                raise StepError("Could not install Node.js automatically.", txt)
            # PATH may not be refreshed in this process (Windows). Re-check, and give a clear hint if not.
            if not (which("node") and which("npm")):
                raise StepError("Node.js was installed but is not visible yet. Close this window, open a new "
                                "one and run the installer again.", txt)
            ok("Node.js installed")
        else:
            raise StepError("Node.js is required. Install it from https://nodejs.org (LTS) and run again.")
    ctx.node_ok = True
    ctx.summary["Python"] = sys.version.split()[0]
    _, nv = run([which("node") or "node", "--version"])
    ctx.summary["Node.js"] = nv.strip()


def step_code(ctx: Ctx) -> None:
    repo = ctx.args.repo or REPO_URL
    root = ctx.root
    git = which("git")
    if (root / ".git").exists() and git:
        info("Existing install found - fetching updates (your data and settings are kept)")
        code, txt = run([git, "pull", "--ff-only"], cwd=root, label="git pull")
        if code != 0:
            raise StepError("git pull failed (you may have local edits).", txt)
    elif git and not (root / "backend").exists():
        info(f"Downloading the code from {repo}")
        tmp = Path(tempfile.mkdtemp(prefix="cg-clone-"))
        code, txt = run([git, "clone", "--depth", "1", repo, str(tmp / "r")], label="git clone")
        if code != 0:
            raise StepError("git clone failed.", txt)
        for item in (tmp / "r").iterdir():
            dest = root / item.name
            if dest.exists():
                continue
            shutil.move(str(item), str(dest))
        shutil.rmtree(tmp, ignore_errors=True)
    elif not (root / "backend").exists():
        info("Git is not installed - downloading a ZIP of the code instead")
        zpath = root / "download.zip"
        try:
            urllib.request.urlretrieve(ZIP_URL, zpath)
            with zipfile.ZipFile(zpath) as z:
                z.extractall(root / "_unzip")
        except Exception as e:
            raise StepError("ZIP download failed.", str(e))
        inner = next((root / "_unzip").iterdir())
        for item in inner.iterdir():
            shutil.move(str(item), str(root / item.name))
        shutil.rmtree(root / "_unzip", ignore_errors=True)
        zpath.unlink(missing_ok=True)
    else:
        info("Code already present - leaving it as it is")
    for must in ("backend/api/app.py", "requirements.txt", "frontend/package.json"):
        if not (root / must).exists():
            raise StepError(f"Code is incomplete: {must} is missing.")
    n_files = sum(1 for _ in (root / "backend").rglob("*.py"))
    ok(f"Code ready ({n_files} backend modules)")
    ctx.summary["Code source"] = repo


def step_python(ctx: Ctx) -> None:
    root = ctx.root
    ctx.venv_py = venv_python(root)
    if not ctx.venv_py.exists():
        info("Creating an isolated Python environment (.venv) - nothing is installed system-wide")
        code, txt = run([ctx.py, "-m", "venv", str(root / ".venv")], cwd=root, label="creating venv")
        if code != 0:
            raise StepError("Could not create the Python environment.", txt)
    ok(f"Python environment: {ctx.venv_py}")
    code, txt = run([ctx.vpy(), "-m", "pip", "install", "-r", "requirements.txt", "--disable-pip-version-check"],
                    cwd=root, label="installing Python packages")
    if code != 0:
        raise StepError("pip could not install the Python packages.", txt)
    code, txt = run([ctx.vpy(), "-c", "import fastapi,uvicorn,pydantic,httpx;print(fastapi.__version__)"], cwd=root)
    if code != 0:
        raise StepError("Python packages installed but cannot be imported.", txt)
    ok(f"Python packages installed and importable (FastAPI {txt.strip()})")


def step_frontend(ctx: Ctx) -> None:
    fe = ctx.root / "frontend"
    npm = which("npm") or "npm"
    cmd = [npm, "ci"] if (fe / "package-lock.json").exists() else [npm, "install"]
    code, txt = run(cmd + ["--no-audit", "--no-fund"], cwd=fe, label="installing web packages")
    if code != 0:
        raise StepError("npm could not install the web packages.", txt)
    ok("Web packages installed")
    code, txt = run([npm, "run", "build"], cwd=fe, label="compiling the web app (type-check + build)")
    if code != 0:
        raise StepError("The web app did not compile.", txt)
    ok("Web app compiles cleanly")


def step_settings(ctx: Ctx) -> None:
    env_path = ctx.root / ".env"
    if env_path.exists():
        ok(".env already exists - keeping your settings")
        ctx.summary["AI provider"] = "kept from existing .env"
        return
    lines = ["# Written by the CrowdGuard installer. Never commit this file."]
    if AI.enabled:
        lines += ["CROWDGUARD_PROVIDER=meta", f"META_MODEL_API_KEY={AI.key}", f"META_MODEL={META_MODEL}"]
        ok("Muse Spark explanations: ON (needs your Meta key; small per-call cost)")
        if yesno("Also turn on voice questions (Muse Voice Transcribe; audio is sent to Meta; ~$0.18/hour)?", False):
            lines += ["CROWDGUARD_VOICE=meta", "META_VOICE_MODE=ENDPOINTING"]
            ok("Voice: ON")
        else:
            lines += ["CROWDGUARD_VOICE=off"]
        ctx.summary["AI provider"] = "Meta (Muse Spark)"
    else:
        lines += ["CROWDGUARD_PROVIDER=local", "CROWDGUARD_VOICE=off"]
        ok("AI features OFF - the app uses its built-in local explanations. You can add a key later in .env")
        ctx.summary["AI provider"] = "none (local templates)"
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass
    ok(f"Settings saved to {env_path} (private; git-ignored)")
    (ctx.root / "data").mkdir(exist_ok=True)
    (ctx.root / "gallery").mkdir(exist_ok=True)
    mp4 = list((ctx.root / "gallery").glob("*.mp4"))
    info(f"Videos: {len(mp4)} .mp4 file(s) in {ctx.root / 'gallery'} - copy your own clips there any time")
    ctx.summary["Videos (gallery)"] = f"{len(mp4)} clip(s) in {ctx.root / 'gallery'}"


def step_selftest(ctx: Ctx) -> None:
    mode = ctx.args.tests
    if mode == "ask":
        mode = "full" if yesno("Run the full self-test now? (about 1-3 minutes)", True) else "none"
    if mode == "none":
        warn("Self-test skipped")
        ctx.summary["Self-test"] = "skipped"
        return
    env = {"CROWDGUARD_VOICE": "off", "CROWDGUARD_PROVIDER": "local"}  # tests must never call outside services
    code, txt = run([ctx.vpy(), "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=ctx.root,
                    label="running backend tests", env=env)
    tail = [ln.strip("= ").strip() for ln in txt.splitlines() if re.search(r"\d+ passed", ln)][-1:] or ["passed"]
    if code != 0:
        if "INTERNALERROR" in txt:
            raise StepError("The test tool (pytest) crashed before running the tests - this is an environment "
                            "problem, not a failing test. Your Python is " + sys.version.split()[0] +
                            "; Python 3.11 or newer is recommended.", txt)
        raise StepError("Some backend tests failed.", txt)
    ok(f"Backend tests: {tail[0]}")
    ctx.summary["Self-test"] = f"backend: {tail[0]}"
    npm = which("npm") or "npm"
    code, txt = run([npm, "test"], cwd=ctx.root / "frontend", label="running web tests")
    summ = [ln for ln in txt.splitlines() if "Tests" in ln]
    if code != 0:
        raise StepError("Some web tests failed.", txt)
    ok(f"Web tests: {summ[-1].strip() if summ else 'passed'}")
    ctx.summary["Self-test"] += f" | web: {summ[-1].strip() if summ else 'passed'}"


def pids_file(root: Path) -> Path:
    return root / ".crowdguard" / "pids.json"


def spawn(cmd: List[str], cwd: Path, logfile: Path) -> int:
    logfile.parent.mkdir(parents=True, exist_ok=True)
    fh = open(logfile, "ab")
    kw: Dict[str, object] = {}
    if IS_WIN:
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    p = subprocess.Popen(cmd, cwd=str(cwd), stdout=fh, stderr=fh, stdin=subprocess.DEVNULL, **kw)  # type: ignore[call-overload]
    return p.pid


def stop_services(root: Path, quiet: bool = False) -> None:
    pf = pids_file(root)
    if not pf.exists():
        if not quiet:
            info("No running CrowdGuard services recorded")
        return
    for name, pid in json.loads(pf.read_text()).items():
        try:
            if IS_WIN:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            if not quiet:
                ok(f"Stopped {name} (pid {pid})")
        except (ProcessLookupError, PermissionError, OSError):
            if not quiet:
                info(f"{name} (pid {pid}) was not running")
    pf.unlink(missing_ok=True)
    time.sleep(1)


def start_services(ctx_or_root: Path, py: str) -> Dict[str, int]:
    root = ctx_or_root
    stop_services(root, quiet=True)
    npm = which("npm") or "npm"
    pids = {
        "backend": spawn([py, "-m", "uvicorn", "backend.api.app:create_app", "--factory",
                          "--host", "127.0.0.1", "--port", str(BACKEND_PORT)], root, root / "logs" / "backend.log"),
        "frontend": spawn([npm, "run", "dev", "--", "--host", "127.0.0.1", "--port", str(FRONTEND_PORT),
                           "--strictPort"], root / "frontend", root / "logs" / "frontend.log"),
    }
    pids_file(root).parent.mkdir(exist_ok=True)
    pids_file(root).write_text(json.dumps(pids))
    return pids


def wait_for(url: str, secs: int, needle: str = "") -> bool:
    end = time.time() + secs
    while time.time() < end:
        code, body = http_get(url, 3)
        if code == 200 and needle in body:
            return True
        time.sleep(1)
    return False


def step_start(ctx: Ctx) -> None:
    info("Starting the backend (port 8000) and the web screens (port 5173) in the background")
    pids = start_services(ctx.root, ctx.vpy())
    sub(f"backend pid {pids['backend']}, frontend pid {pids['frontend']}")
    if not wait_for(f"http://127.0.0.1:{BACKEND_PORT}/api/health", 60, '"status"'):
        log_tail = (ctx.root / "logs" / "backend.log").read_text(errors="replace")[-1500:] \
            if (ctx.root / "logs" / "backend.log").exists() else ""
        raise StepError("The backend did not become healthy within 60 seconds.", log_tail)
    ok(f"Backend is up: http://localhost:{BACKEND_PORT}")
    if not wait_for(f"http://127.0.0.1:{FRONTEND_PORT}/", 60):
        log_tail = (ctx.root / "logs" / "frontend.log").read_text(errors="replace")[-1500:] \
            if (ctx.root / "logs" / "frontend.log").exists() else ""
        raise StepError("The web screens did not start within 60 seconds.", log_tail)
    ok(f"Web screens are up: http://localhost:{FRONTEND_PORT}")


DEMO_USERS = [("admin", "demo-admin", "Administrator"), ("operator", "demo-operator", "Control-room operator"),
              ("manager", "demo-manager", "Pandal manager"), ("auditor", "demo-auditor", "Read-only auditor")]


def inspect_database(root: Path) -> Tuple[Dict[str, int], str]:
    dbp = Path(os.environ.get("CROWDGUARD_DB", str(root / "data" / "crowdguard.db")))
    if not dbp.exists():
        raise StepError(f"Database file not found at {dbp}")
    con = sqlite3.connect(f"file:{dbp.as_posix()}?mode=ro", uri=True)
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = {n: int(con.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]) for n in names}
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()
    return counts, integrity


def verify_all(root: Path, quiet_title: bool = False) -> Dict[str, str]:
    """Real checks against the RUNNING app. Raises StepError on the first hard failure."""
    res: Dict[str, str] = {}
    b = f"http://127.0.0.1:{BACKEND_PORT}"
    code, body = http_get(b + "/api/health")
    if code != 200:
        raise StepError("Backend health check failed.", body)
    h = json.loads(body)
    if h.get("database") != "ok":
        raise StepError("Backend is up but its database check says 'error'.", body)
    ok(f"Backend health: {h.get('status')}  (database: {h.get('database')}, data: {h.get('data_source')})")
    counts, integ = inspect_database(root)
    if integ != "ok":
        raise StepError("Database integrity check failed.", integ)
    ok(f"Database is up: {root / 'data' / 'crowdguard.db'}  (integrity: {integ})")
    ok(f"{len(counts)} tables found:")
    width = max(len(n) for n in counts) if counts else 10
    for n, c in counts.items():
        sub(f"{n:<{width}}  {c:>8} row(s)")
    res["Database"] = f"{len(counts)} tables, {sum(counts.values())} rows total"
    code, login = http_json(b + "/api/auth/login", {"username": "admin", "password": "demo-admin"})
    if code != 200 or "token" not in login:
        raise StepError("Demo sign-in failed (admin / demo-admin).", str(code))
    ok("Demo sign-in works (admin)")
    tok = login["token"]
    for label, path in (("Pandals", "/api/pandals"), ("Cameras", "/api/cameras"), ("Locations", "/api/locations")):
        c, data = _get_list(b + path, tok)
        if c != 200:
            raise StepError(f"{label} endpoint returned HTTP {c}.")
        res[label] = str(len(data))
        ok(f"{label} via API: {len(data)}")
    code, html = http_get(f"http://127.0.0.1:{FRONTEND_PORT}/")
    if code != 200 or "<div id=\"root\"" not in html:
        raise StepError("Web screens respond but do not look like the CrowdGuard app.", html[:300])
    ok("Web screens serve the CrowdGuard app")
    code, _ = http_get(f"http://127.0.0.1:{FRONTEND_PORT}/api/health")
    if code != 200:
        raise StepError("Web screens cannot reach the backend through the /api proxy.")
    ok("Web screens -> backend connection works (/api proxy)")
    return res


def _get_list(url: str, token: str) -> Tuple[int, list]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
            return r.status, data if isinstance(data, list) else data.get("items", [])
    except urllib.error.HTTPError as e:
        return e.code, []
    except Exception:
        return 0, []


def step_verify(ctx: Ctx) -> None:
    ctx.summary.update(verify_all(ctx.root))


# ------------------------------------------------------------------ built-in + AI fix actions
def act_pip_nocache(ctx: Ctx) -> None:
    run([ctx.vpy(), "-m", "pip", "install", "--upgrade", "pip"], cwd=ctx.root, label="upgrading pip")
    code, txt = run([ctx.vpy(), "-m", "pip", "install", "--no-cache-dir", "-r", "requirements.txt"],
                    cwd=ctx.root, label="reinstalling Python packages without cache")
    if code != 0:
        raise StepError("still failing", txt)


def act_recreate_venv(ctx: Ctx) -> None:
    shutil.rmtree(ctx.root / ".venv", ignore_errors=True)
    step_python(ctx)


def act_pytest_upgrade(ctx: Ctx) -> None:
    code, txt = run([ctx.vpy(), "-m", "pip", "install", "--upgrade", "pytest"], cwd=ctx.root, label="upgrading pytest")
    if code != 0:
        raise StepError("pytest upgrade failed", txt)


def act_npm_cache(ctx: Ctx) -> None:
    run([which("npm") or "npm", "cache", "verify"], cwd=ctx.root / "frontend", label="checking npm cache")
    run([which("npm") or "npm", "cache", "clean", "--force"], cwd=ctx.root / "frontend", label="clearing npm cache")


def act_clean_node_modules(ctx: Ctx) -> None:
    shutil.rmtree(ctx.root / "frontend" / "node_modules", ignore_errors=True)


def act_npm_legacy_peers(ctx: Ctx) -> None:
    code, txt = run([which("npm") or "npm", "install", "--legacy-peer-deps", "--no-audit", "--no-fund"],
                    cwd=ctx.root / "frontend", label="npm install (legacy peer deps)")
    if code != 0:
        raise StepError("still failing", txt)


def act_stop_old(ctx: Ctx) -> None:
    stop_services(ctx.root)


def act_restart_services(ctx: Ctx) -> None:
    stop_services(ctx.root, quiet=True)


def act_stash_pull(ctx: Ctx) -> None:
    git = which("git") or "git"
    run([git, "stash", "--include-untracked"], cwd=ctx.root)
    code, txt = run([git, "pull", "--ff-only"], cwd=ctx.root, label="git pull")
    if code != 0:
        raise StepError("still failing", txt)


def act_wait_longer(ctx: Ctx) -> None:
    time.sleep(15)


def build_steps() -> List[Step]:
    return [
        Step("location", "Choose install folder", "Decide where the app lives on your computer.", step_location),
        Step("prereq", "Check your computer", "Looks at Python, Node.js, internet, disk space and ports.", step_prereq),
        Step("code", "Get the code", "Downloads (or updates) CrowdGuard AI from GitHub.", step_code,
             actions={"stash_pull": ("Set aside local edits then update (git stash + pull)", act_stash_pull)}),
        Step("python", "Python environment and packages", "Creates a private .venv and installs the backend packages.",
             step_python,
             builtin=[("Retry pip without cache", act_pip_nocache)],
             actions={"recreate_venv": ("Delete .venv and rebuild it from scratch", act_recreate_venv),
                      "pip_nocache": ("Upgrade pip and reinstall packages with --no-cache-dir", act_pip_nocache)}),
        Step("frontend", "Web screens", "Installs web packages and checks the web app compiles.", step_frontend,
             builtin=[("Clear npm cache and retry", act_npm_cache)],
             actions={"npm_cache": ("Clear the npm cache", act_npm_cache),
                      "clean_node_modules": ("Delete frontend/node_modules and reinstall", act_clean_node_modules),
                      "npm_legacy_peers": ("Install with --legacy-peer-deps", act_npm_legacy_peers)}),
        Step("settings", "Settings, AI provider and videos", "Writes your private .env and prepares folders.",
             step_settings),
        Step("selftest", "Self-test", "Runs the project's own tests to prove the install works.", step_selftest,
             actions={"pytest_upgrade": ("Upgrade pytest inside .venv, then run the tests again", act_pytest_upgrade),
                      "recreate_venv": ("Delete .venv and rebuild it from scratch", act_recreate_venv)},
             optional=True),
        Step("start", "Start the app", "Starts backend (8000) and web screens (5173) in the background.", step_start,
             builtin=[("Stop any old CrowdGuard processes and retry", act_restart_services)],
             actions={"stop_old": ("Stop old CrowdGuard processes, then start again", act_stop_old),
                      "wait_longer": ("Wait 15 more seconds (slow computer)", act_wait_longer),
                      "recreate_venv": ("Delete .venv and rebuild it from scratch", act_recreate_venv)}),
        Step("verify", "Verify everything", "Checks backend, database tables and counts, sign-in, and web screens.",
             step_verify,
             builtin=[("Wait for services to settle and retry", act_wait_longer)],
             actions={"wait_longer": ("Wait 15 more seconds (slow computer)", act_wait_longer),
                      "stop_old": ("Stop old CrowdGuard processes, then start again", act_stop_old)}),
    ]


# ------------------------------------------------------------------ step runner with fix loop
def run_step(ctx: Ctx, step: Step, i: int, n: int) -> bool:
    heading(i, n, step.title, step.why)
    tried_builtin = 0
    ai_attempts = 0
    while True:
        try:
            step.fn(ctx)
            return True
        except StepError as e:
            bad(str(e))
            for line in e.detail.strip().splitlines()[-6:]:
                sub(line[:110])
            log(f"STEP {step.sid} FAILED: {e}\n{e.detail[-3000:]}")
            # 1) deterministic built-in fixes, one by one
            if tried_builtin < len(step.builtin):
                label, fix = step.builtin[tried_builtin]
                tried_builtin += 1
                info(f"Built-in fix: {label}")
                try:
                    fix(ctx)
                except StepError as fe:
                    log(f"built-in fix failed: {fe}")
                continue
            # 2) AI helper suggests one pre-written action; user must approve
            if AI.enabled and step.actions and ai_attempts < 3:
                ai_attempts += 1
                info("Asking the AI helper for a suggestion (only the error text, with secrets removed, is sent)...")
                menu = {k: v[0] for k, v in step.actions.items()}
                sug = AI.suggest(step.title, f"{e}\n{e.detail}", menu)
                if sug and sug[0] != "none":
                    act_id, reason = sug
                    out()
                    out("  " + col("AI helper suggests:", "m", True) + f" {menu[act_id]}")
                    sub(f"Why: {reason}")
                    choice = ask("Apply this fix?  [Y]es / [N]o / [S]kip this step", "y").lower() \
                        if INTERACTIVE else "n"
                    log(f"AI suggested {act_id}; user said {choice!r}")
                    if choice.startswith("y"):
                        try:
                            step.actions[act_id][1](ctx)
                        except StepError as fe:
                            warn(f"That fix did not work: {fe}")
                        continue
                    if choice.startswith("s"):
                        return _skip_or_stop(step)
                else:
                    warn("The AI helper had no further suggestion.")
            elif not AI.enabled and ai_attempts == 0 and step.actions:
                sub("(No AI helper configured - restart the installer with a Meta key to get fix suggestions.)")
            # 3) last resort: human choice
            if not INTERACTIVE:
                return False
            choice = ask("What now?  [R]etry this step / [S]kip / [Q]uit", "q").lower()
            if choice.startswith("r"):
                tried_builtin, ai_attempts = 0, 0
                continue
            if choice.startswith("s"):
                return _skip_or_stop(step)
            return False


def _skip_or_stop(step: Step) -> bool:
    if step.optional:
        warn(f"Skipped: {step.title}")
        return True
    warn(f"'{step.title}' is required for the app to run, so the installer cannot continue past it.")
    return False


# ------------------------------------------------------------------ welcome + summary
def banner() -> None:
    out()
    out(col("  CrowdGuard AI  -  guided installer", "c", True))
    out(col("  Durga Puja multi-pandal crowd monitoring (simulated data). Runs fully on your computer.", "d"))
    out()
    out("  What will happen (9 steps, one at a time, with a check after each):")
    for i, t in enumerate(("Choose install folder", "Check your computer", "Get the code",
                           "Python environment and packages", "Web screens", "Settings, AI provider and videos",
                           "Self-test", "Start the app", "Verify everything"), 1):
        sub(f"{i}. {t}")
    out()
    out("  Normal steps use fixed, predictable checks - no AI. If something breaks, an optional AI helper can")
    out("  suggest a fix, and it asks you before changing anything.")


def welcome_ai(args: argparse.Namespace) -> None:
    out()
    out(col("  Optional: AI helper and AI features", "m", True))
    out("  Provider: Meta (Muse Spark). Press Enter to skip - the app works fully without it.")
    prov = (args.provider or ask("AI provider (meta / none)", "none")).lower()
    if prov not in ("meta",):
        info("No AI provider - continuing without AI help")
        return
    key = os.environ.get("META_MODEL_API_KEY") or os.environ.get("MODEL_API_KEY") or \
        ask("Paste your Meta API key (hidden while typing; Enter to skip)", "", secret=True)
    if not key:
        info("No key entered - continuing without AI help")
        return
    AI.key, AI.provider = key.strip(), "meta"
    good, why = AI.validate()
    if good:
        ok("Meta key accepted - AI helper ready, and AI features can be switched on")
    else:
        warn(f"Could not use the key: {why}. Continuing without AI.")
        AI.key, AI.provider = "", "none"


def final_summary(ctx: Ctx) -> None:
    root = ctx.root
    out()
    out(col("=" * 72, "g"))
    out(col("  INSTALLED AND RUNNING", "g", True))
    out(col("=" * 72, "g"))
    rows = [("Open the app", f"http://localhost:{FRONTEND_PORT}"),
            ("API docs", f"http://localhost:{BACKEND_PORT}/docs"),
            ("Backend health", f"http://localhost:{BACKEND_PORT}/api/health")]
    rows += list(ctx.summary.items())
    pf = pids_file(root)
    if pf.exists():
        p = json.loads(pf.read_text())
        rows.append(("Processes", f"backend pid {p.get('backend')}, frontend pid {p.get('frontend')}"))
    rows += [("Database file", str(root / "data" / "crowdguard.db")), ("Logs", str(root / "logs")),
             ("Settings file", str(root / ".env"))]
    for k, v in rows:
        out(f"  {col(k + ':', 'c', True):<{36 if USE_COLOR else 24}} {v}")
    out()
    out(col("  Demo sign-ins (local evaluation only):", "y", True))
    for u, p, r in DEMO_USERS:
        sub(f"{u:<9} / {p:<14} {r}")
    out()
    out(col("  Handy commands (run inside the install folder):", "y", True))
    launcher = root / ("crowdguard.cmd" if IS_WIN else "crowdguard")
    sub(f"cd \"{root}\"")
    short = ".\\crowdguard.cmd" if IS_WIN else "./crowdguard"
    sub(f"{short} status | stop | start | doctor")
    sub(f"or from anywhere: \"{launcher}\" status")
    out()


def write_launchers(ctx: Ctx) -> None:
    root = ctx.root
    target = root / "installer" / "crowdguard_installer.py"
    if (root / "installer" / "crowdguard_installer.py").exists() is False:
        (root / "installer").mkdir(exist_ok=True)
        shutil.copy2(Path(__file__).resolve(), target)
    sh = root / "crowdguard"
    sh.write_text('#!/bin/sh\ncd "$(dirname "$0")"\nexec "./.venv/bin/python" installer/crowdguard_installer.py "$@"\n',
                  encoding="utf-8", newline="\n")
    try:
        os.chmod(sh, 0o755)
    except OSError:
        pass
    (root / "crowdguard.cmd").write_text(
        '@echo off\r\ncd /d "%~dp0"\r\n".venv\\Scripts\\python.exe" installer\\crowdguard_installer.py %*\r\n',
        encoding="utf-8", newline="")


def open_browser(args: argparse.Namespace) -> None:
    if args.no_browser:
        return
    import webbrowser
    try:
        webbrowser.open(f"http://localhost:{FRONTEND_PORT}")
        ok("Opened your browser")
    except Exception:
        info(f"Open http://localhost:{FRONTEND_PORT} in your browser")


# ------------------------------------------------------------------ commands
def find_root() -> Path:
    here = Path(__file__).resolve().parent
    for cand in (here.parent, Path.cwd()):
        if (cand / "backend" / "api" / "app.py").exists():
            return cand
    raise SystemExit("Run this from inside the CrowdGuard install folder.")


def cmd_install(args: argparse.Namespace) -> int:
    ctx = Ctx(args)
    banner()
    welcome_ai(args)
    steps = build_steps()
    n = len(steps)
    for i, st in enumerate(steps, 1):
        if not run_step(ctx, st, i, n):
            out()
            bad(f"Stopped at step {i}: {st.title}.")
            if LOG:
                out(f"  Full details are in {LOG}")
            out("  Fix the problem and run the installer again - it will continue safely (nothing is duplicated).")
            return 1
    write_launchers(ctx)
    final_summary(ctx)
    open_browser(args)
    return 0


def cmd_start(_: argparse.Namespace) -> int:
    root = find_root()
    py = str(venv_python(root))
    info("Starting CrowdGuard AI...")
    start_services(root, py)
    good = wait_for(f"http://127.0.0.1:{BACKEND_PORT}/api/health", 60) and wait_for(f"http://127.0.0.1:{FRONTEND_PORT}/", 60)
    (ok if good else bad)(f"Backend :{BACKEND_PORT} and web :{FRONTEND_PORT} " + ("are up" if good else "did not start - see logs/"))
    return 0 if good else 1


def cmd_stop(_: argparse.Namespace) -> int:
    stop_services(find_root())
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    root = find_root()
    out(col("  CrowdGuard AI status", "c", True))
    for name, port in (("Backend", BACKEND_PORT), ("Web screens", FRONTEND_PORT)):
        (ok if port_busy(port) else bad)(f"{name} on port {port}: {'listening' if port_busy(port) else 'not running'}")
    try:
        verify_all(root)
        return 0
    except StepError as e:
        bad(str(e))
        return 1


def main() -> int:
    global INTERACTIVE, LOG
    ap = argparse.ArgumentParser(description="CrowdGuard AI guided installer")
    ap.add_argument("command", nargs="?", default="install", choices=["install", "start", "stop", "status", "doctor"])
    ap.add_argument("--dir", help="install folder (skips the question default)")
    ap.add_argument("--repo", help="git URL or path to install from (default: the GitHub repo)")
    ap.add_argument("--provider", choices=["meta", "none"], help="AI provider")
    ap.add_argument("--tests", choices=["ask", "full", "none"], default="ask")
    ap.add_argument("--yes", action="store_true", help="no questions; use defaults (AI helper off)")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    if args.yes:
        INTERACTIVE = False
        if args.tests == "ask":
            args.tests = "full"
    try:
        if args.command == "install":
            return cmd_install(args)
        if args.command in ("status", "doctor"):
            LOG = find_root() / "logs" / "installer.log"
            return cmd_status(args)
        LOG = find_root() / "logs" / "installer.log"
        return {"start": cmd_start, "stop": cmd_stop}[args.command](args)
    except KeyboardInterrupt:
        out()
        warn("Cancelled. Nothing is left half-written; run the installer again to continue.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
