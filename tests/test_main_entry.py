"""
Tests for the main.py entry point and run.py launcher.
"""
import subprocess
import sys
from pathlib import Path


class TestMainPy:
    """Tests for main.py entry point"""

    def test_run_py_exists(self):
        project_root = Path(__file__).resolve().parent.parent
        run_py = project_root / "run.py"
        assert run_py.exists()

    def test_main_py_exists(self):
        project_root = Path(__file__).resolve().parent.parent
        main_py = project_root / "clio_agent_2" / "main.py"
        assert main_py.exists()

    def test_run_py_imports_main(self):
        project_root = Path(__file__).resolve().parent.parent
        run_py_content = (project_root / "run.py").read_text()
        assert "clio_agent_2.main" in run_py_content
        assert "import" in run_py_content


class TestMainPyCommands:
    """Test main.py command parsing via subprocess"""

    def _run_command(self, args):
        project_root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [sys.executable, "-m", "clio_agent_2.main"] + args,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result

    def test_help_shows_usage(self):
        result = self._run_command(["--help"])
        assert result.returncode == 0
        assert "usage:" in result.stdout.lower() or "usage:" in result.stderr.lower()

    def test_version_or_unknown_flag(self):
        result = self._run_command(["--nonexistent"])
        assert result.returncode != 0


class TestSetupEnv:
    """Test setup_env module invocation"""

    def _run_setup(self, args):
        project_root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [sys.executable, "run.py", "setup"] + args,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result

    def test_setup_help(self):
        result = self._run_setup(["--help"])
        assert result.returncode == 0
        assert "usage:" in result.stdout.lower()