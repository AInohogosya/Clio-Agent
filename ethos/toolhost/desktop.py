from __future__ import annotations

import base64
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ethos.observability.logger import get_logger

logger = get_logger("ethos.toolhost.desktop")

DISPLAY = ":1"


class DesktopError(RuntimeError):
    pass


class DesktopController:
    """Desktop control on the agent's own machine: Xvfb display :1 + xdotool."""

    def __init__(self, display: str = DISPLAY):
        self.display = display

    def _env(self) -> dict[str, str]:
        import os

        env = dict(os.environ)
        env["DISPLAY"] = self.display
        return env

    def _available(self) -> bool:
        return shutil.which("xdotool") is not None

    def _run(self, args: list[str], timeout_s: float = 15.0) -> str:
        if not self._available():
            raise DesktopError(
                "desktop control requires xdotool and an Xvfb display (available on the agent VM)"
            )
        result = subprocess.run(
            ["xdotool", *args], capture_output=True, text=True, timeout=timeout_s, env=self._env(),
        )
        if result.returncode != 0:
            raise DesktopError(result.stderr.strip() or "xdotool failed")
        return result.stdout.strip()

    def _screenshot(self) -> dict[str, Any]:
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            path = tmp.name
        try:
            result = subprocess.run(
                ["import", "-window", "root", path],
                capture_output=True, text=True, timeout=30, env=self._env(),
            )
            if result.returncode != 0:
                raise DesktopError("import (ImageMagick) failed; is Xvfb running on :1?")
            data = Path(path).read_bytes()
            return {"image_b64": base64.b64encode(data).decode("ascii"), "bytes": len(data)}
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def key(self, keys: str) -> dict[str, Any]:
        self._run(["key", keys])
        return {"sent_keys": keys}

    def type(self, text: str) -> dict[str, Any]:
        self._run(["type", "--delay", "60", text])
        return {"typed": len(text)}

    def click(self, x: int, y: int, button: int = 1) -> dict[str, Any]:
        self._run(["mousemove", str(int(x)), str(int(y))])
        self._run(["click", str(int(button))])
        return {"clicked": [x, y]}

    def move(self, x: int, y: int) -> dict[str, Any]:
        self._run(["mousemove", str(int(x)), str(int(y))])
        return {"moved": [x, y]}

    def screenshot(self) -> dict[str, Any]:
        return self._screenshot()

    def windows(self) -> dict[str, Any]:
        out = self._run(["search", "--onlyvisible", "--name", ""])
        return {"windows": [line for line in out.splitlines() if line.strip()]}
