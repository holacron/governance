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
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
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
    """Seed presets; the full ABAC matrix is built in Sprint 6."""

    stakeholder_types: list[str] = Field(default_factory=list)
    functional_domains: list[str] = Field(default_factory=list)


class ABACMatrix(BaseModel):
    """Attribute-based access control matrix (S6, ROADMAP §8 S5 / glossary).

    Authority = stakeholder-type × functional-domain → {permissions, weight}.
    A full N×M cell matrix is unwieldy in YAML, so we encode it as:
      - `weights`:        default weight per stakeholder-type (default 1.0).
      - `permissions`:    default permission set per stakeholder-type.
      - `overrides`:      optional cell-level overrides keyed "type:domain".
    `resolve_cell()` merges default + override at registration time.

    Both defaults and overrides are advisory-ish: they're resolved once at
    registration and stored on the agent row, so the hot path (permission
    checks during a cycle) is a cheap JSON-column parse, not a config load.
    """

    weights: dict[str, float] = Field(default_factory=dict)
    permissions: dict[str, list[str]] = Field(default_factory=dict)
    overrides: dict[str, dict] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


def resolve_cell(
    matrix: ABACMatrix | None,
    stakeholder_type: str | None,
    functional_domain: str | None = None,
) -> tuple[set[str], float]:
    """Resolve an agent's (permissions, weight) from the ABAC matrix.

    Merge order: stakeholder-type defaults → cell override ("type:domain").
    An unknown/missing stakeholder_type gets the conservative participant
    default (submit, deliberate, vote) at weight 1.0 — so a NULL taxonomy
    agent (pre-S6 registration) behaves as before.
    """
    if matrix is None or stakeholder_type is None:
        return {"submit", "deliberate", "vote"}, 1.0

    perms: set[str] = set(
        matrix.permissions.get(stakeholder_type, ["submit", "deliberate", "vote"])
    )
    weight: float = float(matrix.weights.get(stakeholder_type, 1.0))

    # Optional cell-level override: "stakeholder_type:functional_domain".
    if functional_domain is not None:
        key = f"{stakeholder_type}:{functional_domain}"
        cell = matrix.overrides.get(key)
        if cell:
            if "permissions" in cell:
                perms = set(cell["permissions"])
            if "weight" in cell:
                weight = float(cell["weight"])

    return perms, weight


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


class CadenceConfig(BaseModel):
    """The epoch cadence for an instance (S7, ROADMAP glossary/§8 S6).

    preset controls whether the in-process scheduler fires epochs:
      - manual   (default): no scheduler; epochs are triggered via the API.
      - realtime: scheduler fires on interval_seconds (test/dev friendly).
      - daily:   scheduler fires every 24h (interval_seconds ignored).
    S7 delivers synchronous-per-epoch cadence: the epoch controls WHEN a
    cycle starts; each cycle still runs to completion synchronously. True
    async mid-cycle windows are deferred (requires CycleState persistence).
    """

    preset: Literal["manual", "realtime", "daily"] = "manual"
    interval_seconds: int = 0  # used by 'realtime'; 0 = no auto-fire

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
    abac: ABACMatrix = Field(default_factory=ABACMatrix)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)
    cadence: CadenceConfig = Field(default_factory=CadenceConfig)

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
