import asyncio
import json
from pathlib import Path
from typing import Any

from code_agent.coding_tools import (
    GitDiffTool,
    GitStatusTool,
    ListFilesTool,
    ReadFileTool,
    ReplaceInFileTool,
    RunCommandTool,
    SearchTextTool,
    WriteFileTool,
    default_coding_tools,
)
from code_agent_core import (
    ApprovalResponse,
    DefaultPermissionPolicy,
    RuntimeEvent,
    ToolExecutor,
    ToolOrigin,
    ToolRegistry,
    ToolStatus,
)
from code_agent_core.runtime.spec import (
    ExecutionContext,
    PermissionContext,
)
from code_agent_core.tools.permissions import ApprovalRequest
from code_agent_llm import ToolCall


class Sink:
    async def emit(self, event: RuntimeEvent) -> None:
        return None


class Approve:
    async def request(
        self,
        request: ApprovalRequest,
        context: ExecutionContext,
    ) -> ApprovalResponse:
        return ApprovalResponse(fingerprint=request.call.fingerprint, approved=True)


class Token:
    def __init__(self) -> None:
        self._cancelled = False

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    async def wait(self) -> None:
        import asyncio

        while not self._cancelled:
            await asyncio.sleep(0)


def make_context(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        workspace=tmp_path,
        session_id="s1",
        run_id="r1",
        cancellation=Token(),
        permission_context=PermissionContext(),
        event_sink=Sink(),
    )


def make_executor(*tools: Any) -> ToolExecutor:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool, origin=ToolOrigin.BUILTIN)
    return ToolExecutor(registry, DefaultPermissionPolicy(), Approve())


def run_tool(tool: Any, arguments: dict[str, Any], tmp_path: Path) -> Any:
    executor = make_executor(tool)
    call = ToolCall(
        id="c1",
        name=tool.spec.name,
        arguments_json=json.dumps(arguments),
    )
    return asyncio.run(executor.execute(call, make_context(tmp_path)))


def test_read_file_returns_numbered_lines(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("line1\nline2\nline3\n", encoding="utf-8")

    result = run_tool(
        ReadFileTool(),
        {"path": "app.py", "offset": 1, "limit": 2},
        tmp_path,
    )

    assert result.status is ToolStatus.SUCCESS
    assert "1: line1" in result.content
    assert "2: line2" in result.content
    assert "line3" not in result.content


def test_read_file_rejects_workspace_escape(tmp_path: Path) -> None:
    result = run_tool(ReadFileTool(), {"path": "../etc/passwd"}, tmp_path)

    assert result.status is ToolStatus.ERROR


def test_list_files_skips_ignored_dirs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x", encoding="utf-8")
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")

    result = run_tool(ListFilesTool(), {"path": "."}, tmp_path)

    assert result.status is ToolStatus.SUCCESS
    assert "README.md" in result.content
    assert "src/" in result.content
    assert ".git" not in result.content


def test_search_text_finds_matches(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def hello():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def world():\n    pass\n", encoding="utf-8")

    result = run_tool(SearchTextTool(), {"pattern": "def hello", "glob": "*.py"}, tmp_path)

    assert result.status is ToolStatus.SUCCESS
    assert "a.py:1:" in result.content
    assert "b.py" not in result.content


def test_search_text_rejects_invalid_regex(tmp_path: Path) -> None:
    result = run_tool(SearchTextTool(), {"pattern": "[invalid"}, tmp_path)

    assert result.status is ToolStatus.ERROR


def test_replace_in_file_requires_unique_match(tmp_path: Path) -> None:
    target = tmp_path / "code.txt"
    target.write_text("alpha\nbeta\nalpha\n", encoding="utf-8")

    ambiguous = run_tool(
        ReplaceInFileTool(),
        {"path": "code.txt", "old_text": "alpha", "new_text": "gamma"},
        tmp_path,
    )
    unique = run_tool(
        ReplaceInFileTool(),
        {"path": "code.txt", "old_text": "beta", "new_text": "delta"},
        tmp_path,
    )

    assert ambiguous.status is ToolStatus.ERROR
    assert unique.status is ToolStatus.SUCCESS
    assert target.read_text(encoding="utf-8") == "alpha\ndelta\nalpha\n"


def test_write_file_creates_nested_file(tmp_path: Path) -> None:
    result = run_tool(
        WriteFileTool(),
        {"path": "src/new/module.py", "content": "value = 1\n"},
        tmp_path,
    )

    assert result.status is ToolStatus.SUCCESS
    assert (tmp_path / "src" / "new" / "module.py").read_text(encoding="utf-8") == "value = 1\n"


def test_run_command_executes_argv(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("hi", encoding="utf-8")

    result = run_tool(
        RunCommandTool(),
        {"command": ["ls", "sample.txt"]},
        tmp_path,
    )

    assert result.status is ToolStatus.SUCCESS
    assert "sample.txt" in result.content
    assert "exit code: 0" in result.content


def test_run_command_reports_failure(tmp_path: Path) -> None:
    result = run_tool(
        RunCommandTool(),
        {"command": ["ls", "does-not-exist.txt"]},
        tmp_path,
    )

    assert result.status is ToolStatus.SUCCESS
    assert "exit code: 1" in result.content


def test_git_tools_report_status_and_diff(tmp_path: Path) -> None:
    import subprocess

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (tmp_path / "tracked.txt").write_text("one\n", encoding="utf-8")
    git("add", "tracked.txt")
    git("commit", "-m", "init")
    (tmp_path / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")

    status = run_tool(GitStatusTool(), {}, tmp_path)
    diff = run_tool(GitDiffTool(), {}, tmp_path)

    assert status.status is ToolStatus.SUCCESS
    assert "tracked.txt" in status.content
    assert diff.status is ToolStatus.SUCCESS
    assert "+two" in diff.content


def test_all_builtin_tools_register() -> None:
    tools = default_coding_tools()
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool, origin=ToolOrigin.BUILTIN)

    names = registry.names()
    assert "read_file" in names
    assert "write_file" in names
    assert "replace_in_file" in names
    assert "run_command" in names
    assert "git_status" in names
    assert "git_diff" in names
    assert "search_text" in names
    assert "list_files" in names


def test_sensitive_files_are_marked(tmp_path: Path) -> None:
    from code_agent.coding_tools.workspace import is_sensitive

    assert is_sensitive(Path(".env"))
    assert is_sensitive(Path("config/id_rsa"))
    assert not is_sensitive(Path(".env.example"))
    assert not is_sensitive(Path("main.py"))
