"""Composition root wiring all agent components together."""

from pathlib import Path

from code_agent_core import (
    DefaultPermissionPolicy,
    RunBudgets,
    RunSpec,
    ToolExecutor,
    ToolOrigin,
    ToolRegistry,
)
from code_agent_core.context import (
    ContextManager,
    ContextPolicy,
    load_project_instructions,
)
from code_agent_core.runtime.loop import AgentLoop as Loop
from code_agent_core.session import SessionStore
from code_agent_llm import OpenAICompatibleProvider, RetryingProvider, RetryPolicy

from .cli.approval import TerminalApprovalPort
from .cli.interrupt import CancelState
from .cli.renderer import TerminalRenderer
from .coding_tools import default_coding_tools
from .config import AppConfig


class AgentRuntime:
    """Fully wired agent ready to serve CLI interactions."""

    def __init__(self, config: AppConfig, session: SessionStore | None = None) -> None:
        self.config = config
        self.workspace = Path(config.workspace).resolve()
        self.session = session or SessionStore()
        self.renderer = TerminalRenderer()
        self.cancel_state = CancelState()

        provider = OpenAICompatibleProvider(config.provider_config)
        self.provider = RetryingProvider(provider, RetryPolicy())

        self.registry = ToolRegistry()
        for tool in default_coding_tools():
            self.registry.register(tool, origin=ToolOrigin.BUILTIN)

        self.executor = ToolExecutor(
            self.registry,
            DefaultPermissionPolicy(),
            TerminalApprovalPort(),
        )

        instructions = load_project_instructions(self.workspace)
        policy = (
            ContextPolicy(project_instructions=instructions) if instructions else ContextPolicy()
        )
        self.context_manager = ContextManager(self.session, policy=policy)

        self.run_spec = RunSpec(
            session_id="default",
            model=config.model,
            tool_set=frozenset(self.registry.names()),
            budgets=RunBudgets(max_turns=config.max_turns),
        )

    def make_loop(self) -> Loop:
        """Create a loop bound to the current session."""
        return Loop(
            provider=self.provider,
            session=self.session,
            context_manager=self.context_manager,
            tool_registry=self.registry,
            tool_executor=self.executor,
            run_spec=self.run_spec,
            cancellation=self.cancel_state,
        )
