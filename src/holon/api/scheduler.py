"""The in-process epoch scheduler (S7).

A background asyncio task that fires epochs on cadence for any instance whose
CadenceConfig.preset is not 'manual'. Dev-grade: lives in the FastAPI process,
dies on restart, single-process. Upgradable to an external worker later.

Each tick, per instance with non-manual cadence:
  - if an epoch is already running → skip (overlap guard)
  - else pop next_tension; if None → open+close a 'skipped' epoch
  - else open an epoch + fire run_deliberation_live (the worker closes it)

All per-instance work is wrapped non-fatal: one instance failing never kills the
scheduler loop. The scheduler holds the process-wide broker + a DB engine.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from sqlmodel import Session as SMSession

from holon.api.live import run_deliberation_live
from holon.config import (
    INSTANCES_DIR,
    load_instance_config,
    load_runtime_config,
)
from holon.store import (
    close_epoch,
    current_epoch,
    make_engine,
    next_tension,
    open_epoch,
)

log = logging.getLogger(__name__)

# How often (seconds) the scheduler scans for instances to tick. Kept short so
# 'realtime' cadence feels responsive in tests; 'daily' instances are checked
# against their own timing within each tick.
_SCAN_INTERVAL = 5.0


def _scheduled_instances() -> list[tuple[str, int]]:
    """Instances whose cadence is non-manual, as (instance_id, interval_seconds).

    Scans the instances/ directory for config files; returns only those with a
    preset that warrants auto-firing. 'daily' uses a 24h interval.
    """
    out: list[tuple[str, int]] = []
    if not INSTANCES_DIR.exists():
        return out
    for path in INSTANCES_DIR.glob("*/instance.yaml"):
        instance_id = path.parent.name
        try:
            ic = load_instance_config(instance_id)
        except Exception:  # noqa: BLE001 — a bad config must not kill the loop
            continue
        if ic.cadence.preset == "realtime" and ic.cadence.interval_seconds > 0:
            out.append((instance_id, ic.cadence.interval_seconds))
        elif ic.cadence.preset == "daily":
            out.append((instance_id, 24 * 3600))
    return out


async def epoch_scheduler(app) -> None:
    """The scheduler loop. Started by create_app's lifespan when at least one
    instance is non-manual. Runs until cancelled (app shutdown)."""
    broker = app.state.broker
    config = load_runtime_config()
    eng = make_engine(config.database_url)
    loop = asyncio.get_running_loop()
    # Per-instance "last-fired" timestamps, so each instance fires on its own
    # interval rather than all at every scan.
    last_fired: dict[str, float] = {}

    log.info("epoch scheduler started")
    try:
        while True:
            now = loop.time()
            for instance_id, interval in _scheduled_instances():
                last = last_fired.get(instance_id, 0.0)
                if now - last < interval:
                    continue
                try:
                    _fire_epoch(instance_id, broker, eng, config, loop)
                except Exception as e:  # noqa: BLE001
                    log.warning("scheduler tick failed for %s (non-fatal): %s", instance_id, e)
                last_fired[instance_id] = now
            await asyncio.sleep(_SCAN_INTERVAL)
    except asyncio.CancelledError:
        log.info("epoch scheduler cancelled (shutdown)")
    finally:
        eng.dispose()


def _fire_epoch(instance_id: str, broker, eng, config, loop) -> None:
    """Fire one epoch for an instance (synchronous DB work; called from the
    async loop). Skips if an epoch is already running (overlap guard)."""
    ic = load_instance_config(instance_id)
    with SMSession(eng) as s:
        if current_epoch(s, instance_id=instance_id) is not None:
            return  # overlap guard
        trow = next_tension(s, instance_id=instance_id)
        if trow is None:
            # No backlog tension → open + immediately close a 'skipped' epoch.
            epoch = open_epoch(s, instance_id=instance_id)
            s.flush()
            close_epoch(s, epoch_id=epoch.id, status="skipped")
            s.commit()
            log.info("scheduler: skipped epoch for %s (empty backlog)", instance_id)
            return
        epoch = open_epoch(s, instance_id=instance_id, tension_id=trow.id)
        s.commit()
        epoch_id = epoch.id

    # Fire the deliberation (the worker starts + closes the epoch).
    run_id = uuid4()
    broker.open(run_id, loop)
    run_deliberation_live(
        instance_id=instance_id, run_id=run_id, broker=broker,
        config=config, instance=ic, epoch_id=epoch_id,
    )
    log.info("scheduler: fired epoch for %s (tension %s)", instance_id, trow.id)


__all__ = ["epoch_scheduler"]
