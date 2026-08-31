"""Textual message types carrying runtime data into the UI."""

import asyncio

from textual.message import Message

from code_agent_core import ApprovalRequest
from code_agent_core.runtime.events import RuntimeEvent


class AgentEvent(Message):
    """One runtime event forwarded from the agent loop."""

    def __init__(self, event: RuntimeEvent) -> None:
        super().__init__()
        self.event = event


class ApprovalAsked(Message):
    """An approval request pending a y/a/n answer from the user."""

    def __init__(self, request: ApprovalRequest, future: "asyncio.Future[str]") -> None:
        super().__init__()
        self.request = request
        self.future = future


class TaskFinished(Message):
    """The background agent loop returned."""

    def __init__(self, summary: str) -> None:
        super().__init__()
        self.summary = summary
