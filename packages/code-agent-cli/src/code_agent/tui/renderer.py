"""Bridge runtime events and approval requests into Textual messages."""

import asyncio

from textual.app import App

from code_agent_core import ApprovalRequest, ApprovalResponse, ApprovalScope
from code_agent_core.runtime.events import RuntimeEvent
from code_agent_core.runtime.spec import ExecutionContext

from .messages import AgentEvent, ApprovalAsked


class TuiRenderer:
    """EventSink that forwards RuntimeEvents to a Textual app."""

    def __init__(self, app: "App[None]") -> None:
        self._app = app

    async def emit(self, event: RuntimeEvent) -> None:
        self._app.post_message(AgentEvent(event))


class TuiApprovalPort:
    """ApprovalPort that renders requests inside the TUI and waits for a key."""

    def __init__(self, app: "App[None]") -> None:
        self._app = app

    async def request(
        self,
        request: ApprovalRequest,
        context: ExecutionContext,
    ) -> ApprovalResponse:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._app.post_message(ApprovalAsked(request=request, future=future))
        answer = await future
        scope = ApprovalScope.SESSION if answer == "a" else ApprovalScope.ONCE
        return ApprovalResponse(
            fingerprint=request.call.fingerprint,
            approved=answer in {"y", "a"},
            scope=scope,
        )
