#!/usr/bin/env bash
# Clio Agent — One-command installer
# Usage:  curl -fsSL https://raw.githubusercontent.com/AInohogosya/VEXIS-CLI/main/install.sh | bash
# Or:     git clone https://github.com/AInohogosya/VEXIS-CLI.git && cd VEXIS-CLI && bash install.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
RESET='\033[0m'

info()  { echo -e "${CYAN}$*${RESET}"; }
ok()    { echo -e "${GREEN}  OK $*${RESET}"; }
warn()  { echo -e "${YELLOW}  WARN $*${RESET}"; }
fail()  { echo -e "${RED}  FAIL $*${RESET}"; }
header(){ echo -e "\n${BOLD}$*${RESET}"; }

header "Clio Agent Installer"

# ── Python check ──
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    fail "Python 3 not found. Install Python 3.8+ and retry."
    exit 1
fi

PY_VERSION=$($PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
ok "Python $PY_VERSION detected ($($PYTHON -c 'import sys; print(sys.executable)'))"

# ── Create venv ──
VENV_DIR="$PROJECT_DIR/venv"
if [ ! -d "$VENV_DIR" ]; then
    info "Creating virtual environment..."
    $PYTHON -m venv "$VENV_DIR"
    ok "Virtual environment created"
else
    ok "Virtual environment already exists"
fi

# Activate
if [ -f "$VENV_DIR/bin/activate" ]; then
    VENV_PYTHON="$VENV_DIR/bin/python"
    VENV_PIP="$VENV_DIR/bin/pip"
elif [ -f "$VENV_DIR/Scripts/activate" ]; then
    VENV_PYTHON="$VENV_DIR/Scripts/python.exe"
    VENV_PIP="$VENV_DIR/Scripts/pip.exe"
else
    fail "Cannot locate venv activate script"
    exit 1
fi

# ── Upgrade pip ──
info "Upgrading pip..."
"$VENV_PYTHON" -m pip install --upgrade pip --quiet
ok "pip upgraded"

# ── Install dependencies ──
info "Installing Clio Agent (with all AI provider SDKs)..."
"$VENV_PIP" install -e "$PROJECT_DIR" --quiet 2>/dev/null || "$VENV_PIP" install "$PROJECT_DIR" --quiet
ok "Clio Agent installed"

# ── Register global commands ──
header "Registering Global Commands"

SHELL_NAME=$(basename "${SHELL:-bash}")
case "$SHELL_NAME" in
    zsh)  RC_FILE="$HOME/.zshrc" ;;
    bash) RC_FILE="$HOME/.bashrc"
          [ -f "$HOME/.bash_profile" ] && RC_FILE="$HOME/.bash_profile"
          ;;
    *)    RC_FILE="$HOME/.profile" ;;
esac

# Create wrapper scripts
WRAPPER_DIR="$HOME/.local/bin"
mkdir -p "$WRAPPER_DIR"

cat > "$WRAPPER_DIR/Clio-Agent" << WRAP
#!/usr/bin/env bash
exec "$VENV_PYTHON" "$PROJECT_DIR/run.py" "\$@"
WRAP
chmod +x "$WRAPPER_DIR/Clio-Agent"

cp "$WRAPPER_DIR/Clio-Agent" "$WRAPPER_DIR/clio-agent"

# Add to PATH if needed
if [[ ":$PATH:" != *":$WRAPPER_DIR:"* ]]; then
    echo "" >> "$RC_FILE"
    echo "# Clio Agent" >> "$RC_FILE"
    echo "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> "$RC_FILE"
    warn "Added $WRAPPER_DIR to PATH in $RC_FILE"
    warn "Run: source $RC_FILE"
fi

# ── Verify ──
header "Verification"
if "$VENV_PYTHON" -c "import ai_agent" 2>/dev/null; then
    ok "ai_agent package imports correctly"
else
    warn "Package import check failed (may need a terminal restart)"
fi

# ── Done ──
header "Installation Complete"
echo -e "  ${GREEN}OK${RESET} Clio Agent is ready!"
echo ""
echo -e "  ${BOLD}Quick start:${RESET}"
echo -e "    ${CYAN}Clio-Agent --help${RESET}          Show all options"
echo -e "    ${CYAN}Clio-Agent${RESET}                 Start interactive setup"
echo -e "    ${CYAN}Clio-Agent \"your task here\"${RESET} Run a specific task"
echo ""
echo -e "  ${DIM}If 'Clio-Agent' is not found, restart your terminal or run:${RESET}"
echo -e "    ${CYAN}source $RC_FILE${RESET}"
echo ""
