"""Terminal-based approval interaction."""

import sys
from typing import TextIO

from code_agent_core import ApprovalRequest, ApprovalResponse
from code_agent_core.runtime.spec import ExecutionContext


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
