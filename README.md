# China Travel Agent

Extensible China travel-planning agent, organized around a clear harness:

```text
AgentHarness
  -> Router
  -> Skill
  -> LangGraph travel graph
  -> Tool/MCP layer
  -> Guardrail
  -> Memory
  -> Trace
```

The first version focuses on a readable runtime skeleton rather than a perfect
itinerary. It can already route a request, extract travel requirements, call the
Baidu Map tool facade, validate the draft, format the answer, and record trace.

## Structure

```text
china_travel_agent/
  app/
    agent/
      harness.py       # Main runtime: Router -> Skill -> Tool/MCP -> Guardrail -> Memory -> Trace
      router.py        # Intent and skill routing
      skills.py        # TravelPlanSkill and future skills
      graph.py         # LangGraph facade with fallback execution
      guardrail.py     # Requirement and plan checks
      formatter.py     # Final answer formatting
      memory.py        # Local memory/cache backend
      trace.py         # Thought/Action/Observation/Final trace
      schemas.py       # TravelRequirement and TravelPlanState
    nlp/
      extractor.py     # Requirement extractor
    tools/
      baidu_map_tool.py # Baidu Agent Plan Tool/MCP facade
    config.py
    main.py            # Optional FastAPI app
  configs/
    planner_rules.yaml
    styles.yaml
  docs/
    step1_framework.md
  prompts/
    extractor.md
    formatter.md
    planner.md
    router.md
  scripts/
    run_agent.py       # CLI entry
  tests/
```

## Environment

Required for real Baidu Agent Plan calls:

```powershell
Copy `.env.example` to `.env`, then set:

BAIDU_MAP_AUTH_TOKEN=your-token
```

The runtime loads `.env.local`, `.env`, the current process environment, and on
Windows also tries the persisted User environment. This keeps CLI scripts,
tests, and future MCP server processes on the same configuration path.

You can still override it for one shell:

```powershell
$env:BAIDU_MAP_AUTH_TOKEN="your-token"
```

Required LLM router settings, using the same DeepSeek-compatible setup as
`D:\制冷站房agent`:

```powershell
DEEPSEEK_API_KEY=your-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_TIMEOUT_SECONDS=30
DEEPSEEK_MAX_RETRIES=2
```

`AgentHarness` routes with `DeepSeekRouterProvider` only. Missing or failed LLM
configuration should be fixed directly.

Optional for LangSmith tracing:

```powershell
$env:LANGSMITH_TRACING="true"
$env:LANGSMITH_API_KEY="your-key"
$env:LANGSMITH_PROJECT="china-travel-agent"
```

## Install

Create and use the project-local virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

The first Baidu tool layer calls Agent Plan HTTP APIs directly. A formal MCP
server can later be placed behind `BaiduMapTool` without changing the harness.

## Run

```powershell
.\.venv\Scripts\python.exe scripts\run_agent.py "我想去杭州玩3天，喜欢自然风景和美食，高铁到杭州东站，轻松一点"
```

If `BAIDU_MAP_AUTH_TOKEN` is not present, the planner still returns a structured
draft with tool errors recorded in state. This lets you test the architecture
without external services.

## Next Extensions

1. Replace `RequirementExtractor` with LLM structured extraction.
2. Replace the thin planner node with a LangChain ReAct planner node.
3. Connect Baidu Map through a formal MCP server.
4. Add separate skills for `place_query`, `route_query`, and `adjust_plan`.
5. Add hotel pricing, ticket booking, budget, and transport providers.
6. Add Redis or LangGraph checkpointer memory for multi-turn edits.
7. Add route validation using real direction durations and geographic constraints.
