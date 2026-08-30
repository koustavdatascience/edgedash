from datetime import datetime, timezone, timedelta
from edgedash.scoring import score_listing, build_reason

# minimal config stub — supports both attribute and dict access patterns in scoring.py
class Cfg:
    def __init__(self, **kw):
        self.target_city = kw.get("target_city", "Berlin")
        self.target_seniority = kw.get("target_seniority", "mid")
        self.my_skills = kw.get("my_skills", ["python", "sql", "excel"])
        self.skills = self.my_skills
        self.score_weights = kw.get("score_weights", {"skill_match":0.45,"seniority_fit":0.25,"location_fit":0.15,"recency":0.15})

def _listing(**kw):
    now = datetime.now(timezone.utc)
    return {
        "location": kw.get("location", "Berlin, Germany"),
        "posted_at": kw.get("posted_at", now.isoformat()),
        "title": "x", "company":"c", "url":"u", "description":"d", "source":"s", "id":"id1"
    }

def _facts(**kw):
    return {
        "required_skills": kw.get("required_skills", ["python","sql"]),
        "nice_to_have": kw.get("nice_to_have", []),
        "seniority": kw.get("seniority", "mid"),
        "years_required": kw.get("years_required", 2),
        "remote_ok": kw.get("remote_ok", False),
    }

def test_perfect_match():
    cfg = Cfg(my_skills=["python","sql","airflow"])
    facts = _facts(required_skills=["python","sql"], nice_to_have=[], seniority="mid", remote_ok=False)
    listing = _listing(location="Berlin", posted_at=datetime.now(timezone.utc).isoformat())
    r = score_listing(listing, facts, cfg)
    assert r["score"] >= 85, r  # near perfect: skill 1.0*0.45 + seniority 1.0*0.25 + location 1.0*0.15 + recency ~1.0*0.15 = 100
    assert r["components"]["skill_match"] == 1.0
    assert "gap" not in r["reason"]  # no gaps

def test_zero_match():
    cfg = Cfg(my_skills=["excel"])
    facts = _facts(required_skills=["python","sql","kubernetes","spark"], nice_to_have=["airflow"], seniority="mid")
    listing = _listing(location="Berlin", posted_at=datetime.now(timezone.utc).isoformat())
    r = score_listing(listing, facts, cfg)
    assert r["components"]["skill_match"] == 0.0
    assert r["score"] < 60  # skill 0 drags down: 0*0.45 + 1.0*0.25 + 1.0*0.15 + 1.0*0.15 = 55
    assert "gap:" in r["reason"]
    assert "python" in r["reason"]  # gaps named

def test_empty_required_skills_no_div0():
    cfg = Cfg(my_skills=["python"])
    facts = _facts(required_skills=[], nice_to_have=[], seniority="mid")
    listing = _listing()
    r = score_listing(listing, facts, cfg)
    assert r["components"]["skill_match"] == 1.0  # explicit handling, no ZeroDivision
    assert 0 <= r["score"] <= 100

def test_empty_required_skills_with_nice_to_have():
    cfg = Cfg(my_skills=["python"])
    facts = _facts(required_skills=[], nice_to_have=["python","sql","unknownskill"])
    listing = _listing()
    r = score_listing(listing, facts, cfg)
    # denom = 0 + 3/3 =1, numer =1/3 => 0.333...
    assert 0.3 < r["components"]["skill_match"] < 0.4

def test_null_posted_at_no_crash():
    cfg = Cfg()
    facts = _facts()
    for raw in [None, "", "   ", "not-a-date"]:
        listing = _listing(posted_at=raw)
        r = score_listing(listing, facts, cfg)
        assert r["components"]["recency"] == 0.5
        assert "unknown" in r["reason"] or "posted" in r["reason"]

def test_null_remote_ok():
    cfg = Cfg(target_city="Berlin")
    facts = _facts(remote_ok=None)
    listing = _listing(location="Berlin")
    r = score_listing(listing, facts, cfg)
    # location still matches Berlin => location_fit 1.0 even though remote_ok null
    assert r["components"]["location_fit"] == 1.0

    listing2 = _listing(location="Munich")
    r2 = score_listing(listing2, facts, cfg)
    assert r2["components"]["location_fit"] == 0.5  # unknown when remote null and city mismatch

def test_remote_ok_true_overrides_location():
    cfg = Cfg(target_city="Berlin")
    facts = _facts(remote_ok=True)
    listing = _listing(location="Munich, Germany")
    r = score_listing(listing, facts, cfg)
    assert r["components"]["location_fit"] == 1.0
    assert "remote" in r["reason"]

def test_seniority_three_bands_off():
    cfg = Cfg(target_seniority="junior")
    facts = _facts(seniority="lead")  # junior(0) -> lead(3) distance 3 => 0.0
    listing = _listing()
    r = score_listing(listing, facts, cfg)
    assert r["components"]["seniority_fit"] == 0.0
    # also test two bands: junior -> senior = 0.25
    facts2 = _facts(seniority="senior")
    r2 = score_listing(listing, facts2, cfg)
    assert r2["components"]["seniority_fit"] == 0.25

def test_recency_decay():
    cfg = Cfg()
    facts = _facts()
    now = datetime.now(timezone.utc)
    today = _listing(posted_at=now.isoformat())
    ago30 = _listing(posted_at=(now - timedelta(days=30)).isoformat())
    assert score_listing(today, facts, cfg)["components"]["recency"] == 1.0
    assert score_listing(ago30, facts, cfg)["components"]["recency"] == 0.0

def test_build_reason_is_deterministic_and_from_numbers():
    cfg = Cfg(my_skills=["python"])
    facts = _facts(required_skills=["python","kubernetes","spark"], seniority="senior", remote_ok=False)
    listing = _listing(location="Berlin", posted_at=datetime.now(timezone.utc).isoformat())
    r = score_listing(listing, facts, cfg)
    reason = r["reason"]
    assert "1/3" in reason or "required skills" in reason
    assert "gap:" in reason
    assert "kubernetes" in reason and "spark" in reason
    # reason is pure code output, never model text
    assert len(reason) < 200
