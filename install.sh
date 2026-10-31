#!/usr/bin/env bash
# CrowdGuard AI - one-line installer for macOS / Linux.
#   curl -fsSL https://raw.githubusercontent.com/roy777rajat/crowdguard-ai/main/install.sh | bash
# This small script only makes sure Python 3.10+ exists, then hands over to the guided installer.
set -u
RAW="${CROWDGUARD_RAW:-https://raw.githubusercontent.com/roy777rajat/crowdguard-ai/main}"
c() { printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
c "1;36" "CrowdGuard AI - bootstrap"

pick_python() {
  for p in python3 python; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)' 2>/dev/null; then
      echo "$p"; return 0
    fi
  done
  return 1
}

PY="$(pick_python || true)"
if [ -z "$PY" ]; then
  c "33" "[WARN] Python 3.10+ not found."
  printf "Install it now with your package manager (may ask for your password)? [Y/n] "
  if ( : </dev/tty ) 2>/dev/null; then read -r ans </dev/tty || ans="n"; else ans="y"; fi
  case "${ans:-y}" in n*|N*) c "31" "Install Python 3.10+ from https://www.python.org and run this again."; exit 1;; esac
  SUDO=""; [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"
  if   command -v brew    >/dev/null 2>&1; then brew install python
  elif command -v apt-get >/dev/null 2>&1; then $SUDO apt-get update && $SUDO apt-get install -y python3 python3-venv python3-pip
  elif command -v dnf     >/dev/null 2>&1; then $SUDO dnf install -y python3 python3-pip
  else c "31" "[FAIL] No supported package manager (brew/apt/dnf). Install Python 3.10+ manually."; exit 1; fi
  PY="$(pick_python || true)"
  [ -z "$PY" ] && { c "31" "[FAIL] Python still not usable. Open a new terminal and retry."; exit 1; }
fi
c "32" "[ OK ] Using $($PY --version 2>&1) ($(command -v $PY))"

# python3-venv is a separate package on Debian/Ubuntu
if ! "$PY" -c 'import venv, ensurepip' 2>/dev/null; then
  c "33" "[WARN] Python 'venv' module missing."
  if command -v apt-get >/dev/null 2>&1; then
    SUDO=""; [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"
    $SUDO apt-get install -y python3-venv python3-pip || { c "31" "[FAIL] Could not install python3-venv"; exit 1; }
  fi
fi

TMP="$(mktemp -d)"
c "34" "[ .. ] Fetching the installer"
if [ -f "$RAW/installer/crowdguard_installer.py" ]; then cp "$RAW/installer/crowdguard_installer.py" "$TMP/i.py"   # local testing
else curl -fsSL "$RAW/installer/crowdguard_installer.py" -o "$TMP/i.py" || { c "31" "[FAIL] Could not download the installer from $RAW (check your internet / proxy)."; exit 1; }; fi
if ( : </dev/tty ) 2>/dev/null; then exec "$PY" "$TMP/i.py" "$@" </dev/tty; else exec "$PY" "$TMP/i.py" --yes "$@"; fi
