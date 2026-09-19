import { useEffect, useState } from "react";
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  Check,
  ChevronRight,
  Menu,
  ShieldCheck,
  Sparkles,
  Target,
  X,
} from "lucide-react";

const DASHBOARD_URL = "https://edgedash-project.streamlit.app/~/+/?view=dashboard";
const GITHUB_URL = "https://github.com/koustavdatascience/edgedash";

const steps = [
  { number: "01", title: "Fetch", text: "Find new jobs" },
  { number: "02", title: "Score", text: "Match your skills" },
  { number: "03", title: "Flag gaps", text: "Spot your next skill" },
  { number: "04", title: "Verify + publish", text: "Show the best matches" },
];

const features = [
  { icon: Activity, title: "Daily automated scoring", text: "A fresh shortlist every day." },
  { icon: Target, title: "Skill-gap tracking", text: "Know what to learn next." },
  { icon: ShieldCheck, title: "Verified accuracy", text: "Only show what checks out." },
];

function Reveal({ children, className = "", delay = 0 }: { children: React.ReactNode; className?: string; delay?: number }) {
  return (
    <div className={`reveal ${className}`} style={{ transitionDelay: `${delay}ms` }}>
      {children}
    </div>
  );
}

function DashboardPreview() {
  return (
    <div className="dashboard-frame" aria-label="Preview of the live EdgeDash dashboard">
      <div className="dashboard-topbar">
        <div className="preview-brand"><span>Edge</span>Dash</div>
        <div className="preview-status"><span className="status-dot" /> Live workspace · Supabase</div>
      </div>
      <div className="dashboard-body">
        <aside className="preview-sidebar">
          <div className="preview-workspace"><strong>Verified workspace</strong><span>Shared public dashboard</span></div>
          <span className="preview-label">Workspace</span>
          <div className="preview-nav active">▣ &nbsp; Overview</div>
          <div className="preview-nav">▤ &nbsp; Listings</div>
          <div className="preview-nav">◒ &nbsp; Skill gaps</div>
          <div className="preview-nav">◷ &nbsp; Activity log</div>
          <span className="preview-label tools-label">Tools</span>
          <div className="preview-nav">✦ &nbsp; Ask Your Data</div>
        </aside>
        <div className="preview-content">
          <div className="preview-kicker">Daily job matches</div>
          <div className="preview-heading">Welcome to EdgeDash</div>
          <div className="preview-subheading">Your latest job matches and next skills to learn.</div>
          <div className="preview-metrics">
            <div><span>Last verified</span><strong>18 Sep 2026</strong></div>
            <div><span>Total listings</span><strong>128</strong></div>
            <div><span>Scored</span><strong>96</strong></div>
            <div><span>Current verdict</span><strong className="pass">✓ pass</strong></div>
          </div>
          <div className="preview-columns">
            <div className="preview-panel listings-panel"><div className="panel-title">Top scored listings <small>10</small></div>
              {[['Senior Data Analyst','92','#b5e7c2'],['Product Analyst · Remote','86','#e9c5df'],['Analytics Specialist','78','#e8c988']].map(([name, score, color]) => (
                <div className="mini-listing" key={name}><div className="mini-score" style={{ color }}>{score}</div><div className="mini-listing-copy"><strong>{name}</strong><span>Fit signal · strong skill match</span><div className="mini-bar"><i style={{ width: `${score}%`, backgroundColor: color }} /></div></div></div>
              ))}
            </div>
            <div className="preview-panel gaps-panel"><div className="panel-title">Top skill gaps <small>10</small></div><div className="fake-chart"><i style={{ height: '54%' }} /><i style={{ height: '82%' }} /><i style={{ height: '42%' }} /><i style={{ height: '68%' }} /><i style={{ height: '30%' }} /></div><div className="gap-row"><span>SQL</span><strong>cost 18.4</strong></div><div className="gap-row"><span>Power BI</span><strong>cost 12.8</strong></div><div className="gap-row"><span>Looker</span><strong>cost 9.5</strong></div></div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Home() {
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.14 });
    document.querySelectorAll(".reveal").forEach((element) => observer.observe(element));
    return () => observer.disconnect();
  }, []);

  const closeMenu = () => setMenuOpen(false);

  return (
    <div className="site-shell">
      <header className="site-header">
        <a className="wordmark" href="#top" onClick={closeMenu}><span className="brand-mark">✦</span><span>Edge</span>Dash</a>
        <button className="mobile-menu-button" aria-label="Toggle navigation" onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? <X size={21} /> : <Menu size={21} />}</button>
        <nav className={`site-nav ${menuOpen ? "open" : ""}`}>
          <a href="#how-it-works" onClick={closeMenu}>Features</a>
          <a href={DASHBOARD_URL} target="_blank" rel="noreferrer" onClick={closeMenu}>Dashboard</a>
          <a href="#how-it-works" onClick={closeMenu}>How it works</a>
          <a href="#about" onClick={closeMenu}>About</a>
          <a href={GITHUB_URL} target="_blank" rel="noreferrer" onClick={closeMenu}>GitHub</a>
          <a className="nav-cta" href={DASHBOARD_URL} target="_blank" rel="noreferrer" onClick={closeMenu}>Open dashboard <ArrowRight size={14} /></a>
        </nav>
      </header>

      <main id="top">
        <section className="reference-hero section-pad">
          <div className="hero-art-wrap"><img className="hero-art" src="/manus-storage/ChatGPTImageSep19,2026,02_51_23AM_2aada80d.png" alt="A lighthouse standing alone in open water" /></div>
          <div className="hero-split">
            <div className="hero-title-wrap"><div className="eyebrow hero-reveal">Your next job, found daily</div><h1 aria-label="An AI agent that finds your next job, every day">An AI agent that finds<br />your next job, every day</h1></div>
            <div className="hero-details"><p className="hero-sub hero-reveal" style={{ animationDelay: "180ms" }}>EdgeDash finds job matches and shows you what to learn next.</p><div className="hero-actions hero-reveal" style={{ animationDelay: "250ms" }}><a className="button button-primary" href={DASHBOARD_URL} target="_blank" rel="noreferrer">View live dashboard <ArrowUpRight size={17} /></a><a className="text-link" href="#how-it-works">How it works <ChevronRight size={16} /></a></div></div>
          </div>
          <div className="source-strip" aria-label="Job sources"><span>Pulls from</span><strong>Arbeitnow</strong><strong>Remote roles</strong><strong>Fresh listings</strong><strong>Daily runs</strong><strong>Verified data</strong></div>
        </section>

        <section className="section-pad process-section" id="how-it-works">
          <Reveal className="process-intro"><div className="eyebrow">Four simple steps</div><h2>From searching to <em>shortlists.</em></h2><p>One quiet loop turns a moving job market into a few clear next moves.</p></Reveal>
          <div className="steps-grid">{steps.map((step, index) => <Reveal className="step" delay={index * 70} key={step.number}><span className="step-number">{step.number}</span><div className="step-line" /><h3>{step.title}</h3><p>{step.text}</p></Reveal>)}</div>
          <Reveal className="process-note"><strong>Every day, EdgeDash narrows the field.</strong><span>You see the matches worth your time — and the skill that could open the next door.</span></Reveal>
        </section>

        <section className="section-pad proof-section" id="dashboard">
          <Reveal className="proof-copy"><div className="eyebrow">Live proof</div><h2>See it<br /><em>in action.</em></h2><p>See today’s job matches and skill gaps in the live dashboard.</p><a className="text-link" href={DASHBOARD_URL} target="_blank" rel="noreferrer">Open the live workspace <ArrowUpRight size={16} /></a></Reveal>
          <Reveal className="proof-visual" delay={100}><DashboardPreview /><div className="proof-caption"><span><span className="status-dot" /> Live dashboard, updated daily.</span><span>Read-only</span></div></Reveal>
        </section>

        <section className="section-pad features-section"><Reveal className="section-intro"><div className="eyebrow">The essentials</div><h2>Less work.<br /><em>More direction.</em></h2></Reveal><div className="feature-grid">{features.map(({ icon: Icon, title, text }, index) => <Reveal className="feature-card" delay={index * 70} key={title}><Icon size={21} strokeWidth={1.6} /><span className="feature-index">0{index + 1}</span><h3>{title}</h3><p>{text}</p></Reveal>)}</div></section>

        <section className="closing-cta section-pad" id="about"><Reveal><div className="eyebrow">Start here</div><h2>Let EdgeDash do<br /><em>the looking.</em></h2><p>See your latest matches.</p><a className="button button-primary" href={DASHBOARD_URL} target="_blank" rel="noreferrer">View live dashboard <ArrowUpRight size={17} /></a></Reveal></section>
      </main>

      <footer className="site-footer"><a className="wordmark" href="#top"><span>Edge</span>Dash</a><p>Your daily job-search shortcut.</p><div><a href={GITHUB_URL} target="_blank" rel="noreferrer">GitHub <ArrowUpRight size={14} /></a><a href="#top">About <ArrowUpRight size={14} /></a></div><small>© 2026 EdgeDash</small></footer>
    </div>
  );
}
