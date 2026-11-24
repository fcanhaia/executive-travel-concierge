# Architecture Specification: Smart Executive Travel & Itinerary Concierge

## 1. Problem Statement & Objective
Executive travelers lose hours reconciling flight schedules, hotel corporate caps, destination weather disruptions, currency conversion, and personal loyalty preferences across fragmented tools. The Smart Executive Travel & Itinerary Concierge orchestrates specialized Google ADK agents under Zero-Trust governance patterns to research options concurrently, optimize multi-city budgets, persist traveler preferences asynchronously, and enforce explicit human approval before confirming any reservation.

## 2. Governed Operational Policy (YAML)
    concierge_platform:
      orchestration:
        coordinator_agent:
          model: "gemini-2.5-pro"
          responsibility: "Intent decomposition, conflict resolution, and executive itinerary synthesis"
        specialist_squad:
          execution_mode: "ParallelAgent"
          workers:
            flight_research_specialist:
              model: "gemini-2.5-flash"
              tool: "search_verified_flight_options"
            hospitality_specialist:
              model: "gemini-2.5-flash"
              tool: "search_curated_hotel_stays"
            climate_and_fx_specialist:
              model: "gemini-2.5-flash"
              tool: "evaluate_destination_weather_and_fx"
      governance_and_safety:
        secret_management:
          provider: "google_cloud_secret_manager"
          allow_hardcoded_credentials: false
        pii_protection:
          redact_fields:
            - "email"
            - "passport_number"
            - "payment_card"
            - "phone_number"
        human_in_the_loop:
          gated_tools:
            - "confirm_executive_itinerary_booking"
          require_explicit_confirmation_token: true
      context_and_memory:
        compaction:
          interval_turns: 5
          overlap_events: 1
        persistence:
          session_backend: "DatabaseSessionService"
          preference_consolidation: "async_background_task"

## 3. Acceptance Scenarios (BDD Gherkin)
    Feature: Governed Multi-City Executive Travel Planning

      Scenario: Concurrent multi-city research within corporate budget
        Given an executive traveler requests a 4-day trip to London and Zurich under a $4,200 USD budget
        And saved traveler preferences specify aisle seats and boutique hotels near transit hubs
        When the Coordinator delegates research to the ParallelAgent specialist squad
        Then flight, hotel, weather, and FX tools return schema-validated options
        And the synthesized itinerary respects the $4,200 USD ceiling with itemized cost breakdown

      Scenario: Mandatory Human-in-the-Loop gate prior to booking execution
        Given a proposed itinerary has been synthesized for the traveler
        When the agent attempts to invoke "confirm_executive_itinerary_booking" without explicit user approval
        Then the before_tool_callback policy hook blocks execution and returns a pending confirmation prompt
        And both tool intent and blocked outcome are recorded as PII-redacted structured JSON logs