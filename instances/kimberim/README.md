# Holon instance — KIMBERIM

> The first instance of the [Holon](../..) platform, for the
> [**Kimberley Rim Grid**](https://kimberim.com) — a conceptual 1 GW
> solar-updraft-tower green-compute campus for the East Kimberley, Western
> Australia.

This directory holds the **instance config** that makes the generic Holon engine
specific to KIMBERIM: branding, stakeholder & domain taxonomy presets, founder
identity, initial decision backlog, and domain circles. The engine itself lives
in the repo root.

## Instance config (to be filled in Sprint 0)

| Field | Value | Notes |
|-------|-------|-------|
| `instance_id` | `kimberim` | unique within the platform |
| `display_name` | KIMBERIM | |
| `founder` | (the principal) | holds the founder veto (ROADMAP §2.3) |
| `domain_circles` | Energy, Compute, Finance, Ethics, Community, Cultural/heritage | ROADMAP §4; refine in Sprint 5 |
| `first_decision` | _(TBD)_ candidate: circle configuration / energy-vs-compute split | MVP test case |
| `engage_surface` | links to the marketing site's Engage section | `../kimberim-site` |

## Notes specific to this instance

- **Traditional Owners / First Nations (Miriwoong/Gija)** are a **first-class,
  non-negotiable** stakeholder type in this instance's taxonomy — KIMBERIM is on
  Country. This is encoded in the platform's default taxonomy (ROADMAP §8, S5)
  but called out here as a hard requirement for this instance.
- **Future-generation proxy seat** — who speaks for the 50-year outcome of a
  green-compute campus? Reserved in this instance.
- Marketing/docs site (separate repo): [`../../../kimberim-site`](../../../kimberim-site)
  — its `docs/ROADMAP.md` points back here.

## Status

🟢 Not yet started — populated during Sprint 0. This is a placeholder so the
multi-instance structure exists from day one.
