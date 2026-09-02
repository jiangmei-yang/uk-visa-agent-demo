from __future__ import annotations

import hashlib
import html
from datetime import date
from pathlib import Path

from visa_agent.domain.models import Case, GateResult


def esc(value: object) -> str:
    return html.escape(str(value))


def humanise(value: object) -> str:
    return str(value).replace("_", " ").strip().title()


def format_date(value: date | None) -> str:
    return "Not confirmed" if value is None else f"{value.day} {value.strftime('%b %Y')}"


def badge(label: str, tone: str = "neutral") -> str:
    return f'<span class="badge badge--{tone}"><i aria-hidden="true"></i>{esc(label)}</span>'


GATE_LABELS = {
    "route_in_scope": "Supported visitor route",
    "applicant_age_at_least_18": "Adult applicant",
    "profile_confirmed": "Applicant details confirmed",
    "all_blocker_requirements_resolved": "Required documents present",
    "no_unresolved_blocker_issue": "No unresolved contradictions",
    "every_critical_fact_has_provenance": "Key facts linked to evidence",
    "applicant_explicitly_confirmed_final_summary": "Final summary confirmed",
    "policy_snapshot_is_current": "Policy snapshot current",
}

DOCUMENT_LABELS = {
    "passport": "Passport",
    "conference_invitation": "Conference invitation",
    "student_letter": "Student status letter",
    "funding_letter": "School funding letter",
    "bank_statement": "Bank statement",
    "legal_residence_evidence": "Hong Kong residence evidence",
    "other_supporting_document": "Original family funds document",
    "certified_translation": "Certified translation",
}


def _pack_meta(case: Case) -> tuple[str, str]:
    if not case.delivery_path:
        return "Not generated", "Not generated"
    path = Path(case.delivery_path)
    if not path.is_file():
        return "Unavailable", "Unavailable"
    return (
        f"{path.stat().st_size / 1024:.0f} KB",
        f"{hashlib.sha256(path.read_bytes()).hexdigest()[:12]}…",
    )


def _gate_audit(gate: GateResult) -> str:
    return "".join(
        f'<li><span class="audit-dot {"pass" if passed else "fail"}" aria-hidden="true">'
        f"{'&check;' if passed else '!'}</span><span>{esc(GATE_LABELS.get(key, humanise(key)))}</span>"
        f"<strong>{'Passed' if passed else 'Blocked'}</strong></li>"
        for key, passed in gate.checks.items()
    )


def _evidence_audit(case: Case) -> str:
    return "".join(
        f"<tr><td>{esc(humanise(item.fact_key))}</td><td>{esc(item.value)}</td>"
        f"<td><code>{esc(item.source_document_id or item.source_event_id)}</code></td>"
        f"<td>{esc(item.page or 'Email')}</td><td>{item.confidence:.2f}</td></tr>"
        for item in case.evidence
        if not item.superseded
    )


def _document_rows(case: Case, technical: bool = False) -> str:
    rows = []
    for doc in case.documents:
        if technical:
            rows.append(
                f"<tr><td>{esc(doc.filename)}</td><td>{esc(doc.status.value)}</td>"
                f'<td>{esc(doc.language)}</td><td><code title="{esc(doc.sha256)}">{esc(doc.sha256[:16])}…</code></td></tr>'
            )
            continue
        pages = f"{doc.page_count} page" + ("s" if doc.page_count != 1 else "")
        if doc.status.value == "SUPERSEDED":
            state, tone, note = "Superseded", "neutral", "Original retained in audit trail"
        elif doc.status.value == "NEEDS_CERTIFIED_TRANSLATION":
            state, tone, note = "Original", "neutral", "Certified translation linked separately"
        else:
            state, tone, note = "Active", "success", pages
        rows.append(
            f"<tr><td><strong>{esc(DOCUMENT_LABELS.get(doc.kind, humanise(doc.kind)))}</strong>"
            f"<small>{esc(doc.filename)}</small></td><td>{badge(state, tone)}</td><td>{esc(note)}</td></tr>"
        )
    return "".join(rows)


def _corrections(case: Case) -> str:
    return "".join(
        f'<li><span class="check" aria-hidden="true">&check;</span><div><strong>{esc(issue.title)}</strong>'
        f"<p>{esc(issue.resolution or issue.detail)}</p></div>{badge('Resolved', 'success')}</li>"
        for issue in case.issues
        if issue.status.value == "RESOLVED"
    )


def _requirements(case: Case) -> str:
    return "".join(
        f'<li><span class="check" aria-hidden="true">&check;</span><span>{esc(item.title)}</span><strong>Complete</strong></li>'
        for item in case.requirements
        if item.applicable
    )


def _walkthrough() -> str:
    return (
        """
    <section class="walkthrough" id="walkthrough" aria-labelledby="walkthrough-title">
      <div class="section-heading"><div><p class="kicker">Start here · two-minute walkthrough</p>
      <h2 id="walkthrough-title">From first submission to review pack</h2></div>
      <p>Select each email to see what the client sent, what the service decided, and why delivery stopped or continued.</p></div>
      <div class="tabs" role="tablist" aria-label="Three-email case walkthrough">
        <button id="tab-intake" role="tab" aria-selected="true" aria-controls="panel-intake" data-panel="intake"><b>1</b><span><strong>Initial email</strong><small>Paused safely</small></span></button>
        <button id="tab-correction" role="tab" aria-selected="false" aria-controls="panel-correction" data-panel="correction" tabindex="-1"><b>2</b><span><strong>Corrections</strong><small>Awaiting confirmation</small></span></button>
        <button id="tab-confirmation" role="tab" aria-selected="false" aria-controls="panel-confirmation" data-panel="confirmation" tabindex="-1"><b>3</b><span><strong>Confirmation</strong><small>Pack released</small></span></button>
      </div>
      <div id="panel-intake" class="panel" role="tabpanel" aria-labelledby="tab-intake">
        <article class="email"><header><span>LC</span><div><strong>Lin Chen</strong><small>To: Visa preparation · 1 Sep, 09:00</small></div></header><h3>Standard Visitor documents for London conference</h3>
        <blockquote>“My university will pay for the flight and hotel, and I will pay my personal expenses.”</blockquote><p class="attachments"><strong>7 attachments</strong><span>Passport.pdf</span><span>Invitation_original.pdf</span><span>+5 more</span></p></article>
        <article class="decision warning"><header><div><p class="kicker">Service decision</p><h3>Pack paused — two corrections required</h3></div>"""
        + badge("Withheld", "warning")
        + """</header>
        <ul class="blockers"><li><strong>Date conflict</strong><span>Invitation ended after the stated trip.</span></li><li><strong>Translation missing</strong><span>A Chinese supporting page had no certified translation.</span></li></ul>
        <div class="reply"><strong>Service response:</strong><p>“I cannot prepare the review pack yet. Please resolve: Travel and invitation dates differ; Certified translation required.”</p></div><span class="button disabled">Pack unavailable at this step</span></article>
      </div>
      <div id="panel-correction" class="panel" role="tabpanel" aria-labelledby="tab-correction" hidden>
        <article class="email"><header><span>LC</span><div><strong>Lin Chen</strong><small>To: Visa preparation · 1 Sep, 11:00</small></div></header><h3>Re: Standard Visitor documents for London conference</h3>
        <blockquote>“The organiser corrected the invitation… I have also attached a complete certified translation.”</blockquote><p class="attachments"><strong>2 replacements</strong><span>Invitation_corrected.pdf</span><span>Certified_translation.pdf</span></p></article>
        <article class="decision"><header><div><p class="kicker">Service decision</p><h3>Documents clear — confirmation still required</h3></div>"""
        + badge("Waiting")
        + """</header>
        <div class="diff"><div><small>Before</small><strong>Event ends 18 Sep</strong><span>Outside the trip</span></div><b aria-hidden="true">→</b><div><small>After</small><strong>Event ends 14 Sep</strong><span>Within the trip</span></div></div>
        <div class="reply"><strong>Service response:</strong><p>“The checks no longer show a document blocker. Please review the final facts summary and reply with the exact confirmation requested.”</p></div><span class="button disabled">Pack unavailable at this step</span></article>
      </div>
      <div id="panel-confirmation" class="panel" role="tabpanel" aria-labelledby="tab-confirmation" hidden>
        <article class="email"><header><span>LC</span><div><strong>Lin Chen</strong><small>To: Visa preparation · 1 Sep, 13:00</small></div></header><h3>Re: Standard Visitor documents for London conference</h3>
        <blockquote>“I reviewed the final facts summary and the listed source documents. I CONFIRM THE FINAL SUMMARY.”</blockquote><p class="attachments"><strong>No attachments</strong> Explicit confirmation recorded</p></article>
        <article class="decision success"><header><div><p class="kicker">Service decision</p><h3>All eight checks passed — pack released</h3></div>"""
        + badge("Released", "success")
        + """</header>
        <ul class="proof-list"><li><b>8/8</b> delivery checks</li><li><b>0</b> open blockers</li><li><b>1</b> human confirmation</li></ul>
        <div class="reply"><strong>Service response:</strong><p>“Your review pack has been prepared for human review. This is not an approval prediction.”</p></div><a class="text-link" href="#pack">See the released pack and proof ↓</a></article>
      </div>
      <noscript><p class="notice">Enable JavaScript to switch between the three recorded emails. The initial safe stop remains visible.</p></noscript>
    </section>"""
    )


def render_case(case: Case, gate: GateResult) -> str:
    profile = case.profile
    ready = gate.allowed and bool(case.delivery_path)
    active_docs = sum(doc.status.value != "SUPERSEDED" for doc in case.documents)
    superseded = len(case.documents) - active_docs
    active_evidence = sum(not item.superseded for item in case.evidence)
    pack_size, pack_hash = _pack_meta(case)
    download = (
        f'<a class="button primary" data-download href="/api/cases/{esc(case.id)}/pack">Download verified pack</a>'
        if ready
        else '<span class="button disabled" aria-disabled="true">Pack withheld by safety checks</span>'
    )
    outcome = (
        "The application pack is ready for adviser review"
        if ready
        else "Delivery is paused until every safety check passes"
    )
    gate_count = sum(gate.checks.values())
    manifest = "".join(
        f'<li><span aria-hidden="true">&check;</span>{label}</li>'
        for label in (
            "Read-me and limitations",
            "Case summary",
            "Personalised checklist",
            "Document index",
            "Cover-letter draft",
            "Structured answers",
            "Open-issues report",
        )
    )
    return f"""
    <a class="skip-link" href="#main-content">Skip to case</a>
    <header class="topbar"><a class="brand" href="/"><i aria-hidden="true">VP</i>Visa preparation Demo</a><span>Synthetic case · no legal advice</span></header>
    <main id="main-content">
      <section class="hero" aria-labelledby="case-title"><div><p class="kicker">Featured assessment case</p><div class="title"><h1 id="case-title">{esc(profile.full_name or case.id)}</h1>{badge("Ready for human adviser review", "success") if ready else badge("Action required", "warning")}</div>
      <p>Standard Visitor · {esc(humanise(profile.visit_purpose or "visit"))} · applying from {esc(profile.application_country or "Unknown")}</p></div><div class="hero-action">{download}<small id="download-status" aria-live="polite"></small></div></section>
      <section class="outcome" aria-labelledby="outcome-title"><div><p class="kicker">Current outcome</p><h2 id="outcome-title">{outcome}</h2><p>The service stopped on two evidence problems, accepted corrected files, then waited for Lin’s exact confirmation before releasing anything.</p></div>
      <dl class="proof-strip"><div><dt>{gate_count}/8</dt><dd>checks passed</dd></div><div><dt>{active_evidence}</dt><dd>source-linked facts</dd></div><div><dt>{active_docs}+{superseded}</dt><dd>active + superseded</dd></div><div><dt>Stable</dt><dd>duplicate replay</dd></div></dl></section>
      {_walkthrough()}
      <section class="pack-section" id="pack" aria-labelledby="pack-title"><div class="section-heading"><div><p class="kicker">Final delivery</p><h2 id="pack-title">What the adviser receives</h2></div><p>Generated only from confirmed, source-linked case data. It organises evidence; it does not submit or decide the application.</p></div>
      <div class="pack-layout"><div class="pack-card"><b>ZIP</b><div><h3>Application review pack</h3><p>{pack_size} · SHA-256 <code>{pack_hash}</code></p></div>{download}</div><ul class="manifest">{manifest}<li><span aria-hidden="true">&check;</span>{active_docs} active supporting documents</li></ul></div></section>
      <section class="details" aria-labelledby="details-title"><div class="section-heading"><div><p class="kicker">Prepared case</p><h2 id="details-title">Corrections, trip facts, and files</h2></div><p>The original replaced evidence stays visible for traceability.</p></div>
      <div class="details-grid"><div><h3>Corrections handled</h3><ul class="corrections">{_corrections(case)}</ul></div><aside class="snapshot"><h3>Confirmed trip</h3><dl>
      <div><dt>Travel dates</dt><dd>{format_date(profile.planned_arrival_date)} – {format_date(profile.planned_departure_date)}</dd></div><div><dt>Purpose</dt><dd>{esc(humanise(profile.visit_purpose or "Not confirmed"))}</dd></div><div><dt>Accommodation</dt><dd>{esc(profile.uk_accommodation or "Not confirmed")}</dd></div><div><dt>Estimated cost</dt><dd>£{profile.estimated_trip_cost_gbp or 0:,.0f}</dd></div><div><dt>Funding</dt><dd>{esc(humanise(profile.funding_source or "Not confirmed"))}</dd></div></dl></aside></div>
      <div class="checklist"><h3>Application checklist</h3><ul>{_requirements(case)}</ul></div><div class="documents"><div class="subheading"><h3>Document register</h3><span>{active_docs} active · {superseded} superseded</span></div><div class="table-wrap" role="region" aria-label="Document register; scroll horizontally for all columns" tabindex="0"><table class="doc-table"><thead><tr><th>Document</th><th>Status</th><th>Note</th></tr></thead><tbody>{_document_rows(case)}</tbody></table></div></div></section>
      <section class="audit" id="audit"><details><summary><span><span class="kicker">Engineering evidence</span><strong>Inspect why delivery was allowed</strong></span><b aria-hidden="true">⌄</b></summary><div class="audit-body"><p>Eight deterministic checks—not model confidence—control delivery. Synthetic data, policy, provenance, lifecycle, and hashes are preserved here.</p><dl class="audit-meta"><div><dt>Case ID</dt><dd><code>{esc(case.id)}</code></dd></div><div><dt>Policy</dt><dd>{esc(case.policy_version)}</dd></div><div><dt>Workflow</dt><dd>{esc(humanise(case.stage.value))}</dd></div></dl>
      <div class="audit-grid"><div class="gate"><h3>Delivery gate</h3><ul>{_gate_audit(gate)}</ul></div><div class="audit-panel"><h3>Active evidence ledger</h3><div class="table-wrap" role="region" aria-label="Evidence ledger; scroll horizontally for all columns" tabindex="0"><table><thead><tr><th>Fact</th><th>Value</th><th>Source</th><th>Page</th><th>Confidence</th></tr></thead><tbody>{_evidence_audit(case)}</tbody></table></div></div></div><div class="audit-panel"><h3>Technical document register</h3><div class="table-wrap" role="region" aria-label="Technical document register; scroll horizontally for all columns" tabindex="0"><table><thead><tr><th>File</th><th>Lifecycle</th><th>Language</th><th>SHA-256</th></tr></thead><tbody>{_document_rows(case, True)}</tbody></table></div></div></div></details></section>
      <footer>This synthetic service prepares materials for a human adviser. It does not give legal advice, decide eligibility, submit an application, or predict an outcome.</footer>
    </main>"""


BASE_CSS = """
:root{color-scheme:light;--canvas:oklch(.975 .006 252);--paper:#fff;--ink:oklch(.22 .025 252);--muted:oklch(.43 .025 252);--quiet:oklch(.94 .012 252);--line:oklch(.86 .014 252);--primary:oklch(.4 .13 252);--primary-dark:oklch(.32 .11 252);--primary-soft:oklch(.955 .028 252);--success:oklch(.39 .105 155);--success-soft:oklch(.95 .04 155);--warning:oklch(.46 .12 72);--warning-soft:oklch(.955 .045 72);--danger:oklch(.46 .18 26);--focus:oklch(.62 .17 252);--sans:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}*{box-sizing:border-box}html{background:var(--paper);scroll-behavior:smooth}body{margin:0;color:var(--ink);font:16px/1.55 var(--sans)}a{color:inherit}button{font:inherit}a:focus-visible,button:focus-visible,summary:focus-visible,[tabindex="0"]:focus-visible{outline:3px solid var(--focus);outline-offset:3px}.skip-link{position:fixed;z-index:9;top:10px;left:10px;padding:10px 14px;color:#fff;background:var(--primary-dark);transform:translateY(-160%)}.skip-link:focus{transform:none}.topbar{height:64px;display:flex;align-items:center;justify-content:space-between;padding:0 max(24px,calc((100vw - 1180px)/2));border-bottom:1px solid var(--line)}.topbar>span{color:var(--muted);font-size:13px}.brand{display:flex;align-items:center;gap:10px;text-decoration:none;font-weight:780}.brand i{width:32px;height:32px;display:grid;place-items:center;border-radius:8px;color:#fff;background:var(--primary-dark);font-size:12px;font-style:normal}main{max-width:1180px;margin:auto;padding:38px 24px 64px}.kicker{margin:0 0 5px;color:var(--muted);font-size:13px;font-weight:720}.hero{display:flex;align-items:end;justify-content:space-between;gap:28px}.hero>div>p:last-child{margin:8px 0 0;color:var(--muted)}.title{display:flex;flex-wrap:wrap;align-items:center;gap:12px}h1{margin:0;font-size:32px;line-height:1.18;letter-spacing:-.025em}h2{margin:0;font-size:24px;line-height:1.25;letter-spacing:-.02em;text-wrap:balance}h3{margin:0;font-size:16px}.hero-action{display:grid;justify-items:end}.hero-action small{min-height:19px;color:var(--success)}.button{min-height:46px;display:inline-flex;align-items:center;justify-content:center;padding:10px 17px;border-radius:9px;font-weight:740;text-decoration:none}.primary{color:#fff;background:var(--primary-dark)}.primary:hover{background:var(--primary)}.disabled{color:var(--muted);background:var(--quiet)}.badge{display:inline-flex;align-items:center;gap:7px;width:fit-content;padding:5px 9px;border-radius:999px;background:var(--quiet);font-size:12px;font-weight:740;white-space:nowrap}.badge i{width:7px;height:7px;border-radius:50%;background:currentColor}.badge--success{color:var(--success);background:var(--success-soft)}.badge--warning{color:var(--warning);background:var(--warning-soft)}.outcome{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:34px;margin-top:26px;padding:24px 26px;border:1px solid color-mix(in oklch,var(--success) 28%,transparent);background:var(--success-soft)}.outcome>div>p:last-child{max-width:70ch;margin:7px 0 0;color:var(--muted)}.proof-strip{display:grid;grid-template-columns:repeat(4,minmax(90px,1fr));margin:0}.proof-strip div{padding:0 15px;border-left:1px solid var(--line)}.proof-strip dt{font-size:18px;font-weight:780}.proof-strip dd{margin:1px 0 0;color:var(--muted);font-size:12px}.walkthrough,.pack-section,.details,.audit{padding:48px 0;border-bottom:1px solid var(--line)}.section-heading{display:flex;align-items:end;justify-content:space-between;gap:32px;margin-bottom:22px}.section-heading>p{max-width:56ch;margin:0;color:var(--muted);font-size:14px}.tabs{display:grid;grid-template-columns:repeat(3,1fr);border:1px solid var(--line);border-radius:12px 12px 0 0;overflow:hidden}.tabs button{min-height:74px;display:flex;align-items:center;gap:12px;padding:12px 16px;border:0;border-right:1px solid var(--line);color:var(--muted);background:var(--canvas);text-align:left;cursor:pointer}.tabs button:last-child{border-right:0}.tabs button[aria-selected="true"]{color:var(--primary-dark);background:var(--paper);box-shadow:inset 0 -3px var(--primary)}.tabs b{width:30px;height:30px;display:grid;place-items:center;flex:0 0 auto;border:1px solid;border-radius:50%}.tabs strong,.tabs small{display:block}.tabs small{font-size:12px}.panel{display:grid;grid-template-columns:minmax(0,.9fr) minmax(0,1.1fr);min-height:390px;border:1px solid var(--line);border-top:0;border-radius:0 0 12px 12px}.email,.decision{min-width:0;padding:28px}.email{border-right:1px solid var(--line);background:var(--canvas)}.email header{display:flex;align-items:center;gap:11px}.email header>span{width:38px;height:38px;display:grid;place-items:center;border-radius:50%;color:var(--primary-dark);background:var(--primary-soft);font-size:12px;font-weight:780}.email header strong,.email header small{display:block}.email header small{color:var(--muted)}.email h3{margin-top:22px}.email blockquote{margin:16px 0;padding-left:16px;box-shadow:inset 2px 0 var(--line);color:var(--muted)}.attachments{display:flex;flex-wrap:wrap;gap:7px;margin-top:22px}.attachments strong{width:100%;color:var(--muted);font-size:13px}.attachments span{padding:6px 9px;border:1px solid var(--line);border-radius:7px;background:#fff;font-size:12px}.decision{display:flex;flex-direction:column}.decision.warning{background:var(--warning-soft)}.decision.success{background:var(--success-soft)}.decision>header{display:flex;align-items:start;justify-content:space-between;gap:16px}.blockers,.proof-list{list-style:none;margin:20px 0 0;padding:0}.blockers li{display:grid;padding:12px 0;border-top:1px solid color-mix(in oklch,var(--warning) 25%,transparent)}.blockers span{color:var(--muted)}.reply{margin:20px 0;padding:14px 16px;border-radius:8px;background:color-mix(in oklch,#fff 72%,transparent)}.reply p{margin:4px 0 0;color:var(--muted)}.decision>.button,.decision>.text-link{align-self:flex-start;margin-top:auto}.diff{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:12px;margin-top:24px}.diff>div{display:grid;padding:14px;border-radius:8px;background:var(--canvas)}.diff small,.diff span{color:var(--muted)}.proof-list{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.proof-list li{padding:13px;border-radius:8px;background:color-mix(in oklch,#fff 75%,transparent);font-size:13px}.proof-list b{display:block;color:var(--success);font-size:22px}.text-link{color:var(--primary-dark);font-weight:740}.pack-layout{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(300px,.95fr);gap:34px}.pack-card{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:16px;padding:22px;border:1px solid var(--line);border-radius:12px}.pack-card>b{width:52px;height:60px;display:grid;place-items:center;border-radius:7px;color:#fff;background:var(--primary-dark);font-size:12px}.pack-card p{margin:4px 0 0;color:var(--muted);font-size:13px}.manifest{list-style:none;margin:0;padding:0;columns:2}.manifest li{break-inside:avoid;padding:7px 0;font-size:14px}.manifest span,.check{display:inline-grid;place-items:center;width:20px;height:20px;margin-right:8px;border-radius:50%;color:#fff;background:var(--success);font-size:10px}.details-grid{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(280px,.75fr);gap:42px}.corrections,.checklist ul,.gate ul{list-style:none;margin:12px 0 0;padding:0}.corrections li{display:grid;grid-template-columns:28px minmax(0,1fr) auto;gap:10px;padding:14px 0;border-top:1px solid var(--line)}.corrections p{margin:3px 0 0;color:var(--muted)}.snapshot{padding:20px;border-radius:10px;background:var(--canvas)}.snapshot dl{margin:12px 0 0}.snapshot dl div{display:grid;grid-template-columns:105px 1fr;gap:12px;padding:9px 0;border-top:1px solid var(--line)}.snapshot dt{color:var(--muted)}.snapshot dd{margin:0;font-weight:650}.checklist,.documents{margin-top:34px}.checklist ul{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));border-top:1px solid var(--line)}.checklist li{display:grid;grid-template-columns:28px minmax(0,1fr) auto;align-items:center;min-height:50px;border-bottom:1px solid var(--line)}.checklist li:nth-child(odd){margin-right:24px}.checklist strong{color:var(--success);font-size:12px}.subheading{display:flex;justify-content:space-between;margin-bottom:12px}.subheading span{color:var(--muted)}.table-wrap{width:100%;max-width:100%;overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:13px}th{padding:10px 12px;color:var(--muted);background:var(--canvas);text-align:left;font-size:12px}td{padding:12px;border-bottom:1px solid var(--line);vertical-align:top}.doc-table td:first-child strong,.doc-table td:first-child small{display:block}.doc-table small{color:var(--muted)}code{font:.9em/1.4 var(--mono);overflow-wrap:anywhere}.audit{border-bottom:0}details{min-width:0;border:1px solid var(--line);border-radius:12px;background:var(--canvas)}summary{min-height:72px;display:flex;align-items:center;justify-content:space-between;padding:16px 20px;cursor:pointer;list-style:none}summary::-webkit-details-marker{display:none}summary>b{font-size:24px;transition:transform 180ms}details[open] summary>b{transform:rotate(180deg)}.audit-body{min-width:0;padding:22px;border-top:1px solid var(--line)}.audit-body>p{max-width:78ch;margin:0;color:var(--muted)}.audit-meta{display:flex;flex-wrap:wrap;gap:20px 36px;margin:18px 0}.audit-meta dt{color:var(--muted);font-size:12px}.audit-meta dd{margin:2px 0 0}.audit-grid{min-width:0;display:grid;grid-template-columns:minmax(260px,.65fr) minmax(0,1.35fr);gap:28px;margin-top:28px}.gate,.audit-panel{min-width:0}.audit-panel{margin-top:26px}.audit-grid .audit-panel{margin-top:0}.gate li{min-width:0;display:grid;grid-template-columns:22px minmax(0,1fr) auto;align-items:center;gap:9px;min-height:42px;border-bottom:1px solid var(--line)}.gate li>strong{color:var(--success);font-size:12px}.audit-dot{width:20px;height:20px;display:grid;place-items:center;border-radius:50%;color:#fff;font-size:10px}.audit-dot.pass{background:var(--success)}.audit-dot.fail{background:var(--danger)}footer{padding-top:24px;color:var(--muted);font-size:13px}
@media(max-width:980px){.outcome{grid-template-columns:1fr}.panel{grid-template-columns:1fr}.email{border-right:0;border-bottom:1px solid var(--line)}.pack-layout,.details-grid,.audit-grid{grid-template-columns:1fr}.pack-card{grid-template-columns:auto minmax(0,1fr)}.pack-card>.button{grid-column:1/-1;width:100%}}
@media(max-width:680px){.topbar{height:60px;padding:0 18px}.topbar>span{display:none}main{padding:28px 18px 46px}.hero,.section-heading{align-items:start;flex-direction:column}.hero-action,.hero-action .button{width:100%}.hero-action{justify-items:stretch}.title{align-items:start;flex-direction:column}h1{font-size:28px}h2{font-size:22px}.outcome{padding:20px}.proof-strip{grid-template-columns:repeat(2,1fr);gap:16px}.proof-strip div{padding:0;border:0}.walkthrough,.pack-section,.details,.audit{padding:38px 0}.tabs{grid-template-columns:1fr}.tabs button{min-height:62px;border-right:0;border-bottom:1px solid var(--line)}.tabs button[aria-selected="true"]{box-shadow:inset 4px 0 var(--primary)}.panel{min-height:0}.email,.decision{padding:22px 18px}.decision>header{flex-direction:column}.diff{grid-template-columns:1fr}.diff>b{transform:rotate(90deg);text-align:center}.proof-list{grid-template-columns:1fr}.pack-card{padding:18px}.manifest{columns:1}.checklist ul{grid-template-columns:1fr}.checklist li:nth-child(odd){margin-right:0}.corrections li{grid-template-columns:28px minmax(0,1fr)}.corrections .badge{grid-column:2}.snapshot dl div{grid-template-columns:1fr}.subheading{flex-direction:column}.audit-body{padding:18px 14px}.audit-grid{min-width:0}.gate li{grid-template-columns:22px minmax(0,1fr)}.gate li>strong{grid-column:2}.doc-table{min-width:470px}.audit-panel table{min-width:560px}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;transition-duration:.01ms!important}}
"""


INTERACTION_JS = """
const tabs=[...document.querySelectorAll('[role="tab"]')],panels=[...document.querySelectorAll('[role="tabpanel"]')];
function selectTab(tab,focus=false){tabs.forEach(t=>{const active=t===tab;t.setAttribute('aria-selected',String(active));t.tabIndex=active?0:-1});panels.forEach(p=>p.hidden=p.id!==`panel-${tab.dataset.panel}`);if(focus)tab.focus()}
tabs.forEach((tab,index)=>{tab.addEventListener('click',()=>selectTab(tab));tab.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();let next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;selectTab(tabs[next],true)})});
document.querySelectorAll('[data-download]').forEach(link=>link.addEventListener('click',()=>{const status=document.querySelector('#download-status');if(status)status.textContent='Download started — check your Downloads folder.'}));
"""


def page(body: str) -> str:
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Visa preparation Demo</title><style>{BASE_CSS}</style></head><body>{body}<script>{INTERACTION_JS}</script></body></html>'


def render_page(case: Case, gate: GateResult) -> str:
    return page(render_case(case, gate))


def render_empty_page() -> str:
    return page(
        """<a class="skip-link" href="#main-content">Skip to case</a><header class="topbar"><a class="brand" href="/"><i aria-hidden="true">VP</i>Visa preparation Demo</a></header><main id="main-content"><section class="hero"><div><p class="kicker">Assessment mode</p><h1>No demo case is loaded</h1><p>The synthetic case normally prepares itself when the Demo starts.</p></div></section><section class="outcome"><div><p class="kicker">Try this first</p><h2>Refresh in a few seconds</h2><p>If this remains, close the tab and double-click START_DEMO again. It will rebuild the case and reopen this page.</p></div><a class="button primary" href="/">Refresh case</a></section></main>"""
    )
