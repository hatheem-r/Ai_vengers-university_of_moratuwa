"""Model registry: which Claude model each agent uses under each profile.

- `mix`    (system M-mix): Intake = the model Ajey picks (Haiku 5.5 until then),
                           Planner = Sonnet 5.5, Checker = Haiku 5.5.
- `sonnet` (system M-S):   every agent on Sonnet 5.5.
- baseline (system B):     one agent on Sonnet 5.5, whatever the profile.

Ajey adds Intake candidates with `register()` and records the decision with
`set_intake_model()`. Token prices live in core/tracing.py (Shazan), not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .schemas import AgentName, ModelProfile, RunMode


@dataclass(frozen=True)
class ModelSpec:
    key: str                      # short registry key, e.g. "haiku-5-5"
    model_id: str                 # API model ID, e.g. "claude-haiku-5-5"
    provider: str = "anthropic"
    max_output_tokens: int = 8192
    notes: str = ""
    extra: dict = field(default_factory=dict)  # provider-specific settings (temperature, ...)


REGISTRY: dict[str, ModelSpec] = {}


def register(spec: ModelSpec) -> ModelSpec:
    """Add or replace a model. Used by Ajey's Intake model evaluation."""
    REGISTRY[spec.key] = spec
    return spec


register(ModelSpec("haiku-5-5", "claude-haiku-5-5", notes="Intake (default) and Checker in M-mix"))
register(ModelSpec("sonnet-5-5", "claude-sonnet-5-5", max_output_tokens=16384,
                   notes="Planner; every agent in M-S; the baseline agent"))

BASELINE_MODEL = "sonnet-5-5"

PROFILES: dict[str, dict[str, str]] = {
    "mix": {"intake": "haiku-5-5", "planner": "sonnet-5-5", "checker": "haiku-5-5"},
    "sonnet": {"intake": "sonnet-5-5", "planner": "sonnet-5-5", "checker": "sonnet-5-5"},
}


def get(key: str) -> ModelSpec:
    try:
        return REGISTRY[key]
    except KeyError:
        raise KeyError(f"unknown model {key!r}; registered: {sorted(REGISTRY)}") from None


def set_intake_model(key: str) -> None:
    """Record Ajey's Intake decision for the `mix` profile (due Wed 25 Nov)."""
    get(key)
    PROFILES["mix"]["intake"] = key


def model_for(agent: AgentName, profile: ModelProfile = "mix", mode: RunMode = "multi_agent") -> ModelSpec:
    """The model an agent node should call."""
    if mode == "baseline" or agent == "agent":
        return get(BASELINE_MODEL)
    if agent == "approval":
        raise ValueError("the approval step is human; it has no model")
    return get(PROFILES[profile][agent])
