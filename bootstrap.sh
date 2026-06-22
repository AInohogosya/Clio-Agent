#!/usr/bin/env bash
# Clio Agent - Bare-metal bootstrapper
# Handles the case where Python is NOT installed at all.
# Usage: bash bootstrap.sh

set -euo pipefail

GREEN='[0;32m'
YELLOW='[1;33m'
CYAN='[0;36m'
RED='[0;31m'
BOLD='[1m'
RESET='[0m'

info()  { echo -e "${CYAN}$*${RESET}"; }
ok()    { echo -e "${GREEN}  OK $*${RESET}"; }
fail()  { echo -e "${RED}  FAIL $*${RESET}"; }
header(){ echo -e "
${BOLD}$*${RESET}"; }

header "Clio Agent - Bare-Metal Bootstrap"

# Detect OS
OS="unknown"
OS_ID=""
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
elif [[ "$OSTYPE" == "linux-gnu"* ]] || [[ -f /etc/os-release ]]; then
    OS="linux"
    [[ -f /etc/os-release ]] && . /etc/os-release && OS_ID="${ID:-unknown}"
elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ -n "${WINDIR:-}" ]]; then
    OS="windows"
fi

info "Detected OS: $OS ${OS_ID:+($OS_ID)}"

# Check if Python 3.8+ is available
PYTHON=""
for exe in python3 python; do
    if command -v "$exe" &>/dev/null; then
        v=$("$exe" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null) || continue
        maj=$(echo "$v" | cut -d. -f1)
        min=$(echo "$v" | cut -d. -f2)
        if [[ "$maj" == "3" ]] && (( min >= 8 )); then
            PYTHON="$exe"
            ok "Found Python $v via $PYTHON"
            break
        fi
    fi
done

# Install Python if not found
if [[ -z "$PYTHON" ]]; then
    header "Python 3.8+ Not Found - Installing"

    if [[ "$OS" == "macos" ]]; then
        if ! command -v brew &>/dev/null; then
            info "Installing Homebrew..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || {
                fail "Homebrew install failed."; exit 1
            }
            [[ -d /opt/homebrew/bin ]] && export PATH="/opt/homebrew/bin:$PATH"
        fi
        brew install python@3.12 2>/dev/null || brew install python@3.11 2>/dev/null || brew install python3 || {
            fail "Homebrew Python install failed."; exit 1
        }

    elif [[ "$OS" == "linux" ]]; then
        if command -v apt-get &>/dev/null; then
            sudo apt-get update -y && sudo apt-get install -y python3 python3-pip python3-venv
        elif command -v apt &>/dev/null; then
            sudo apt update -y && sudo apt install -y python3 python3-pip python3-venv
        elif command -v dnf &>/dev/null; then
            sudo dnf install -y python3 python3-pip
        elif command -v yum &>/dev/null; then
            sudo yum install -y python3 python3-pip
        elif command -v pacman &>/dev/null; then
            sudo pacman -Sy --noconfirm python python-pip
        elif command -v zypper &>/dev/null; then
            sudo zypper install -y python3 python3-pip python3-venv
        elif command -v apk &>/dev/null; then
            apk add --no-cache python3 py3-pip
        else
            fail "No supported package manager."; exit 1
        fi

    elif [[ "$OS" == "windows" ]]; then
        if command -v winget &>/dev/null; then
            winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements || {
                fail "winget install failed."; exit 1
            }
        else
            fail "winget not available. Install Python manually."; exit 1
        fi
    else
        fail "Unsupported OS."; exit 1
    fi

    PYTHON=""
    for exe in python3 python; do
        if command -v "$exe" &>/dev/null; then
            v=$("$exe" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null) || continue
            maj=$(echo "$v" | cut -d. -f1)
            min=$(echo "$v" | cut -d. -f2)
            if [[ "$maj" == "3" ]] && (( min >= 8 )); then
                PYTHON="$exe"; break
            fi
        fi
    done
    [[ -z "$PYTHON" ]] && { fail "Python installed but not in PATH. Restart terminal."; exit 1; }
    ok "Python installed: $PYTHON"
fi

# Install git if missing
if ! command -v git &>/dev/null; then
    info "Installing git..."
    if [[ "$OS" == "macos" ]]; then
        xcode-select --install 2>/dev/null || true
    elif [[ "$OS" == "linux" ]]; then
        command -v apt-get &>/dev/null && sudo apt-get install -y git
        command -v apt &>/dev/null && sudo apt install -y git
        command -v dnf &>/dev/null && sudo dnf install -y git
        command -v yum &>/dev/null && sudo yum install -y git
        command -v pacman &>/dev/null && sudo pacman -Sy --noconfirm git
        command -v zypper &>/dev/null && sudo zypper install -y git
        command -v apk &>/dev/null && apk add --no-cache git
    fi
fi

# Hand off to Python bootstrap
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
info "Handing off to Python bootstrap..."
exec "$PYTHON" "$SCRIPT_DIR/run.py" "$@"
