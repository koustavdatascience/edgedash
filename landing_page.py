from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import streamlit as st

ROOT = Path(__file__).parent
DASHBOARD_URL = "https://edgedash-project.streamlit.app/~/+/?view=dashboard"


@st.cache_data(show_spinner=False)
def img_data(path: Path) -> str:
    return "data:image/webp;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


@st.cache_data(ttl=300, show_spinner=False)
def load_live_data() -> dict[str, Any]:
    demo = {"listings": 128, "scored": 42, "gaps": 7, "last_run": "Today, 06:04", "jobs": [("Product Data Analyst", "Northstar Labs", 94), ("Growth Analyst", "Loopline", 89), ("Business Intelligence Analyst", "Aster & Co.", 84), ("Data Operations Lead", "Morrow", 81)]}
    try:
        from edgedash.config import load_config
        from edgedash.storage_factory import get_storage_module
        from edgedash.verified import last_passing_cycle, verified_listings
        config = load_config(); storage = get_storage_module(config); cycle = last_passing_cycle(storage)
        if not cycle: return demo
        rows = verified_listings(storage, cycle, limit=500)
        scored = sorted([r for r in rows if r.get("fit_score") is not None], key=lambda r: int(r.get("fit_score") or 0), reverse=True)
        stats = storage.get_stats()
        return {"listings": int(stats.get("total_listings", len(rows))), "scored": len(scored), "gaps": len(storage.get_skill_gaps()), "last_run": "Verified just now", "jobs": [(r.get("title", "Untitled role"), r.get("company", "Unknown company"), int(r.get("fit_score") or 0)) for r in scored[:4]] or demo["jobs"]}
    except Exception:
        return demo


def inject_css() -> None:
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;600;700&family=Newsreader:opsz,wght@6..72,400;6..72,500&display=swap');
    :root { --ink:#101828; --muted:#667085; --blue:#155eef; --blue-dark:#0b3baf; --line:#dbe2ea; --paper:#f8fafc; }
    .stApp { background:var(--paper); color:var(--ink); font-family:'DM Sans',sans-serif; }
    .block-container { max-width:1180px; padding:0 32px 72px; }
    header[data-testid="stHeader"] { display:none !important; height:0 !important; min-height:0 !important; pointer-events:none !important; } [data-testid="stToolbar"] { display:none !important; pointer-events:none !important; } #MainMenu { display:none !important; }
    .ed-nav { height:50px; display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid var(--line); margin-bottom:18px; }
    .brand { display:flex; align-items:center; gap:10px; font-weight:700; letter-spacing:-.05em; color:var(--ink); text-decoration:none; }
    .brand-mark { width:25px; height:25px; display:grid; place-items:center; color:#fff; background:var(--blue); border-radius:8px; font-size:17px; }
    .nav-links { display:flex; align-items:center; height:100%; gap:10px; margin-left:auto; margin-right:10px; color:#475467; font-size:12px; } .nav-links a { color:inherit; text-decoration:none; }
    .nav-install { display:inline-flex; align-items:center; justify-content:center; height:38px; line-height:1; vertical-align:middle; color:var(--ink); background:rgba(255,255,255,.66); border:1px solid rgba(21,94,239,.18); border-radius:999px; padding:9px 15px; font-size:12px; box-shadow:0 5px 18px rgba(16,24,40,.06); text-decoration:none; } .menu-wrap { position:relative; } .menu-wrap summary { list-style:none; cursor:pointer; display:grid; place-items:center; width:42px; height:36px; border:1px solid rgba(21,94,239,.18); border-radius:12px; background:rgba(255,255,255,.66); box-shadow:0 5px 18px rgba(16,24,40,.06); } .menu-wrap summary::-webkit-details-marker { display:none; } .hamburger,.hamburger:before,.hamburger:after { display:block; width:16px; height:1.5px; background:var(--blue); content:''; } .hamburger { position:relative; } .hamburger:before { position:absolute; top:-5px; } .hamburger:after { position:absolute; top:5px; } .menu-pop { position:absolute; z-index:10; top:46px; right:0; width:190px; padding:7px; border:1px solid rgba(21,94,239,.16); border-radius:14px; background:rgba(255,255,255,.78); backdrop-filter:blur(18px); box-shadow:0 18px 40px rgba(16,24,40,.12); } .menu-pop a { display:block; padding:11px 12px; color:var(--ink); border-radius:9px; text-decoration:none; font-size:12px; } .menu-pop a:hover { color:var(--blue); background:rgba(21,94,239,.08); }
    .hero { position:relative; overflow:hidden; border-radius:20px; display:block; background:#cdd7dc; box-shadow:0 20px 60px rgba(16,24,40,.12); }
    .hero-img { position:relative; inset:auto; display:block; width:100%; height:auto; min-width:0; min-height:0; object-fit:cover; object-position:center; filter:saturate(.82) contrast(.96); } .hero:after { content:''; position:absolute; inset:0; background:linear-gradient(180deg,rgba(7,29,64,.04) 30%,rgba(7,24,52,.7) 100%); }
    .status { position:absolute; z-index:2; top:22px; right:22px; display:flex; align-items:center; gap:7px; padding:8px 12px; border:1px solid rgba(255,255,255,.34); color:white; border-radius:999px; background:rgba(16,24,40,.34); backdrop-filter:blur(14px); font:10px 'DM Mono',monospace; }
    .status i { width:7px; height:7px; display:inline-block; border-radius:99px; background:#6ea8ff; box-shadow:0 0 0 3px rgba(110,168,255,.2); }
    .hero-copy { position:relative; z-index:1; padding:38px 44px 42px; color:#fff; max-width:760px; } .eyebrow { color:#b8d2ff; text-transform:uppercase; letter-spacing:.03em; font:500 10px 'DM Mono',monospace; margin-bottom:14px; }
    h1 { font:400 clamp(42px,6.2vw,76px)/.93 'Newsreader',serif; letter-spacing:-.045em; margin:0 0 18px; max-width:680px; } .hero-sub { font-size:15px; line-height:1.45; max-width:440px; color:rgba(255,255,255,.82); margin:0 0 25px; }
    .cta-row { display:flex; gap:10px; flex-wrap:wrap; } .cta { display:inline-flex; align-items:center; justify-content:center; border-radius:999px; padding:12px 18px; font-size:12px; font-weight:600; text-decoration:none; transition:transform .16s ease,background .16s ease; } .cta:hover { transform:translateY(-2px); }
    .cta-primary { background:#fff; color:var(--blue-dark); box-shadow:0 8px 22px rgba(8,39,103,.18); } .cta-secondary { color:#fff; border:1px solid rgba(255,255,255,.42); background:rgba(255,255,255,.1); backdrop-filter:blur(10px); }
    .section { padding:86px 0 0; } .section-head { display:flex; align-items:end; justify-content:space-between; gap:24px; margin-bottom:24px; } .section-label { color:var(--blue); font:500 10px 'DM Mono',monospace; letter-spacing:.015em; text-transform:uppercase; margin-bottom:10px; } h2 { font:400 clamp(31px,4vw,48px)/1 'Newsreader',serif; letter-spacing:-.055em; margin:0; } .section-note { font-size:12px; color:var(--muted); max-width:260px; text-align:right; } .signal-strip { display:grid; grid-template-columns:repeat(4,1fr); gap:0; margin-top:28px; border-top:1px solid var(--line); border-bottom:1px solid var(--line); } .signal { padding:17px 14px; color:var(--muted); text-align:center; font:10px 'DM Mono',monospace; text-transform:uppercase; letter-spacing:0; } .signal + .signal { border-left:1px solid var(--line); } .final-cta { margin-top:86px; padding:42px; border-radius:20px; background:#e9f1ff; border:1px solid #c8dbfa; display:flex; align-items:center; justify-content:space-between; gap:24px; } .final-cta h2 { color:var(--ink); } .final-cta a { color:#fff; background:var(--blue); }
    .step-grid,.feature-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; } .feature-grid { grid-template-columns:repeat(3,1fr); } .glass,.flat { border:1px solid var(--line); border-radius:16px; padding:20px; background:rgba(255,255,255,.64); } .glass { background:rgba(255,255,255,.56); backdrop-filter:blur(13px); box-shadow:0 12px 32px rgba(16,24,40,.05); } .flat { background:#eef3f8; }
    .step-card { position:relative; } .step-card:not(:last-child):after { content:''; position:absolute; z-index:-1; width:10px; height:1px; right:-10px; top:50%; background:#b9c7d8; } .step-icon { width:28px; height:28px; display:grid; place-items:center; color:var(--blue); border:1px solid #b9ccea; border-radius:9px; margin:13px 0 20px; } .step-icon svg { width:15px; height:15px; } .step-num { color:var(--blue); font:500 11px 'DM Mono',monospace; } .step-title { font-size:14px; font-weight:600; } .step-copy { color:var(--muted); font-size:11px; line-height:1.4; margin-top:8px; } .feature-card { min-height:120px; } .feature-title { font-weight:600; margin-bottom:8px; } .feature-copy { color:var(--muted); font-size:12px; line-height:1.4; }
    .proof { background:#0e1b35; border-radius:20px; padding:22px; color:#fff; box-shadow:0 20px 60px rgba(9,30,66,.14); } .proof-top { display:flex; justify-content:space-between; align-items:center; margin-bottom:18px; font-size:12px; } .proof-brand { font-weight:700; letter-spacing:-.02em; } .proof-live { color:#8fb9ff; font:10px 'DM Mono',monospace; } .proof-grid { display:grid; grid-template-columns:1fr 2.3fr; gap:12px; } .proof-stats { display:grid; grid-template-columns:1fr 1fr; gap:12px; } .proof-card { background:rgba(255,255,255,.09); border:1px solid rgba(255,255,255,.12); border-radius:12px; padding:17px; } .proof-kicker { color:#91a3c0; font:10px 'DM Mono',monospace; text-transform:uppercase; letter-spacing:.03em; } .proof-value { font-size:31px; margin:9px 0 3px; } .proof-muted { color:#91a3c0; font-size:11px; } .job-row { display:flex; justify-content:space-between; gap:12px; align-items:center; padding:11px 0; border-bottom:1px solid rgba(255,255,255,.1); font-size:12px; } .job-row:last-child { border-bottom:0; padding-bottom:0; } .job-company { color:#91a3c0; font-size:10px; margin-top:3px; } .job-score { color:#8fb9ff; font:500 14px 'DM Mono',monospace; }
    .proof-image { display:block; width:100%; height:auto; object-fit:cover; border:1px solid var(--line); border-radius:20px; box-shadow:0 20px 60px rgba(16,24,40,.1); } .reference-proof { display:grid; grid-template-columns:.72fr 1.55fr; gap:70px; align-items:center; } .reference-proof-copy h2 { font-size:clamp(44px,5.6vw,76px); line-height:.88; margin-bottom:26px; } .reference-proof-copy p { color:var(--muted); font-size:15px; line-height:1.5; max-width:360px; } .reference-link { color:var(--ink); text-decoration:none; font-size:12px; font-weight:600; } .reference-link:hover { color:var(--blue); } .reference-hero-copy { max-width:730px; } .reference-hero-copy h1 { font-size:clamp(46px,6.5vw,82px); } .reference-hero-copy .hero-sub { font-size:16px; } .reference-hero-actions { display:flex; gap:22px; align-items:center; margin-top:26px; } .reference-hero-actions a { font-size:12px; font-weight:600; text-decoration:none; } .reference-hero-actions .cta-primary { color:var(--blue-dark); } .reference-hero-actions .text-link { color:#d8e7ff; } .reference-steps-intro { max-width:580px; margin-bottom:30px; } .reference-steps-intro h2 { margin-bottom:14px; } .reference-steps-intro p { color:var(--muted); font-size:14px; line-height:1.5; } .reference-emphasis { margin-top:26px; color:var(--ink); font-size:13px; font-weight:600; } .reference-feature-head { margin-bottom:30px; } .reference-feature-head h2 { font-size:clamp(42px,5vw,70px); } .reference-feature-card { min-height:148px; } .reference-feature-card .step-num { margin-bottom:38px; } .reference-footer { margin-top:86px; padding-top:30px; border-top:1px solid var(--line); } .reference-footer .brand { font-size:18px; } .reference-footer p { color:var(--muted); font-size:12px; margin:8px 0 18px; } .reference-footer-links { display:flex; gap:22px; font-size:11px; }
    .footer { border-top:1px solid var(--line); margin-top:92px; padding-top:25px; display:flex; justify-content:space-between; align-items:center; color:var(--muted); font-size:11px; } .footer .cta-primary { background:var(--blue); color:#fff; } .docs-hero { background:linear-gradient(135deg,#0e1b35 0%,#123d89 100%); color:#fff; border-radius:20px; padding:42px; box-shadow:0 20px 60px rgba(9,30,66,.14); } .docs-hero .section-label { color:#9fc2ff; } .docs-hero h1 { margin:0 0 14px; } .docs-hero p { color:#d7e5ff; max-width:570px; line-height:1.55; margin:0; } .docs-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:14px; } .docs-card { min-height:180px; } .docs-card h3 { margin:8px 0 10px; } .flow { display:grid; grid-template-columns:repeat(5,1fr); gap:0; margin-top:24px; } .flow-step { position:relative; padding:16px 12px; background:#eef3f8; border:1px solid var(--line); font-size:11px; } .flow-step:not(:last-child):after { content:'→'; position:absolute; z-index:1; right:-8px; top:29px; color:var(--blue); font-weight:700; } .flow-step strong { display:block; margin-top:12px; font-size:12px; } .dash-shell { background:#0e1b35; color:#fff; border-radius:20px; padding:24px; box-shadow:0 20px 60px rgba(9,30,66,.14); } .dash-nav { display:flex; justify-content:space-between; border-bottom:1px solid rgba(255,255,255,.12); padding-bottom:18px; margin-bottom:18px; } .dash-metrics { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; } .dash-metric { background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.12); border-radius:12px; padding:17px; } .dash-metric b { display:block; font-size:30px; margin:10px 0 2px; } .dash-metric span { color:#91a3c0; font:10px 'DM Mono',monospace; text-transform:uppercase; }
    @media (max-width:760px) { .block-container { padding:0 16px 48px; } .hero-copy { padding:28px 24px; } .step-grid,.feature-grid,.proof-grid,.proof-stats,.docs-grid,.dash-metrics { grid-template-columns:1fr; } .docs-hero { padding:28px 24px; } .flow { grid-template-columns:1fr; } .flow-step:not(:last-child):after { content:'↓'; right:auto; left:18px; top:auto; bottom:-10px; } .step-card:not(:last-child):after { width:1px; height:10px; right:auto; left:34px; top:auto; bottom:-10px; } .signal-strip { grid-template-columns:1fr 1fr; } .signal:nth-child(3) { border-left:0; border-top:1px solid var(--line); } .signal:nth-child(4) { border-top:1px solid var(--line); } .final-cta { display:block; padding:28px 24px; } .final-cta .cta { margin-top:20px; } .section { padding-top:58px; } .section-head { display:block; } .section-note { text-align:left; margin-top:12px; } .footer { display:block; } .footer .cta { margin-top:18px; } }
    /* Editorial landing replacement: image-first hero matching the approved page */
    .ed-nav { position:relative; z-index:100; isolation:isolate; pointer-events:auto; margin:0 0 20px; padding:0 18px; height:58px; border:1px solid rgba(255,255,255,.28); border-radius:14px; background:rgba(255,255,255,.72); backdrop-filter:blur(16px); box-shadow:0 8px 28px rgba(16,24,40,.08); }
    .brand-mark { color:#fff; background:#171717; } .ed-nav a { position:relative; z-index:101; pointer-events:auto; cursor:pointer; box-sizing:border-box; }
    .hero { position:relative; overflow:hidden; margin:0; height:min(68vh,620px); min-height:0; flex-direction:column; border-radius:20px; box-shadow:0 20px 60px rgba(16,24,40,.12); display:flex; justify-content:flex-end; background:#0e1b35; }
    .hero:after { display:block; background:linear-gradient(180deg,rgba(7,29,64,.05) 22%,rgba(7,24,52,.84) 100%); }
    .hero-img { position:absolute !important; inset:0 !important; display:block !important; width:100% !important; height:100% !important; min-width:0; min-height:0; object-fit:cover; filter:none; object-position:center; }
    .hero-copy { position:relative; z-index:1; width:100%; max-width:none; margin-top:auto; padding:42px 44px 46px; color:#fff; }
    .hero-copy .eyebrow { color:#d6e5ff; display:block; }
    .hero-copy h1 { color:#fff; font-size:clamp(34px,5vw,58px); max-width:680px; margin:0 0 18px; letter-spacing:-.06em; }
    .hero-copy .hero-sub { color:rgba(255,255,255,.86); font-size:15px; max-width:440px; margin:0 0 22px; }
    .hero-copy .cta-primary { color:var(--blue-dark); background:#fff; }
    .hero-copy .text-link { color:#fff; }
    @media (max-width:760px) { .ed-nav { margin:0 0 12px; } .nav-links { display:flex; gap:6px; margin-right:0; } .nav-links a { display:none; } .hero { margin:0; height:560px; } .hero-copy { padding:28px 24px 30px; } .hero-copy h1 { font-size:39px; } }
    </style>
    """, unsafe_allow_html=True)


def render_nav() -> None:
    st.markdown(f'<nav class="ed-nav"><a class="brand" href="#top"><span class="brand-mark">→</span><span>edgedash</span></a><div class="nav-links"><a class="nav-install" href="{DASHBOARD_URL}" target="_self">About the dashboard</a><a class="nav-install" href="https://edgedash-project.streamlit.app/~/+/?page=docs" target="_self">Documentation</a><a class="nav-install" href="https://github.com/koustavdatascience/edgedash" target="_blank" rel="noopener noreferrer">GitHub</a></div></nav>', unsafe_allow_html=True)


def render_hero() -> None:
    hero = img_data(ROOT / "assets" / "hero.webp")
    st.markdown(f'<section class="hero" id="top"><img class="hero-img" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;" src="{hero}" loading="eager" alt="A lighthouse standing alone in open water"/><div class="status"><i></i>AGENT ACTIVE · DAILY RUN</div><div class="hero-copy"><div class="eyebrow">Your next job, found daily</div><h1>An AI agent that finds<br/>your next job, every day.</h1><p class="hero-sub">EdgeDash finds job matches and shows you what to learn next.</p></div></section>', unsafe_allow_html=True)


def render_signals() -> None:
    st.markdown('<div class="signal-strip"><div class="signal">Live job discovery</div><div class="signal">Remote roles</div><div class="signal">Fresh listings</div><div class="signal">Verified data</div></div>', unsafe_allow_html=True)


def render_how() -> None:
    steps = [("01", "search", "Fetch", "Find new jobs"), ("02", "check", "Score", "Match your skills"), ("03", "spark", "Flag gaps", "Spot your next skill"), ("04", "arrow", "Verify + publish", "Show the best matches")]
    icons = {"search": '<circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/>', "check": '<path d="m5 12 4 4L19 6"/>', "spark": '<path d="m12 3 1.7 6.3L20 11l-6.3 1.7L12 19l-1.7-6.3L4 11l6.3-1.7L12 3Z"/>', "arrow": '<path d="M4 12h15M13 6l6 6-6 6"/>'}
    cards = ''.join(f'<div class="glass step-card"><div class="step-num">{n}</div><div class="step-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{icons[i]}</svg></div><div class="step-title">{t}</div><div class="step-copy">{c}</div></div>' for n, i, t, c in steps)
    st.markdown(f'<section class="section" id="how"><div class="reference-steps-intro"><div class="section-label">How it works</div><h2>From searching to <em>shortlists.</em></h2><p>One quiet loop turns a moving job market into a few clear next moves.</p></div><div class="step-grid">{cards}</div><div class="reference-emphasis">Every day, EdgeDash narrows the field.</div></section>', unsafe_allow_html=True)


def render_proof(data: dict[str, Any]) -> None:
    proof = img_data(ROOT / "assets" / "live-proof-cropped.webp")
    st.markdown(f'<section class="section" id="proof"><div class="reference-proof"><div class="reference-proof-copy"><div class="section-label">Live proof</div><h2>See it<br/><em>in action.</em></h2><p>See today’s job matches and skill gaps in the live dashboard.</p><a class="reference-link" href="{DASHBOARD_URL}">Open the live workspace&nbsp; ↗</a></div><div><img class="proof-image" src="{proof}" loading="lazy" alt="EdgeDash live dashboard showing verified job matches and skill gaps"/></div></div><div style="display:flex;justify-content:space-between;gap:20px;margin-top:14px;color:var(--muted);font:10px DM Mono,monospace"><span>● LIVE DASHBOARD, UPDATED DAILY</span><span>READ-ONLY</span></div></section>', unsafe_allow_html=True)


def render_features() -> None:
    features = [("01", "Daily automated scoring", "A fresh shortlist every day."), ("02", "Skill-gap tracking", "Know what to learn next."), ("03", "Verified accuracy", "Only show what checks out.")]
    cards = ''.join(f'<div class="flat feature-card reference-feature-card"><div class="step-num">{n}</div><div class="feature-title">{t}</div><div class="feature-copy">{c}</div></div>' for n, t, c in features)
    st.markdown(f'<section class="section" id="features"><div class="reference-feature-head"><div class="section-label">The essentials</div><h2>Less work.<br/><em>More direction.</em></h2></div><div class="feature-grid">{cards}</div></section>', unsafe_allow_html=True)


def render_final_cta() -> None:
    st.markdown('<section class="final-cta"><div><div class="section-label">Start here</div><h2>Let EdgeDash do<br/><em>the looking.</em></h2><p style="color:var(--muted);font-size:13px;margin:12px 0 0">See your latest matches.</p></div><a class="cta" href="https://edgedash-project.streamlit.app/~/+/?view=dashboard">View live dashboard&nbsp; ↗</a></section>', unsafe_allow_html=True)


def render_docs() -> None:
    st.markdown('<nav class="ed-nav"><a class="brand" href="?"><span class="brand-mark">→</span><span>edgedash</span></a><div style="display:flex;gap:10px"><a class="nav-install" href="https://edgedash-project.streamlit.app/~/+/?view=dashboard">Open dashboard</a><a class="nav-install" href="?">Back to landing</a></div></nav>', unsafe_allow_html=True)
    st.markdown('    <section class="section" style="padding-top:30px">\n      <div class="docs-hero"><div class="section-label">Documentation / Quickstart</div><h1>Run EdgeDash<br/>on your machine.</h1><p>Set up the career intelligence loop locally, run your first verified cycle, and open the dashboard in a few focused steps.</p></div>\n      <div class="flow"><div class="flow-step"><div class="step-num">01</div><strong>Install</strong>Get the code running.</div><div class="flow-step"><div class="step-num">02</div><strong>Configure</strong>Set your target profile.</div><div class="flow-step"><div class="step-num">03</div><strong>Run</strong>Fetch and score roles.</div><div class="flow-step"><div class="step-num">04</div><strong>Verify</strong>Keep only trusted data.</div><div class="flow-step"><div class="step-num">05</div><strong>Explore</strong>Open your dashboard.</div></div>\n      <div class="docs-grid">\n        <div class="flat docs-card"><div class="section-label">01 / Install</div><h3>Clone and install</h3><pre><code>git clone https://github.com/koustavdatascience/edgedash.git\ncd edgedash\npython -m venv .venv\nsource .venv/bin/activate\npip install -r requirements.txt</code></pre></div>\n        <div class="flat docs-card"><div class="section-label">02 / Configure</div><h3>Set your profile</h3><p class="feature-copy">Copy the example environment file, add your provider key, then customize your role, city, skills, keywords, weights, and thresholds in <code>config.yaml</code>.</p><pre><code>cp .env.example .env\nLLM_PROVIDER=gemini\nGEMINI_API_KEY=your_actual_api_key_here\nLLM_MODEL=gemini-1.5-flash</code></pre></div>\n        <div class="flat docs-card"><div class="section-label">03 / Run</div><h3>Start the intelligence loop</h3><pre><code>python run_cycle.py\nstreamlit run dashboard.py\npython -m edgedash.health</code></pre><p class="feature-copy">Use <code>--dry-run</code>, <code>--force scorer</code>, or <code>--explain</code> for extra visibility.</p></div>\n        <div class="flat docs-card"><div class="section-label">04 / Store</div><h3>SQLite locally. PostgreSQL when needed.</h3><p class="feature-copy">SQLite is the default. For PostgreSQL, set <code>db_backend: postgres</code> and provide <code>DATABASE_URL</code> in <code>.env</code>.</p><p class="feature-copy">The dashboard reads only from the latest passing cycle.</p></div>\n      </div>\n      <div class="flat" style="margin-top:14px"><div class="section-label">05 / Automation and tests</div><h3>Keep the loop moving</h3><p class="feature-copy">Use the daily GitHub Actions workflow or a local scheduler. Run the full suite with:</p><pre><code>python -m pytest tests/ -q\npython run_scheduler.py</code></pre></div>\n    </section>\n    <footer class="footer"><span>Built for evidence-driven career decisions.</span><a href="https://github.com/koustavdatascience/edgedash" style="color:var(--blue);text-decoration:none">View the repository →</a></footer>\n', unsafe_allow_html=True)


def main() -> None:
    inject_css()
    if st.query_params.get("page") == "docs":
        render_docs()
        return
    data = load_live_data(); render_nav(); render_hero(); render_signals(); render_how(); render_proof(data); render_features(); render_final_cta()
    st.markdown('<footer class="footer"><span>EdgeDash runs the search. You make the move.</span><a href="?page=docs" style="color:var(--blue);text-decoration:none">Read the documentation →</a></footer>', unsafe_allow_html=True)


main()
