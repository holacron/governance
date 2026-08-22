"""ABAC matrix + permission resolution unit tests (S6).

Pure config-layer tests — no LLM, no DB. Verifies the matrix model and the
resolve_cell merge logic (defaults → cell override). The registration +
permission-gate integration is covered by test_api_unit.py / test_backlog_unit.
"""

from __future__ import annotations

from olon.config import ABACMatrix, resolve_cell
from olon.schema import Permission


def test_resolve_cell_returns_defaults_for_known_stakeholder():
    """A founder gets the seeded permissions + weight from the matrix."""
    matrix = ABACMatrix(
        weights={"founder": 2.0},
        permissions={"founder": ["submit", "deliberate", "vote", "veto"]},
    )
    perms, weight = resolve_cell(matrix, "founder", None)
    assert perms == {"submit", "deliberate", "vote", "veto"}
    assert weight == 2.0


def test_resolve_cell_override_merges_over_default():
    """A cell override ("type:domain") supersedes the stakeholder-type default."""
    matrix = ABACMatrix(
        weights={"staff": 1.0},
        permissions={"staff": ["submit", "deliberate", "vote"]},
        overrides={
            "staff:ethics": {"weight": 1.5, "permissions": ["submit", "vote"]},
        },
    )
    perms, weight = resolve_cell(matrix, "staff", "ethics")
    assert perms == {"submit", "vote"}  # override replaces, not extends
    assert weight == 1.5


def test_resolve_cell_partial_override_only_weights():
    """An override may set only weight (permissions inherit the type default)."""
    matrix = ABACMatrix(
        weights={"investor": 1.0},
        permissions={"investor": ["submit", "deliberate", "vote"]},
        overrides={"investor:finance": {"weight": 1.25}},
    )
    perms, weight = resolve_cell(matrix, "investor", "finance")
    assert perms == {"submit", "deliberate", "vote"}  # type default preserved
    assert weight == 1.25


def test_resolve_cell_unknown_stakeholder_gets_participant_default():
    """An unrecognised stakeholder type gets the conservative default."""
    matrix = ABACMatrix(
        weights={"founder": 2.0},
        permissions={"founder": ["submit"]},
    )
    perms, weight = resolve_cell(matrix, "mystery-type", None)
    assert perms == {"submit", "deliberate", "vote"}
    assert weight == 1.0


def test_resolve_cell_null_stakeholder_back_compat():
    """NULL stakeholder_type (pre-S6 agents) → participant default, weight 1.0.

    This is the back-compat contract: every agent registered before S6 had no
    taxonomy, and behaved as an equal-weight participant. resolve_cell must
    preserve that when stakeholder_type is None.
    """
    matrix = ABACMatrix(
        weights={"founder": 2.0},
        permissions={"founder": ["submit", "veto"]},
    )
    perms, weight = resolve_cell(matrix, None, None)
    assert perms == {"submit", "deliberate", "vote"}
    assert weight == 1.0


def test_resolve_cell_null_matrix_back_compat():
    """No matrix at all (an instance without an abac block) → default."""
    perms, weight = resolve_cell(None, "founder", None)
    assert perms == {"submit", "deliberate", "vote"}
    assert weight == 1.0


def test_kimberim_matrix_loads_from_yaml():
    """The KIMBERIM instance config carries the full seeded ABAC matrix."""
    from olon.config import load_instance_config

    ic = load_instance_config("kimberim")
    assert ic.abac.weights.get("founder") == 2.0
    assert ic.abac.weights.get("traditional-owners") == 2.0
    # Founder has veto + admit; Traditional Owners have vote but not veto.
    founder_perms, founder_w = resolve_cell(ic.abac, "founder", None)
    assert Permission.VETO in founder_perms
    assert Permission.ADMIT in founder_perms
    assert founder_w == 2.0
    to_perms, to_w = resolve_cell(ic.abac, "traditional-owners", None)
    assert Permission.VOTE in to_perms
    assert Permission.VETO not in to_perms
    assert to_w == 2.0
    # Regulator is observe-only + submit (oversight, not decision).
    reg_perms, _ = resolve_cell(ic.abac, "regulator", None)
    assert Permission.OBSERVE in reg_perms
    assert Permission.VOTE not in reg_perms
    # An unrecognised type still gets the participant default.
    unk_perms, unk_w = resolve_cell(ic.abac, "ngo", None)
    assert unk_perms == {"submit", "deliberate", "vote"}
    assert unk_w == 1.0
