// HOLACRON engage UI — vanilla JS, no dependencies.
// Loads the instance summary, registers agents, starts a deliberation, and
// streams the consent-cycle events live via SSE.

const INSTANCE = "kimberim";

const $ = (id) => document.getElementById(id);
const feedEl = $("feed");
const outcomeEl = $("feed-outcome");

// Friendly labels + a short detail extractor for each event type.
const EVENT_LABELS = {
  "tension-raised": "Tension raised",
  "proposal-drafted": "Proposal drafted",
  "clarifying-question": "Clarifying question",
  reaction: "Reaction",
  amendment: "Amendment",
  "objection-raised": "Objection raised",
  "objection-integrated": "Objection integrated",
  "position-stated": "Position stated",
  digest: "Digest",
  "core-disagreement": "Core disagreement",
  "vote-cast": "Vote cast",
  "consent-reached": "Consent reached",
  "founder-veto": "Founder veto",
  "veto-override": "Veto override",
  escalation: "Escalation",
  "decision-recorded": "Decision recorded",
};

function eventDetail(type, payload) {
  if (type === "proposal-drafted" || type === "amendment")
    return payload.title || "";
  if (type === "objection-raised")
    return `${payload.criterion || ""}: ${payload.reason || ""}`.trim();
  if (type === "position-stated")
    return `${payload.position || ""}`;
  if (type === "vote-cast")
    return `${payload.kind || ""} (w ${payload.cast_by?.weight ?? 1})`;
  if (type === "consent-reached")
    return `weighted_consent=${payload.weighted_consent ?? 0}`;
  if (type === "founder-veto")
    return payload.reason || "";
  if (type === "decision-recorded")
    return `outcome=${payload.outcome}`;
  if (type === "digest")
    return `consent ${payload.consent_count ?? 0} / objection ${payload.objection_count ?? 0}`;
  return "";
}

function appendEvent(type, payload) {
  const li = document.createElement("li");
  const label = EVENT_LABELS[type] || type;
  const detail = eventDetail(type, payload || {});
  li.innerHTML = `<span class="ev__type">${label}</span><span class="ev__detail"></span>`;
  li.querySelector(".ev__detail").textContent = detail;
  feedEl.appendChild(li);
  feedEl.scrollTop = feedEl.scrollHeight;
}

function showOutcome(outcome) {
  outcomeEl.hidden = false;
  outcomeEl.textContent = outcome.toUpperCase();
  outcomeEl.className = `outcome outcome--${outcome}`;
}

// ── Load instance summary ──────────────────────────────────────
async function loadInstance() {
  const r = await fetch(`/instances/${INSTANCE}`);
  const data = await r.json();
  $("brand-name").textContent = data.display_name || "HOLACRON";
  $("instance-tagline").textContent = data.tagline || "";
  if (data.first_decision) {
    $("decision-title").textContent = data.first_decision.title;
    $("decision-summary").textContent = data.first_decision.summary;
    $("start-btn").disabled = false;
  }
}

// ── Agents ─────────────────────────────────────────────────────
async function loadAgents() {
  const r = await fetch(`/instances/${INSTANCE}/agents`);
  const data = await r.json();
  const list = $("agents-list");
  list.innerHTML = "";
  if (!data.agents.length) {
    list.innerHTML = `<li class="agents__empty">No agents yet — welcome one above.</li>`;
    return;
  }
  for (const a of data.agents) {
    const li = document.createElement("li");
    li.innerHTML = `<b></b><small></small>`;
    li.querySelector("b").textContent = a.display_name;
    li.querySelector("small").textContent =
      [a.owner, a.capability].filter(Boolean).join(" · ") || "—";
    list.appendChild(li);
  }
}

$("register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const status = $("register-status");
  status.className = "status";
  const form = e.target;
  const body = Object.fromEntries(new FormData(form));
  try {
    const r = await fetch(`/instances/${INSTANCE}/agents`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    status.classList.add("status--ok");
    status.textContent = `Welcomed: ${body.display_name}`;
    form.reset();
    await loadAgents();
  } catch (err) {
    status.classList.add("status--err");
    status.textContent = `Failed: ${err.message}`;
  }
});

// ── Start deliberation + stream live ───────────────────────────
$("start-btn").addEventListener("click", async () => {
  feedEl.innerHTML = "";
  outcomeEl.hidden = true;
  const btn = $("start-btn");
  btn.disabled = true;
  btn.textContent = "Deliberating…";
  try {
    const r = await fetch(`/instances/${INSTANCE}/deliberations`, { method: "POST" });
    const data = await r.json();
    subscribe(data.events_url);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Start deliberation";
    appendEvent("error", { reason: err.message });
  }
});

function subscribe(eventsUrl) {
  const es = new EventSource(eventsUrl);
  es.addEventListener("ping", () => {}); // keep-alive; ignore
  es.addEventListener("close", () => {
    es.close();
    finish();
  });
  es.onerror = () => {
    es.close();
    finish();
  };
  // One listener per known event type would be cleaner, but a catch-all via the
  // message event isn't possible with named events — so register each.
  for (const type of Object.keys(EVENT_LABELS)) {
    es.addEventListener(type, (e) => {
      let payload = {};
      try { payload = JSON.parse(e.data); } catch {}
      appendEvent(type, payload);
      if (type === "decision-recorded") showOutcome(payload.outcome || "adopted");
    });
  }
}

function finish() {
  const btn = $("start-btn");
  btn.disabled = false;
  btn.textContent = "Start deliberation";
}

// ── init ───────────────────────────────────────────────────────
loadInstance();
loadAgents();
