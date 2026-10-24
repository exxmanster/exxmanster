#!/usr/bin/env python3
"""Render the profile stats card (light + dark SVG) from real GitHub data.

Counts public AND private repos, so it needs a token that can read them:
STATS_TOKEN in GitHub Actions, or GH_TOKEN when run locally.
Without a token it exits quietly and leaves the current cards untouched.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://api.github.com/graphql"
OUT = Path(__file__).resolve().parent.parent / "assets"

PROFILE_QUERY = """
query {
  viewer {
    id
    login
    createdAt
    repositories(ownerAffiliations: OWNER) { totalCount }
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date weekday contributionCount contributionLevel } }
      }
    }
  }
}
"""

# Your own commits on the default branch of every repo you own (forks skipped).
COMMITS_QUERY = """
query($after: String, $since: GitTimestamp!, $uid: ID!) {
  viewer {
    repositories(ownerAffiliations: OWNER, first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        isFork
        defaultBranchRef {
          target { ... on Commit { history(since: $since, author: {id: $uid}) { totalCount } } }
        }
      }
    }
  }
}
"""


def gql(token, query, variables):
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.load(resp)
    if body.get("errors"):
        raise SystemExit(f"GraphQL error: {body['errors']}")
    return body["data"]["viewer"]


def collect(token):
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")

    profile = gql(token, PROFILE_QUERY, {})
    created = datetime.fromisoformat(profile["createdAt"].replace("Z", "+00:00"))
    years = (now - created).days // 365
    repos = profile["repositories"]["totalCount"]

    commits, after = 0, None
    while True:
        page = gql(token, COMMITS_QUERY, {"after": after, "since": since, "uid": profile["id"]})
        for repo in page["repositories"]["nodes"]:
            ref = repo["defaultBranchRef"]
            if repo["isFork"] or not ref or not ref["target"]:
                continue
            commits += ref["target"]["history"]["totalCount"]
        info = page["repositories"]["pageInfo"]
        if not info["hasNextPage"]:
            break
        after = info["endCursor"]

    calendar = profile["contributionsCollection"]["contributionCalendar"]
    return {
        "years": years,
        "repos": repos,
        "commits": commits,
        "login": profile["login"],
        "contributions": calendar["totalContributions"],
        "weeks": [w["contributionDays"] for w in calendar["weeks"]],
    }


THEMES = {
    "light": {"tile": "#f6f8fa", "border": "#d0d7de", "num": "#1a7f37", "label": "#59636e"},
    "dark": {"tile": "#161b22", "border": "#30363d", "num": "#3fb950", "label": "#8b949e"},
}

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"


def render(stats, theme):
    c = THEMES[theme]
    tiles = [
        (stats["years"], "years on GitHub"),
        (stats["repos"], "repositories"),
        (stats["commits"], "commits in the last year"),
    ]
    w, h, gap = 270, 112, 15
    width = 3 * w + 2 * gap
    parts = []
    for i, (value, label) in enumerate(tiles):
        x = i * (w + gap)
        parts.append(f"""
  <g>
    <rect x="{x + 0.5}" y="0.5" width="{w - 1}" height="{h - 1}" rx="10" fill="{c['tile']}" stroke="{c['border']}"/>
    <text x="{x + 22}" y="64" class="num">{value}</text>
    <text x="{x + 22}" y="92" class="label">{label}</text>
  </g>""")
    title = f"{stats['years']} years on GitHub, {stats['repos']} repositories, {stats['commits']} commits in the last year"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{h}" viewBox="0 0 {width} {h}" role="img" aria-label="{title}">
  <title>{title}</title>
  <style>
    .num {{ font: 700 44px {FONT}; fill: {c['num']}; }}
    .label {{ font: 400 17px {FONT}; fill: {c['label']}; }}
  </style>{''.join(parts)}
</svg>
"""


MONO = "ui-monospace, 'SF Mono', Menlo, Consolas, 'DejaVu Sans Mono', 'Liberation Mono', monospace"
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
LEVELS = ["NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE"]
CELL_CHARS = ["·", "░", "▒", "▓", "█"]
CELL_COLORS = ["#30363d", "#2ea043", "#3fb950", "#56d364", "#7ee787"]


def summarize(weeks):
    days = [d for w in weeks for d in w]
    active = sum(1 for d in days if d["contributionCount"])
    longest = run = 0
    for d in days:
        run = run + 1 if d["contributionCount"] else 0
        longest = max(longest, run)
    # Like GitHub: today without contributions yet doesn't break the streak.
    tail = days[:-1] if days and not days[-1]["contributionCount"] else days
    current = 0
    for d in reversed(tail):
        if not d["contributionCount"]:
            break
        current += 1
    peak = max(days, key=lambda d: d["contributionCount"])
    return {"active": active, "longest": longest, "current": current, "peak": peak}


def render_terminal(stats):
    """The contribution calendar as terminal output: two block glyphs per day."""
    weeks = stats["weeks"]
    s = summarize(weeks)
    fs, cw, pitch, lh = 13, 7.6, 17, 17  # font size, glyph advance, cell pitch, row height
    pad, label_w = 20, 4 * cw + 8
    width = round(pad * 2 + label_w + len(weeks) * pitch)
    gx = pad + label_w  # grid x origin

    y = 30 + 26  # below the title bar
    out = []

    def text(x, y, s_, fill, extra=""):
        out.append(f'<text x="{x:.1f}" y="{y}" fill="{fill}"{extra}>{s_}</text>')

    text(pad, y, "$", "#3fb950")
    text(pad + 2 * cw, y, f"contrib --user {stats['login']} --last 365d", "#e6edf3")
    y += lh + 10

    # month labels where the month of a week's first day changes,
    # dropping one that would run into the next label
    month_of = lambda week: int(week[0]["date"][5:7])
    labels = [(i, month_of(w)) for i, w in enumerate(weeks) if i == 0 or month_of(w) != month_of(weeks[i - 1])]
    for k, (i, month) in enumerate(labels):
        if k + 1 < len(labels) and labels[k + 1][0] - i < 2:
            continue
        text(gx + i * pitch, y, MONTHS[month - 1], "#6e7681")
    y += lh

    for wd in range(7):
        if wd in (1, 3, 5):
            text(pad, y, ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][wd], "#6e7681")
        spans, run_level, xs = [], None, []
        for i, week in enumerate(weeks):
            day = next((d for d in week if d["weekday"] == wd), None)
            if day is None:
                continue
            level = LEVELS.index(day["contributionLevel"])
            if level != run_level and xs:
                spans.append((run_level, xs))
                xs = []
            run_level = level
            x = gx + i * pitch
            xs += [x, x + cw]
        if xs:
            spans.append((run_level, xs))
        tspans = "".join(
            f'<tspan x="{" ".join(f"{v:.1f}" for v in xs)}" fill="{CELL_COLORS[lv]}">{CELL_CHARS[lv] * len(xs)}</tspan>'
            for lv, xs in spans
        )
        out.append(f'<text y="{y}">{tspans}</text>')
        y += lh

    y += 14
    peak = s["peak"]
    peak_date = f"{MONTHS[int(peak['date'][5:7]) - 1]} {int(peak['date'][8:10])}"
    rows = [
        ("total", f"{stats['contributions']} contributions"),
        ("active", f"{s['active']} days"),
        ("streak", f"{s['current']} days now, longest {s['longest']}"),
        ("peak", f"{peak['contributionCount']} on {peak_date}"),
    ]
    legend_y = y
    for key, value in rows:
        text(pad, y, key, "#79c0ff")
        text(pad + 9 * cw, y, value, "#e6edf3")
        y += lh + 2
    # legend on the right of the summary
    lx = width - pad - (4 + 5 * 3 + 4) * cw
    text(lx, legend_y, "less", "#6e7681")
    for lv in range(5):
        x = lx + (5 + lv * 3) * cw
        out.append(f'<text x="{x:.1f} {x + cw:.1f}" y="{legend_y}" fill="{CELL_COLORS[lv]}">{CELL_CHARS[lv] * 2}</text>')
    text(lx + (5 + 15) * cw, legend_y, "more", "#6e7681")

    y += 6
    text(pad, y, "$", "#3fb950")
    out.append(f'<rect class="cursor" x="{pad + 2 * cw:.1f}" y="{y - fs + 1}" width="{cw:.1f}" height="{fs + 2}" fill="#e6edf3"/>')
    height = y + 22

    title = (f"{stats['contributions']} contributions in the last year, {s['active']} active days, "
             f"current streak {s['current']} days, longest {s['longest']}")
    body = "\n  ".join(out)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{title}">
  <title>{title}</title>
  <style>
    text {{ font: 400 {fs}px {MONO}; white-space: pre; }}
    .bar {{ font: 400 12px {FONT}; }}
    .cursor {{ animation: blink 1.1s steps(1) infinite; }}
    @keyframes blink {{ 50% {{ opacity: 0; }} }}
  </style>
  <rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="10" fill="#010409" stroke="#30363d"/>
  <path d="M0.5 30 V10.5 a10 10 0 0 1 10 -10 H{width - 10.5} a10 10 0 0 1 10 10 V30 Z" fill="#161b22"/>
  <line x1="0.5" y1="30" x2="{width - 0.5}" y2="30" stroke="#30363d"/>
  <circle cx="20" cy="15" r="6" fill="#ff5f57"/><circle cx="40" cy="15" r="6" fill="#febc2e"/><circle cx="60" cy="15" r="6" fill="#28c840"/>
  <text class="bar" x="{width / 2:.1f}" y="19" fill="#7d8590" text-anchor="middle">{stats['login']}@prod: ~</text>
  {body}
</svg>
"""


def main():
    token = os.environ.get("STATS_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("::warning::STATS_TOKEN is not set, keeping the current stats cards")
        return 0
    stats = collect(token)
    print(f"stats: years={stats['years']} repos={stats['repos']} commits={stats['commits']} "
          f"contributions={stats['contributions']}")
    OUT.mkdir(exist_ok=True)
    for theme in THEMES:
        (OUT / f"stats-{theme}.svg").write_text(render(stats, theme))
    (OUT / "contrib-terminal.svg").write_text(render_terminal(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
