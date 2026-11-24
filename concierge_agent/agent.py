"""Multi-agent orchestration and strategic Gemini model routing for ADK.

Composes a Coordinator LlmAgent (gemini-2.5-pro) delegating concurrent domain
research to a ParallelAgent specialist squad (gemini-2.5-flash) followed by a
SequentialAgent budget synthesis pipeline and governed App container.
"""

from __future__ import annotations

import warnings

from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent
from google.adk.apps.app import App, ResumabilityConfig

from concierge_agent.config import RuntimeSettings, load_runtime_settings
from concierge_agent.guardrails import (
    TravelGovernancePlugin,
    enforce_booking_human_approval,
)
from concierge_agent.memory import (
    build_compaction_config,
    build_context_filter_plugin,
)
from concierge_agent.observability import configure_observability
from concierge_agent.prompts import (
    BUDGET_SYNTHESIZER_INSTRUCTION,
    COORDINATOR_CONSTITUTION,
    FLIGHT_SPECIALIST_INSTRUCTION,
    HOTEL_SPECIALIST_INSTRUCTION,
    WEATHER_FX_SPECIALIST_INSTRUCTION,
)
from concierge_agent.tools import (
    confirm_executive_itinerary_booking,
    evaluate_destination_weather_and_fx,
    search_curated_hotel_stays,
    search_verified_flight_options,
)


def build_multi_agent_hierarchy(
    settings: RuntimeSettings | None = None,
) -> LlmAgent:
    """Constructs the Coordinator + ParallelAgent + SequentialAgent hierarchy."""
    cfg = settings or load_runtime_settings()
    configure_observability(service_name=cfg.otel_service_name)

    flight_specialist = LlmAgent(
        name="flight_research_specialist",
        model=cfg.fast_worker_model,
        description="Searches verified corporate flight schedules and fares.",
        instruction=FLIGHT_SPECIALIST_INSTRUCTION,
        tools=[search_verified_flight_options],
        output_key="flight_options_report",
    )

    hotel_specialist = LlmAgent(
        name="hospitality_specialist",
        model=cfg.fast_worker_model,
        description="Finds curated corporate hotels near major transit hubs.",
        instruction=HOTEL_SPECIALIST_INSTRUCTION,
        tools=[search_curated_hotel_stays],
        output_key="hotel_options_report",
    )

    climate_fx_specialist = LlmAgent(
        name="climate_and_fx_specialist",
        model=cfg.fast_worker_model,
        description="Evaluates destination weather risk and local FX rates.",
        instruction=WEATHER_FX_SPECIALIST_INSTRUCTION,
        tools=[evaluate_destination_weather_and_fx],
        output_key="climate_fx_report",
    )

    itinerary_synthesizer = LlmAgent(
        name="itinerary_budget_synthesizer",
        model=cfg.reasoning_coordinator_model,
        description="Synthesizes parallel research into an itemized itinerary within budget.",
        instruction=BUDGET_SYNTHESIZER_INSTRUCTION,
        output_key="synthesized_itinerary_plan",
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        parallel_research_squad = ParallelAgent(
            name="parallel_research_squad",
            description="Executes flight, hotel, and weather/FX research concurrently.",
            sub_agents=[
                flight_specialist,
                hotel_specialist,
                climate_fx_specialist,
            ],
        )
        travel_planning_pipeline = SequentialAgent(
            name="travel_planning_pipeline",
            description="Runs parallel research followed by executive budget synthesis.",
            sub_agents=[parallel_research_squad, itinerary_synthesizer],
        )

    return LlmAgent(
        name="executive_travel_coordinator",
        model=cfg.reasoning_coordinator_model,
        description="Principal concierge coordinating multi-city planning and booking approval.",
        instruction=COORDINATOR_CONSTITUTION,
        sub_agents=[travel_planning_pipeline],
        tools=[confirm_executive_itinerary_booking],
        before_tool_callback=enforce_booking_human_approval,
    )


def create_concierge_app(settings: RuntimeSettings | None = None) -> App:
    """Creates the governed ADK App with compaction, context filter, and safety plugins."""
    cfg = settings or load_runtime_settings()
    coordinator = build_multi_agent_hierarchy(cfg)
    return App(
        name="concierge_agent",
        root_agent=coordinator,
        plugins=[
            TravelGovernancePlugin(),
            build_context_filter_plugin(num_invocations_to_keep=8, remove_amount=2),
        ],
        events_compaction_config=build_compaction_config(),
        resumability_config=ResumabilityConfig(is_resumable=True),
    )


app = create_concierge_app()
root_agent = app.root_agent