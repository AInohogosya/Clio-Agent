#!/usr/bin/env python3
"""
Clio-Agent-1 Agent Core - Autonomous Loop Entry Point
Retained for backward compatibility.  Primary entry is via run.py.
"""

import os
import platform
import sys
from pathlib import Path


def get_os_context() -> str:
    """Get detailed OS context for the system prompt."""
    try:
        system = platform.system()
        release = platform.release()
        version = platform.version()
        machine = platform.machine()

        mem_info = ""
        disk_info = ""
        try:
            import psutil
            mem = psutil.virtual_memory()
            total_gb = mem.total / (1024 ** 3)
            available_gb = mem.available / (1024 ** 3)
            mem_info = (
                f", Memory: {total_gb:.1f}GB total, "
                f"{available_gb:.1f}GB available"
            )
            disk = psutil.disk_usage(str(Path.home()))
            free_gb = disk.free / (1024 ** 3)
            disk_info = f", Disk free: {free_gb:.1f}GB"
        except Exception:
            pass

        shell = os.environ.get("SHELL", "Unknown")

        if system == "Linux":
            try:
                with open("/etc/os-release") as f:
                    lines = f.readlines()
                distro_info = {}
                for line in lines:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        distro_info[k] = v.strip('"')
                name = distro_info.get("NAME", "Unknown Linux")
                ver = distro_info.get("VERSION", "")
                os_str = f"{name} {ver} ({system} {release} {machine})"
            except Exception:
                os_str = f"Linux {release} {machine}"
        elif system == "Darwin":
            os_str = f"macOS {release} {machine}"
        elif system == "Windows":
            os_str = f"Windows {release} {machine}"
        else:
            os_str = f"{system} {release} {machine}"

        parts = [os_str, mem_info, disk_info]
        if system in ("Linux", "Darwin"):
            parts.append(f"Shell: {shell}")
        # Filter empty
        parts = [p for p in parts if p]
        return ", ".join(parts)

    except Exception:
        return "OS Context unavailable"


def load_config(config_path: str = "config.yaml"):
    """Load YAML configuration, returning a dict."""
    try:
        import yaml
        with open(config_path, "r") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def autonomous_loop():
    """Main autonomous loop – kept thin; real work happens via run.py."""
    config = load_config()
    ctx = get_os_context()
    print(f"OS Context: {ctx}")
    if config and "api" in config:
        provider = config["api"].get("preferred_provider", "unknown")
        print(f"Provider: {provider}")
    else:
        print("Provider: not configured")


if __name__ == "__main__":
    autonomous_loop()
