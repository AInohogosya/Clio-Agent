#!/usr/bin/env bash
# Clio Agent - Bare-metal bootstrapper
# Handles the case where Python is NOT installed at all.
# Usage: bash bootstrap.sh
# 
# Features:
# - Multi-platform support (Linux, macOS, Windows)
# - Automatic Python 3.8+ installation
# - Git installation if missing
# - System dependency validation
# - Comprehensive error handling with recovery options
# - Detailed logging and progress reporting

set -euo pipefail

# Color codes for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

# Logging functions
info()  { echo -e "${CYAN}ℹ $*${RESET}"; }
ok()    { echo -e "${GREEN}  ✓ $*${RESET}"; }
warn()  { echo -e "${YELLOW}  ⚠ $*${RESET}"; }
fail()  { echo -e "${RED}  ✗ $*${RESET}"; }
header(){ echo -e "\n${BOLD}$*${RESET}"; }
step()  { echo -e "\n${BOLD}▶ $*${RESET}"; }

# Cleanup handler
cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        warn "Bootstrap interrupted (exit code: $exit_code)"
        warn "You can resume by running: bash bootstrap.sh"
    fi
}
trap cleanup EXIT

# Detect OS with enhanced detection
detect_os() {
    local os="unknown"
    local os_id=""
    local os_version=""
    
    if [[ "$OSTYPE" == "darwin"* ]]; then
        os="macos"
        os_version=$(sw_vers -productVersion 2>/dev/null || echo "unknown")
    elif [[ "$OSTYPE" == "linux-gnu"* ]] || [[ -f /etc/os-release ]]; then
        os="linux"
        if [[ -f /etc/os-release ]]; then
            . /etc/os-release
            os_id="${ID:-unknown}"
            os_version="${VERSION_ID:-unknown}"
        fi
    elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ -n "${WINDIR:-}" ]]; then
        os="windows"
        os_version=$(wmic os get Version 2>/dev/null | tail -1 || echo "unknown")
    fi
    
    echo "$os|$os_id|$os_version"
}

# Check command availability
has_command() {
    command -v "$1" &>/dev/null
}

# Check if Python 3.8+ is available
find_python() {
    local python_exe=""
    local python_version=""
    
    for exe in python3.12 python3.11 python3.10 python3.9 python3.8 python3 python; do
        if has_command "$exe"; then
            local v
            v=$("$exe" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null) || continue
            local maj min
            maj=$(echo "$v" | cut -d. -f1)
            min=$(echo "$v" | cut -d. -f2)
            
            if [[ "$maj" == "3" ]] && (( min >= 8 )); then
                python_exe="$exe"
                python_version="$v"
                break
            fi
        fi
    done
    
    if [[ -n "$python_exe" ]]; then
        echo "$python_exe|$python_version"
    else
        echo "|"
    fi
}

# Install Python on macOS
install_python_macos() {
    step "Installing Python on macOS"
    
    # Install Homebrew if needed
    if ! has_command brew; then
        info "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || {
            fail "Homebrew installation failed"
            return 1
        }
        
        # Add Homebrew to PATH
        if [[ -d /opt/homebrew/bin ]]; then
            export PATH="/opt/homebrew/bin:$PATH"
        elif [[ -d /usr/local/bin ]]; then
            export PATH="/usr/local/bin:$PATH"
        fi
        ok "Homebrew installed"
    else
        ok "Homebrew already installed"
    fi
    
    # Update Homebrew
    info "Updating Homebrew..."
    brew update --quiet || true
    
    # Install Python
    local python_versions=("python@3.12" "python@3.11" "python@3.10" "python@3.9")
    local installed=false
    
    for pyver in "${python_versions[@]}"; do
        if brew list "$pyver" &>/dev/null; then
            ok "Python $pyver already installed"
            installed=true
            break
        fi
    done
    
    if [[ "$installed" == "false" ]]; then
        info "Installing Python..."
        for pyver in "${python_versions[@]}"; do
            if brew install "$pyver" 2>/dev/null; then
                ok "Python $pyver installed"
                installed=true
                break
            fi
        done
        
        if [[ "$installed" == "false" ]]; then
            fail "Could not install any Python version"
            return 1
        fi
    fi
    
    return 0
}

# Install Python on Linux
install_python_linux() {
    step "Installing Python on Linux"
    
    local os_info
    os_info=$(detect_os)
    local os_id
    os_id=$(echo "$os_info" | cut -d'|' -f2)
    
    # Function to run commands with timeout
    run_with_timeout() {
        local timeout_sec=300
        timeout "$timeout_sec" sudo "$@" 2>&1 || {
            local rc=$?
            if [[ $rc -eq 124 ]]; then
                fail "Package manager timed out (${timeout_sec}s)"
            fi
            return $rc
        }
    }
    
    case "$os_id" in
        ubuntu|debian|linuxmint|pop)
            info "Using APT package manager..."
            run_with_timeout apt-get update -y || {
                fail "APT update failed"
                return 1
            }
            run_with_timeout apt-get install -y python3 python3-pip python3-venv python3-dev || {
                fail "APT installation failed"
                return 1
            }
            ;;
        fedora|rhel|centos|rocky|almalinux)
            info "Using DNF/YUM package manager..."
            if has_command dnf; then
                run_with_timeout dnf install -y python3 python3-pip python3-devel || {
                    fail "DNF installation failed"
                    return 1
                }
            elif has_command yum; then
                run_with_timeout yum install -y python3 python3-pip python3-devel || {
                    fail "YUM installation failed"
                    return 1
                }
            fi
            ;;
        arch|manjaro|endeavouros)
            info "Using Pacman package manager..."
            run_with_timeout pacman -Sy --noconfirm python python-pip || {
                fail "Pacman installation failed"
                return 1
            }
            ;;
        opensuse-tumbleweed|opensuse-leap|sles)
            info "Using Zypper package manager..."
            run_with_timeout zypper install -y python3 python3-pip python3-devel || {
                fail "Zypper installation failed"
                return 1
            }
            ;;
        alpine)
            info "Using APK package manager..."
            timeout 300 apk add --no-cache python3 py3-pip py3-venv || {
                fail "APK installation failed"
                return 1
            }
            ;;
        *)
            fail "Unsupported Linux distribution: $os_id"
            warn "Please install Python 3.8+ manually"
            return 1
            ;;
    esac
    
    ok "Python packages installed"
    return 0
}

# Install Python on Windows
install_python_windows() {
    step "Installing Python on Windows"
    
    if has_command winget; then
        info "Installing Python via winget..."
        winget install -e --id Python.Python.3.12 \
            --accept-package-agreements \
            --accept-source-agreements \
            --silent || {
            warn "winget Python 3.12 install failed, trying 3.11..."
            winget install -e --id Python.Python.3.11 \
                --accept-package-agreements \
                --accept-source-agreements \
                --silent || {
                fail "winget install failed"
                return 1
            }
        }
        ok "Python installed via winget"
    else
        fail "winget not available. Please install Python 3.8+ manually from:"
        echo "  https://www.python.org/downloads/"
        return 1
    fi
    
    return 0
}

# Install git if missing
install_git() {
    if has_command git; then
        ok "Git already installed"
        return 0
    fi
    
    step "Installing Git"
    
    local os_info
    os_info=$(detect_os)
    local os
    os=$(echo "$os_info" | cut -d'|' -f1)
    local os_id
    os_id=$(echo "$os_info" | cut -d'|' -f2)
    
    case "$os" in
        macos)
            info "Installing Xcode Command Line Tools (includes git)..."
            xcode-select --install 2>/dev/null || {
                warn "xcode-select install already initiated or failed"
                # Try homebrew as fallback
                if has_command brew; then
                    brew install git || true
                fi
            }
            ;;
        linux)
            case "$os_id" in
                ubuntu|debian|linuxmint|pop)
                    sudo apt-get install -y git || true
                    ;;
                fedora|rhel|centos|rocky|almalinux)
                    sudo dnf install -y git || sudo yum install -y git || true
                    ;;
                arch|manjaro|endeavouros)
                    sudo pacman -Sy --noconfirm git || true
                    ;;
                opensuse-*)
                    sudo zypper install -y git || true
                    ;;
                alpine)
                    apk add --no-cache git || true
                    ;;
                *)
                    warn "Cannot install git automatically for $os_id"
                    ;;
            esac
            ;;
        windows)
            if has_command winget; then
                winget install -e --id Git.Git --accept-package-agreements --silent || true
            fi
            ;;
    esac
    
    if has_command git; then
        ok "Git installed"
    else
        warn "Git installation failed - some features may be unavailable"
    fi
    
    return 0
}

# Validate system dependencies
validate_system_deps() {
    step "Validating system dependencies"
    
    local missing=()
    
    # Check for build essentials (needed for some Python packages)
    case "$(detect_os | cut -d'|' -f1)" in
        linux)
            if ! has_command gcc && ! has_command clang; then
                missing+=("gcc or clang (C compiler)")
            fi
            if ! has_command make; then
                missing+=("make")
            fi
            ;;
    esac
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        warn "Some system dependencies are missing:"
        for dep in "${missing[@]}"; do
            echo "  - $dep"
        done
        warn "Some Python packages may fail to build"
    else
        ok "System dependencies validated"
    fi
    
    return 0
}

# Main execution
header "🚀 Clio Agent - Bare-Metal Bootstrap"
echo ""
info "This script will set up Python 3.8+ and all required dependencies"
info "Detected OS: $(detect_os | tr '|' ' ')"
echo ""

# Detect OS
OS_INFO=$(detect_os)
OS=$(echo "$OS_INFO" | cut -d'|' -f1)
OS_ID=$(echo "$OS_INFO" | cut -d'|' -f2)
OS_VERSION=$(echo "$OS_INFO" | cut -d'|' -f3)

if [[ "$OS" == "unknown" ]]; then
    fail "Could not detect operating system"
    exit 1
fi

# Check for Python
PYTHON_RESULT=$(find_python)
PYTHON_EXE=$(echo "$PYTHON_RESULT" | cut -d'|' -f1)
PYTHON_VER=$(echo "$PYTHON_RESULT" | cut -d'|' -f2)

if [[ -z "$PYTHON_EXE" ]]; then
    header "📦 Python 3.8+ Not Found - Installing"
    
    case "$OS" in
        macos)
            install_python_macos || exit 1
            ;;
        linux)
            install_python_linux || exit 1
            ;;
        windows)
            install_python_windows || exit 1
            ;;
        *)
            fail "Unsupported operating system: $OS"
            exit 1
            ;;
    esac
    
    # Re-detect Python after installation
    PYTHON_RESULT=$(find_python)
    PYTHON_EXE=$(echo "$PYTHON_RESULT" | cut -d'|' -f1)
    PYTHON_VER=$(echo "$PYTHON_RESULT" | cut -d'|' -f2)
    
    if [[ -z "$PYTHON_EXE" ]]; then
        fail "Python installation completed but not found in PATH"
        warn "Please restart your terminal and run bootstrap.sh again"
        exit 1
    fi
    
    ok "Python $PYTHON_VER installed successfully"
else
    ok "Found Python $PYTHON_VER ($PYTHON_EXE)"
fi

# Verify Python version meets minimum requirement
PY_MAJOR=$(echo "$PYTHON_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PYTHON_VER" | cut -d. -f2)

if [[ "$PY_MAJOR" != "3" ]] || (( PY_MINOR < 8 )); then
    fail "Python 3.8+ required but found Python $PYTHON_VER"
    exit 1
fi

# Install git if missing
install_git

# Validate system dependencies
validate_system_deps

# Display summary
header "✅ Bootstrap Complete"
echo ""
ok "Python $PYTHON_VER: $PYTHON_EXE"
ok "Git: $(git --version 2>/dev/null || echo 'not installed')"
echo ""
info "Next steps:"
echo "  1. Run: bash install.sh"
echo "  2. Or run directly: $PYTHON_EXE run.py"
echo ""
info "Handing off to Python bootstrap..."
echo ""

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Execute run.py with the detected Python
exec "$PYTHON_EXE" "$SCRIPT_DIR/run.py" "$@"
