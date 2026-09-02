from __future__ import annotations

import html
from datetime import date

from visa_agent.domain.models import Case, GateResult


def esc(value: object) -> str:
    return html.escape(str(value))


def humanise(value: object) -> str:
    return str(value).replace("_", " ").strip().title()


def format_date(value: date | None) -> str:
    if value is None:
        return "Not confirmed"
    return f"{value.day} {value.strftime('%b %Y')}"


def badge(label: str, tone: str = "neutral") -> str:
    return f'<span class="badge badge--{tone}"><span aria-hidden="true"></span>{esc(label)}</span>'


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


def _journey(case: Case) -> str:
    ready = case.status.value == "READY_FOR_HUMAN_REVIEW"
    steps = [
        (
            "09:00",
            "Initial documents received",
            "The agent reviewed seven files and paused the case instead of producing a pack.",
            "Asked for a corrected invitation and a certified translation.",
        ),
        (
            "11:00",
            "Corrections received",
            "The replacement invitation matched the trip dates and the translation was attached.",
            "Document blockers cleared; final confirmation still required.",
        ),
        (
            "13:00",
            "Final summary confirmed",
            "Lin confirmed the travel details and funding summary in the email thread.",
            "Application pack created for adviser review."
            if ready
            else "Waiting for final review.",
        ),
    ]
    return "".join(
        f"""
        <li class="journey-step">
          <div class="journey-time">{time}<span>1 Sep</span></div>
          <div class="journey-marker" aria-hidden="true"><span>&check;</span></div>
          <div class="journey-copy"><h3>{esc(title)}</h3><p>{esc(detail)}</p>
          <p class="journey-result"><strong>Service response:</strong> {esc(result)}</p></div>
        </li>
        """
        for time, title, detail, result in steps
    )


def _resolved_issues(case: Case) -> str:
    items = [item for item in case.issues if item.status.value == "RESOLVED"]
    if not items:
        return '<p class="empty-state">No corrections have been recorded for this case.</p>'
    return "".join(
        f"""
        <li class="resolution-row">
          <span class="resolution-check" aria-hidden="true">&check;</span>
          <div><h3>{esc(item.title)}</h3><p>{esc(item.resolution or item.detail)}</p></div>
          {badge("Resolved", "success")}
        </li>
        """
        for item in items
    )


def _requirements(case: Case) -> str:
    return "".join(
        f"""
        <li class="check-row">
          <span class="check-symbol" aria-hidden="true">{"&check;" if item.satisfied else "!"}</span>
          <span>{esc(item.title)}</span>
          <strong>{"Complete" if item.satisfied else "Needed"}</strong>
        </li>
        """
        for item in case.requirements
        if item.applicable
    )


def _documents(case: Case) -> str:
    rows = []
    for doc in case.documents:
        if doc.status.value == "SUPERSEDED":
            label, tone, note = "Replaced", "neutral", "Kept in the audit trail"
        elif doc.status.value == "NEEDS_CERTIFIED_TRANSLATION":
            label, tone, note = "Original", "neutral", "Certified translation received separately"
        else:
            label, tone, note = "Included", "success", f"{doc.page_count} page"
        rows.append(
            f"""
            <tr><td><strong>{esc(DOCUMENT_LABELS.get(doc.kind, humanise(doc.kind)))}</strong>
            <span>{esc(doc.filename)}</span></td><td>{badge(label, tone)}</td><td>{esc(note)}</td></tr>
            """
        )
    return "".join(rows)


def _gate_audit(gate: GateResult) -> str:
    return "".join(
        f"""
        <li><span class="audit-result {"audit-result--pass" if passed else "audit-result--blocked"}"
        aria-hidden="true">{"&check;" if passed else "!"}</span>
        <span>{esc(GATE_LABELS.get(check, humanise(check)))}</span>
        <strong>{"Passed" if passed else "Blocked"}</strong></li>
        """
        for check, passed in gate.checks.items()
    )


def _evidence_audit(case: Case) -> str:
    return "".join(
        "<tr>"
        f"<td>{esc(humanise(item.fact_key))}</td><td>{esc(item.value)}</td>"
        f"<td><code>{esc(item.source_document_id or item.source_event_id)}</code></td>"
        f"<td>{esc(item.page or 'Email')}</td><td>{item.confidence:.2f}</td>"
        "</tr>"
        for item in case.evidence
        if not item.superseded
    )


def _document_audit(case: Case) -> str:
    return "".join(
        "<tr>"
        f"<td>{esc(doc.filename)}</td><td>{esc(doc.status.value)}</td>"
        f'<td>{esc(doc.language)}</td><td><code title="{esc(doc.sha256)}">{esc(doc.sha256[:16])}…</code></td>'
        "</tr>"
        for doc in case.documents
    )


def render_case(case: Case, gate: GateResult) -> str:
    profile = case.profile
    ready = gate.allowed
    active_documents = sum(doc.status.value != "SUPERSEDED" for doc in case.documents)
    resolved_count = sum(item.status.value == "RESOLVED" for item in case.issues)
    download = (
        f'<a class="button button--primary" href="/api/cases/{esc(case.id)}/pack">Download application pack</a>'
        if case.delivery_path
        else '<span class="button button--disabled" aria-disabled="true">Pack not ready</span>'
    )
    outcome_title = (
        "The application pack is ready for adviser review"
        if ready
        else "This case still needs information before review"
    )
    outcome_copy = (
        "The document problems were resolved, the applicant confirmed the final summary, and a traceable review pack was assembled."
        if ready
        else "Preparation is paused. Review the outstanding items below before continuing."
    )
    status_badge = (
        badge("Ready for adviser review", "success") if ready else badge("Action needed", "warning")
    )

    return f"""
    <main id="main-content">
      <header class="case-header">
        <div><p class="breadcrumb">Cases <span>/</span> {esc(profile.full_name or case.id)}</p>
        <div class="title-line"><h1>{esc(profile.full_name or "Unnamed applicant")}</h1>{status_badge}</div>
        <p class="case-subtitle">Standard Visitor · {esc(humanise(profile.visit_purpose or "visit"))} · applying from {esc(profile.application_country or "Unknown")}</p></div>
        <div class="header-actions">{download}</div>
      </header>

      <section class="outcome" aria-labelledby="outcome-title">
        <div class="outcome-copy"><p class="section-label">Current outcome</p><h2 id="outcome-title">{outcome_title}</h2>
        <p>{outcome_copy}</p></div>
        <dl class="outcome-facts">
          <div><dt>Documents</dt><dd>{active_documents} ready</dd></div>
          <div><dt>Corrections</dt><dd>{resolved_count} resolved</dd></div>
          <div><dt>Applicant</dt><dd>{"Confirmed" if case.final_summary_confirmed else "Awaiting reply"}</dd></div>
        </dl>
      </section>

      <nav class="section-nav" aria-label="Case sections">
        <a href="#journey">Email journey</a><a href="#preparation">Preparation</a>
        <a href="#documents">Documents</a><a href="#audit">Audit details</a>
      </nav>

      <section class="workspace-section" id="journey" aria-labelledby="journey-title">
        <div class="section-heading"><div><p class="section-label">Email journey</p>
        <h2 id="journey-title">From first submission to review pack</h2></div>
        <p>Preparation stopped when evidence conflicted, resumed after corrections, and waited for explicit confirmation.</p></div>
        <ol class="journey">{_journey(case)}</ol>
      </section>

      <section class="workspace-section" id="preparation" aria-labelledby="preparation-title">
        <div class="section-heading"><div><p class="section-label">Preparation status</p>
        <h2 id="preparation-title">What changed, and what is now complete</h2></div></div>
        <div class="preparation-layout">
          <div><h3 class="subsection-title">Corrections handled</h3><ul class="resolution-list">{_resolved_issues(case)}</ul></div>
          <aside class="case-snapshot" aria-labelledby="snapshot-title"><h3 id="snapshot-title">Trip snapshot</h3>
            <dl><div><dt>Travel dates</dt><dd>{format_date(profile.planned_arrival_date)} – {format_date(profile.planned_departure_date)}</dd></div>
            <div><dt>Purpose</dt><dd>{esc(humanise(profile.visit_purpose or "Not confirmed"))}</dd></div>
            <div><dt>Accommodation</dt><dd>{esc(profile.uk_accommodation or "Not confirmed")}</dd></div>
            <div><dt>Estimated cost</dt><dd>£{profile.estimated_trip_cost_gbp or 0:,.0f}</dd></div>
            <div><dt>Funding</dt><dd>{esc(humanise(profile.funding_source or "Not confirmed"))}</dd></div></dl>
          </aside>
        </div>
        <div class="checklist-block"><h3 class="subsection-title">Application checklist</h3>
        <ul class="check-list">{_requirements(case)}</ul></div>
      </section>

      <section class="workspace-section" id="documents" aria-labelledby="documents-title">
        <div class="section-heading"><div><p class="section-label">Case file</p><h2 id="documents-title">Documents prepared for review</h2></div>
        {badge(f"{len(case.documents)} files")}</div>
        <div class="table-wrap"><table class="document-table"><thead><tr><th>Document</th><th>Status</th><th>Note</th></tr></thead>
        <tbody>{_documents(case)}</tbody></table></div>
      </section>

      <section class="workspace-section audit-section" id="audit" aria-labelledby="audit-title">
        <details><summary><span><span class="section-label">For technical review</span>
        <strong id="audit-title">Open audit details</strong></span><span class="summary-action">Show</span></summary>
        <div class="audit-intro"><p>This section preserves the deterministic checks, evidence sources, rule version, and hashes used to reproduce the outcome.</p>
        <dl><div><dt>Case ID</dt><dd><code>{esc(case.id)}</code></dd></div><div><dt>Policy</dt><dd>{esc(case.policy_version)}</dd></div>
        <div><dt>Workflow stage</dt><dd>{esc(case.stage.value)}</dd></div></dl></div>
        <div class="audit-grid"><div><h3>Delivery gate</h3><ul class="audit-list">{_gate_audit(gate)}</ul></div>
        <div><h3>Active evidence ledger</h3><div class="table-wrap"><table><thead><tr><th>Fact</th><th>Value</th><th>Source</th><th>Page</th><th>Confidence</th></tr></thead>
        <tbody>{_evidence_audit(case)}</tbody></table></div></div></div>
        <div class="audit-documents"><h3>Technical document register</h3><div class="table-wrap"><table><thead><tr><th>File</th><th>Lifecycle status</th><th>Language</th><th>SHA-256</th></tr></thead>
        <tbody>{_document_audit(case)}</tbody></table></div></div>
        </details>
      </section>

      <footer class="product-note">Synthetic assessment case. This service prepares documents for human review; it does not decide eligibility, submit an application, or predict an outcome.</footer>
    </main>
    """


BASE_CSS = """
:root {
  color-scheme: light;
  --canvas: oklch(0.975 0.006 252); --paper: oklch(1 0 0); --ink: oklch(0.22 0.025 252);
  --muted: oklch(0.46 0.025 252); --quiet: oklch(0.94 0.012 252); --line: oklch(0.88 0.014 252);
  --primary: oklch(0.478 0.136 251.8); --primary-dark: oklch(0.39 0.12 252);
  --primary-soft: oklch(0.955 0.028 252); --success: oklch(0.43 0.11 155);
  --success-soft: oklch(0.96 0.035 155); --warning: oklch(0.5 0.13 72);
  --warning-soft: oklch(0.96 0.035 72); --danger: oklch(0.46 0.18 26);
  --focus: oklch(0.62 0.17 251.8); --radius: 12px;
  --sans: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, monospace;
}
* { box-sizing: border-box; }
html { background: var(--canvas); scroll-behavior: smooth; }
body { margin: 0; color: var(--ink); background: var(--canvas); font: 15px/1.55 var(--sans); }
a { color: inherit; }
a:focus-visible, summary:focus-visible { outline: 3px solid var(--focus); outline-offset: 3px; }
.app-shell { width: min(100%, 1600px); min-height: 100vh; margin: 0 auto; display: grid; grid-template-columns: 220px minmax(0, 1fr); background: var(--paper); }
.rail { position: sticky; top: 0; height: 100vh; display: flex; flex-direction: column; padding: 24px 16px 20px; border-right: 1px solid var(--line); background: var(--canvas); }
.brand { display: flex; align-items: center; gap: 10px; padding: 0 8px; font-size: 15px; font-weight: 760; letter-spacing: -.01em; }
.brand-mark { width: 30px; height: 30px; display: grid; place-items: center; border-radius: 8px; color: white; background: var(--primary-dark); font-size: 12px; }
.rail-nav { display: grid; gap: 4px; margin-top: 34px; }
.rail-nav a { display: flex; align-items: center; min-height: 40px; padding: 8px 10px; border-radius: 8px; color: var(--muted); text-decoration: none; font-weight: 650; }
.rail-nav a:hover { color: var(--ink); background: var(--quiet); }
.rail-nav a[aria-current="page"] { color: var(--primary-dark); background: var(--primary-soft); }
.rail-section-title { margin: 28px 10px 9px; color: var(--muted); font-size: 12px; font-weight: 700; }
.case-link { display: block; padding: 11px 10px; border-radius: 9px; color: var(--ink); background: var(--paper); text-decoration: none; }
.case-link strong, .case-link span { display: block; }
.case-link span { margin-top: 3px; color: var(--muted); font-size: 12px; }
.rail-footer { margin-top: auto; padding: 14px 10px 0; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; }
main { min-width: 0; padding: 34px clamp(24px, 4vw, 64px) 56px; }
.case-header { display: flex; align-items: end; justify-content: space-between; gap: 24px; max-width: 1180px; margin: 0 auto 24px; }
.breadcrumb { margin: 0 0 8px; color: var(--muted); font-size: 13px; }.breadcrumb span { margin: 0 4px; color: var(--line); }
.title-line { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
h1 { margin: 0; font-size: 28px; line-height: 1.2; letter-spacing: -.025em; }
h2 { margin: 0; font-size: 20px; line-height: 1.3; letter-spacing: -.018em; text-wrap: balance; }
h3 { margin: 0; font-size: 15px; line-height: 1.4; }
p { text-wrap: pretty; }
.case-subtitle { margin: 7px 0 0; color: var(--muted); }
.header-actions { flex: 0 0 auto; }
.button { min-height: 42px; display: inline-flex; align-items: center; justify-content: center; padding: 9px 15px; border-radius: 8px; font-weight: 720; text-decoration: none; transition: background 180ms ease-out, transform 180ms ease-out; }
.button--primary { color: white; background: var(--primary-dark); }.button--primary:hover { background: var(--primary); }.button:active { transform: translateY(1px); }
.button--disabled { color: var(--muted); background: var(--quiet); cursor: not-allowed; }
.badge { display: inline-flex; align-items: center; gap: 7px; width: fit-content; padding: 4px 9px; border-radius: 999px; color: var(--ink); background: var(--quiet); font-size: 12px; font-weight: 720; white-space: nowrap; }
.badge > span { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.badge--success { color: var(--success); background: var(--success-soft); }.badge--warning { color: var(--warning); background: var(--warning-soft); }
.outcome { max-width: 1180px; margin: 0 auto; display: flex; align-items: center; justify-content: space-between; gap: 36px; padding: 24px 26px; border-radius: var(--radius); background: var(--primary-soft); }
.outcome-copy { max-width: 650px; }.outcome-copy > p:last-child { margin: 7px 0 0; color: var(--muted); }
.section-label { display: block; margin: 0 0 4px; color: var(--muted); font-size: 12px; font-weight: 700; }
.outcome-facts { flex: 0 0 auto; display: flex; margin: 0; }
.outcome-facts div { min-width: 108px; padding: 0 18px; border-left: 1px solid var(--line); }
.outcome-facts dt { color: var(--muted); font-size: 12px; }.outcome-facts dd { margin: 3px 0 0; font-weight: 750; font-variant-numeric: tabular-nums; }
.section-nav { position: sticky; top: 0; z-index: 2; max-width: 1180px; margin: 20px auto 0; display: flex; gap: 22px; padding: 0 2px; border-bottom: 1px solid var(--line); background: color-mix(in oklch, var(--paper) 94%, transparent); backdrop-filter: blur(10px); }
.section-nav a { padding: 12px 0 10px; color: var(--muted); text-decoration: none; font-size: 13px; font-weight: 680; }
.section-nav a:hover { color: var(--primary-dark); }
.workspace-section { max-width: 1180px; margin: 0 auto; padding: 38px 0; border-bottom: 1px solid var(--line); scroll-margin-top: 52px; }
.section-heading { display: flex; align-items: end; justify-content: space-between; gap: 28px; margin-bottom: 22px; }
.section-heading > p { max-width: 54ch; margin: 0; color: var(--muted); font-size: 13px; }
.journey { list-style: none; margin: 0; padding: 0; }
.journey-step { display: grid; grid-template-columns: 66px 28px minmax(0, 1fr); column-gap: 14px; }
.journey-time { padding-top: 2px; font-weight: 750; font-variant-numeric: tabular-nums; text-align: right; }.journey-time span { display: block; color: var(--muted); font-size: 11px; font-weight: 500; }
.journey-marker { position: relative; display: flex; justify-content: center; }.journey-marker::after { content: ""; position: absolute; top: 25px; bottom: 0; width: 1px; background: var(--line); }
.journey-step:last-child .journey-marker::after { display: none; }.journey-marker span { z-index: 1; width: 24px; height: 24px; display: grid; place-items: center; border-radius: 50%; color: white; background: var(--success); font-size: 12px; font-weight: 800; }
.journey-copy { padding: 0 0 26px; }.journey-copy p { margin: 5px 0 0; color: var(--muted); }.journey-result { padding: 9px 11px; border-radius: 8px; background: var(--canvas); font-size: 13px; }.journey-result strong { color: var(--ink); }
.preparation-layout { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(280px, .7fr); gap: 42px; }
.subsection-title { margin-bottom: 12px; }.resolution-list, .check-list, .audit-list { list-style: none; margin: 0; padding: 0; }
.resolution-row { min-height: 70px; display: grid; grid-template-columns: 26px minmax(0, 1fr) auto; align-items: start; gap: 12px; padding: 14px 0; border-top: 1px solid var(--line); }
.resolution-row:last-child { border-bottom: 1px solid var(--line); }.resolution-row p { margin: 4px 0 0; color: var(--muted); }.resolution-check, .check-symbol { width: 22px; height: 22px; display: grid; place-items: center; border-radius: 50%; color: white; background: var(--success); font-size: 12px; font-weight: 800; }
.case-snapshot { padding: 18px 20px; border-radius: 10px; background: var(--canvas); }.case-snapshot h3 { margin-bottom: 12px; }
.case-snapshot dl { margin: 0; }.case-snapshot dl div { display: grid; grid-template-columns: 100px 1fr; gap: 12px; padding: 8px 0; border-top: 1px solid var(--line); }.case-snapshot dt { color: var(--muted); }.case-snapshot dd { margin: 0; font-weight: 650; overflow-wrap: anywhere; }
.checklist-block { margin-top: 30px; }.check-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border-top: 1px solid var(--line); }
.check-row { min-height: 48px; display: grid; grid-template-columns: 22px minmax(0, 1fr) auto; align-items: center; gap: 10px; padding: 8px 14px 8px 0; border-bottom: 1px solid var(--line); }.check-row:nth-child(odd) { margin-right: 24px; }.check-row strong { color: var(--success); font-size: 12px; }
.table-wrap { width: 100%; overflow-x: auto; } table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { padding: 9px 12px; color: var(--muted); background: var(--canvas); text-align: left; font-size: 11px; font-weight: 730; }
td { padding: 12px; border-bottom: 1px solid var(--line); vertical-align: top; }.document-table td:first-child strong, .document-table td:first-child span { display: block; }.document-table td:first-child span { margin-top: 2px; color: var(--muted); font-size: 12px; }.document-table td:last-child { color: var(--muted); }
.audit-section { border-bottom: 0; } details { border-radius: var(--radius); background: var(--canvas); } summary { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 18px 20px; cursor: pointer; list-style: none; } summary::-webkit-details-marker { display: none; } summary strong { display: block; font-size: 15px; }.summary-action { color: var(--primary-dark); font-weight: 700; font-size: 13px; } details[open] .summary-action { font-size: 0; } details[open] .summary-action::after { content: "Hide"; font-size: 13px; }
.audit-intro { padding: 20px; border-top: 1px solid var(--line); }.audit-intro > p { max-width: 70ch; margin: 0 0 16px; color: var(--muted); }.audit-intro dl { display: flex; flex-wrap: wrap; gap: 18px 34px; margin: 0; }.audit-intro dt { color: var(--muted); font-size: 11px; }.audit-intro dd { margin: 2px 0 0; font-weight: 650; }
.audit-grid { display: grid; grid-template-columns: minmax(280px, .65fr) minmax(0, 1.35fr); gap: 30px; padding: 0 20px 24px; }.audit-grid h3, .audit-documents h3 { margin-bottom: 12px; }
.audit-list li { display: grid; grid-template-columns: 22px minmax(0, 1fr) auto; align-items: center; gap: 9px; min-height: 38px; border-bottom: 1px solid var(--line); }.audit-list strong { color: var(--success); font-size: 11px; }.audit-result { width: 19px; height: 19px; display: grid; place-items: center; border-radius: 50%; color: white; font-size: 10px; font-weight: 800; }.audit-result--pass { background: var(--success); }.audit-result--blocked { background: var(--danger); }
.audit-documents { padding: 0 20px 22px; } code { font: .9em/1.4 var(--mono); overflow-wrap: anywhere; }.empty-state { padding: 18px; color: var(--muted); background: var(--canvas); text-align: center; }
.product-note { max-width: 1180px; margin: 0 auto; padding: 20px 0 0; color: var(--muted); font-size: 12px; }
@media (max-width: 980px) {
  .app-shell { grid-template-columns: 190px minmax(0, 1fr); }.outcome { align-items: start; flex-direction: column; gap: 20px; }.preparation-layout, .audit-grid { grid-template-columns: 1fr; }
}
@media (max-width: 760px) {
  .app-shell { display: block; }.rail { position: static; height: auto; display: block; padding: 14px 18px; border-right: 0; border-bottom: 1px solid var(--line); }.rail-nav, .rail-section-title, .case-link, .rail-footer { display: none; }
  main { padding: 24px 18px 40px; }.case-header { align-items: start; flex-direction: column; }.header-actions, .button { width: 100%; }.outcome { padding: 20px; }.outcome-facts { width: 100%; }.outcome-facts div { flex: 1; min-width: 0; padding: 0 10px; }.outcome-facts div:first-child { padding-left: 0; border-left: 0; }
  .section-nav { gap: 18px; overflow-x: auto; }.section-nav a { white-space: nowrap; }.section-heading { align-items: start; flex-direction: column; gap: 8px; }.check-list { grid-template-columns: 1fr; }.check-row:nth-child(odd) { margin-right: 0; }
}
@media (max-width: 480px) {
  h1 { font-size: 24px; }.title-line { align-items: start; flex-direction: column; }.outcome-facts { display: grid; grid-template-columns: 1fr; }.outcome-facts div { padding: 8px 0; border-top: 1px solid var(--line); border-left: 0; }.journey-step { grid-template-columns: 48px 24px minmax(0, 1fr); gap: 9px; }.resolution-row { grid-template-columns: 24px minmax(0, 1fr); }.resolution-row .badge { grid-column: 2; }.case-snapshot dl div { grid-template-columns: 1fr; gap: 2px; }
}
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; } }
"""


def page(body: str, case: Case | None = None) -> str:
    case_link = (
        f'<a class="case-link" href="/"><strong>{esc(case.profile.full_name or case.id)}</strong>'
        f"<span>{esc(humanise(case.profile.visit_purpose or 'Visitor case'))} · {esc(humanise(case.status.value))}</span></a>"
        if case
        else '<div class="case-link"><strong>No demo case</strong><span>Run the demo to begin</span></div>'
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1"><title>Visa preparation case</title>
    <style>{BASE_CSS}</style></head><body><div class="app-shell"><aside class="rail" aria-label="Case navigation">
    <div class="brand"><span class="brand-mark" aria-hidden="true">VP</span><span>Visa preparation</span></div>
    <nav class="rail-nav"><a href="/" aria-current="page">Case overview</a><a href="#journey">Email journey</a>
    <a href="#documents">Documents</a><a href="#audit">Audit details</a></nav>
    <p class="rail-section-title">Current case</p>{case_link}
    <p class="rail-footer">Assessment mode<br>Policy {esc(case.policy_version) if case else "not loaded"}</p>
    </aside>{body}</div></body></html>"""


def render_page(case: Case, gate: GateResult) -> str:
    return page(render_case(case, gate), case)


def render_empty_page() -> str:
    body = """<main id="main-content"><header class="case-header"><div><p class="breadcrumb">Assessment mode</p>
    <h1>No demo case yet</h1><p class="case-subtitle">Start the featured email replay, then return here.</p></div></header>
    <section class="outcome"><div class="outcome-copy"><p class="section-label">Getting started</p>
    <h2>Run the synthetic case to prepare the review workspace</h2><p>Use the one-click Demo launcher, then refresh this page.</p></div></section></main>"""
    return page(body)
