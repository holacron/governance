"""The minimal autonomous runner — Olon's first real product (ROADMAP §11).

This is the bootstrap primitive: a loop that ① reads the next task, ② calls
call_agent() to execute it, ③ runs the build/test gate (pytest must pass),
④ commits if green (else stops), ⑤ checkpoints state to Postgres, ⑥ respects
the cost cap. It proves the autonomy primitive on a single task.

In S1 this loop becomes the governed Orchestrator+Verifier core that runs the
consent cycle. Nothing here is throwaway.

Safety rails (the autonomy leash, L2 ceiling):
- Cost cap: CostCapExceeded aborts the run cleanly (§2.5).
- Build/test gate: a failing gate stops the loop — never commits broken code.
- Checkpointing: every iteration persists runner_state, so a stop is resumable
  and a human can inspect/intervene (the consent gate at sprint boundaries).
- Max iterations: a hard ceiling so the loop can never run away indefinitely.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

from sqlmodel import Session as SMSession
from sqlmodel import select

from olon.config import REPO_ROOT, RuntimeConfig, load_runtime_config
from olon.gateway import CostCapExceeded, LLMGateway
from olon.store import RunnerStateRow, make_engine

log = logging.getLogger(__name__)

# A Task is anything with a description and a way to know if it's done.
Task = str  # a free-text instruction fed to call_agent
TaskResult = str  # the agent's textual output for that task


@dataclass
class BuildGateResult:
    ok: bool
    output: str


@dataclass
class RunnerConfig:
    """Tunables for a single run. Defaults are deliberately conservative (L2)."""

    repo_root: Path = REPO_ROOT
    max_iterations: int = 8          # hard ceiling — the loop can't run away
    commit_on_green: bool = True     # L2: commit only when the gate is green
    git_branch: str = "main"
    # The gate command. Override for testing (e.g. a no-op gate).
    gate_cmd: list[str] = field(
        default_factory=lambda: [sys.executable, "-m", "pytest", "-q"]
    )


@dataclass
class Runner:
    """The minimal autonomous runner.

    Built on real components: LLMGateway (Z.ai), Postgres (checkpoint), and the
    build/test gate (pytest). This is the Orchestrator+Verifier seed.
    """

    config: RuntimeConfig
    runner_config: RunnerConfig = field(default_factory=RunnerConfig)
    gateway: LLMGateway = field(init=False)
    run_id: UUID = field(default_factory=uuid4)
    iterations: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.gateway = LLMGateway(self.config)

    # ── public API ────────────────────────────────────────────────────────────

    def run(self, tasks: list[Task], instance_id: str = "olon-bootstrap") -> None:
        """Execute a list of tasks autonomously, stopping at the first failure
        or cap breach. Checkpoints state each iteration.
        """
        self._checkpoint(instance_id, status="running", current_task="(start)")
        log.info("runner %s starting with %d task(s)", self.run_id, len(tasks))

        for task in tasks:
            if self.iterations >= self.runner_config.max_iterations:
                log.warning("max_iterations (%d) reached — stopping",
                            self.runner_config.max_iterations)
                self._checkpoint(instance_id, status="stopped", current_task=task)
                return

            self.iterations += 1
            self._checkpoint(instance_id, status="running", current_task=task)

            try:
                result = self._execute_task(task)
            except CostCapExceeded as e:
                log.warning("cost cap exceeded — stopping: %s", e)
                self._checkpoint(instance_id, status="stopped", current_task=task)
                return

            gate = self._run_gate()
            if not gate.ok:
                log.error("build/test gate FAILED — stopping (not committing):\n%s",
                          gate.output)
                self._checkpoint(
                    instance_id, status="stopped", current_task=task,
                )
                return

            if self.runner_config.commit_on_green:
                self._commit(task, result)
            self._checkpoint(
                instance_id, status="running", current_task=f"(done) {task}",
            )

        self._checkpoint(instance_id, status="done", current_task="(end)")
        log.info("runner %s completed all tasks", self.run_id)

    # ── the three core operations ─────────────────────────────────────────────

    def _execute_task(self, task: Task) -> TaskResult:
        """① call_agent to do the work. The single primitive the runner is built on."""
        log.info("[iter %d] executing: %s", self.iterations, task)
        resp = self.gateway.call_agent(
            role="orchestrator",
            prompt=(
                f"You are executing a development task in the Olon repo.\n"
                f"Task: {task}\n\n"
                "Describe concisely what you did and what should be verified. "
                "Do not write code in this response; just summarise the step."
            ),
            max_tokens=400,
        )
        log.info("[iter %d] agent responded (%d tokens)", self.iterations,
                 resp.output_tokens)
        return resp.text

    def _run_gate(self) -> BuildGateResult:
        """③ the build/test gate — pytest must pass. The strongest guardrail."""
        cmd = self.runner_config.gate_cmd
        log.info("[iter %d] running gate: %s", self.iterations, " ".join(cmd))
        try:
            r = subprocess.run(
                cmd, cwd=str(self.runner_config.repo_root),
                capture_output=True, text=True, timeout=300,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return BuildGateResult(ok=False, output=f"gate error: {e}")
        out = (r.stdout + r.stderr).strip()
        return BuildGateResult(ok=(r.returncode == 0), output=out[-2000:])

    def _commit(self, task: Task, result: TaskResult) -> None:
        """④ commit if green. Uses git on the repo."""
        msg = f"runner({self.run_id}): {task[:72]}"
        cmds = [
            ["git", "add", "-A"],
            ["git", "commit", "-q", "-m", msg],
        ]
        for c in cmds:
            subprocess.run(
                c, cwd=str(self.runner_config.repo_root),
                capture_output=True, text=True, check=False,
            )
        log.info("[iter %d] committed (gate green)", self.iterations)

    # ── checkpointing (the L2 consent gate) ───────────────────────────────────

    def _checkpoint(self, instance_id: str, *, status: str, current_task: str) -> None:
        """⑤ persist runner_state to Postgres so a stop is resumable + inspectable."""
        try:
            eng = make_engine(self.config.database_url)
            with SMSession(eng) as s:
                row = RunnerStateRow(
                    run_id=self.run_id,
                    instance_id=instance_id,
                    status=status,
                    current_task=current_task[:500],
                    spent_usd=self.gateway.spent_usd,
                    iterations=self.iterations,
                )
                # merge so the same run_id updates in place across iterations
                s.merge(row)
                s.commit()
        except Exception as e:  # noqa: BLE001
            # Checkpointing must never kill the run — log and continue.
            log.warning("checkpoint failed (non-fatal): %s", e)

    # ── inspection ────────────────────────────────────────────────────────────

    @classmethod
    def latest_state(cls, config: RuntimeConfig, run_id: UUID) -> RunnerStateRow | None:
        eng = make_engine(config.database_url)
        with SMSession(eng) as s:
            stmt = select(RunnerStateRow).where(RunnerStateRow.run_id == run_id)
            return s.exec(stmt).first()


# ── Convenience entrypoint ────────────────────────────────────────────────────


def run_tasks(
    tasks: list[str],
    *,
    config: RuntimeConfig | None = None,
    runner_config: RunnerConfig | None = None,
) -> Runner:
    """Build a Runner from .env and execute tasks. Returns the runner for inspection."""
    r = Runner(
        config=config or load_runtime_config(),
        runner_config=runner_config or RunnerConfig(),
    )
    r.run(tasks)
    return r


__all__ = [
    "BuildGateResult",
    "Runner",
    "RunnerConfig",
    "run_tasks",
]
