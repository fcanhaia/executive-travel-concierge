"""Zero-Trust governance plugin and Human-in-the-Loop booking callbacks.

Implements Model-Armor-inspired prompt screening, post-generation PII output
guardrails, global Intent/Outcome structured auditing, and an explicit pre-tool
Human-in-the-Loop confirmation gate for high-stakes reservation actions.
"""

from __future__ import annotations

from typing import Any, Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from concierge_agent.observability import (
    get_structured_logger,
    record_tool_intent,
    record_tool_outcome,
    scrub_pii,
)

logger = get_structured_logger("concierge_agent.guardrails")

_BLOCKED_PROMPT_PATTERNS: tuple[str, ...] = (
    "ignore previous instructions",
    "bypass human approval",
    "override corporate cap",
    "reveal secret",
    "print api key",
)

GATED_HIGH_STAKES_TOOLS: frozenset[str] = frozenset(
    {"confirm_executive_itinerary_booking"}
)


def enforce_booking_human_approval(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: Optional[ToolContext] = None,
    *,
    max_corporate_trip_usd: float = 10000.0,
) -> Optional[dict[str, Any]]:
    """Explicit Human-in-the-Loop gate executed prior to high-stakes tools.

    Short-circuits execution when `confirm_executive_itinerary_booking` is called
    without an explicit `APPROVED-` confirmation token or exceeds corporate policy.
    """
    tool_name = getattr(tool, "name", str(tool))
    if tool_name not in GATED_HIGH_STAKES_TOOLS:
        return None

    approval_token = str(args.get("human_approval_token", "")).strip()
    total_usd = float(args.get("total_estimated_usd", 0.0) or 0.0)

    if total_usd > max_corporate_trip_usd:
        blocked = {
            "status": "POLICY_BLOCKED",
            "hitl_gate": "CORPORATE_SPEND_CAP_EXCEEDED",
            "message": (
                f"Itinerary total (${total_usd:.2f} USD) exceeds the automated "
                f"corporate ceiling (${max_corporate_trip_usd:.2f} USD)."
            ),
            "recovery_instructions": (
                "Select economy fares or lower-rate transit hotels so the total "
                f"remains under ${max_corporate_trip_usd:.2f} USD before requesting approval."
            ),
        }
        record_tool_outcome(tool_name, "blocked_by_policy", blocked)
        return blocked

    if not approval_token.startswith("APPROVED-"):
        interruption = {
            "status": "PENDING_HUMAN_APPROVAL",
            "hitl_gate": "BLOCKED_BEFORE_EXECUTION",
            "tool": tool_name,
            "message": (
                "Human-in-the-Loop confirmation required before committing funds."
            ),
            "recovery_instructions": (
                "Present the itemized flight numbers, hotel IDs, and total USD "
                "amount to the traveler and wait for an explicit 'APPROVED-<id>' confirmation."
            ),
        }
        logger.warning(
            "Paused high-stakes booking tool awaiting explicit human confirmation",
            extra={"event": "hitl_gate_triggered", "tool_name": tool_name},
        )
        record_tool_outcome(tool_name, "paused_for_hitl", interruption)
        return interruption

    return None


class TravelGovernancePlugin(BasePlugin):
    """Application-wide security, PII self-audit, and HITL policy plugin."""

    def __init__(
        self,
        name: str = "travel_governance_plugin",
        max_corporate_trip_usd: float = 10000.0,
    ) -> None:
        super().__init__(name=name)
        self.max_corporate_trip_usd = max_corporate_trip_usd

    async def before_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> Optional[LlmResponse]:
        """Screens inbound model requests against prompt-injection patterns."""
        combined_text = []
        for content in llm_request.contents or []:
            for part in content.parts or []:
                if getattr(part, "text", None):
                    combined_text.append(part.text.lower())

        joined = " ".join(combined_text)
        for phrase in _BLOCKED_PROMPT_PATTERNS:
            if phrase in joined:
                logger.warning(
                    "Blocked unsafe prompt pattern in before_model_callback",
                    extra={"event": "guardrail_prompt_blocked", "pattern": phrase},
                )
                return LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part.from_text(
                                text=(
                                    "Request declined by TravelGovernancePlugin: "
                                    "cannot bypass corporate approval or security policies."
                                )
                            )
                        ],
                    )
                )
        return None

    async def after_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> Optional[LlmResponse]:
        """Scrubs accidental PII leakage from generated model responses."""
        if not llm_response.content or not llm_response.content.parts:
            return None

        modified = False
        sanitized_parts: list[types.Part] = []
        for part in llm_response.content.parts:
            raw_text = getattr(part, "text", None)
            if raw_text:
                clean_text = scrub_pii(raw_text)
                if clean_text != raw_text:
                    modified = True
                sanitized_parts.append(types.Part.from_text(text=clean_text))
            else:
                sanitized_parts.append(part)

        if modified:
            logger.info(
                "Redacted PII from model output in after_model_callback",
                extra={"event": "guardrail_output_redacted"},
            )
            return LlmResponse(
                content=types.Content(
                    role=llm_response.content.role or "model",
                    parts=sanitized_parts,
                )
            )
        return None

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Optional[dict[str, Any]]:
        """Records tool intent BEFORE execution and enforces HITL policy."""
        agent_name = getattr(tool_context, "agent_name", "concierge")
        record_tool_intent(tool.name, tool_args, agent_name=agent_name)
        return enforce_booking_human_approval(
            tool,
            tool_args,
            tool_context,
            max_corporate_trip_usd=self.max_corporate_trip_usd,
        )

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Records sanitized tool outcome AFTER execution."""
        agent_name = getattr(tool_context, "agent_name", "concierge")
        status = (
            str(result.get("status", "completed"))
            if isinstance(result, dict)
            else "completed"
        )
        record_tool_outcome(tool.name, status, result, agent_name=agent_name)
        return None