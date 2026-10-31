#!/usr/bin/env python3
"""CrowdGuard AI - guided installer / launcher (Windows, macOS, Linux). Standard library only.

    python crowdguard_installer.py            # installs, or opens the Control Room if already installed
    python crowdguard_installer.py install    # force the guided install
    python crowdguard_installer.py menu|start|stop|status|stop-backend|stop-frontend|chat|uninstall

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
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

REPO_URL = "https://github.com/roy777rajat/crowdguard-ai.git"
ZIP_URL = "https://github.com/roy777rajat/crowdguard-ai/archive/refs/heads/main.zip"
META_URL = "https://api.meta.ai/v1/responses"
META_MODEL = "muse-spark-1.3"
BACKEND_PORT, FRONTEND_PORT = 8000, 5173
# The web screens listen on this computer only. To open them from another computer (for example a cloud server),
# start with CROWDGUARD_BIND=0.0.0.0 and allow port 5173 in your firewall for YOUR address only.
WEB_BIND = os.environ.get("CROWDGUARD_BIND", "127.0.0.1")
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
        self.note = ""  # why the last suggest() gave nothing (shown to the user)

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
            log(f"AI raw reply: {text[:500]}")
            m = re.search(r"\{.*\}", text, re.S)
            if not m:
                self.note = "the AI reply was not in the expected format"
                return None
            obj = json.loads(m.group(0))
            act = str(obj.get("action", "none"))
            if act not in actions:
                self.note = (f"the AI found no matching fix on the menu: {obj.get('reason', '')}"
                             if act == "none" else f"the AI chose '{act}', which is not on the menu")
                return None
            return act, str(obj.get("reason", ""))
        except urllib.error.HTTPError as e:
            self.note = f"Meta API returned HTTP {e.code}"
            log(f"AI helper unavailable: {e}")
        except Exception as e:
            self.note = f"could not get an answer from Meta ({type(e).__name__})"
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
            record_system_install(ctx.root, "node")
        else:
            raise StepError("Node.js is required. Install it from https://nodejs.org (LTS) and run again.")
    ctx.node_ok = True
    ctx.summary["Python"] = sys.version.split()[0]
    _, nv = run([which("node") or "node", "--version"])
    ctx.summary["Node.js"] = nv.strip()


def move_aside_untracked(root: Path, git_output: str) -> List[str]:
    """Rename untracked files that git says a pull would overwrite (never deletes anything)."""
    moved: List[str] = []
    grab = False
    for line in git_output.splitlines():
        if "untracked working tree files would be overwritten" in line:
            grab = True
            continue
        if grab:
            if line.startswith("Please move") or not line.strip():
                break
            rel = line.strip()
            src = root / rel
            if src.is_file():
                dst = src.with_name(src.name + ".old")
                try:
                    if dst.exists():
                        dst.unlink()
                    src.rename(dst)
                    moved.append(rel)
                except OSError as e:
                    log(f"could not move {rel}: {e}")
    return moved


def step_code(ctx: Ctx) -> None:
    repo = ctx.args.repo or REPO_URL
    root = ctx.root
    git = which("git")
    if (root / ".git").exists() and git:
        info("Existing install found - fetching updates (your data and settings are kept)")
        code, txt = run([git, "pull", "--ff-only"], cwd=root, label="git pull")
        if code != 0 and "untracked working tree files would be overwritten" in txt:
            # Typical after an older install: the launcher copy of the installer is now a tracked file.
            moved = move_aside_untracked(root, txt)
            if moved:
                info("Moved aside files that were in the way (kept as *.old): " + ", ".join(moved))
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
        "frontend": spawn([npm, "run", "dev", "--", "--host", WEB_BIND, "--port", str(FRONTEND_PORT),
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
                    warn(f"The AI helper had no suggestion: {AI.note or 'no reason given'}.")
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
    out(col("  Control menu (start/stop backend or web screens, AI chat, uninstall) - run any time:", "y", True))
    launcher = root / ("crowdguard.cmd" if IS_WIN else "crowdguard")
    sub(f"cd \"{root}\"")
    short = ".\\crowdguard.cmd" if IS_WIN else "./crowdguard"
    sub(f"{short}            (opens the menu)")
    sub(f"{short} status | start | stop | stop-backend | stop-frontend | chat | uninstall")
    sub(f"or from anywhere: \"{launcher}\" status")
    out()


def write_launchers(ctx: Ctx) -> None:
    root = ctx.root
    target = root / "installer" / "crowdguard_installer.py"
    if (root / "installer" / "crowdguard_installer.py").exists() is False:
        (root / "installer").mkdir(exist_ok=True)
        shutil.copy2(Path(__file__).resolve(), target)
    sh = root / "crowdguard"
    sh.write_text('#!/bin/sh\ncd "$(dirname "$0")"\n[ $# -eq 0 ] && set -- menu\n'
                  'exec "./.venv/bin/python" installer/crowdguard_installer.py "$@"\n',
                  encoding="utf-8", newline="\n")
    try:
        os.chmod(sh, 0o755)
    except OSError:
        pass
    (root / "crowdguard.cmd").write_text(
        '@echo off\r\ncd /d "%~dp0"\r\nif "%~1"=="" (\r\n  ".venv\\Scripts\\python.exe" installer\\crowdguard_installer.py menu\r\n'
        ') else (\r\n  ".venv\\Scripts\\python.exe" installer\\crowdguard_installer.py %*\r\n)\r\n',
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


# ------------------------------------------------------------------ control menu: single services, AI chat, uninstall
def pointer_file() -> Path:
    return Path.home() / ".crowdguard" / "install_path.txt"


def remember_install(root: Path) -> None:
    try:
        pointer_file().parent.mkdir(parents=True, exist_ok=True)
        pointer_file().write_text(str(root), encoding="utf-8")
    except OSError:
        pass


def is_install(p: Path) -> bool:
    return (p / "backend" / "api" / "app.py").exists()


def known_install(dir_arg: Optional[str] = None) -> Optional[Path]:
    cands: List[Path] = []
    if dir_arg:
        cands.append(Path(dir_arg).expanduser())
    try:
        cands.append(Path(pointer_file().read_text(encoding="utf-8").strip()))
    except OSError:
        pass
    cands += [Path("C:/CrowdGuard") if IS_WIN else Path.home() / "CrowdGuard", Path.cwd(),
              Path(__file__).resolve().parent.parent]
    for c in cands:
        try:
            if c and is_install(c):
                return c.resolve()
        except OSError:
            continue
    return None


def pkg_manager_name() -> Tuple[str, str]:
    """(manager, how to remove Node) for the package manager pkg_install() would use."""
    if IS_WIN and which("winget"):
        return "winget", "winget uninstall OpenJS.NodeJS.LTS"
    if platform.system() == "Darwin" and which("brew"):
        return "brew", "brew uninstall node"
    if which("apt-get"):
        return "apt", "sudo apt-get remove nodejs npm"
    if which("dnf"):
        return "dnf", "sudo dnf remove nodejs npm"
    return "unknown", ""


def record_system_install(root: Path, tool: str) -> None:
    """Remember what this installer put on the SYSTEM (outside the folder) so uninstall can tell you."""
    try:
        f = root / ".crowdguard" / "system_installs.json"
        data = json.loads(f.read_text()) if f.exists() else {}
        mgr, hint = pkg_manager_name()
        data[tool] = {"package_manager": mgr, "remove_with": hint, "when": time.strftime("%Y-%m-%d %H:%M")}
        f.parent.mkdir(exist_ok=True)
        f.write_text(json.dumps(data, indent=1))
    except (OSError, ValueError):
        pass


def health_problems(root: Path) -> Tuple[List[str], List[str]]:
    """(needs_install, needs_start): plain-language problems found right now."""
    fix_install: List[str] = []
    fix_start: List[str] = []
    if not venv_python(root).exists():
        fix_install.append("The private Python environment (.venv) is missing")
    if not (root / "frontend" / "node_modules").exists():
        fix_install.append("The web packages (frontend/node_modules) are missing")
    if not port_busy(BACKEND_PORT):
        fix_start.append(f"The backend is not running (port {BACKEND_PORT})")
    else:
        code, body = http_get(f"http://127.0.0.1:{BACKEND_PORT}/api/health", 4)
        if code != 200 or '"database":"ok"' not in body.replace(" ", ""):
            fix_start.append("The backend is running but its health check FAILED")
    if not port_busy(FRONTEND_PORT):
        fix_start.append(f"The web screens are not running (port {FRONTEND_PORT})")
    return fix_install, fix_start


def show_health(root: Path) -> None:
    need_install, need_start = health_problems(root)
    if not need_install and not need_start:
        out("  " + col("ALL SYSTEMS UP", "g", True) + col("  backend, database and web screens are healthy", "d"))
        return
    out("  " + col("FAILED - the system is not fully up:", "r", True))
    for pr in need_install + need_start:
        out("    " + col("x", "r", True) + " " + pr)
    out("  What to do:")
    if need_install:
        out("    - Choose " + col("13", "y", True) + " (install / repair) - something the app needs is missing")
    if need_start:
        out("    - Choose " + col("2", "y", True) + " (start everything), or 4 / 6 to start one part")
    out(f"    - Still failing? Contact your system administrator and share the logs in {root / 'logs'}")


def gateway(args: argparse.Namespace) -> int:
    """One command for everyone: installs if nothing is installed yet, otherwise offers the Control Room."""
    root = known_install(args.dir)
    if root is None or not INTERACTIVE:
        return cmd_install(args)
    global LOG
    LOG = root / "logs" / "installer.log"
    out()
    out(col("  CrowdGuard AI", "c", True))
    out(f"  An install was found: {root}")
    out("  " + service_line(root))
    show_health(root)
    out()
    out(f"   {col(' 1', 'y', True)}  Open the Control Room (start/stop, AI chat, uninstall ...)")
    out(f"   {col(' 2', 'y', True)}  Install again / repair / update this install")
    out(f"   {col(' 3', 'y', True)}  Install a new copy in a different folder")
    out(f"   {col(' 0', 'y', True)}  Exit")
    ch = ask("What would you like to do? (enter a number)", "1")
    if ch == "1":
        return menu(root)
    if ch == "2":
        args.dir = str(root)
        return cmd_install(args)
    if ch == "3":
        args.dir = None
        return cmd_install(args)
    return 0


def read_pids(root: Path) -> Dict[str, int]:
    pf = pids_file(root)
    try:
        return {k: int(v) for k, v in json.loads(pf.read_text()).items()} if pf.exists() else {}
    except (ValueError, OSError):
        return {}


def write_pids(root: Path, pids: Dict[str, int]) -> None:
    pf = pids_file(root)
    if not pids:
        pf.unlink(missing_ok=True)
        return
    pf.parent.mkdir(exist_ok=True)
    pf.write_text(json.dumps(pids))


def kill_pid(pid: int) -> bool:
    try:
        if IS_WIN:
            r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            return r.returncode == 0
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


SERVICES = {"backend": ("Backend", BACKEND_PORT), "frontend": ("Web screens", FRONTEND_PORT)}


def stop_one(root: Path, name: str) -> None:
    label, port = SERVICES[name]
    pids = read_pids(root)
    pid = pids.pop(name, None)
    if pid is not None and kill_pid(pid):
        ok(f"Stopped {label} (pid {pid})")
    elif pid is not None:
        info(f"{label} (pid {pid}) was not running")
    else:
        info(f"No {label} process of this install is recorded")
    write_pids(root, pids)
    for _ in range(10):  # give the port a moment to close
        if not port_busy(port):
            break
        time.sleep(0.5)
    if port_busy(port):
        warn(f"Port {port} is still in use - something else (or an older run) is listening on it.")
    else:
        ok(f"Port {port} is free")


def start_one(root: Path, name: str, py: str) -> bool:
    label, port = SERVICES[name]
    if port_busy(port):
        warn(f"{label}: port {port} is already in use, so it is probably already running. Nothing started.")
        return False
    if name == "backend":
        pid = spawn([py, "-m", "uvicorn", "backend.api.app:create_app", "--factory", "--host", "127.0.0.1",
                     "--port", str(BACKEND_PORT)], root, root / "logs" / "backend.log")
        url, needle = f"http://127.0.0.1:{BACKEND_PORT}/api/health", '"status"'
    else:
        pid = spawn([which("npm") or "npm", "run", "dev", "--", "--host", WEB_BIND, "--port", str(FRONTEND_PORT),
                     "--strictPort"], root / "frontend", root / "logs" / "frontend.log")
        url, needle = f"http://127.0.0.1:{FRONTEND_PORT}/", ""
    pids = read_pids(root)
    pids[name] = pid
    write_pids(root, pids)
    info(f"Starting {label} (pid {pid})...")
    good = wait_for(url, 60, needle)
    (ok if good else bad)(f"{label} " + (f"is up on port {port}" if good else "did not start - see the logs folder"))
    return good


def service_line(root: Path) -> str:
    pids = read_pids(root)
    parts = []
    for name, (label, port) in SERVICES.items():
        up = port_busy(port)
        pid = f" pid {pids[name]}" if name in pids and up else ""
        parts.append(f"{label} :{port} " + (col("RUNNING", "g", True) + pid if up else col("stopped", "r", True)))
    return "     ".join(parts)


def show_logs(root: Path) -> None:
    for fname in ("backend.log", "frontend.log", "installer.log"):
        p = root / "logs" / fname
        out(col(f"  --- {fname} (last 12 lines) ---", "c", True))
        if not p.exists():
            sub("(no log yet)")
            continue
        for line in p.read_text(errors="replace").splitlines()[-12:]:
            sub(line[:140])


# ---- AI chat (talks to the running backend's own /api/assistant routes; nothing in the app is changed)
def _post(url: str, body: bytes, ctype: str, token: str = "", timeout: float = 90) -> Tuple[int, dict]:
    headers = {"Content-Type": ctype}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"detail": f"{type(e).__name__}: {e}"}


def _wrap(text: str, indent: str = "    ") -> str:
    import textwrap
    return "\n".join(textwrap.fill(p, 92, initial_indent=indent, subsequent_indent=indent) if p.strip() else ""
                     for p in text.splitlines())


def read_wav_for_upload(path: Path) -> bytes:
    """Light local check so the user gets a plain message before anything is sent."""
    import wave
    if not path.is_file():
        raise StepError(f"File not found: {path}")
    data = path.read_bytes()
    try:
        with wave.open(str(path), "rb") as w:
            ch, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    except (wave.Error, EOFError):
        raise StepError("That is not a readable WAV file. Use a mono, 16-bit WAV at 16 kHz or 24 kHz.")
    if ch != 1 or width != 2 or rate not in (16000, 24000):
        raise StepError(f"WAV must be mono, 16-bit, 16 or 24 kHz (this one: {ch} channel(s), {width * 8}-bit, {rate} Hz).")
    if frames / float(rate) > 60:
        raise StepError("Keep the recording under one minute.")
    return data


MIC_SILENCE_SECS, MIC_WAIT_SECS = 4.0, 20.0


MIC_SCRIPT = r'''
import array, sys, time, wave
import sounddevice as sd
rate, path = 16000, sys.argv[1]
silence_stop = float(sys.argv[2])
wait_max = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0
limit = 60.0
st = {"noise": [], "thr": None, "spoke": False, "quiet": 0.0, "level": 0.0}
frames = []
def rms(b):
    a = array.array("h")
    a.frombytes(b)
    return (sum(x * x for x in a) / max(1, len(a))) ** 0.5
def cb(indata, n, t, s):
    b = bytes(indata)
    frames.append(b)
    r = rms(b)
    st["level"] = r
    dt = len(b) / 2.0 / rate
    if st["thr"] is None:
        st["noise"].append(r)
        if len(st["noise"]) >= 5:
            base = sorted(st["noise"])[len(st["noise"]) // 2]
            st["thr"] = min(1500.0, max(250.0, base * 3.0))
        return
    if r > st["thr"]:
        st["spoke"], st["quiet"] = True, 0.0
    else:
        st["quiet"] += dt
tty = sys.stdout.isatty()
def say(msg):
    if tty:
        sys.stdout.write("\r\033[90m" + msg.ljust(78) + "\033[0m")
        sys.stdout.flush()
t0 = time.time()
reason = "limit"
try:
    with sd.RawInputStream(samplerate=rate, channels=1, dtype="int16", blocksize=1600, callback=cb):
        while True:
            time.sleep(0.1)
            el = time.time() - t0
            thr = st["thr"] or 1.0
            bar = "#" * min(20, int(st["level"] / thr * 5))
            if st["spoke"]:
                say("  listening... [%-20s] pause %.1fs of %.0fs" % (bar, st["quiet"], silence_stop))
                if st["quiet"] >= silence_stop:
                    reason = "silence"
                    break
            else:
                say("  waiting for you to speak... [%-20s] %.0fs" % (bar, el))
                if el >= wait_max:
                    reason = "nospeech"
                    break
            if el >= limit:
                break
except KeyboardInterrupt:
    reason = "interrupted"
if tty:
    sys.stdout.write("\r" + " " * 80 + "\r")
if not st["spoke"]:
    sys.exit(3)
with wave.open(path, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    w.writeframes(b"".join(frames))
'''


def record_mic(py: str) -> Optional[Path]:
    """Record from the default microphone (optional 'sounddevice' package, installed only if you agree).
    Stops by itself after 4 seconds of silence once you have spoken; waits up to 20 s for you to start."""
    code, _ = run([py, "-c", "import sounddevice"])
    if code != 0:
        warn("Microphone recording needs the small 'sounddevice' package (installed inside this app's .venv only).")
        if not yesno("Install it now?", True):
            return None
        code, txt = run([py, "-m", "pip", "install", "sounddevice", "--disable-pip-version-check"],
                        label="installing sounddevice")
        if code != 0:
            bad("Could not install it (on Linux this may need the PortAudio library). You can still use /voice file.wav")
            sub(txt.strip().splitlines()[-1][:110] if txt.strip() else "")
            return None
    code, _ = run([py, "-c", "import sounddevice as sd; sd.query_devices(kind='input')"])
    if code != 0:
        bad("No microphone is available on this computer (a cloud server has no sound card).")
        sub("Use the app in your browser for voice, or record a WAV on your own computer and use /voice file.wav")
        return None
    path = Path(tempfile.mkdtemp(prefix="cg-mic-")) / "clip.wav"
    out("  " + col("Speak now. It sends by itself after 4 seconds of silence (max 60 s).", "m", True))
    try:
        r = subprocess.run([py, "-c", MIC_SCRIPT, str(path), str(MIC_SILENCE_SECS), str(MIC_WAIT_SECS)], timeout=100)
    except subprocess.TimeoutExpired:
        bad("Recording took too long and was stopped.")
        return None
    if r.returncode == 3:
        warn(f"No speech heard in {int(MIC_WAIT_SECS)} seconds. Check your microphone and try /mic again.")
        return None
    if r.returncode != 0 or not path.exists():
        bad("Recording failed (no microphone, or the app has no permission to use it).")
        return None
    return path


def stream_text(text: str, cps: float = 250, dim: bool = False) -> None:
    """Print text gradually (typewriter). Plain instant print when not on a colour terminal."""
    if not USE_COLOR:
        out(text)
        return
    delay = 1.0 / cps
    sys.stdout.write("\033[90m" if dim else "")
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(delay)
    sys.stdout.write("\033[0m\n")
    sys.stdout.flush()


def post_with_thinking(url: str, body: bytes, ctype: str, token: str, show: bool) -> Tuple[int, dict]:
    """Run the request in a thread and show a live, dim 'thinking' line with elapsed time."""
    box: Dict[str, Tuple[int, dict]] = {}
    t = threading.Thread(target=lambda: box.update(r=_post(url, body, ctype, token)), daemon=True)
    t.start()
    t0, i = time.time(), 0
    while t.is_alive():
        t.join(0.2)
        if show and USE_COLOR:
            sys.stdout.write("\r" + col("  thinking %s %4.1fs" % ("|/-\\"[i % 4], time.time() - t0), "d"))
            sys.stdout.flush()
            i += 1
    if show and USE_COLOR:
        sys.stdout.write("\r" + " " * 40 + "\r")
    return box.get("r", (0, {"detail": "no reply"}))


def thinking_trace(res: dict) -> List[str]:
    """What the assistant actually did, taken from the reply: tools, sources, turns, timing. Not invented."""
    lines = ["agent steps:"]
    for t in res.get("tools", []):
        ms = f", {t['duration_ms']:.0f} ms" if t.get("duration_ms") is not None else ""
        lines.append(f"  - checked {t.get('name')}: " + ("ok" if t.get("ok") else f"failed ({t.get('error_code')})") + ms)
    for sref in res.get("sources", []):
        lines.append(f"  - data source: {sref.get('source')} (via {sref.get('tool')})")
    bits = [f"{res.get('turns', 0)} reasoning turn(s)"]
    if res.get("model"):
        bits.append(str(res["model"]))
    if res.get("duration_ms") is not None:
        bits.append(f"{res['duration_ms']:.0f} ms")
    use = res.get("usage") or {}
    toks = [f"{k.replace('_', ' ')} {use[k]}" for k in ("input_tokens", "output_tokens") if k in use]
    lines.append("  - " + ", ".join(bits + toks))
    return lines


def chat_session(root: Path) -> None:
    py = str(venv_python(root)) if venv_python(root).exists() else sys.executable
    b = f"http://127.0.0.1:{BACKEND_PORT}"
    if not port_busy(BACKEND_PORT):
        if not yesno("The backend is not running. Start it now?", True) or not start_one(root, "backend", py):
            return
    user = ask("Sign in as which demo user? (admin / operator / manager / auditor)", "operator").lower()
    pw = {u: p for u, p, _ in DEMO_USERS}.get(user)
    if not pw:
        bad("Unknown user.")
        return
    code, login = http_json(b + "/api/auth/login", {"username": user, "password": pw})
    if code != 200 or "token" not in login:
        bad(f"Sign-in failed (HTTP {code}).")
        return
    tok = login["token"]
    req = urllib.request.Request(b + "/api/assistant/capabilities", headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            caps = json.loads(r.read().decode())
    except Exception as e:
        bad(f"Could not read the assistant status: {e}")
        return
    if not caps["chat"]["available"]:
        warn(str(caps["chat"].get("reason") or "The assistant is not available."))
        sub("Use menu option 'Turn AI features on/off' to add your Meta key, then try again.")
        return
    voice_ok = bool(caps["voice"]["available"])
    langs = [str(x) for x in caps["voice"].get("languages", ["auto"])] or ["auto"]
    language, think = "auto", True
    out()
    out(col(f"  AI assistant ready ({caps['chat'].get('model')}), signed in as {user}.", "g", True))
    sub("Answers are decision support for people to review, not instructions. Data is simulated.")
    sub("Type a question and press Enter. Commands: /mic  /voice file.wav  /lang  /think  /new  /help  /quit")
    sub("The thinking view shows the assistant's real steps (tools, sources, timing); the model's own")
    sub("private reasoning text is not exposed by the app, so it is not shown.")
    if voice_ok:
        sub("Voice language: auto-detect. If a transcript is wrong, set it with /lang (e.g. /lang Bengali).")
    else:
        sub("(Voice is OFF on the server: " + str(caps["voice"].get("reason") or "") + ")")
    for q in caps.get("suggestions", [])[:3]:
        sub(f"Try: {q}")
    conv: Optional[str] = None
    while True:
        try:
            line = input("\n  " + col("You>", "m", True) + " ").strip()
        except (EOFError, KeyboardInterrupt):
            out()
            return
        mode = "text"
        if not line:
            continue
        low = line.lower()
        if low in ("/quit", "/exit", "/q"):
            return
        if low == "/help":
            sub("/mic               speak; it sends by itself after 4 seconds of silence")
            sub("/voice <file.wav>  send a recording (mono 16-bit WAV, 16 or 24 kHz, under 1 minute)")
            sub("/lang [name]       voice language: auto, " + ", ".join(x for x in langs if x != "auto"))
            sub("/think             turn the thinking view on or off")
            sub("/new               start a fresh conversation     /quit  leave the chat")
            sub("When the assistant proposes a change it waits for you: reply yes or no.")
            continue
        if low == "/new":
            conv = None
            ok("New conversation")
            continue
        if low == "/think":
            think = not think
            ok("Thinking view " + ("ON" if think else "OFF"))
            continue
        if low.startswith("/lang"):
            arg = line[5:].strip()
            if not arg:
                sub(f"Voice language now: {language}. Choices: " + ", ".join(langs))
                continue
            match = next((x for x in langs if x.lower() == arg.lower()), None)
            if match is None:
                warn("Unknown language. Choices: " + ", ".join(langs))
            else:
                language = match
                ok(f"Voice language: {language}" + ("  (the model detects it, including mixed speech)" if language == "auto" else ""))
            continue
        if low.startswith("/voice") or low == "/mic":
            if not voice_ok:
                warn("Voice is not enabled on the server (needs CROWDGUARD_VOICE=meta and a Meta key).")
                continue
            try:
                if low == "/mic":
                    clip = record_mic(py)
                    if clip is None:
                        continue
                else:
                    arg = line[6:].strip().strip('"')
                    if not arg:
                        warn("Usage: /voice path\\to\\recording.wav")
                        continue
                    clip = Path(arg).expanduser()
                data = read_wav_for_upload(clip)
            except StepError as e:
                bad(str(e))
                continue
            info(f"Transcribing, language {language} (the audio is sent to Meta and is not stored)...")
            c, res = _post(b + "/api/assistant/transcribe?language=" + urllib.parse.quote(language), data,
                           "audio/wav", tok)
            if c != 200:
                bad(f"Transcription failed: {res.get('detail') or 'HTTP ' + str(c)}")
                continue
            line = str(res["transcript"])
            out("  " + col("You said:", "c", True) + f" {line}")
            mode = "voice"  # sent straight away - no extra confirmation
        body = {"message": line[:1500], "input_mode": mode}
        if conv:
            body["conversation_id"] = conv
        c, res = post_with_thinking(b + "/api/assistant/chat", json.dumps(body).encode(), "application/json", tok, think)
        if c != 200:
            bad(f"The assistant could not answer: {res.get('detail') or 'HTTP ' + str(c)}")
            continue
        conv = res.get("conversation_id") or conv
        if res.get("status") != "ok":
            warn(str(res.get("message") or f"Assistant status: {res.get('status')}"))
            continue
        if think:
            for ln in thinking_trace(res):
                stream_text("    " + ln, 400, dim=True)
        out(col("  Assistant>", "c", True))
        stream_text(_wrap(str(res.get("answer") or "")), 220)
        for p in res.get("pending_actions", []):
            warn(f"Waiting for your decision: {p.get('summary')}  -> reply 'yes' to confirm or 'no' to decline")


def set_env_values(env_path: Path, values: Dict[str, str]) -> None:
    """Update or add KEY=value lines in .env, keeping every other line."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    done = set()
    for i, ln in enumerate(lines):
        k = ln.split("=", 1)[0].strip()
        if k in values and not ln.lstrip().startswith("#"):
            lines[i] = f"{k}={values[k]}"
            done.add(k)
    lines += [f"{k}={v}" for k, v in values.items() if k not in done]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass


def configure_ai(root: Path) -> None:
    env_path = root / ".env"
    cur = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    on = "CROWDGUARD_PROVIDER=meta" in cur
    out(f"  AI features are currently {col('ON', 'g', True) if on else col('OFF', 'y', True)}.")
    out("    1  Turn ON / change the Meta key")
    out("    2  Turn OFF (app uses its built-in local explanations)")
    out("    3  Turn voice questions ON or OFF")
    out("    0  Back")
    ch = ask("Choose", "0")
    vals: Dict[str, str] = {}
    if ch == "1":
        key = ask("Paste your Meta API key (hidden while typing)", "", secret=True)
        if not key:
            info("No key entered - nothing changed")
            return
        AI.key, AI.provider = key.strip(), "meta"
        good, why = AI.validate()
        if not good:
            bad(f"Meta did not accept that key: {why}. Nothing was saved.")
            AI.key, AI.provider = "", "none"
            return
        ok("Key accepted")
        vals = {"CROWDGUARD_PROVIDER": "meta", "META_MODEL_API_KEY": key.strip(), "META_MODEL": META_MODEL}
    elif ch == "2":
        vals = {"CROWDGUARD_PROVIDER": "local", "CROWDGUARD_VOICE": "off"}
    elif ch == "3":
        if not on:
            warn("Voice needs the AI provider ON first (option 1).")
            return
        if yesno("Turn voice ON? (audio is sent to Meta, about $0.18 per hour; No turns it off)", False):
            vals = {"CROWDGUARD_VOICE": "meta", "META_VOICE_MODE": "ENDPOINTING"}
        else:
            vals = {"CROWDGUARD_VOICE": "off"}
    else:
        return
    set_env_values(env_path, vals)
    ok("Settings saved to .env (private; the key is never printed or logged)")
    py = str(venv_python(root))
    if port_busy(BACKEND_PORT):
        info("Restarting the backend so the change takes effect...")
        stop_one(root, "backend")
    start_one(root, "backend", py)


# ---- uninstall
def unsafe_to_delete(root: Path) -> str:
    """Return a reason if this folder must not be deleted, else an empty string."""
    home = Path.home().resolve()
    if root == root.parent or root == home or root in home.parents or len(root.parts) < 2:
        return "this looks like a system, drive or home folder"
    if not (root / "backend" / "api" / "app.py").exists() and not (root / "installer").exists():
        return "this does not look like a CrowdGuard install folder"
    return ""


def folder_size(root: Path) -> int:
    total = 0
    for dp, _, fs in os.walk(root):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


DELETE_SCRIPT = r'''
import os, shutil, stat, sys, time
root = sys.argv[1]
def force(func, path, exc):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass
time.sleep(float(sys.argv[2]))
for _ in range(10):
    shutil.rmtree(root, onerror=force)
    if not os.path.exists(root):
        break
    time.sleep(1.5)
'''


def delete_tree(root: Path) -> bool:
    """Delete the install folder. On Windows the running Python lives inside it, so a separate
    interpreter (outside the folder) finishes the job a moment after we exit."""
    os.chdir(root.parent)
    if IS_WIN:
        base = getattr(sys, "_base_executable", None) or sys.executable
        kw: Dict[str, object] = {"creationflags": 0x00000008 | 0x00000200}
        subprocess.Popen([base, "-c", DELETE_SCRIPT, str(root), "3"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)  # type: ignore[call-overload]
        return True
    import stat

    def force(func: Callable[..., object], path: str, _exc: object) -> None:
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except OSError:
            pass
    shutil.rmtree(root, onerror=force)
    return not root.exists()


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


ITEM_NOTES = {
    ".venv": "private Python environment - every Python package the app uses",
    "frontend": "web app source code + its node_modules packages",
    "backend": "application code (API, forecasting engine, assistant)",
    "data": "the SQLite database with all stored data",
    ".env": "your settings, including any Meta API key",
    "logs": "installer, backend and web logs",
    "gallery": "your video clips",
    ".crowdguard": "process ids and the record of system installs",
    ".git": "git history of the code",
    "crowdguard": "launcher (Linux/macOS)",
    "crowdguard.cmd": "launcher (Windows)",
    "installer": "this installer",
    "docs": "documentation", "tests": "automated tests", "scripts": "helper scripts",
}


def cmd_out(cmd: List[str], timeout: int = 20) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def uninstall_details(root: Path) -> None:
    """Point-by-point list of everything the uninstall will and will not touch."""
    out()
    out(col("  A. INSIDE THE INSTALL FOLDER - all of this will be permanently DELETED", "r", True))
    out(f"     Folder: {root}   (total {human(folder_size(root))})")
    entries = []
    for p in sorted(root.iterdir()):
        size = folder_size(p) if p.is_dir() else (p.stat().st_size if p.exists() else 0)
        entries.append((size, p))
    for size, p in sorted(entries, key=lambda e: -e[0]):
        note = ITEM_NOTES.get(p.name, "other file/folder in the install")
        if p.name == "gallery":
            note = f"{len([x for x in p.rglob('*') if x.is_file()])} file(s) - your video clips"
        out(f"     {human(size):>9}  {p}{os.sep if p.is_dir() else ''}  - {note}")
    nm = root / "frontend" / "node_modules"
    if nm.exists():
        out(f"     {human(folder_size(nm)):>9}  {nm}{os.sep}  - of the 'frontend' size above: web packages installed by npm")
    out()
    out(col("     Python packages in the private environment (.venv) - deleted with it:", "w", True))
    req = root / "requirements.txt"
    wanted = [ln.strip() for ln in req.read_text(errors="replace").splitlines()
              if ln.strip() and not ln.strip().startswith("#")] if req.exists() else []
    for w in wanted:
        out(f"       - {w}")
    vpy = venv_python(root)
    if vpy.exists():
        try:
            lst = subprocess.run([str(vpy), "-m", "pip", "list", "--format=freeze"], capture_output=True,
                                 text=True, timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            lst = ""
        n = len([x for x in lst.splitlines() if "==" in x])
        out(f"       (about {n} packages in total, including their dependencies)")
    dbp = root / "data" / "crowdguard.db"
    if dbp.exists():
        try:
            counts, _ = inspect_database(root)
            out()
            out(col(f"     Database {dbp} ({human(dbp.stat().st_size)}): {len(counts)} tables, "
                    f"{sum(counts.values())} rows - all of it is deleted.", "w", True))
        except Exception:
            pass
    pids = read_pids(root)
    out()
    out(col("     Running processes that will be stopped first:", "w", True))
    for name, (label, port) in SERVICES.items():
        out(f"       - {label}: " + (f"pid {pids[name]}, port {port}" if name in pids else f"port {port}") +
            (" (running)" if port_busy(port) else " (not running)"))
    out()
    out(col("  B. OUTSIDE THE FOLDER (your laptop / SYSTEM)", "y", True))
    ptr = pointer_file()
    try:
        if ptr.exists() and Path(ptr.read_text(encoding="utf-8").strip()).resolve() == root:
            out(f"     Will be DELETED: {ptr}  (the small note that remembers where CrowdGuard is installed)")
    except OSError:
        pass
    out("     Will NOT be touched (the installer never removes these):")
    out(f"       - Python itself: {getattr(sys, '_base_executable', sys.executable)}")
    out(f"       - Node.js: {which('node') or 'not found'}")
    out(f"       - Git: {which('git') or 'not found'}")
    pipc = cmd_out([str(vpy) if vpy.exists() else sys.executable, "-m", "pip", "cache", "dir"])
    npmc = cmd_out([which("npm") or "npm", "config", "get", "cache"])
    out(f"       - pip download cache: {pipc or 'unknown'}")
    out(f"       - npm download cache: {npmc or 'unknown'}")
    out("       - your GitHub repository and any other project")
    led = root / ".crowdguard" / "system_installs.json"
    try:
        sysinst = json.loads(led.read_text()) if led.exists() else {}
    except (OSError, ValueError):
        sysinst = {}
    if sysinst:
        out("     Installed on your SYSTEM by this installer (NOT removed automatically):")
        for tool, d in sysinst.items():
            out(f"       - {tool} via {d.get('package_manager')} on {d.get('when')}. To remove it yourself: "
                f"{d.get('remove_with') or 'use your package manager'}")
    else:
        out("     This installer did not record installing anything system-wide (Node.js, Git).")
    out("     If the one-line install command installed Python for you, remove it yourself from")
    out("     'Apps & features' (Windows) or your package manager. It is a normal program you may want to keep.")
    out()
    out("     There is no undo and nothing goes to the Recycle Bin / Trash.")


def uninstall(root: Path, assume_yes: bool = False) -> bool:
    why = unsafe_to_delete(root)
    if why:
        bad(f"Refusing to delete {root}: {why}.")
        return False
    out()
    out(col("  UNINSTALL CROWDGUARD AI - please read carefully", "r", True))
    uninstall_details(root)
    if not assume_yes:
        out()
        first = ask("Do you want to continue and delete everything listed in section A? Type yes or no", "no")
        if first.lower() != "yes":
            info("Cancelled - nothing was deleted.")
            return False
        out()
        out(col(f"  FINAL CONFIRMATION: this permanently deletes {root} and everything in it.", "r", True))
        typed = ask("Type DELETE (capital letters) to confirm, or press Enter to cancel", "")
        if typed != "DELETE":
            info("Cancelled - nothing was deleted.")
            return False
    stop_services(root, quiet=True)
    for name, (_, port) in SERVICES.items():
        if port_busy(port):
            warn(f"Port {port} is still in use by something that is not tracked by this install; it is left alone.")
    ok("Services stopped")
    try:
        if pointer_file().exists() and Path(pointer_file().read_text(encoding="utf-8").strip()).resolve() == root:
            pointer_file().unlink()
            ok(f"Removed {pointer_file()}")
    except OSError:
        pass
    done = delete_tree(root)
    if IS_WIN:
        ok(f"Deleting {root} - this finishes a few seconds after this window closes.")
    elif done:
        ok(f"Deleted {root}")
    else:
        warn(f"Some files could not be deleted. Remove {root} manually.")
    out("  Uninstalled. To install again, run the one-line install command from the README.")
    return done


def reinstall(root: Path) -> None:
    a = argparse.Namespace(dir=str(root), repo=None, provider=None, tests="ask", yes=False, no_browser=True)
    cmd_install(a, offer_menu=False)


def menu(root: Path) -> int:
    if not INTERACTIVE:
        bad("The menu needs a keyboard. Use: start, stop, status, stop-backend, stop-frontend, uninstall --yes")
        return 1
    global LOG
    LOG = root / "logs" / "installer.log"
    py = str(venv_python(root))
    items = [
        ("1", "Show status and verify everything"), ("2", "Start everything"), ("3", "Stop everything"),
        ("4", "Start backend only"), ("5", "Stop backend only"),
        ("6", "Start web screens only"), ("7", "Stop web screens only"),
        ("8", "Restart everything"), ("9", "Open the app in your browser"), ("10", "Show recent log lines"),
        ("11", "Chat with the AI assistant (text or voice)"), ("12", "Turn AI features on/off (Meta key)"),
        ("13", "Install / repair / update (re-run the installer)"), ("14", "Uninstall and delete everything"),
        ("0", "Exit")]
    while True:
        out()
        out(col("=" * 72, "c"))
        out(col("  CrowdGuard AI - control menu", "c", True))
        out("  " + service_line(root))
        out(col(f"  Install folder: {root}", "d"))
        show_health(root)
        out(col("=" * 72, "c"))
        for k, t in items:
            out(f"   {col(k.rjust(2), 'y', True)}  {t}")
        ch = ask("What would you like to do? (enter a number)", "0")
        out()
        try:
            if ch == "0":
                return 0
            elif ch == "1":
                for name, (label, port) in SERVICES.items():
                    (ok if port_busy(port) else bad)(f"{label} on port {port}: " + ("listening" if port_busy(port) else "not running"))
                verify_all(root)
            elif ch == "2":
                start_one(root, "backend", py)
                start_one(root, "frontend", py)
            elif ch == "3":
                stop_services(root)
            elif ch == "4":
                start_one(root, "backend", py)
            elif ch == "5":
                stop_one(root, "backend")
            elif ch == "6":
                start_one(root, "frontend", py)
            elif ch == "7":
                stop_one(root, "frontend")
            elif ch == "8":
                stop_services(root, quiet=True)
                start_one(root, "backend", py)
                start_one(root, "frontend", py)
            elif ch == "9":
                import webbrowser
                webbrowser.open(f"http://localhost:{FRONTEND_PORT}")
                ok(f"Opened http://localhost:{FRONTEND_PORT}")
            elif ch == "10":
                show_logs(root)
            elif ch == "11":
                chat_session(root)
            elif ch == "12":
                configure_ai(root)
            elif ch == "13":
                reinstall(root)
            elif ch == "14":
                if uninstall(root):
                    return 0
            else:
                warn("Please enter one of the numbers shown.")
        except StepError as e:
            bad(str(e))
            for line in e.detail.strip().splitlines()[-4:]:
                sub(line[:110])
        except KeyboardInterrupt:
            out()
            warn("Interrupted - back to the menu.")


# ------------------------------------------------------------------ commands
def find_root() -> Path:
    here = Path(__file__).resolve().parent
    for cand in (here.parent, Path.cwd()):
        if (cand / "backend" / "api" / "app.py").exists():
            return cand
    raise SystemExit("Run this from inside the CrowdGuard install folder.")


def cmd_install(args: argparse.Namespace, offer_menu: bool = True) -> int:
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
    remember_install(ctx.root)
    final_summary(ctx)
    open_browser(args)
    if offer_menu and INTERACTIVE and yesno("Open the control menu now (stop/start parts, AI chat, uninstall)?", True):
        return menu(ctx.root)
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
    ap.add_argument("command", nargs="?", default=None, choices=["install", "menu", "start", "stop", "status", "doctor", "start-backend",
                                                                  "stop-backend", "start-frontend", "stop-frontend", "chat",
                                                                  "uninstall"])
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
        if args.command is None:
            return gateway(args)
        if args.command == "install":
            return cmd_install(args)
        if args.command in ("status", "doctor"):
            LOG = find_root() / "logs" / "installer.log"
            return cmd_status(args)
        LOG = find_root() / "logs" / "installer.log"
        if args.command == "menu":
            return menu(find_root())
        root = find_root()
        py = str(venv_python(root))
        if args.command == "chat":
            chat_session(root)
            return 0
        if args.command == "uninstall":
            return 0 if uninstall(root, assume_yes=args.yes) else 1
        if args.command in ("start-backend", "start-frontend"):
            return 0 if start_one(root, args.command.split("-")[1], py) else 1
        if args.command in ("stop-backend", "stop-frontend"):
            stop_one(root, args.command.split("-")[1])
            return 0
        return {"start": cmd_start, "stop": cmd_stop}[args.command](args)
    except KeyboardInterrupt:
        out()
        warn("Cancelled. Nothing is left half-written; run the installer again to continue.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
