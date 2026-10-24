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
    createdAt
    repositories(ownerAffiliations: OWNER) { totalCount }
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

    return {"years": years, "repos": repos, "commits": commits}


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


def main():
    token = os.environ.get("STATS_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("::warning::STATS_TOKEN is not set, keeping the current stats cards")
        return 0
    stats = collect(token)
    print(f"stats: {stats}")
    OUT.mkdir(exist_ok=True)
    for theme in THEMES:
        (OUT / f"stats-{theme}.svg").write_text(render(stats, theme))
    return 0


if __name__ == "__main__":
    sys.exit(main())
