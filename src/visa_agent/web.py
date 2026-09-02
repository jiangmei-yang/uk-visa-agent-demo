from __future__ import annotations

import html
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from visa_agent.config import Settings
from visa_agent.domain.models import Case
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate
from visa_agent.storage.sqlite import SQLiteStore

settings = Settings.from_env()
policy = load_policy(settings.policy_path)


app = FastAPI(title="UK Visa Agent Review Console")


def load_cases() -> list[Case]:
    request_store = SQLiteStore(settings.database_path)
    try:
        return request_store.list_cases()
    finally:
        request_store.close()


def load_case(case_id: str) -> Case | None:
    request_store = SQLiteStore(settings.database_path)
    try:
        return request_store.get_case(case_id)
    finally:
        request_store.close()


def esc(value: object) -> str:
    return html.escape(str(value))


def badge(label: str, tone: str = "neutral") -> str:
    return f'<span class="badge badge--{tone}"><span aria-hidden="true"></span>{esc(label)}</span>'


def render_case(case: Case) -> str:
    gate = evaluate_gate(case, policy, date.today())
    profile_items = "".join(
        f"<div><dt>{esc(key.replace('_', ' ').title())}</dt><dd>{esc(value or 'Unknown')}</dd></div>"
        for key, value in case.profile.model_dump(mode="json").items()
    )
    requirement_rows = "".join(
        "<tr>"
        f"<td>{esc(item.title)}</td>"
        f"<td>{badge('Satisfied', 'success') if item.satisfied else badge('Outstanding', 'danger')}</td>"
        f"<td><code>{esc(item.id)}</code></td>"
        f"<td>{esc(item.rule_version)}</td>"
        "</tr>"
        for item in case.requirements
        if item.applicable
    )
    issue_rows = "".join(
        f'<li class="issue issue--{item.status.value.lower()}">'
        f"<div>{badge(item.status, 'danger' if item.status.value == 'OPEN' else 'success')}"
        f"<strong>{esc(item.title)}</strong></div><p>{esc(item.detail)}</p>"
        f"<small><code>{esc(item.code)}</code> · {esc(item.resolution or 'No resolution recorded')}</small>"
        "</li>"
        for item in case.issues
    ) or '<li class="empty">No consistency issues have been recorded.</li>'
    document_rows = "".join(
        "<tr>"
        f"<td><strong>{esc(doc.filename)}</strong><br><small>{esc(doc.kind)}</small></td>"
        f"<td>{badge(doc.status, 'success' if doc.status.value == 'ACCEPTED_FOR_REVIEW' else 'neutral')}</td>"
        f"<td>{esc(doc.language)}</td><td>{doc.page_count}</td>"
        f'<td><code class="hash" title="{esc(doc.sha256)}">{esc(doc.sha256[:14])}…</code></td>'
        "</tr>"
        for doc in case.documents
    )
    gate_rows = "".join(
        "<li>"
        f"{badge('Pass', 'success') if passed else badge('Blocked', 'danger')}"
        f"<span>{esc(check.replace('_', ' '))}</span>"
        "</li>"
        for check, passed in gate.checks.items()
    )
    evidence_rows = "".join(
        "<tr>"
        f"<td><code>{esc(item.fact_key)}</code></td><td>{esc(item.value)}</td>"
        f"<td>{esc(item.source_document_id or item.source_event_id)}</td>"
        f"<td>{esc(item.page or 'Email')}</td><td>{item.confidence:.2f}</td>"
        "</tr>"
        for item in case.evidence
        if not item.superseded
    )
    download = (
        f'<a class="button" href="/api/cases/{esc(case.id)}/pack">Download review pack</a>'
        if case.delivery_path
        else '<span class="button button--disabled" aria-disabled="true">Pack blocked</span>'
    )
    return f"""
    <main id="main-content">
      <header class="case-header">
        <div>
          <p class="context">Synthetic assessment case · Policy {esc(case.policy_version)}</p>
          <h1>{esc(case.profile.full_name or 'Unnamed case')}</h1>
          <div class="status-line">{badge(case.status, 'success' if gate.allowed else 'neutral')}
          <span>Workflow: <strong>{esc(case.stage)}</strong></span></div>
        </div>
        <div class="header-actions">{download}</div>
      </header>

      <section class="notice" aria-labelledby="notice-title">
        <div class="notice-icon" aria-hidden="true">i</div>
        <div><h2 id="notice-title">Preparation support, not a legal decision</h2>
        <p>This console uses synthetic data. It does not decide eligibility, submit an application,
        or predict an outcome. Every ready pack still requires human review.</p></div>
      </section>

      <div class="review-grid">
        <section class="panel gate" aria-labelledby="gate-title">
          <div class="panel-heading"><div><h2 id="gate-title">Delivery gate</h2>
          <p>{'All deterministic checks pass.' if gate.allowed else 'Finalisation remains blocked.'}</p></div>
          {badge(f'{sum(gate.checks.values())}/{len(gate.checks)} checks', 'success' if gate.allowed else 'danger')}</div>
          <ul class="gate-list">{gate_rows}</ul>
        </section>
        <section class="panel" aria-labelledby="profile-title">
          <div class="panel-heading"><div><h2 id="profile-title">Confirmed profile</h2>
          <p>Structured facts used by rules and pack generation.</p></div></div>
          <dl class="profile-grid">{profile_items}</dl>
        </section>
      </div>

      <section class="panel" aria-labelledby="issues-title">
        <div class="panel-heading"><div><h2 id="issues-title">Issues and resolutions</h2>
        <p>Contradictions are described neutrally and preserved after resolution.</p></div></div>
        <ul class="issue-list">{issue_rows}</ul>
      </section>

      <section class="panel" aria-labelledby="requirements-title">
        <div class="panel-heading"><div><h2 id="requirements-title">Personalised requirements</h2>
        <p>Each row is computed from the versioned policy snapshot.</p></div></div>
        <div class="table-wrap"><table><thead><tr><th>Requirement</th><th>Result</th><th>Rule</th><th>Version</th></tr></thead>
        <tbody>{requirement_rows}</tbody></table></div>
      </section>

      <section class="panel" aria-labelledby="documents-title">
        <div class="panel-heading"><div><h2 id="documents-title">Document register</h2>
        <p>Original filenames, classifications, lifecycle status, and content hashes.</p></div>
        {badge(f'{len(case.documents)} files')}</div>
        <div class="table-wrap"><table><thead><tr><th>Document</th><th>Status</th><th>Language</th><th>Pages</th><th>SHA-256</th></tr></thead>
        <tbody>{document_rows}</tbody></table></div>
      </section>

      <section class="panel" aria-labelledby="evidence-title">
        <div class="panel-heading"><div><h2 id="evidence-title">Active evidence ledger</h2>
        <p>Final facts retain their event or document source. Superseded facts remain in audit JSON.</p></div></div>
        <div class="table-wrap"><table><thead><tr><th>Fact</th><th>Value</th><th>Source</th><th>Page</th><th>Confidence</th></tr></thead>
        <tbody>{evidence_rows}</tbody></table></div>
      </section>
    </main>
    """


BASE_CSS = """
:root {
  color-scheme: light;
  --bg: oklch(1 0 0); --surface: oklch(0.975 0.006 252);
  --surface-strong: oklch(0.94 0.012 252); --ink: oklch(0.22 0.025 252);
  --muted: oklch(0.46 0.025 252); --primary: oklch(0.478 0.136 251.8);
  --primary-dark: oklch(0.39 0.12 252); --success: oklch(0.44 0.11 155);
  --success-bg: oklch(0.95 0.04 155); --warning: oklch(0.48 0.13 72);
  --danger: oklch(0.46 0.18 26); --danger-bg: oklch(0.96 0.03 26);
  --border: oklch(0.88 0.014 252); --focus: oklch(0.62 0.17 251.8);
  --radius: 12px; --sans: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, monospace;
}
* { box-sizing: border-box; }
html { background: var(--surface); scroll-behavior: smooth; }
body { margin: 0; color: var(--ink); font: 16px/1.55 var(--sans); }
a { color: var(--primary-dark); }
a:focus-visible, button:focus-visible { outline: 3px solid var(--focus); outline-offset: 3px; }
.shell { max-width: 1440px; min-height: 100vh; margin: 0 auto; display: grid; grid-template-columns: 248px minmax(0, 1fr); background: var(--bg); }
.rail { padding: 28px 20px; color: white; background: oklch(0.24 0.055 252); }
.brand { display: flex; align-items: center; gap: 11px; margin-bottom: 40px; font-weight: 750; letter-spacing: -0.015em; }
.brand-mark { display: grid; width: 32px; height: 32px; place-items: center; border-radius: 8px; background: var(--primary); font-size: 14px; }
.rail h2 { margin: 0 0 10px; color: oklch(0.78 0.025 252); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }
.case-link { display: block; padding: 12px; border-radius: 9px; color: white; background: oklch(0.3 0.055 252); text-decoration: none; }
.case-link strong, .case-link small { display: block; }
.case-link small { margin-top: 3px; color: oklch(0.78 0.025 252); }
.rail-note { margin-top: 30px; color: oklch(0.8 0.02 252); font-size: 13px; }
main { min-width: 0; padding: 38px clamp(20px, 4vw, 58px) 72px; }
.case-header { display: flex; justify-content: space-between; align-items: end; gap: 24px; margin-bottom: 28px; }
.context { margin: 0 0 5px; color: var(--muted); font-size: 14px; }
h1 { margin: 0; font-size: 2rem; line-height: 1.2; letter-spacing: -.025em; }
h2 { margin: 0; font-size: 1.08rem; letter-spacing: -.01em; }
p { text-wrap: pretty; }
.status-line { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-top: 12px; color: var(--muted); font-size: 14px; }
.button { min-height: 44px; display: inline-flex; align-items: center; justify-content: center; padding: 10px 16px; border-radius: 9px; color: white; background: var(--primary-dark); font-weight: 700; text-decoration: none; transition: background 180ms ease-out, transform 180ms ease-out; }
.button:hover { background: var(--primary); }
.button:active { transform: translateY(1px); }
.button--disabled { color: oklch(0.45 0.01 252); background: var(--surface-strong); cursor: not-allowed; }
.notice { display: flex; gap: 14px; padding: 16px 18px; margin-bottom: 20px; border-radius: var(--radius); background: oklch(0.95 0.035 252); }
.notice > div:last-child { min-width: 0; }
.notice-icon { flex: 0 0 26px; height: 26px; display: grid; place-items: center; border-radius: 50%; color: white; background: var(--primary-dark); font-weight: 800; }
.notice h2 { margin-top: 1px; overflow-wrap: anywhere; }
.notice p, .panel-heading p { max-width: 72ch; margin: 4px 0 0; color: var(--muted); font-size: 14px; }
.review-grid { display: grid; grid-template-columns: minmax(320px, .85fr) minmax(420px, 1.15fr); gap: 18px; }
.panel { margin-top: 18px; padding: 20px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--bg); }
.panel-heading { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: start; gap: 18px; margin-bottom: 17px; }
.badge { display: inline-flex; align-items: center; gap: 7px; width: fit-content; padding: 4px 9px; border-radius: 999px; color: var(--ink); background: var(--surface-strong); font-size: 12px; font-weight: 750; white-space: nowrap; }
.badge > span { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.badge--success { color: var(--success); background: var(--success-bg); }
.badge--danger { color: var(--danger); background: var(--danger-bg); }
.gate-list, .issue-list { list-style: none; margin: 0; padding: 0; }
.gate-list { display: grid; gap: 2px; }
.gate-list li { min-height: 38px; display: flex; align-items: center; gap: 10px; padding: 6px 0; border-bottom: 1px solid var(--surface-strong); font-size: 14px; text-transform: capitalize; }
.gate-list li:last-child { border: 0; }
.profile-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 20px; margin: 0; }
.profile-grid div { min-width: 0; }
.profile-grid dt { color: var(--muted); font-size: 12px; }
.profile-grid dd { margin: 1px 0 0; overflow-wrap: anywhere; font-weight: 650; }
.issue-list { display: grid; gap: 10px; }
.issue { padding: 14px; border-radius: 10px; background: var(--danger-bg); }
.issue--resolved { background: var(--success-bg); }
.issue > div { display: flex; align-items: center; gap: 10px; }
.issue p { margin: 8px 0 3px; }
.issue small { color: var(--muted); }
.table-wrap { width: 100%; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th { padding: 9px 10px; color: var(--muted); background: var(--surface); text-align: left; font-size: 12px; font-weight: 750; }
td { padding: 11px 10px; border-bottom: 1px solid var(--surface-strong); vertical-align: top; }
tr:last-child td { border-bottom: 0; }
code { font-family: var(--mono); font-size: .88em; overflow-wrap: anywhere; }
.hash { white-space: nowrap; }
.empty { padding: 18px; color: var(--muted); background: var(--surface); border-radius: 9px; text-align: center; }
@media (max-width: 980px) { .review-grid { grid-template-columns: 1fr; } }
@media (max-width: 820px) {
  .shell { display: block; }
  .rail { padding: 18px 20px; }
  .brand { margin-bottom: 14px; }
  .rail h2, .rail-note { display: none; }
  main { padding-top: 26px; }
  .case-header { align-items: start; flex-direction: column; }
  .header-actions, .button { width: 100%; }
  .panel-heading { display: block; }
  .panel-heading > .badge { margin-top: 10px; }
  .notice h2 { max-width: 24ch; }
}
@media (max-width: 540px) { .profile-grid { grid-template-columns: 1fr; } .panel { padding: 16px; } h1 { font-size: 1.7rem; } }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; } }
"""


def page(body: str, case: Case | None = None) -> str:
    rail_case = (
        f'<a class="case-link" href="/"><strong>{esc(case.profile.full_name or case.id)}</strong>'
        f"<small>{esc(case.stage)}</small></a>"
        if case
        else '<div class="case-link"><strong>No demo case</strong><small>Run make demo</small></div>'
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1"><title>Visa case review</title>
    <style>{BASE_CSS}</style></head><body><div class="shell"><aside class="rail" aria-label="Case navigation">
    <div class="brand"><span class="brand-mark" aria-hidden="true">UK</span><span>Visa review console</span></div>
    <h2>Assessment cases</h2>{rail_case}<p class="rail-note">Synthetic data only.<br>Credential-free reviewer mode.</p>
    </aside>{body}</div></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    cases = load_cases()
    if not cases:
        body = """<main id="main-content"><header class="case-header"><div><p class="context">Reviewer mode</p>
        <h1>No synthetic case yet</h1></div></header><section class="panel"><h2>Run the featured demo</h2>
        <p>Use <code>make demo</code>, then refresh this page to inspect the gate, evidence, and pack.</p></section></main>"""
        return HTMLResponse(page(body))
    case = cases[0]
    return HTMLResponse(page(render_case(case), case))


@app.get("/api/cases")
def list_cases() -> list[dict[str, object]]:
    return [case.model_dump(mode="json") for case in load_cases()]


@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict[str, object]:
    case = load_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case.model_dump(mode="json")


@app.get("/api/cases/{case_id}/pack")
def get_pack(case_id: str) -> FileResponse:
    case = load_case(case_id)
    if case is None or case.delivery_path is None:
        raise HTTPException(status_code=404, detail="Review pack is not available")
    path = Path(case.delivery_path).resolve()
    allowed_root = settings.output_dir.resolve()
    if allowed_root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Review pack is not available")
    return FileResponse(path, filename=path.name, media_type="application/zip")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "policy_version": policy.version}
