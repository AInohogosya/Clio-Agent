"""
Sample usage demo for all 6 agent tools.

Run directly (from project root):
    python examples/tool_usage_demo.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_agent.tools.base import PermissionSet, get_tool_registry, get_tool_logger
from ai_agent.tools.file_read import FileReadTool, FileReadInput
from ai_agent.tools.file_write import FileWriteTool, FileWriteInput
from ai_agent.tools.file_edit import FileEditTool, FileEditInput
from ai_agent.tools.bash import BashTool, BashInput
from ai_agent.tools.glob import GlobTool, GlobInput
from ai_agent.tools.grep import GrepTool, GrepInput

GREET_CONTENT = "def greet(name):\n    return 'Hello, ' + name + '!'\n\nprint(greet('World'))\n"


def demo():
    tmp = Path(tempfile.mkdtemp(prefix="tool_demo_"))
    print(f"Working in: {tmp}\n")
    print("=" * 60)

    perms = PermissionSet()  # all permissions granted

    # ── 1. FileWriteTool ──────────────────────────────────────────
    print("\n[1] FileWriteTool — Create a new file")
    write_tool = FileWriteTool(perms)
    result = write_tool.execute(FileWriteInput(
        file_path=str(tmp / "example.py"),
        content=GREET_CONTENT,
    ))
    print(f"  Success: {result.success}")
    print(f"  Output:  {result.output}")
    print(f"  Meta:    {result.metadata}")

    # ── 2. FileReadTool ───────────────────────────────────────────
    print("\n[2] FileReadTool — Read the file back")
    read_tool = FileReadTool(perms)
    result = read_tool.execute(FileReadInput(
        file_path=str(tmp / "example.py"),
    ))
    print(f"  Success: {result.success}")
    print(f"  Content:\n{result.output}")
    print(f"  Meta:    lines={result.metadata['total_lines']}")

    # ── 3. FileEditTool ───────────────────────────────────────────
    print("\n[3] FileEditTool — Replace 'World' with 'Universe'")
    edit_tool = FileEditTool(perms)
    result = edit_tool.execute(FileEditInput(
        file_path=str(tmp / "example.py"),
        old_string="greet('World')",
        new_string="greet('Universe')",
    ))
    print(f"  Success: {result.success}")
    print(f"  Output:  {result.output}")

    # Verify the edit
    result = read_tool.execute(FileReadInput(file_path=str(tmp / "example.py")))
    print(f"  Verified content:\n{result.output}")

    # ── 4. BashTool ───────────────────────────────────────────────
    print("\n[4] BashTool — Execute a shell command")
    bash_tool = BashTool(perms)
    result = bash_tool.execute(BashInput(
        command=f"python {tmp / 'example.py'}",
    ))
    print(f"  Success: {result.success}")
    print(f"  Output:  {result.output}")

    # BashTool — blocked command
    print("\n[4b] BashTool — Blocked command (rm -rf /)")
    result = bash_tool.execute(BashInput(command="rm -rf /"))
    print(f"  Success: {result.success}")
    print(f"  Error:   {result.error.code} — {result.error.message}")

    # ── Create some more files for glob / grep ───────────────────
    (tmp / "app.py").write_text(
        "import os\nfrom flask import Flask\n\napp = Flask(__name__)\n",
        encoding="utf-8",
    )
    (tmp / "utils.py").write_text(
        "def helper():\n    return True\n\ndef greet(name):\n    return f'hi {name}'\n",
        encoding="utf-8",
    )
    (tmp / "README.md").write_text(
        "# My App\n\nThis is a Flask app.\n", encoding="utf-8",
    )
    sub = tmp / "tests"
    sub.mkdir()
    (sub / "test_app.py").write_text(
        "from app import app\ndef test_greet():\n    assert True\n",
        encoding="utf-8",
    )

    # ── 5. GlobTool ───────────────────────────────────────────────
    print("\n[5] GlobTool — Find all .py files recursively")
    glob_tool = GlobTool(perms)
    result = glob_tool.execute(GlobInput(
        pattern="**/*.py",
        path=str(tmp),
    ))
    print(f"  Success: {result.success}")
    print(f"  Output:\n{result.output}")
    print(f"  Meta:    {result.metadata['match_count']} files")

    # ── 6. GrepTool ───────────────────────────────────────────────
    print("\n[6] GrepTool — Search for 'greet' in all files")
    grep_tool = GrepTool(perms)
    result = grep_tool.execute(GrepInput(
        pattern="greet",
        path=str(tmp),
    ))
    print(f"  Success: {result.success}")
    print(f"  Output:{result.output}")
    print(f"  Meta:    {result.metadata['match_count']} matches")

    # GrepTool — with extension filter
    print("\n[6b] GrepTool — Search 'greet' only in .py files")
    result = grep_tool.execute(GrepInput(
        pattern="greet",
        path=str(tmp),
        file_extensions=["py"],
    ))
    print(f"  Success: {result.success}")
    print(f"  Output:{result.output}")

    # ── ToolRegistry ─────────────────────────────────────────────
    print("\n[7] ToolRegistry — Register and list all tools")
    registry = get_tool_registry()
    for tool in [read_tool, write_tool, edit_tool, bash_tool, glob_tool, grep_tool]:
        registry.register(tool)
    print(f"  Registered tools: {registry.list_tools()}")

    # Execute via registry
    result = registry.execute("file_read", FileReadInput(
        file_path=str(tmp / "README.md"),
    ))
    print(f"\n  Registry 'file_read' result: success={result.success}")
    print(f"  Content: {result.output.strip()}")

    # ── Execution log ─────────────────────────────────────────────
    print("\n[8] Execution Log Summary")
    logger = get_tool_logger()
    for rec in logger.records:
        status = "OK" if rec.success else "FAIL"
        print(f"  [{status}] {rec.tool_name}  {rec.duration_ms:.1f}ms  id={rec.execution_id}")

    # Cleanup
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nCleaned up {tmp}")
    print("\nDemo complete.")


if __name__ == "__main__":
    demo()
