from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.agent.memory import MemoryBackend, build_memory_backend
from app.agent.router import Intent, LLMRouter
from app.agent.skills import EchoSkill, SkillResult, TravelPlanSkill
from app.agent.trace import TraceRecorder
from app.config import settings


@dataclass(frozen=True)
class AgentResponse:
    job_id: str
    session_id: str
    answer: str
    trace: dict[str, Any]
    artifacts: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


class AgentHarness:
    """Router -> Skill(Graph) -> Tool/MCP -> Guardrail -> Memory -> Trace."""

    def __init__(
        self,
        memory: MemoryBackend | None = None,
        *,
        router: LLMRouter | None = None,
    ):
        self.memory = memory or build_memory_backend()
        self.router = router or LLMRouter()
        travel_skill = TravelPlanSkill()
        chat_skill = EchoSkill()
        self.skills = {
            Intent.TRAVEL_PLAN: travel_skill,
            Intent.ADJUST_PLAN: travel_skill,
            Intent.PLACE_QUERY: travel_skill,
            Intent.ROUTE_QUERY: travel_skill,
            Intent.CITY_RECOMMEND: travel_skill,
            Intent.CHAT: chat_skill,
        }

    def run(self, text: str, session_id: str | None = None, job_id: str | None = None) -> AgentResponse:
        session_id = session_id or "default"
        job_id = job_id or str(uuid4())
        trace = TraceRecorder(job_id)

        trace.thought("Receive user request and inspect intent.", user_input=text)
        cache_key = f"{settings.agent_cache_version}:{session_id}:{text}"
        cached = self.memory.result_cache.get(cache_key)
        if cached is not None:
            trace.observation("Result cache hit.", cache_key=cache_key)
            trace.final("Return cached result.")
            return AgentResponse(
                job_id=job_id,
                session_id=session_id,
                answer=cached["answer"],
                trace=trace.to_dict(),
                artifacts=cached.get("artifacts", []),
                data=cached.get("data", {}),
            )

        trace.action("Route request with LLMRouter.", router="LLMRouter")
        decision = self.router.route(text)
        trace.observation(
            "Router selected skill.",
            intent=decision.intent.value,
            skill=decision.skill_name,
            confidence=decision.confidence,
            reason=decision.reason,
            source=decision.source,
            raw=decision.raw,
        )

        skill = self.skills[decision.intent]
        trace.action("Execute skill.", skill=skill.name)
        result: SkillResult = skill.run(text, trace=trace)
        trace.observation("Skill execution completed.", artifacts=result.artifacts)
        trace.final("Return final answer.", answer_preview=result.answer[:240])

        response = AgentResponse(
            job_id=job_id,
            session_id=session_id,
            answer=result.answer,
            trace=trace.to_dict(),
            artifacts=result.artifacts,
            data=result.data,
        )
        self._persist_response(response, session_id, text, decision.intent.value, cache_key)
        return response

    def _persist_response(
        self,
        response: AgentResponse,
        session_id: str,
        user_input: str,
        intent: str,
        cache_key: str,
    ) -> None:
        self.memory.append_session(
            session_id,
            {
                "job_id": response.job_id,
                "user": user_input,
                "intent": intent,
                "answer": response.answer,
            },
        )
        self.memory.set_job(response.job_id, response.__dict__)
        self.memory.result_cache.set(
            cache_key,
            {
                "answer": response.answer,
                "artifacts": response.artifacts,
                "data": response.data,
            },
        )
