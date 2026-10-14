# ISPilot architecture

This document describes the system as implemented in this repository. Where something exists in the code but is not part of the live request path, it is marked as such.

## Full flow

```mermaid
flowchart TB
  CUST(["Customer"])

  subgraph CHANNEL["Channel: Streamlit UI (ui/app.py)"]
    UI["Text chat, microphone recorder, audio player"]
  end

  subgraph VOICE["Voice pipeline (local, agent/voice.py)"]
    STT["POST /transcribe<br/>faster-whisper speech-to-text"]
    TTS["POST /synthesize<br/>Piper text-to-speech, WAV output"]
  end

  subgraph GATEWAY["FastAPI gateway (agent/main.py)"]
    GW["POST /ask<br/>per-session state store"]
    ID["Phase 1: identify customer<br/>CIN or phone number via find_customer"]
    RUN["Phase 2: run the LangGraph with<br/>persisted graph state + last 6 messages"]
  end

  CUST --> UI
  UI -->|"recorded audio"| STT
  STT -->|"transcript"| UI
  UI -->|"text message"| GW
  GW --> ID
  ID --> RUN
  GW -->|"reply text"| UI
  UI -->|"reply text"| TTS
  TTS -->|"WAV"| UI

  subgraph GRAPH["LangGraph orchestration (agent/agent_graph.py): code decides, LLM only phrases"]
    R{"route_entry<br/>deterministic rules<br/>keyword + state flags"}

    CA["check_account"]
    CO["check_outage"]
    SUS["suspended"]
    OA["outage_active"]
    GC["greet_customer<br/>static template, no LLM"]
    CD["collect_diagnostics<br/>runs the diagnostic tools"]
    SS["select_scenario<br/>rules first, LLM only for ambiguous cases"]
    LG["load_guide<br/>metadata-filtered ChromaDB lookup<br/>of the scenario procedure (no semantic search)"]
    EI["execute_intervention<br/>scenario to fixed action sequence"]
    RAI{"route_after_intervention"}
    CT["create_ticket"]
    GR["generate_response"]

    RES["resolved"]
    CTP["create_ticket_persists"]
    FRC["factory_reset_confirmation<br/>ask, then execute only after yes"]
    PUC["plan_upgrade_confirmation<br/>ask, then execute only after yes"]
    EK["extended_knowledge<br/>business and protocol questions"]
    AH["ask_how_to_help"]
    SF["suspended_followup"]
    ENDN(["End of turn: reply returned to gateway"])

    R -->|"customer says it works"| RES
    R -->|"awaiting reset confirmation"| FRC
    R -->|"awaiting upgrade confirmation"| PUC
    R -->|"outage already notified"| CO
    R -->|"identification message or new problem"| CA
    R -->|"still broken after a fix"| CTP
    R -->|"diagnostics done, same problem"| SS
    R -->|"follow-up after a fix"| GR
    R -->|"non-problem question with topic keywords"| EK
    R -->|"non-problem message"| AH
    R -->|"suspended account follow-up"| SF

    CA -->|"suspended"| SUS
    CA -->|"active"| CO
    CO -->|"active outage"| OA
    CO -->|"identification, no outage"| GC
    CO -->|"problem, no outage"| CD
    CD --> SS --> LG --> EI --> RAI
    RAI -->|"offline_physical"| CT
    RAI -->|"all other scenarios"| GR

    RES & SUS & OA & GC & GR & CT & CTP & FRC & PUC & EK & AH & SF --> ENDN
  end

  RUN --> R
  ENDN --> GW

  subgraph TOOLS["Typed tools (LangChain @tool, 30 defined), invoked by graph nodes"]
    T1["Device tools (10)<br/>get_device, get_device_parameters,<br/>reboot_device, factory_reset_device,<br/>push_pppoe_credentials, set_mtu,<br/>force_connection_request, check_firmware_version, ..."]
    T2["Subscriber and session tools (8)<br/>get_radius_user, change_radius_password,<br/>disconnect_session, change_user_group,<br/>reset_daily_quota, ..."]
    T3["Business tools (9)<br/>find_customer, check_account_status,<br/>check_region_outage, get_session_history,<br/>get_auth_failures, get_plan_info,<br/>get_group_plan_specs, get_network_metrics,<br/>create_ticket"]
    T4["Knowledge tools (3)<br/>search_troubleshooting_guide,<br/>search_extended_knowledge_v2,<br/>search_extended_knowledge (v1, kept unrouted)"]
  end

  CD & EI & FRC & PUC & CT & CTP & CA & CO & EK --> TOOLS

  subgraph RAG["Adaptive hybrid retrieval (agent/tools/rag_tools.py)"]
    Q1["1. Semantic search on the raw query"]
    G{"2. Top relevance above gate threshold?"}
    Q2["3a. Return top results<br/>(no extra LLM call)"]
    Q3["3b. LLM reformulates the query,<br/>search again, LLM reranks vs original query"]
    Q4["Fallback if rerank fails:<br/>cosine-threshold filter"]
    Q1 --> G
    G -->|"yes"| Q2
    G -->|"no"| Q3
    Q3 -.->|"LLM error or bad output"| Q4
  end

  T4 --> Q1

  subgraph STORES["Backends (all local, all synthetic data)"]
    ACS["GenieACS (TR-069 ACS)<br/>+ simulated routers"]
    DB[("MySQL: FreeRADIUS tables,<br/>customers, outages, tickets")]
    FR["FreeRADIUS"]
    CH[("ChromaDB<br/>isp_troubleshooting<br/>isp_knowledge_extended_v2")]
  end

  T1 --> ACS
  T2 --> DB
  T3 --> DB
  FR --- DB
  ACS --- SIM["TR-069 device simulator (25 devices)"]
  Q2 & Q3 & Q4 --> CH
  LG --> CH

  LLM["Ollama: Qwen2.5 14B (local)<br/>Used only for: reply wording, query<br/>translation/reformulation, reranking,<br/>ambiguous scenario choice"]
  GR & FRC & PUC & EK & CTP & RES & SS & Q3 -.->|"language tasks only"| LLM

  subgraph GUARD["Guardrails and tracing"]
    direction TB
    GD1["Deterministic routing: every action is chosen by code"]
    GD2["Closed-set validation: LLM scenario answer must be<br/>one of 4 allowed labels, else 'general'"]
    GD3["Confirm-then-execute for factory reset and plan upgrade"]
    GD4["Replies are generated from the real tool results held<br/>in graph state, with prompt rules against invented actions,<br/>phone numbers, prices and speeds"]
    GD5["Tool errors use fixed codes ([NOT_FOUND], [DB_ERROR], ...)<br/>and every LLM/RAG step has a safe fallback"]
    TR1["Trace: every tool result is stored in graph state<br/>and gateway prints per-turn audit lines"]
    TR2["ConversationLogger (agent/logger.py): per-session JSON with tool trace<br/>(present in repo, NOT wired into the request path)"]
  end

  GRAPH -.-> GUARD
  TR1 --- TR2

  classDef notwired stroke-dasharray: 4 3
  class TR2 notwired
```

## Key design decisions

**1. The LLM never chooses actions.** The graph is a state machine. Routing (`route_entry`, `route_after_account`, `route_after_outage`, `route_after_intervention`) and scenario selection are plain Python rules over keywords and diagnostic data. Interventions (reboot, password sync, MTU change, session disconnect) are fixed sequences per scenario, executed by `execute_intervention`. The model is limited to wording the reply, translating/reformulating retrieval queries, reranking retrieved passages, and choosing between a few labels in cases the rules cannot settle. Even that last choice is validated against a closed set (`general`, `wifi_confusion`, `slow_speed`, `high_latency`); anything else falls back to `general`.

**2. Diagnose first, always.** Before any decision, `collect_diagnostics` fetches a fresh snapshot (session history, authentication failures, device status and parameters, plan, retrieved procedure). Scenario selection reads that snapshot, not the model's memory of the conversation.

**3. Actions that cost money or disrupt service need a yes.** Factory reset and plan upgrade are two-step flows. The first turn asks and sets `awaiting_*` in the state; the next turn executes only if the customer explicitly confirms.

**4. Replies are grounded in what ran.** The reply prompt receives the actual scenario and the actual result of the executed tools. The prompt forbids describing actions that were not performed, inventing phone numbers, or using internal jargon. For plan questions, speeds come from the RADIUS group definition via `get_group_plan_specs`, and prices come from the retrieved catalog entry, instead of from the model's recall.

**5. Retrieval is adaptive.** A plain semantic search runs first. Only when its top relevance is below a gate threshold does the system pay for an LLM query reformulation plus reranking. If the LLM step fails, it falls back to a cosine-threshold filter, and if the vector store is unreachable the tool returns an explicit `[RAG_UNAVAILABLE]` marker that the graph handles.

**6. Local by design.** LLM, embeddings, speech-to-text and text-to-speech all run in the local stack. There is no external API key anywhere in the project.

## What the tracing layer is, and what it is not

- **Implemented:** all tool outputs used for a decision live in the LangGraph state for the session; the gateway prints a per-turn audit line (scenario, intervention flag, pending confirmations); tool failures carry fixed error codes.
- **Present but not wired in:** `agent/logger.py` (`ConversationLogger`) can write one JSON file per session with the conversation and an ordered tool-call trace, and `agent/review_tool.py` is a reviewer helper for those files. The current `main.py` does not call the logger, so no JSON traces are produced out of the box.
- **Not implemented:** an automatic post-hoc check that the final text matches the executed actions. Consistency currently relies on points 1, 3 and 4 above plus the scripted scenarios in `test_scenarios*.py`.
