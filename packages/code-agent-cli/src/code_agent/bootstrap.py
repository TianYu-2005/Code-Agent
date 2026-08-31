"""Composition root wiring all agent components together."""

from pathlib import Path

from code_agent_core import (
    ApprovalPort,
    DefaultPermissionPolicy,
    RunBudgets,
    RunSpec,
    ToolExecutor,
    ToolOrigin,
    ToolRegistry,
)
from code_agent_core.context import (
    Compactor,
    ContextManager,
    ContextPolicy,
    load_project_instructions,
)
from code_agent_core.runtime.loop import AgentLoop as Loop
from code_agent_core.runtime.spec import EventSink
from code_agent_core.session import SessionStore
from code_agent_llm import (
    ModelProvider,
    OpenAICompatibleProvider,
    RetryingProvider,
    RetryPolicy,
)

from .cli.approval import TerminalApprovalPort
from .cli.interrupt import CancelState
from .cli.renderer import RecordingSink, TerminalRenderer
from .cli.sessions import SessionManager
from .coding_tools import default_coding_tools
from .config import AppConfig


class AgentRuntime:
    """Fully wired agent ready to serve CLI interactions."""

    def __init__(
        self,
        config: AppConfig,
        session: SessionStore | None = None,
        *,
        provider: ModelProvider | None = None,
        renderer: TerminalRenderer | None = None,
        approval_port: ApprovalPort | None = None,
        event_sink: EventSink | None = None,
        context_policy: ContextPolicy | None = None,
    ) -> None:
        self.config = config
        self.workspace = Path(config.workspace).resolve()
        self.session_manager = SessionManager(self.workspace)
        self.session = session if session is not None else self.session_manager.create()
        self.renderer = renderer or TerminalRenderer()
        self.cancel_state = CancelState()
        self._event_sink = event_sink
        self._context_policy = context_policy

        if provider is None:
            provider = RetryingProvider(
                OpenAICompatibleProvider(config.provider_config),
                RetryPolicy(),
            )
        self.provider = provider
        self.compactor = Compactor(provider)

        self.registry = ToolRegistry()
        for tool in default_coding_tools():
            self.registry.register(tool, origin=ToolOrigin.BUILTIN)

        self.executor = ToolExecutor(
            self.registry,
            DefaultPermissionPolicy(),
            approval_port or TerminalApprovalPort(),
        )

        self._rebind_session()

    def new_session(self) -> None:
        """Start a fresh persisted session, dropping the old one if empty."""
        self.session_manager.remove_if_empty(self.session)
        self.session = self.session_manager.create()
        self._rebind_session()

    def load_session(self, session_id: str) -> None:
        """Resume a persisted session by identifier."""
        self.session_manager.remove_if_empty(self.session)
        self.session = self.session_manager.load(session_id)
        self._rebind_session()

    def _rebind_session(self) -> None:
        """Rebuild context manager and run spec for the active session."""
        instructions = load_project_instructions(self.workspace)
        if self._context_policy is not None:
            policy = (
                ContextPolicy(
                    system_prompt=self._context_policy.system_prompt,
                    project_instructions=instructions,
                    token_budget=self._context_policy.token_budget,
                )
                if instructions
                else self._context_policy
            )
        else:
            policy = (
                ContextPolicy(project_instructions=instructions)
                if instructions
                else ContextPolicy()
            )
        self.context_manager = ContextManager(self.session, policy=policy)

        session_id = getattr(self.session, "session_id", "default")
        self.run_spec = RunSpec(
            session_id=session_id if isinstance(session_id, str) else "default",
            model=self.config.model,
            tool_set=frozenset(self.registry.names()),
            budgets=RunBudgets(max_turns=self.config.max_turns),
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
            event_sink=self._event_sink or RecordingSink(self.renderer),
            compactor=self.compactor,
        )
