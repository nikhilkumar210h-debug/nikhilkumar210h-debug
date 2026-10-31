#!/usr/bin/env python3
"""Refresh the dynamic parts of the GitHub profile.

What it does (standard library only, no pip install needed):
  1. Reads your public repositories from the GitHub API.
  2. Picks the ML / data-science ones (see config.json) and draws one animated
     SVG card per repository into assets/cards/.
  3. Rewrites the block between <!--PROJECTS:START--> and <!--PROJECTS:END-->
     in README.md so each card links to its repository.
  4. Draws assets/stats.svg with live numbers (repos, followers, stars,
     contributions in the last year).

Run locally:  GH_USER=nikhilkumar210h-debug python scripts/update_profile.py
Preview with sample data (no network):  python scripts/update_profile.py --demo
"""
import datetime
import html
import json
import os
import pathlib
import re
import sys
import textwrap
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
USER = os.environ.get("GH_USER") or "nikhilkumar210h-debug"
TOKEN = os.environ.get("GITHUB_TOKEN", "")
FONT = "'JetBrains Mono','Fira Code','Cascadia Code',Consolas,Menlo,monospace"

LANG_COLORS = {
    "Python": "#3572A5", "Jupyter Notebook": "#DA5B0B", "R": "#198CE7",
    "SQL": "#E38C00", "PLpgSQL": "#336790", "HTML": "#E34C26",
    "JavaScript": "#F1E05A", "TypeScript": "#3178C6", "C++": "#F34B7D",
    "Java": "#B07219", "Shell": "#89E051",
}

DEFAULT_CONFIG = {
    "include": [],
    "exclude": [],
    "exclude_keywords": ["trio", "grude", "website", "web", "app", "portfolio", "landing"],
    "ml_keywords": ["ml", "machine", "learning", "model", "predict", "prediction",
                    "classification", "regression", "clustering", "data", "dataset",
                    "analysis", "analytics", "eda", "kaggle", "neural", "nlp", "deep",
                    "stats", "statistics", "probability", "numpy", "pandas", "sklearn"],
    "ml_topics": ["machine-learning", "ml", "data-science", "deep-learning", "nlp",
                  "data-analysis", "eda", "kaggle", "artificial-intelligence", "ai"],
    "max_projects": 6,
}


# ---------------------------------------------------------------- helpers
def esc(s):
    return html.escape(str(s), quote=True)


def tokens(s):
    return [t for t in re.split(r"[^a-z0-9]+", str(s).lower()) if t]


def slug(name):
    return re.sub(r"[^A-Za-z0-9_.-]", "-", name)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    path = ROOT / "config.json"
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            print(f"config.json is not valid JSON ({exc}); using defaults", file=sys.stderr)
    return cfg


def api(url, payload=None):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "profile-updater"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_all():
    user = api(f"https://api.github.com/users/{USER}")
    repos, page = [], 1
    while True:
        chunk = api(f"https://api.github.com/users/{USER}/repos?per_page=100&type=owner&sort=pushed&page={page}")
        repos += chunk
        if len(chunk) < 100:
            break
        page += 1
    contrib = None
    if TOKEN:
        try:
            q = ('{ user(login:"%s"){ contributionsCollection { contributionCalendar '
                 '{ totalContributions } } } }' % USER)
            res = api("https://api.github.com/graphql", {"query": q})
            contrib = res["data"]["user"]["contributionsCollection"]["contributionCalendar"]["totalContributions"]
        except Exception as exc:  # contributions are optional
            print(f"contributions unavailable: {exc}", file=sys.stderr)
    return user, repos, contrib


def pick_projects(repos, cfg):
    by_name = {r["name"].lower(): r for r in repos}
    if cfg["include"]:
        return [by_name[n.lower()] for n in cfg["include"] if n.lower() in by_name][: cfg["max_projects"]]
    skip_names = {n.lower() for n in cfg["exclude"]} | {USER.lower()}
    skip_kw = set(cfg["exclude_keywords"])
    cands = []
    for r in repos:
        if r.get("fork") or r.get("archived") or r["name"].lower() in skip_names:
            continue
        if skip_kw & set(tokens(r["name"] + " " + (r.get("description") or ""))):
            continue
        cands.append(r)
    ml_topics, ml_kw = set(cfg["ml_topics"]), set(cfg["ml_keywords"])

    def is_ml(r):
        return bool(ml_topics & set(r.get("topics") or [])) or bool(
            ml_kw & set(tokens(r["name"] + " " + (r.get("description") or ""))))

    ml = [r for r in cands if is_ml(r)]
    chosen = ml if ml else cands
    chosen.sort(key=lambda r: r.get("pushed_at") or "", reverse=True)
    return chosen[: cfg["max_projects"]]


# ---------------------------------------------------------------- svg: shared
DEFS = """
<defs>
  <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#00F5FF"/><stop offset=".55" stop-color="#7C3AED"/><stop offset="1" stop-color="#FF2E93"/>
  </linearGradient>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#0E1526"/><stop offset="1" stop-color="#080C18"/>
  </linearGradient>
  <filter id="glow" x="-30%" y="-30%" width="160%" height="160%">
    <feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
</defs>"""


def wrap_lines(text, width, max_lines):
    text = (text or "No description yet.").strip()
    lines = textwrap.wrap(text, width) or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: max(0, width - 1)].rstrip() + "…"
    return lines


def render_card(repo, idx):
    w, h = 580, 176
    name = repo["name"]
    shown = name if len(name) <= 28 else name[:27] + "…"
    desc = wrap_lines(repo.get("description"), 48, 2)
    lang = repo.get("language") or "—"
    color = LANG_COLORS.get(repo.get("language") or "", "#8B949E")
    stars = repo.get("stargazers_count", 0)
    pushed = (repo.get("pushed_at") or "")[:10]
    bars = "".join(
        f'<rect x="{486 + i * 14}" y="{140 - hh}" width="8" height="{hh}" rx="2" fill="url(#g)" opacity=".85">'
        f'<animate attributeName="height" values="{hh};{hh + 18};{hh}" dur="{1.6 + i * .25:.2f}s" repeatCount="indefinite"/>'
        f'<animate attributeName="y" values="{140 - hh};{122 - hh};{140 - hh}" dur="{1.6 + i * .25:.2f}s" repeatCount="indefinite"/></rect>'
        for i, hh in enumerate([10, 22, 16, 30, 20]))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(name)}">
{DEFS}
<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="14" fill="url(#bg)" stroke="#1F2A44"/>
<rect x="1.5" y="1.5" width="{w - 3}" height="{h - 3}" rx="14" fill="none" stroke="url(#g)" stroke-width="2" stroke-dasharray="150 1330" filter="url(#glow)">
  <animate attributeName="stroke-dashoffset" from="0" to="-1480" dur="6s" repeatCount="indefinite"/>
</rect>
<text x="24" y="32" font-family="{FONT}" font-size="13" fill="#6B7A99" letter-spacing="2">// PROJECT {idx:02d}</text>
<text x="24" y="68" font-family="{FONT}" font-size="26" font-weight="700" fill="#00F5FF">{esc(shown)}</text>
<text x="24" y="98" font-family="{FONT}" font-size="16" fill="#A8B3CF">{esc(desc[0])}</text>
<text x="24" y="120" font-family="{FONT}" font-size="16" fill="#A8B3CF">{esc(desc[1]) if len(desc) > 1 else ""}</text>
<circle cx="31" cy="150" r="6" fill="{color}"/>
<text x="44" y="155" font-family="{FONT}" font-size="15" fill="#E5E7EB">{esc(lang)}</text>
<text x="190" y="155" font-family="{FONT}" font-size="15" fill="#FACC15">★ {stars}</text>
<text x="262" y="155" font-family="{FONT}" font-size="15" fill="#6B7A99">updated {esc(pushed)}</text>
{bars}
</svg>
"""


def render_stats(vals):
    def fmt(v):
        return "—" if v is None else f"{v:,}"

    tiles = [("PUBLIC REPOS", vals.get("repos")), ("FOLLOWERS", vals.get("followers")),
             ("TOTAL STARS", vals.get("stars")), ("CONTRIBUTIONS · 1Y", vals.get("contrib"))]
    out = []
    for i, (label, v) in enumerate(tiles):
        x = 24 + i * 292
        out.append(f"""<g transform="translate({x},24)">
  <rect width="268" height="122" rx="14" fill="url(#bg)" stroke="#1F2A44"/>
  <rect x="0" y="0" width="268" height="122" rx="14" fill="none" stroke="url(#g)" stroke-width="1.5" stroke-dasharray="60 640" filter="url(#glow)">
    <animate attributeName="stroke-dashoffset" from="0" to="-700" dur="{5 + i}s" repeatCount="indefinite"/>
  </rect>
  <text x="24" y="38" font-family="{FONT}" font-size="14" fill="#6B7A99" letter-spacing="2">{label}</text>
  <text x="24" y="86" font-family="{FONT}" font-size="46" font-weight="700" fill="url(#g)">{fmt(v)}</text>
  <rect x="24" y="100" width="40" height="3" rx="1.5" fill="#00F5FF"><animate attributeName="width" values="40;220;40" dur="4s" repeatCount="indefinite"/></rect>
</g>""")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="170" viewBox="0 0 1200 170" role="img" aria-label="GitHub statistics">
{DEFS}
{''.join(out)}
</svg>
"""


# ---------------------------------------------------------------- readme
START, END = "<!--PROJECTS:START-->", "<!--PROJECTS:END-->"


def update_readme(block):
    path = ROOT / "README.md"
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pattern.search(text):
        raise SystemExit("README.md must contain the PROJECTS:START / PROJECTS:END markers")
    path.write_text(pattern.sub(lambda _m: f"{START}\n{block}\n{END}", text), encoding="utf-8")


def build_block(projects):
    cells = []
    for r in projects:
        cells.append(f'<a href="{esc(r["html_url"])}"><img src="assets/cards/{esc(slug(r["name"]))}.svg" '
                     f'width="49%" alt="{esc(r["name"])}" /></a>')
    return '<div align="center">\n' + "\n".join(cells) + "\n</div>"


def main(demo=False):
    cfg = load_config()
    if demo:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        user = {"public_repos": 14, "followers": 23}
        contrib = 612
        repos = [
            {"name": "house-price-prediction", "description": "Predicting house prices with EDA, feature engineering and model comparison on a real dataset",
             "language": "Jupyter Notebook", "stargazers_count": 3, "pushed_at": now, "html_url": "https://github.com/demo/house-price-prediction", "topics": ["machine-learning"]},
            {"name": "eda-titanic", "description": "Complete exploratory data analysis with Pandas and Seaborn",
             "language": "Python", "stargazers_count": 1, "pushed_at": now, "html_url": "https://github.com/demo/eda-titanic", "topics": []},
            {"name": "stats-for-ml-practice", "description": "Hypothesis tests, distributions and probability simulations in NumPy",
             "language": "Python", "stargazers_count": 0, "pushed_at": now, "html_url": "https://github.com/demo/stats-for-ml-practice", "topics": []},
            {"name": "trio_day_app", "description": "web app", "language": "JavaScript", "stargazers_count": 0,
             "pushed_at": now, "html_url": "https://github.com/demo/trio", "topics": []},
        ]
    else:
        user, repos, contrib = fetch_all()

    projects = pick_projects(repos, cfg)
    cards = ROOT / "assets" / "cards"
    cards.mkdir(parents=True, exist_ok=True)
    keep = {"placeholder.svg"}
    for i, r in enumerate(projects, 1):
        fname = slug(r["name"]) + ".svg"
        keep.add(fname)
        (cards / fname).write_text(render_card(r, i), encoding="utf-8")
    for old in cards.glob("*.svg"):
        if old.name not in keep:
            old.unlink()

    own = [r for r in repos if not r.get("fork")]
    stats = {"repos": user.get("public_repos"), "followers": user.get("followers"),
             "stars": sum(r.get("stargazers_count", 0) for r in own), "contrib": contrib}
    (ROOT / "assets" / "stats.svg").write_text(render_stats(stats), encoding="utf-8")

    if projects:
        update_readme(build_block(projects))
    print(f"projects: {[r['name'] for r in projects]}")
    print(f"stats: {stats}")


if __name__ == "__main__":
    main(demo="--demo" in sys.argv)
