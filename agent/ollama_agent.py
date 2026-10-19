"""Minimal Ollama-based agent for the current phys-MCP prototype.

This agent mirrors the role of the Gemini-based agent but uses a local Ollama
model instead. It is intended as a free, immediately testable alternative.

Current focus
-------------
- discover resources through phys-MCP
- ask a local Ollama model to produce a structured execution plan
- execute that plan only through phys-MCP
- ask the model for a concise technical summary

Expected location:
    agent/ollama_agent.py

Expected run command from project root:
    python -m agent.ollama_agent

Requirements
------------
- requests
- a running Ollama server, typically at http://localhost:11434
- an installed model, for example:
      ollama pull qwen2.5:7b-instruct
  or
      ollama pull qwen2.5:14b-instruct
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


def bootstrap_project_root() -> Path:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    return project_root


PROJECT_ROOT = bootstrap_project_root()

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from demos.common import build_live_target_orchestrator, make_cortical_task  # noqa: E402


PLANNING_PROMPT = """You are a planner for a phys-MCP control-plane client.
Return ONLY valid JSON with this schema:

{
  "action": "run_cortical_screen",
  "arguments": {
    "preferred_backend_id": "cortical-labs-backend",
    "channel": <int>,
    "amplitude": <float>,
    "observation_window_ms": <int>,
    "pre_delay_ms": <int>,
    "allow_fallback": <bool>,
    "human_supervision_available": <bool>
  },
  "rationale": "<short string>"
}

Rules:
- Use action exactly "run_cortical_screen"
- Prefer backend_id "cortical-labs-backend"
- Keep amplitude between 0.1 and 1.0
- Keep channel as a positive integer
- observation_window_ms between 50 and 500
- pre_delay_ms between 0 and 100
- Unless the user explicitly requests otherwise:
  - allow_fallback = false
  - human_supervision_available = true
- Output JSON only, no markdown, no explanation.
"""

SUMMARY_PROMPT_TEMPLATE = """You are summarizing a phys-MCP execution result.

User goal:
{user_goal}

Structured plan:
{plan_json}

Execution result:
{result_json}

Write a short, technically clear summary for a researcher.
Mention:
- whether the run succeeded
- which backend was used
- backend latency
- observation latency
- recording path if available
- whether fallback was used
Keep it to 4-8 sentences.
"""


@dataclass
class AgentResult:
    plan: dict[str, Any]
    resources: list[dict[str, Any]]
    run_result: dict[str, Any]
    summary: str


def to_jsonable(value: Any) -> Any:
    """Recursively convert result payloads into plain JSON-compatible values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return to_jsonable(value.model_dump())
    if hasattr(value, "tolist"):
        try:
            return to_jsonable(value.tolist())
        except (TypeError, ValueError):
            pass
    if hasattr(value, "item"):
        try:
            return to_jsonable(value.item())
        except (TypeError, ValueError):
            pass
    return str(value)


def json_text(value: Any, *, indent: int = 2) -> str:
    return json.dumps(to_jsonable(value), indent=indent)


class OllamaClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float = 180.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL") or "qwen2.5:7b-instruct"
        self.timeout_s = timeout_s

    def generate(self, prompt: str, *, temperature: float = 0.1) -> str:
        response = requests.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature,
                },
            },
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        payload = response.json()
        text = payload.get("response")
        if not isinstance(text, str):
            raise RuntimeError(f"Unexpected Ollama response: {payload}")
        return text.strip()

    def healthcheck(self) -> dict[str, Any]:
        response = requests.get(f"{self.base_url}/api/tags", timeout=30.0)
        response.raise_for_status()
        return response.json()


class PhysMCPOllamaAgent:
    def __init__(
        self,
        model: str | None = None,
        ollama_base_url: str | None = None,
    ) -> None:
        self.llm = OllamaClient(base_url=ollama_base_url, model=model)
        self.orchestrator = build_live_target_orchestrator(include_cortical_labs=True)

    def discover_resources(self) -> list[dict[str, Any]]:
        return self.orchestrator.discover_backends()

    def plan(self, user_goal: str) -> dict[str, Any]:
        prompt = f"{PLANNING_PROMPT}\n\nUser goal: {user_goal}\n"
        text = self.llm.generate(prompt, temperature=0.1)
        return json.loads(text)

    def execute_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        action = plan.get("action")
        if action != "run_cortical_screen":
            raise RuntimeError(f"Unsupported action: {action!r}")

        args = plan.get("arguments", {})

        task = make_cortical_task(
            task_id="agent-cortical-run",
            direct_backend_id=args.get("preferred_backend_id", "cortical-labs-backend"),
            allow_fallback=bool(args.get("allow_fallback", False)),
        )

        task.human_supervision_available = bool(
            args.get("human_supervision_available", True)
        )
        task.metadata["stimulation_pattern"] = {
            "channels": [int(args.get("channel", 1))],
            "amplitude": float(args.get("amplitude", 0.4)),
        }
        task.metadata["observation_window_ms"] = int(
            args.get("observation_window_ms", 100)
        )
        task.metadata["pre_delay_ms"] = int(args.get("pre_delay_ms", 20))

        run_result = self.orchestrator.execute_task(task)
        invocation = run_result.invocation
        payload = invocation.output_payload if invocation is not None else {}

        return to_jsonable({
            "success": run_result.success,
            "selected_backend": run_result.decision.selected_backend_id,
            "used_fallback": run_result.decision.used_fallback,
            "failure_reason": run_result.failure_reason,
            "decision_notes": list(run_result.decision.notes or []),
            "validation_failures": list(run_result.validation_failures or []),
            "recovery_actions": list(run_result.recovery_actions or []),
            "execution_latency_ms": getattr(invocation, "execution_latency_ms", None),
            "confidence": getattr(invocation, "confidence", None),
            "output_payload": payload,
            "telemetry_before": run_result.telemetry_before or {},
            "telemetry_after": run_result.telemetry_after or {},
        })

    def summarize(
        self,
        user_goal: str,
        plan: dict[str, Any],
        run_result: dict[str, Any],
    ) -> str:
        prompt = SUMMARY_PROMPT_TEMPLATE.format(
            user_goal=user_goal,
            plan_json=json_text(plan),
            result_json=json_text(run_result),
        )
        return self.llm.generate(prompt, temperature=0.2)

    def run(self, user_goal: str) -> AgentResult:
        resources = self.discover_resources()
        plan = self.plan(user_goal)
        run_result = self.execute_plan(plan)
        summary = self.summarize(user_goal, plan, run_result)
        return AgentResult(
            plan=plan,
            resources=resources,
            run_result=run_result,
            summary=summary,
        )


def main() -> None:
    default_user_goal = (
        "Probe whether the cultured network produces a stable response under a "
        "candidate stimulation pattern. Prefer Cortical Labs. Use a short "
        "observation window and do not enable fallback."
    )
    user_goal = " ".join(sys.argv[1:]).strip() or default_user_goal

    agent = PhysMCPOllamaAgent()
    health = agent.llm.healthcheck()

    print("=" * 80)
    print("Ollama health check")
    print("=" * 80)
    print(json_text(health)[:3000])

    result = agent.run(user_goal)

    print("=" * 80)
    print("Ollama phys-MCP agent result")
    print("=" * 80)
    print("\nPlan:")
    print(json_text(result.plan))
    print("\nDiscovered resources:")
    print(json_text(result.resources)[:4000])
    print("\nExecution result:")
    print(json_text(result.run_result))
    print("\nSummary:")
    print(result.summary)


if __name__ == "__main__":
    main()
