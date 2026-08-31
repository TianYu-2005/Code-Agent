"""Authorization contracts and conservative default tool policy."""

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import Field

from code_agent_llm import ProtocolModel

from ..runtime.spec import ExecutionContext
from .base import ToolEffect, ToolOrigin, ToolSpec, ValidatedToolCall


class PermissionAction(StrEnum):
    """Decision returned before a tool may execute."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class ApprovalScope(StrEnum):
    """Lifetime of an interactive approval."""

    ONCE = "once"
    SESSION = "session"


class PermissionDecision(ProtocolModel):
    """Auditable policy decision for one validated invocation."""

    action: PermissionAction
    reason: str = Field(min_length=1, max_length=1_024)


class ApprovalRequest(ProtocolModel):
    """Immutable prompt passed to a trusted interaction adapter."""

    call: ValidatedToolCall
    description: str = Field(min_length=1, max_length=4_096)


class ApprovalResponse(ProtocolModel):
    """Response bound to the exact approved invocation fingerprint."""

    fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    approved: bool
    scope: ApprovalScope = ApprovalScope.ONCE


@runtime_checkable
class PermissionPolicy(Protocol):
    """Evaluate a validated invocation without user interaction."""

    def decide(
        self,
        call: ValidatedToolCall,
        spec: ToolSpec,
        context: ExecutionContext,
    ) -> PermissionDecision:
        """Return allow, ask, or deny."""
        ...


@runtime_checkable
class ApprovalPort(Protocol):
    """Trusted interface used to ask the user for permission."""

    async def request(
        self,
        request: ApprovalRequest,
        context: ExecutionContext,
    ) -> ApprovalResponse:
        """Ask whether one invocation may proceed."""
        ...


class DefaultPermissionPolicy:
    """Allow safe built-in reads, ask for effects, and fail closed."""

    def decide(
        self,
        call: ValidatedToolCall,
        spec: ToolSpec,
        context: ExecutionContext,
    ) -> PermissionDecision:
        if ToolEffect.UNKNOWN in call.effective_effects or not call.effective_effects:
            return PermissionDecision(
                action=PermissionAction.DENY,
                reason="tool effects are unknown",
            )
        if any(target.sensitive or target.external for target in call.targets):
            return PermissionDecision(
                action=PermissionAction.DENY,
                reason="tool targets a protected resource",
            )
        if call.effective_effects <= {ToolEffect.READ} and spec.origin is ToolOrigin.BUILTIN:
            return PermissionDecision(
                action=PermissionAction.ALLOW,
                reason="trusted read-only invocation",
            )
        return PermissionDecision(
            action=PermissionAction.ASK,
            reason="invocation requires user approval",
        )


class DenyApprovalPort:
    """Non-interactive approval adapter that rejects every request."""

    async def request(
        self,
        request: ApprovalRequest,
        context: ExecutionContext,
    ) -> ApprovalResponse:
        return ApprovalResponse(
            fingerprint=request.call.fingerprint,
            approved=False,
        )
