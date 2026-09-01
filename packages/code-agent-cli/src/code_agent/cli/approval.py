"""Terminal-based approval interaction."""

import sys
from typing import TextIO

from code_agent_core import ApprovalPort, ApprovalRequest, ApprovalResponse
from code_agent_core.runtime.spec import ExecutionContext

from ..config import ApprovalMode


class TerminalApprovalPort:
    """Ask the user for permission on the terminal."""

    def __init__(
        self, *, input_stream: TextIO | None = None, output_stream: TextIO | None = None
    ) -> None:
        self._input = input_stream or sys.stdin
        self._output = output_stream or sys.stdout

    async def request(
        self,
        request: ApprovalRequest,
        context: ExecutionContext,
    ) -> ApprovalResponse:
        targets = request.call.targets
        description = request.description
        lines = [f"  工具: {request.call.name}"]
        lines.append(f"  说明: {description}")
        for target in targets:
            lines.append(f"  目标: {target.resource} ({target.effect.value})")
        self._output.write("\n" + "\n".join(lines) + "\n")
        while True:
            self._output.write("允许执行？[y]允许 [a]本会话允许 [n]拒绝: ")
            self._output.flush()
            answer = self._input.readline().strip().lower()
            if answer in {"y", "yes"}:
                return ApprovalResponse(
                    fingerprint=request.call.fingerprint,
                    approved=True,
                )
            if answer in {"n", "no", ""}:
                return ApprovalResponse(
                    fingerprint=request.call.fingerprint,
                    approved=False,
                )
            self._output.write("请输入 y / a / n。\n")


class ModeApprovalPort:
    """Wrap an ApprovalPort with a runtime-switchable approval mode.

    In ``auto`` mode every ask decision is approved without interrupting the
    user; policy denials still apply because they never reach this port.
    """

    def __init__(self, inner: ApprovalPort, *, mode: ApprovalMode = ApprovalMode.ASK) -> None:
        self._inner = inner
        self._mode = mode

    @property
    def mode(self) -> ApprovalMode:
        """Current approval mode."""
        return self._mode

    def set_mode(self, mode: ApprovalMode) -> None:
        """Switch between interactive confirmation and auto-accept."""
        self._mode = mode

    async def request(
        self,
        request: ApprovalRequest,
        context: ExecutionContext,
    ) -> ApprovalResponse:
        if self._mode is ApprovalMode.AUTO:
            return ApprovalResponse(
                fingerprint=request.call.fingerprint,
                approved=True,
            )
        return await self._inner.request(request, context)
