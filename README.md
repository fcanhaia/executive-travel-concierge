# Smart Executive Travel & Itinerary Concierge

A governed, production-grade multi-agent travel planning system built with **Google Agent Development Kit (ADK) for Python** on the **Concierge Track**.

## 1. Problem Statement & Solution
Executive travelers lose hours reconciling multi-city flight schedules, corporate hotel rate caps, destination weather advisories, foreign exchange conversions, and personal loyalty preferences across fragmented tools.

**Smart Executive Travel & Itinerary Concierge** solves this through a Zero-Trust, layered multi-agent architecture inspired by enterprise Google Cloud agent platform blueprints:
1. **Concurrent Multi-Agent Research**: A Coordinator (`gemini-2.5-pro`) delegates simultaneous flight, hospitality, and climate/FX lookups to a `ParallelAgent` squad powered by low-latency `gemini-2.5-flash` workers, followed by a `SequentialAgent` budget synthesizer (`gemini-2.5-pro`).
2. **Contract-Validated Tools & Guided Recovery**: Every domain tool enforces strict Pydantic input/output JSON schemas and returns actionable `recovery_instructions` instead of unhandled exceptions.
3. **Persistent Sessions, Compaction & Async Memory**: Combines ADK `DatabaseSessionService` (SQLite locally, Cloud SQL/AlloyDB in production), `EventsCompactionConfig` + `ContextFilterPlugin`, and non-blocking `asyncio.create_task` preference consolidation.
4. **Zero-Trust Guardrails & Human-in-the-Loop Gate**: `TravelGovernancePlugin(BasePlugin)` screens prompt-injection attempts, scrubs PII (emails, passports, cards, phone numbers), records pre-execution `BEFORE_EXECUTION` intent and post-execution `AFTER_EXECUTION` outcomes as OpenTelemetry-correlated JSON logs, and pauses `confirm_executive_itinerary_booking` via `before_tool_callback` until explicit human `APPROVED-<id>` confirmation is provided.

## 2. Multi-Agent Architecture

    [User Request]
          |
          v
    +------------------------------------------------------------------+
    | App Container (EventsCompactionConfig + TravelGovernancePlugin)   |
    |  - PII Redaction Filter + OpenTelemetry Span Correlation         |
    |  - SecretResolver (Google Cloud Secret Manager + SecretStr)      |
    +------------------------------------------------------------------+
          |
          v
    +------------------------------------------------------------------+
    | executive_travel_coordinator (LlmAgent | gemini-2.5-pro)         |
    |  - System Constitution + HITL before_tool_callback Gate          |
    |  - Gated Tool: confirm_executive_itinerary_booking               |
    +-----------------------------+------------------------------------+
                                  |
                                  v
            +---------------------------------------------+
            | travel_planning_pipeline (SequentialAgent)  |
            +---------------------+-----------------------+
                                  |
             +--------------------+--------------------+
             |                                         |
             v                                         v
    +-----------------------------------+   +----------------------------------+
    | parallel_research_squad           |   | itinerary_budget_synthesizer     |
    | (ParallelAgent | gemini-2.5-flash)|   | (LlmAgent | gemini-2.5-pro)      |
    | 1. flight_research_specialist     |   | Reconciles fares, hotel totals,  |
    | 2. hospitality_specialist         |   | weather risk, FX, and async      |
    | 3. climate_and_fx_specialist      |   | preferences under USD budget cap |
    +-----------------------------------+   +----------------------------------+

## 3. Repository Structure

    executive-travel-concierge/
    ├── concierge_agent/
    │   ├── agent.py                  # Coordinator (Pro), Parallel (Flash), Sequential & App
    │   ├── prompts.py                # System Constitution & specialist instructions
    │   ├── config/secrets.py         # Secret Manager client, TTL cache & SecretStr settings
    │   ├── guardrails/policy_plugin.py # BasePlugin guardrails & HITL before_tool_callback
    │   ├── memory/session_store.py   # Compaction, DatabaseSessionService & async memory
    │   ├── observability/telemetry.py # Structured JSON logs, Intent/Outcome, OTel & PII filter
    │   └── tools/travel_tools.py     # Strict Pydantic JSON schemas & guided error recovery
    ├── specs/concierge_spec.md       # Spec-Driven Development YAML policy & Gherkin scenarios
    ├── tests/
    │   ├── golden_travel_eval.test.json    # Official ADK EvalSet golden dataset
    │   └── test_concierge_eval_suite.py    # Automated regression & governance test suite
    ├── terraform/                    # Cloud Run, Secret Manager & least-privilege IAM IaC
    └── .github/workflows/ci_eval.yml # Automated CI evaluation gate on push & PR

## 4. Local Quickstart & Automated Evaluation

Install dependencies and run the automated evaluation suite:

    python3 -m pip install --upgrade pip
    pip install -e ".[dev]"
    pytest -v

Run interactive ADK web playground or CLI trajectory evaluation:

    adk web concierge_agent
    adk eval concierge_agent tests/golden_travel_eval.test.json

## 5. Cloud Deployment via Terraform & Google Agents CLI

Scaffold, evaluate, or deploy using `google-agents-cli` / **Google Cloud Agent Starter Pack**:

    uvx google-agents-cli eval --agent concierge_agent --eval-set tests/golden_travel_eval.test.json
    uvx agent-starter-pack enhance --cicd-runner github_actions

Provision cloud infrastructure declaratively with **Terraform** (`terraform/`):

    cd terraform
    terraform init
    terraform plan -var="project_id=YOUR_GCP_PROJECT_ID"
    terraform apply -var="project_id=YOUR_GCP_PROJECT_ID"

## 6. License
Licensed under the Apache License, Version 2.0.