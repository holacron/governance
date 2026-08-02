"""Configuration loading for Holon.

Two layers of config:
  1. Runtime settings  — from .env (secrets, DB URL, model routing, cost cap).
  2. Instance config   — from instances/<id>/instance.yaml (branding, taxonomy,
                         founder, circles, first decision).

The engine is generic; an instance makes it specific. See ROADMAP §7.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

# Repo root = parents of this file: src/holon/config/__init__.py -> holon/
REPO_ROOT = Path(__file__).resolve().parents[3]
INSTANCES_DIR = REPO_ROOT / "instances"


# ── Runtime settings (from .env) ──────────────────────────────────────────────


class RuntimeConfig(BaseModel):
    """Runtime/secrets loaded from .env. Never logged or serialized."""

    zai_api_key: str = Field(default="", alias="ZAI_API_KEY")
    zai_base_url: str = Field(
        default="https://api.z.ai/api/anthropic", alias="ZAI_BASE_URL"
    )
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    # Comma-separated model IDs the gateway may route to.
    harness_models: list[str] = Field(
        default_factory=lambda: ["GLM-5-Turbo"],
        alias="HARNESS_MODELS",
    )
    # Per-run cost cap in USD before a run aborts (ROADMAP §2.5).
    harness_cost_cap_usd: float = Field(default=5.0, alias="HARNESS_COST_CAP_USD")
    harness_port: int = Field(default=8787, alias="HARNESS_PORT")
    database_url: str = Field(default="", alias="DATABASE_URL")

    model_config = {"populate_by_name": True, "extra": "ignore"}

    @field_validator("harness_models", mode="before")
    @classmethod
    def _split_models(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [m.strip() for m in v.split(",") if m.strip()]
        return v

    @property
    def has_provider(self) -> bool:
        return bool(self.zai_api_key or self.openai_api_key or self.anthropic_api_key)


def load_runtime_config(env_file: Path | None = None) -> RuntimeConfig:
    """Load runtime config from .env. Searches REPO_ROOT upwards."""
    if env_file is None:
        env_file = REPO_ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)
    # Also respect any vars already in the environment (e.g. CI secrets).
    return RuntimeConfig.model_validate(os.environ)


# ── Instance config (from instances/<id>/instance.yaml) ───────────────────────


class FounderConfig(BaseModel):
    name: str
    role: str = "principal"


class FirstDecision(BaseModel):
    id: str
    title: str
    summary: str
    seed_tensions: list[str] = Field(default_factory=list)


class Branding(BaseModel):
    primary_color: str = "#10B981"
    primary_color_dark: str = "#059669"
    ink: str = "#0F172A"
    display_font: str = "Sora"
    body_font: str = "Inter"
    mono_font: str = "JetBrains Mono"


class EngageSurface(BaseModel):
    url: str = ""
    repo: str = ""


class TaxonomyPresets(BaseModel):
    """Seed presets; the full ABAC matrix is built in Sprint 5."""

    stakeholder_types: list[str] = Field(default_factory=list)
    functional_domains: list[str] = Field(default_factory=list)


class GovernanceConfig(BaseModel):
    """Tunable governance parameters for the consent cycle (ADR 0001).

    These are the '§2.5 knobs' and the ADR's 'parameters left to Sprint 0→1'.
    Per-instance so each venture can tune its own holacracy. Defaults match the
    ADR's proposed values.
    """

    # Max integration rounds before the cycle escalates (ADR: default 3).
    integration_loop_cap: int = 3
    # Founder veto window length in hours (ADR: default 24h, timezone-fair).
    veto_window_h: float = 24.0
    # Max veto->rework rounds before participant override is available (confirmed: 3).
    veto_round_cap: int = 3
    # Override threshold as a fraction of weighted participant votes (confirmed: 0.75).
    override_threshold: float = 0.75
    # How abstain (silent-past-window) counts in the weighted tally.
    # 'neither' = abstain contributes to neither consent nor objection weight
    # (ADR open question #2, resolved: abstain = not-an-objection).
    abstain_counts_as: Literal["neither", "consent"] = "neither"

    model_config = {"extra": "ignore"}


class InstanceConfig(BaseModel):
    """A deployment of Holon for one venture (ROADMAP §7, §13 glossary)."""

    instance_id: str
    display_name: str
    tagline: str = ""
    founder: FounderConfig
    domain_circles: list[str] = Field(default_factory=list)
    first_decision: FirstDecision | None = None
    engage_surface: EngageSurface = Field(default_factory=EngageSurface)
    branding: Branding = Field(default_factory=Branding)
    taxonomy: TaxonomyPresets = Field(default_factory=TaxonomyPresets)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)

    model_config = {"extra": "ignore"}


def load_instance_config(instance_id: str | Literal["kimberim"]) -> InstanceConfig:
    """Load and validate an instance's config from instances/<id>/instance.yaml."""
    path = INSTANCES_DIR / instance_id / "instance.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"No instance config at {path}. Available: "
            f"{[p.parent.name for p in INSTANCES_DIR.glob('*/instance.yaml')]}"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return InstanceConfig.model_validate(data)
