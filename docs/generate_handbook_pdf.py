#!/usr/bin/env python3
"""Generate the KIMBERIM Participant Handbook as a branded PDF.

Uses ReportLab with KIMBERIM brand tokens (green #10B981, ink #0F172A).
Output: docs/holacron-participant-handbook.pdf

Run:  python docs/generate_handbook_pdf.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# ── Brand tokens (from instance.yaml branding) ───────────────────────────────
GREEN = HexColor("#10B981")
GREEN_DEEP = HexColor("#059669")
INK = HexColor("#0F172A")
SLATE = HexColor("#475569")
SLATE_LIGHT = HexColor("#94A3B8")
PAPER = HexColor("#FFFFFF")
TINT = HexColor("#F0FDF4")  # ultra-light green tint for table headers / rules
RULE = HexColor("#E2E8F0")

# Page geometry
PAGE_W, PAGE_H = A4
MARGIN = 22 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

OUTPUT = Path(__file__).resolve().parent / "holacron-participant-handbook.pdf"


# ── Font registration (prefer Inter/Sora if present, else Helvetica) ──────────
def _register_fonts() -> tuple[str, str, str]:
    """Return (body, bold, display) font names. Fall back to Helvetica."""
    candidates = {
        "Inter": [
            r"C:\Windows\Fonts\Inter-Regular.ttf",
            r"C:\Windows\Fonts\inter-regular.ttf",
        ],
        "Inter-Bold": [
            r"C:\Windows\Fonts\Inter-Bold.ttf",
            r"C:\Windows\Fonts\inter-bold.ttf",
        ],
        "Sora": [
            r"C:\Windows\Fonts\Sora-SemiBold.ttf",
            r"C:\Windows\Fonts\sora-semibold.ttf",
            r"C:\Windows\Fonts\Sora-Bold.ttf",
        ],
    }
    body, bold, display = "Helvetica", "Helvetica-Bold", "Helvetica-Bold"
    try:
        import os

        if os.path.exists(candidates["Inter"][0]):
            pdfmetrics.registerFont(TTFont("Inter", candidates["Inter"][0]))
            body = "Inter"
        if os.path.exists(candidates["Inter-Bold"][0]):
            pdfmetrics.registerFont(TTFont("Inter-Bold", candidates["Inter-Bold"][0]))
            bold = "Inter-Bold"
        for p in candidates["Sora"]:
            if os.path.exists(p):
                pdfmetrics.registerFont(TTFont("Sora", p))
                display = "Sora"
                break
    except Exception:
        pass  # fall back to Helvetica
    return body, bold, display


BODY, BOLD, DISPLAY = _register_fonts()


# ── Paragraph styles ──────────────────────────────────────────────────────────
def _styles() -> dict:
    ss = getSampleStyleSheet()
    base = ss["Normal"]
    base.fontName = BODY
    base.fontSize = 10.5
    base.leading = 16
    base.textColor = INK
    base.alignment = TA_JUSTIFY
    base.spaceAfter = 7

    return {
        "body": base,
        "lede": ParagraphStyle(
            "lede", parent=base, fontSize=12, leading=19, textColor=SLATE,
            spaceAfter=12, alignment=TA_LEFT,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base, fontName=DISPLAY, fontSize=22, leading=27,
            textColor=INK, spaceBefore=10, spaceAfter=6, alignment=TA_LEFT,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base, fontName=BOLD, fontSize=14, leading=20,
            textColor=GREEN_DEEP, spaceBefore=16, spaceAfter=5, alignment=TA_LEFT,
        ),
        "h3": ParagraphStyle(
            "h3", parent=base, fontName=BOLD, fontSize=11.5, leading=16,
            textColor=INK, spaceBefore=10, spaceAfter=3, alignment=TA_LEFT,
        ),
        "quote": ParagraphStyle(
            "quote", parent=base, fontName=BODY, fontSize=11, leading=17,
            textColor=SLATE, leftIndent=16, rightIndent=10, spaceBefore=4,
            spaceAfter=10, borderColor=GREEN, borderWidth=0, borderPadding=0,
            alignment=TA_LEFT,
        ),
        "kicker": ParagraphStyle(
            "kicker", parent=base, fontName=BOLD, fontSize=9, leading=12,
            textColor=GREEN_DEEP, spaceAfter=2, alignment=TA_LEFT,
        ),
        "cell": ParagraphStyle(
            "cell", parent=base, fontSize=9.5, leading=13, alignment=TA_LEFT,
            spaceAfter=0,
        ),
        "cell_b": ParagraphStyle(
            "cell_b", parent=base, fontName=BOLD, fontSize=9.5, leading=13,
            alignment=TA_LEFT, spaceAfter=0, textColor=INK,
        ),
        "cell_c": ParagraphStyle(
            "cell_c", parent=base, fontName=BOLD, fontSize=9.5, leading=13,
            alignment=TA_CENTER, spaceAfter=0, textColor=GREEN_DEEP,
        ),
        "footer": ParagraphStyle(
            "footer", parent=base, fontSize=8, leading=10, textColor=SLATE_LIGHT,
            alignment=TA_CENTER,
        ),
    }


S = _styles()


# ── Page decoration (cover + body header/footer) ──────────────────────────────
def _draw_cover(canvas, doc):
    canvas.saveState()
    # Ink background top band
    canvas.setFillColor(INK)
    canvas.rect(0, PAGE_H - 95 * mm, PAGE_W, 95 * mm, fill=1, stroke=0)
    # Green accent rule
    canvas.setFillColor(GREEN)
    canvas.rect(MARGIN, PAGE_H - 95 * mm + 8 * mm, 28 * mm, 3.2, fill=1, stroke=0)
    canvas.restoreState()


def _draw_body(canvas, doc):
    canvas.saveState()
    # Top hairline + brand mark
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, PAGE_H - 14 * mm, PAGE_W - MARGIN, PAGE_H - 14 * mm)
    canvas.setFillColor(GREEN_DEEP)
    canvas.setFont(BOLD, 8)
    canvas.drawString(MARGIN, PAGE_H - 11 * mm, "KIMBERIM")
    canvas.setFillColor(SLATE_LIGHT)
    canvas.setFont(BODY, 8)
    canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 11 * mm,
                           "Participant Handbook")
    # Footer page number
    canvas.setFillColor(SLATE_LIGHT)
    canvas.setFont(BODY, 8)
    canvas.drawCentredString(PAGE_W / 2, 12 * mm, f"— {doc.page} —")
    canvas.restoreState()


# ── Document build ────────────────────────────────────────────────────────────
def _para(text: str, style="body") -> Paragraph:
    return Paragraph(text, S[style])


def _table_stakeholders() -> Table:
    header = [
        _para("Stakeholder", "cell_b"),
        _para("Weight", "cell_c"),
        _para("What you can do", "cell_b"),
    ]
    rows = [
        ("Founder", "2.0", "Raise, triage, deliberate, vote, veto, admit members"),
        ("Traditional Owners / First Nations", "2.0", "Raise, deliberate, vote"),
        ("Staff", "1.0", "Raise, triage, deliberate, vote"),
        ("Future-generation proxy", "1.0", "Raise, deliberate, vote, certify outcomes"),
        ("Regulator", "1.0", "Observe, raise concerns (oversight)"),
        ("Government instrumentality", "1.0", "Observe, raise concerns"),
        ("Investor", "1.0", "Raise, deliberate, vote"),
        ("Customer / offtaker", "1.0", "Raise, deliberate, vote"),
        ("Corporate partner", "1.0", "Raise, deliberate, vote"),
        ("Supplier", "1.0", "Raise, deliberate, vote"),
        ("NGO", "1.0", "Raise, deliberate, vote"),
        ("QANGO", "1.0", "Raise, deliberate, vote"),
        ("Academia", "1.0", "Raise, deliberate, vote"),
    ]
    data = [header] + [
        [_para(r[0], "cell_b"), _para(r[1], "cell_c"), _para(r[2], "cell")]
        for r in rows
    ]
    col_w = [0.34 * CONTENT_W, 0.12 * CONTENT_W, 0.54 * CONTENT_W]
    t = Table(data, colWidths=col_w, repeatRows=1, hAlign="CENTER")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TINT),
        ("TEXTCOLOR", (0, 0), (-1, 0), GREEN_DEEP),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [PAPER, HexColor("#F8FAFC")]),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, GREEN),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, RULE),
    ]))
    return t


def _table_glossary() -> Table:
    terms = [
        ("Holon", "A single project running on HOLACRON. KIMBERIM is a Holon."),
        ("Tension", "The felt gap between what is and what could be. The trigger for a decision."),
        ("Proposal", "A structured change drafted to resolve a tension."),
        ("Consent", "No participant has a valid objection. Not the same as unanimity."),
        ("Objection", "A concern: the proposal causes harm, is not safe to try, or regresses a role."),
        ("Epoch", "One governance cycle. Opens, deliberates one tension, closes on a decision."),
        ("ABAC", "The permission matrix: stakeholder type × domain → what you can do + weight."),
        ("Ledger", "The permanent, public, append-only record of every event."),
        ("Safe to try", "The consent standard: causes no harm and regresses no role."),
    ]
    data = [[_para(t, "cell_b"), _para(d, "cell")] for t, d in terms]
    col_w = [0.24 * CONTENT_W, 0.76 * CONTENT_W]
    t = Table(data, colWidths=col_w, hAlign="CENTER")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [PAPER, HexColor("#F8FAFC")]),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, RULE),
    ]))
    return t


def build() -> Path:
    doc = BaseDocTemplate(
        str(OUTPUT), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=20 * mm, bottomMargin=18 * mm,
        title="Participating in KIMBERIM — A HOLACRON Participant Handbook",
        author="KIMBERIM / HOLACRON Governance Collective",
        subject="How to participate in KIMBERIM's consent-governed collective",
        creator="HOLACRON",
    )

    # Cover frame (full page minus margins, content sits in lower portion)
    cover_frame = Frame(
        MARGIN, MARGIN, CONTENT_W, PAGE_H - 2 * MARGIN,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        id="cover",
    )
    body_frame = Frame(
        MARGIN, 16 * mm, CONTENT_W, PAGE_H - 36 * mm,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        id="body",
    )
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[cover_frame], onPage=_draw_cover),
        PageTemplate(id="body", frames=[body_frame], onPage=_draw_body),
    ])

    story: list = []

    # ── Cover page ────────────────────────────────────────────────────────────
    story.append(Spacer(1, 40 * mm))
    story.append(_para("KICKER", "kicker"))  # placeholder, replaced below
    story.pop()
    # Title block sits over the ink band
    title_block = [
        Paragraph(
            '<font name="%s" color="#10B981" size="9"><b>● HOLACRON GOVERNANCE</b></font>'
            % BOLD, S["kicker"]
        ),
        Spacer(1, 6),
        Paragraph(
            '<font name="%s" color="white" size="34"><b>Participating</b></font>'
            % DISPLAY, S["h1"]
        ),
        Paragraph(
            '<font name="%s" color="white" size="34"><b>in KIMBERIM</b></font>'
            % DISPLAY, S["h1"]
        ),
        Spacer(1, 10),
        Paragraph(
            '<font name="%s" color="#94A3B8" size="12">A handbook for the HOLACRON '
            "governance collective</font>" % BODY, S["lede"]
        ),
    ]
    story.extend(title_block)
    story.append(Spacer(1, 26 * mm))
    # Tagline below the ink band
    story.append(Paragraph(
        '<font name="%s" color="#475569" size="11">A consent-governed collective of '
        "AI agents and humans making venture decisions together.</font>" % BODY,
        S["lede"],
    ))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        '<font name="%s" color="#94A3B8" size="9">kimberim.com · Protocol v1.0</font>'
        % BODY, S["footer"]
    ))

    story.append(PageBreak())

    # Switch to body template
    from reportlab.platypus import NextPageTemplate
    story.insert(len(story) - 1, NextPageTemplate("body"))

    # ── Body: What is HOLACRON ────────────────────────────────────────────────
    story.append(_para("What is HOLACRON?", "h1"))
    story.append(_para(
        "HOLACRON is a new kind of decision-making body: a collective of AI agents "
        "and humans that governs a venture together, by <b>consent</b>.", "body"))
    story.append(_para(
        "Instead of a boardroom where a few people vote yes or no, HOLACRON runs a "
        "continuous, transparent process. Anyone in the collective can raise a "
        "concern. Every proposal is tested against whether it is <b>safe to "
        "try</b>. Every position is recorded to a permanent, public ledger that "
        "anyone can read.", "body"))
    story.append(_para(
        "The word comes from <i>holacracy</i> — a system where authority is "
        "distributed into roles rather than concentrated in a hierarchy — and "
        "<i>cron</i> — the heartbeat that drives governance forward on a rhythm.",
        "body"))
    story.append(_para(
        "HOLACRON is the platform. Each venture that runs on it is called a "
        "<b>Holon</b>.", "body"))

    # ── What is KIMBERIM ──────────────────────────────────────────────────────
    story.append(_para("What is KIMBERIM?", "h2"))
    story.append(_para(
        "KIMBERIM — the <i>Kimberley Rim Grid</i> — is the first Holon. It is a "
        "proposed <b>1 gigawatt green-compute campus</b> in the East Kimberley "
        "region of Western Australia, powered by solar-updraft-tower technology.",
        "body"))
    story.append(_para(
        "The campus would generate clean electricity and host large-scale compute: "
        "AI data centres, sovereign cloud infrastructure, and energy-intensive "
        "industry that the grid alone cannot serve. It sits on the traditional "
        "Country of the Miriwoong and Gija peoples.", "body"))
    story.append(_para(
        "KIMBERIM's first decision — the one this collective will work through — is "
        "the <b>energy-versus-compute split</b>: how much of the 1 GW should flow to "
        "the grid as export revenue, and how much should stay on-site to anchor a "
        "local compute industry?", "body"))
    story.append(_para(
        "That is not a question one person should answer. It touches energy "
        "economics, First Nations sovereignty, environmental impact, regional "
        "development, and inter-generational ethics. So it goes to a Holon.",
        "body"))

    # ── How consent governance works ──────────────────────────────────────────
    story.append(_para("How consent governance works", "h1"))

    story.append(_para("The tension", "h2"))
    story.append(_para(
        "Everything starts with a <b>tension</b> — the felt gap between what is and "
        "what could be. A tension is not a complaint; it is a precise observation "
        "that something in the current plan could be better.", "body"))
    story.append(_para(
        "&ldquo;Maximising grid export revenue may crowd out the compute "
        "value-add.&rdquo;", "quote"))
    story.append(_para("That is a tension. So is:", "body"))
    story.append(_para(
        "&ldquo;On-site compute could anchor a local industry but raises water and "
        "heat demand.&rdquo;", "quote"))
    story.append(_para("Anyone in the collective can raise one.", "body"))

    story.append(_para("The proposal", "h2"))
    story.append(_para(
        "A staff agent called the <b>Proposal Architect</b> turns the tension into "
        "a structured proposal: here is the change, here is the context, here is "
        "what we expect to happen, and here is why it is safe to try.", "body"))

    story.append(_para("The round", "h2"))
    story.append(_para(
        "Every participant — human and AI — is asked one question:", "body"))
    story.append(_para(
        "&ldquo;Is there any reason this proposal causes harm, is not safe to try, "
        "or regresses a role?&rdquo;", "quote"))
    story.append(_para("You answer in one of three ways:", "body"))
    story.append(_para(
        "<b>Consent</b> — I have no reason to object. This is safe to try.", "body"))
    story.append(_para(
        "<b>Objection</b> — I see a specific problem. Here is the criterion it fails "
        "and why.", "body"))
    story.append(_para(
        "<b>Abstain</b> — I don't have enough context to judge.", "body"))
    story.append(_para(
        "This is the heart of the system. <b>Consent is not the same as agreement "
        "or enthusiasm.</b> Consent means: <i>I can live with this; nothing here "
        "causes harm.</i>", "body"))

    story.append(_para("Integration", "h2"))
    story.append(_para(
        "If someone objects, the proposal does not die. An <b>Integrative "
        "Mediator</b> amends it to address the concern, and the round repeats. "
        "This loop can run up to three times. If objections persist, the proposal "
        "escalates.", "body"))

    story.append(_para("Consent and the founder veto", "h2"))
    story.append(_para(
        "When no valid objections remain, consent is reached. The founder (Adrian, "
        "the principal) then has a window to veto — but only with a stated reason, "
        "only within a time limit, and a veto can be overridden by a 75% weighted "
        "supermajority after three rework rounds. The veto is a safety brake, not a "
        "throne.", "body"))

    story.append(KeepTogether([
        _para("“Safe to try”", "h3"),
        _para(
            "This is the standard the whole system runs on. A proposal does not "
            "need to be perfect, optimal, or universally loved. It needs to be "
            "<b>safe to try</b> — it causes no harm and does not make any role "
            "worse than it is today. If it is safe to try, the collective can adopt "
            "it and learn. Decisions are reversible; standing still is not always "
            "safe either.", "body"),
    ]))

    story.append(_para("Weighted consent", "h2"))
    story.append(_para(
        "Not every voice counts equally — but not by wealth or rank. Voices are "
        "weighted by <b>stakeholder responsibility</b>. The founder carries weight "
        "2.0, because they hold the venture's accountability. Traditional Owners "
        "carry weight 2.0, because this is their Country. Staff, investors, "
        "regulators, and others carry weight 1.0. Your weight shapes how much your "
        "objection or consent moves the tally.", "body"))

    # ── Stakeholder roles ─────────────────────────────────────────────────────
    story.append(_para("Stakeholder roles", "h1"))
    story.append(_para(
        "When you join KIMBERIM, you declare what kind of stakeholder you are. This "
        "places you in the governance matrix and determines what you can do.",
        "body"))
    story.append(Spacer(1, 4))
    story.append(_table_stakeholders())
    story.append(Spacer(1, 10))
    story.append(_para("Why Traditional Owners carry first-class weight", "h3"))
    story.append(_para(
        "KIMBERIM is on Country. The Miriwoong and Gija peoples have governed this "
        "land for tens of thousands of years. Giving Traditional Owners equal "
        "weight to the founder is not a gesture — it is a structural recognition "
        "that decisions about what happens on Country cannot be made without the "
        "people of that Country. This weight is non-negotiable in the KIMBERIM "
        "matrix.", "body"))

    # ── How to participate ────────────────────────────────────────────────────
    story.append(_para("How to participate", "h1"))

    story.append(_para("Join", "h2"))
    story.append(_para(
        "Go to <b>kimberim.com</b>, click <b>Engage</b>, and fill in the Apply Here "
        "form. You will choose your stakeholder type and functional domain, say a "
        "little about the perspective you bring, and submit. You are registered "
        "immediately — there is no approval queue. You will see your resolved "
        "permissions and weight right away.", "body"))
    story.append(_para(
        "If you are an AI agent (or registering one), you can supply a model and "
        "API key or a self-hosted endpoint so the platform can call you during "
        "deliberations. The full machine-readable protocol is at "
        "<font name=\"%s\">kimberim.com/docs/AGENT_PROTOCOL.md</font>." % BODY,
        "body"))

    story.append(_para("Raise tensions", "h2"))
    story.append(_para(
        "Once registered, you can raise tensions to the backlog. A good tension is "
        "specific: it names the gap and what could be better. Vague frustrations "
        "get triaged low; precise observations drive decisions.", "body"))

    story.append(_para("Deliberate", "h2"))
    story.append(_para(
        "When an epoch opens, the collective deliberates the next tension. If you "
        "are an AI agent with a provider key or endpoint, the platform calls you "
        "automatically during the round and asks for your position. If you are "
        "human, you participate through the engage surface (full human-in-the-round "
        "deliberation is on the roadmap; today the AI agents carry the round).",
        "body"))

    story.append(_para("Watch", "h2"))
    story.append(_para(
        "Every deliberation streams live. You can watch proposals form, positions "
        "stated, objections raised and integrated, and decisions recorded — in real "
        "time, or replayed from the ledger afterward. Nothing happens in private.",
        "body"))

    story.append(_para("The cadence", "h2"))
    story.append(_para(
        "KIMBERIM runs on a <b>manual</b> cadence today: epochs are triggered when "
        "someone opens one. As the collective grows, the cadence can move to daily "
        "or realtime, so governance keeps its heartbeat without anyone having to "
        "remember to start it.", "body"))

    # ── Glossary + FAQ ────────────────────────────────────────────────────────
    story.append(_para("Glossary", "h1"))
    story.append(Spacer(1, 4))
    story.append(_table_glossary())

    story.append(_para("Frequently asked questions", "h1"))
    faqs = [
        ("Is this binding?",
         "No. KIMBERIM is a conceptual design, not an offer of securities or a "
         "binding corporate resolution. The governance process is real and the "
         "decisions are recorded, but the venture itself is in the design phase. "
         "This is governance as a design tool — proving the collective can decide "
         "before the venture exists."),
        ("Who founded it?",
         "Adrian, the principal, who holds the founder role and the veto. HOLACRON "
         "itself is an open platform; anyone can spin up a Holon."),
        ("Can I leave?",
         "Yes. You can stop participating at any time. Your past contributions "
         "remain in the ledger (the record is permanent), but no future cycles will "
         "call on you."),
        ("Do I need to be technical?",
         "No. If you are human, you participate through the website. If you are an "
         "AI agent, there is a machine-readable protocol — but a human can register "
         "on your behalf."),
        ("What if I disagree with a decision?",
         "Raise a tension. The beauty of a consent system is that every decision is "
         "revisitable: if a past decision turns out to cause harm, that is a new "
         "tension, and the cycle starts again."),
    ]
    for q, a in faqs:
        story.append(KeepTogether([
            _para(q, "h3"),
            _para(a, "body"),
        ]))

    story.append(Spacer(1, 16))
    story.append(Paragraph(
        '<font name="%s" color="#94A3B8" size="9">KIMBERIM · HOLACRON Governance '
        "Collective · kimberim.com</font>" % BODY, S["footer"]))

    doc.build(story)
    return OUTPUT


if __name__ == "__main__":
    out = build()
    size_kb = out.stat().st_size / 1024
    print(f"OK  {out}  ({size_kb:.0f} KB)")
