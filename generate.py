#!/usr/bin/env python3
"""Builds the weekly Beekeeprs report from Sleeper's public API.

Usage:  python generate.py            (auto-detects the week)
        WEEK=3 python generate.py     (build a specific week; older weeks only update the archive)
Writes docs/index.html (latest week) and docs/weeks/week-N.html. Stdlib only.
"""
import json, os, re, sys, glob, math, html, subprocess, statistics as st, urllib.request, datetime
from collections import defaultdict
from zoneinfo import ZoneInfo

LEAGUE_ID = "1326441943491710976"
API = "https://api.sleeper.app"
MARGIN_SD = 34.0  # std dev of the scoring margin used for win probability
COLORS = ["#D1495B", "#00798C", "#EDAE49", "#30638E", "#6A4C93",
          "#2E933C", "#E4572E", "#8E5572", "#3D5A80", "#9C6644"]


def get(path):
    req = urllib.request.Request(API + path, headers={"User-Agent": "beekeeprs-report"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


# ---------- pull ----------
state = get("/v1/state/nfl")
league = get(f"/v1/league/{LEAGUE_ID}")
users = {u["user_id"]: u for u in get(f"/v1/league/{LEAGUE_ID}/users")}
rosters = get(f"/v1/league/{LEAGUE_ID}/rosters")
players = get("/v1/players/nfl")
season = state["season"]
S = league["scoring_settings"]
SLOTS = [s for s in league["roster_positions"] if s not in ("BN", "IR", "TAXI")]
POSOK = {"QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"}, "K": {"K"}, "DEF": {"DEF"},
         "FLEX": {"RB", "WR", "TE"}, "WRRB_FLEX": {"RB", "WR"}, "REC_FLEX": {"WR", "TE"},
         "SUPER_FLEX": {"QB", "RB", "WR", "TE"}}

def matchups(w):
    return get(f"/v1/league/{LEAGUE_ID}/matchups/{w}")

def done(ms):
    return bool(ms) and all((m.get("points") or 0) > 0 for m in ms)

# The latest week. Sleeper can lag flipping to the next week, so on Tue/Wed (ET),
# if the current week is fully scored, treat it as final and preview the next one.
LIVE_W = state["week"]
if datetime.datetime.now(ZoneInfo("America/New_York")).weekday() in (1, 2) and done(matchups(LIVE_W)):
    LIVE_W += 1
FORCED = os.environ.get("WEEK", "").strip()
W = int(FORCED) if FORCED else LIVE_W
IS_LATEST = W >= LIVE_W
COMPLETED = W - 1
M = {w: matchups(w) for w in range(1, W + 1)}

team = {}
for i, r in enumerate(sorted(rosters, key=lambda r: r["roster_id"])):
    u = users.get(r["owner_id"], {})
    meta = u.get("metadata") or {}
    name = (meta.get("team_name") or u.get("display_name") or f"Team {r['roster_id']}").strip()
    av = meta.get("avatar") or (f"https://sleepercdn.com/avatars/thumbs/{u['avatar']}" if u.get("avatar") else "")
    team[r["roster_id"]] = {"name": name, "mgr": u.get("display_name", ""), "avatar": av,
                            "color": COLORS[i % len(COLORS)]}
IDS = sorted(team)

def pname(pid):
    p = players.get(pid) or {}
    return f"{p.get('first_name','')} {p.get('last_name','')}".strip() or pid

def pos(pid):
    return (players.get(pid) or {}).get("position")

def pairs(w):
    d = defaultdict(list)
    for m in M[w]:
        if m.get("matchup_id") is not None:
            d[m["matchup_id"]].append(m)
    return [v for _, v in sorted(d.items()) if len(v) == 2]

# ---------- lineup math ----------
def best_lineup(pp, roster):
    """Best legal lineup total from a roster, given points per player (greedy, strictest slots first)."""
    order = sorted(SLOTS, key=lambda s: len(POSOK.get(s, {s})))
    avail = sorted(roster, key=lambda p: -pp.get(p, 0))
    used, tot = set(), 0.0
    for s in order:
        for p in avail:
            if p not in used and pos(p) in POSOK.get(s, {s}):
                used.add(p); tot += pp.get(p, 0); break
    return round(tot, 2)

def optimal(m):
    return best_lineup(m["players_points"], m["players"])

def worst_bench_swap(m):
    """Biggest single bench-over-starter miss for an eligible slot."""
    pp, best = m["players_points"], None
    starters = m["starters"]
    for bp in m["players"]:
        if bp in starters: continue
        for i, sp in enumerate(starters):
            if i < len(SLOTS) and pos(bp) in POSOK.get(SLOTS[i], {SLOTS[i]}):
                gap = pp.get(bp, 0) - pp.get(sp, 0)
                if gap > 0 and (best is None or gap > best[2]):
                    best = (bp, sp, gap)
    return best

# ---------- projections ----------
_proj_cache = {}
def projections(w):
    if w not in _proj_cache:
        rows = get(f"/projections/nfl/{season}/{w}?season_type=regular&position[]=QB&position[]=RB"
                   "&position[]=WR&position[]=TE&position[]=K&position[]=DEF")
        _proj_cache[w] = {p["player_id"]: sum(v * S[k] for k, v in (p.get("stats") or {}).items()
                                              if k in S and isinstance(v, (int, float))) for p in rows}
    return _proj_cache[w]

def strength(w):
    """Projected best-lineup points for each team in week w, from the roster they had that week."""
    pj = projections(w)
    return {m["roster_id"]: best_lineup(pj, m["players"]) for m in M[w]}

# ---------- results ----------
res = {w: {} for w in range(1, COMPLETED + 1)}
for w in res:
    for a, b in pairs(w):
        for x, y in ((a, b), (b, a)):
            res[w][x["roster_id"]] = (x["points"], y["points"])

def ranking(thru):
    rows = {}
    pts = [res[w][r][0] for w in range(1, thru + 1) for r in IDS]
    mu, sd = st.mean(pts), st.pstdev(pts) or 1
    nxt = strength(thru + 1)  # full-strength projection for the week ahead
    pmu, psd = st.mean(nxt.values()), st.pstdev(nxt.values()) or 1
    recent = [w for w in range(max(1, thru - 2), thru + 1)]
    n = len(IDS) - 1
    for r in IDS:
        wk = range(1, thru + 1)
        wins = sum(res[w][r][0] > res[w][r][1] for w in wk)
        ties = sum(res[w][r][0] == res[w][r][1] for w in wk)
        pf = sum(res[w][r][0] for w in wk)
        pa = sum(res[w][r][1] for w in wk)
        apw = sum(res[w][r][0] > res[w][o][0] for w in wk for o in IDS if o != r)
        apn = n * thru
        wts = {w: (2 if w in recent else 1) for w in wk}  # last 3 weeks count double
        form = sum(res[w][r][0] * wts[w] for w in wk) / sum(wts.values())
        score = (0.20 * (wins + 0.5 * ties) / thru + 0.25 * apw / apn
                 + 0.25 * (0.5 + 0.2 * (form - mu) / sd)
                 + 0.20 * (0.5 + 0.2 * (nxt[r] - pmu) / psd)
                 + 0.10 * (0.5 + 0.2 * (pa / thru - mu) / sd))
        xw = apw / n  # expected wins if you played everyone every week
        rows[r] = dict(w=wins, l=thru - wins - ties, t=ties, pf=pf, pa=pa, apw=apw,
                       apl=apn - apw, score=score, xw=xw, strength=nxt[r], luck=wins + 0.5 * ties - xw)
    for i, r in enumerate(sorted(IDS, key=lambda r: -rows[r]["score"])):
        rows[r]["rank"] = i + 1
    return rows

R = ranking(COMPLETED)
PREV = ranking(COMPLETED - 1) if COMPLETED > 1 else None

# ---------- last week ----------
recap = []
for a, b in pairs(COMPLETED):
    win, lose = (a, b) if a["points"] >= b["points"] else (b, a)
    sides = []
    for x in (win, lose):
        top = max(x["starters"], key=lambda p: x["players_points"].get(p, 0))
        sides.append(dict(rid=x["roster_id"], pts=x["points"], top=pname(top), topv=x["players_points"].get(top, 0),
                          opt=optimal(x), swap=worst_bench_swap(x), m=x))
    recap.append(dict(mid=a["matchup_id"], w=sides[0], l=sides[1], margin=round(win["points"] - lose["points"], 2)))

allsides = [s for g in recap for s in (g["w"], g["l"])]
week_rank = {s["rid"]: sorted(allsides, key=lambda z: -z["pts"]).index(s) + 1 for s in allsides}
hi = max(allsides, key=lambda s: s["pts"])
lo = min(allsides, key=lambda s: s["pts"])
closest = min(recap, key=lambda g: g["margin"])
blowout = max(recap, key=lambda g: g["margin"])
week_max = max(max(s["pts"], s["opt"]) for s in allsides)

# bench pain rows (per team, biggest single swap)
pain = []
for g in recap:
    for side, other in ((g["l"], g["w"]), (g["w"], g["l"])):
        if not side["swap"]: continue
        bp, sp, gap = side["swap"]
        lost = side is g["l"]
        flipped = lost and side["pts"] + gap > other["pts"]
        if gap < 5 and not flipped: continue
        pain.append(dict(rid=side["rid"], bp=bp, sp=sp, gap=gap, flipped=flipped, lost=lost, m=side["m"]))
pain.sort(key=lambda z: (not z["flipped"], -z["gap"]))

# awards
starters_all = [(s["m"]["players_points"].get(p, 0), p, s["rid"]) for s in allsides for p in s["m"]["starters"]]
nuke = max(starters_all)
blunder = max(allsides, key=lambda s: s["opt"] - s["pts"])
sharp = max(allsides, key=lambda s: s["pts"] / s["opt"] if s["opt"] else 0)
lucky_win = max([g["w"] for g in recap], key=lambda s: week_rank[s["rid"]])
heartbreak = min([g["l"] for g in recap], key=lambda s: week_rank[s["rid"]])

# ---------- preview ----------
def proj_pts(pid):
    return projections(W).get(pid, 0)

def phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))

def moneyline(p):
    p = min(max(p, 0.01), 0.99)
    return f"-{round(100 * p / (1 - p))}" if p >= 0.5 else f"+{round(100 * (1 - p) / p)}"

final_preview = not IS_LATEST and done(M[W])
preview = []
for a, b in pairs(W):
    sides = []
    for x in (a, b):
        st_ = [p for p in x["starters"] if p and p != "0"]
        q = [f"{pname(p)} ({(players.get(p) or {}).get('injury_status')})" for p in st_
             if (players.get(p) or {}).get("injury_status")]
        sides.append(dict(rid=x["roster_id"], proj=sum(proj_pts(p) for p in st_), q=q,
                          empty=len(x["starters"]) - len(st_), final=x.get("points") or 0))
    p = phi((sides[0]["proj"] - sides[1]["proj"]) / MARGIN_SD)
    sides[0]["p"], sides[1]["p"] = p, 1 - p
    sides[0]["ml"], sides[1]["ml"] = moneyline(p), moneyline(1 - p)
    preview.append(dict(mid=a["matchup_id"], a=sides[0], b=sides[1], closeness=abs(p - 0.5)))
pv = sorted(preview, key=lambda g: g["closeness"])

# ---------- notes (optional commish writing) ----------
notes = {}
npath = f"notes/week-{W}.json"
if os.path.exists(npath):
    with open(npath) as f:
        notes = json.load(f)

def note(section, key):
    return (notes.get(section) or {}).get(str(key))

# ---------- render helpers ----------
E = html.escape
def f2(x): return f"{x:.2f}".rstrip("0").rstrip(".")
def rec_str(r): return f"{R[r]['w']}-{R[r]['l']}" + (f"-{R[r]['t']}" if R[r]["t"] else "")
def tn(r): return E(team[r]["name"])
def mgr(r): return E(team[r]["mgr"])
def ordinal(n): return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"

def avatar(r, size="md"):
    t = team[r]
    initials = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", t["name"])[:2]).upper() or "?"
    img = (f'<img src="{E(t["avatar"])}" alt="" loading="lazy" onerror="this.remove()">' if t["avatar"] else "")
    return f'<span class="av {size}" style="--tc:{t["color"]}" aria-hidden="true"><span>{E(initials)}</span>{img}</span>'

def who(r, extra=""):
    return (f'<span class="who">{avatar(r)}<span class="nm"><b>{tn(r)}</b>'
            f'<small>{mgr(r)}{extra}</small></span></span>')

def movement(r):
    if not PREV: return '<span class="mv same">New</span>'
    d = PREV[r]["rank"] - R[r]["rank"]
    if d > 0: return f'<span class="mv up">Up {d}</span>'
    if d < 0: return f'<span class="mv down">Down {-d}</span>'
    return '<span class="mv same">No change</span>'

def auto_blurb(r):
    x = R[r]
    if x["luck"] >= 0.5: return f"The record is running ahead of the scoring. An all-play mark of {x['apw']}-{x['apl']} says some of those wins were gifts."
    if x["luck"] <= -0.5: return f"Scoring well enough to have a better record. The schedule has not been kind."
    return "The record matches the scoring. No complaints and no excuses."

# ---------- storylines ----------
stories = []
c = closest
if c["margin"] < 10:
    stories.append(("Closest call", f"{tn(c['w']['rid'])} beat {tn(c['l']['rid'])} by {f2(c['margin'])}."))
flips = [p for p in pain if p["flipped"]]
if flips:
    f = flips[0]; g = next(g for g in recap if g["l"]["rid"] == f["rid"])
    stories.append(("Self-inflicted", f"{tn(f['rid'])} benched {E(pname(f['bp']))} ({f2(f['m']['players_points'].get(f['bp'],0))}) "
                    f"and lost by {f2(g['margin'])}. {len(flips)} of {len(recap)} losers had a winning lineup on the bench."))
if week_rank[heartbreak["rid"]] <= 3:
    stories.append(("Wrong place, wrong time", f"{tn(heartbreak['rid'])} put up the {ordinal(week_rank[heartbreak['rid']])} best score of the week "
                    f"({f2(heartbreak['pts'])}) and still lost."))
fraud = max(IDS, key=lambda r: R[r]["luck"])
if R[fraud]["luck"] >= 0.5:
    stories.append(("Fraud watch", f"{tn(fraud)} is {rec_str(fraud)} with a {R[fraud]['apw']}-{R[fraud]['apl']} all-play record."))
if pv:
    g0 = pv[0]
    top2 = sorted(IDS, key=lambda r: R[r]["rank"])[:2]
    marquee = next((g for g in preview if {g["a"]["rid"], g["b"]["rid"]} == set(top2)), None)
    g = marquee or g0
    label = "Up next" if IS_LATEST else f"Week {W}"
    fav = g["a"] if g["a"]["p"] >= 0.5 else g["b"]
    dog = g["b"] if fav is g["a"] else g["a"]
    stories.append((label, f"{tn(g['a']['rid'])} vs {tn(g['b']['rid'])}. {tn(fav['rid'])} {fav['ml']}, {tn(dog['rid'])} {dog['ml']}."))
while len(stories) > 4:  # keep the preview hook; drop the weakest recap angle first
    drop = next((i for i, (k, _) in enumerate(stories) if k == "Wrong place, wrong time"), len(stories) - 2)
    stories.pop(drop)
stories_html = "".join(f'<li><b>{k}</b><span>{v}</span></li>' for k, v in stories)

# ---------- rankings ----------
rank_html = []
for r in sorted(IDS, key=lambda r: R[r]["rank"]):
    x = R[r]
    rank_html.append(f"""
    <li class="rank" style="--tc:{team[r]['color']}">
      <div class="cell"><span>{x['rank']}</span></div>
      <div class="rbody">
        <div class="rhead">{who(r)}{movement(r)}</div>
        <p class="stats"><b>{rec_str(r)}</b> &middot; {f2(x['pf'])} PF &middot; all-play {x['apw']}-{x['apl']} &middot; Wk {W} full strength {x['strength']:.1f}</p>
        <p>{E(note('blurbs', r) or auto_blurb(r))}</p>
      </div>
    </li>""")

# ---------- recap scoreboards ----------
def auto_recap(g):
    w, l = g["w"], g["l"]
    s = f"{tn(w['rid'])} by {f2(g['margin'])}."
    if l["opt"] > w["pts"]:
        s += f" {tn(l['rid'])}'s best lineup ({f2(l['opt'])}) would have won it."
    return s

recap_html = []
for g in recap:
    rows = []
    for s in (g["w"], g["l"]):
        pct = lambda v: f"{100 * v / week_max:.1f}"
        rows.append(f"""
        <div class="side" data-actual="{s['pts']}" data-best="{s['opt']}" style="--tc:{team[s['rid']]['color']}">
          {who(s['rid'])}
          <span class="sc"><span class="wtag">W</span><span class="num">{f2(s['pts'])}</span></span>
          <span class="bar"><i style="width:{pct(s['pts'])}%" data-a="{pct(s['pts'])}" data-b="{pct(s['opt'])}"></i></span>
        </div>""")
    w, l = g["w"], g["l"]
    flip = l["opt"] > w["opt"]  # winner changes when both sides play their best lineup
    recap_html.append(f"""
    <article class="game{' flip' if flip else ''}">
      {''.join(rows)}
      <p class="tops">Top scorers: {E(w['top'])} {f2(w['topv'])} / {E(l['top'])} {f2(l['topv'])}</p>
      <p class="bestnote">Left on the bench: {tn(w['rid'])} {f2(w['opt'] - w['pts'])}, {tn(l['rid'])} {f2(l['opt'] - l['pts'])}.</p>
      <p>{E(note('recap', g['mid']) or '') or auto_recap(g)}</p>
    </article>""")

# ---------- awards ----------
awards = [
    ("Nuclear performance", f"{E(pname(nuke[1]))}, {f2(nuke[0])}", tn(nuke[2])),
    ("Top score", f"{tn(hi['rid'])}, {f2(hi['pts'])}", ""),
    ("The funeral", f"{tn(lo['rid'])}, {f2(lo['pts'])}", "Lowest score of the week"),
    ("Blowout", f"{tn(blowout['w']['rid'])} by {f2(blowout['margin'])}", f"over {tn(blowout['l']['rid'])}"),
    ("Bench blunder", f"{tn(blunder['rid'])}, {f2(blunder['opt'] - blunder['pts'])} left", "Most points left on the bench"),
    ("Sharp shooter", f"{tn(sharp['rid'])}, {100 * sharp['pts'] / sharp['opt']:.0f}%", "Share of best possible lineup"),
    ("Luckiest win", f"{tn(lucky_win['rid'])}, {f2(lucky_win['pts'])}", f"Only the {ordinal(week_rank[lucky_win['rid']])} best score"),
    ("Heartbreaker", f"{tn(heartbreak['rid'])}, {f2(heartbreak['pts'])}", "Best score to take a loss"),
]
awards_html = "".join(f'<div class="award"><dt>{a}</dt><dd>{b}</dd>{f"<small>{c}</small>" if c else ""}</div>' for a, b, c in awards)

# ---------- bench pain table ----------
pain_rows = []
for p in pain:
    pp = p["m"]["players_points"]
    verdict = "Cost them the game" if p["flipped"] else ("Lost anyway" if p["lost"] else "Won anyway")
    pain_rows.append(f"""<li class="{'bad' if p['flipped'] else ''}">
      <div class="ph">{who(p['rid'])}<span class="lost"><b>{f2(p['gap'])}</b><small>pts lost</small></span></div>
      <p>Benched {E(pname(p['bp']))} ({f2(pp.get(p['bp'],0))}) and started {E(pname(p['sp']))} ({f2(pp.get(p['sp'],0))}).
      <span class="verdict">{verdict}.</span></p></li>""")
pain_html = f'<ul class="pain">{"".join(pain_rows)}</ul>' if pain_rows else "<p>Clean week. Nobody got burned by their bench.</p>"

# ---------- luck meter ----------
maxluck = max(abs(R[r]["luck"]) for r in IDS) or 1
luck_html = []
for r in sorted(IDS, key=lambda r: -R[r]["luck"]):
    x = R[r]; v = x["luck"]
    label = "Lucky" if v >= 0.5 else ("Unlucky" if v <= -0.5 else "Earned it")
    w = 50 * abs(v) / maxluck
    style = f"left:50%;width:{w:.1f}%" if v >= 0 else f"left:{50 - w:.1f}%;width:{w:.1f}%"
    luck_html.append(f"""<li>{who(r)}<span class="meter"><i class="{'pos' if v >= 0 else 'neg'}" style="{style}"></i></span>
      <span class="lk"><b>{label}</b><small>{rec_str(r)} actual, {x['xw']:.1f} expected</small></span></li>""")

# ---------- preview ----------
preview_html = []
for i, g in enumerate(pv):
    a, b = g["a"], g["b"]
    label = "Game of the week" if i == 0 else ("Lock of the week" if i == len(pv) - 1 else "")
    rows = "".join(f"""<div class="oline">{who(s['rid'], ' &middot; ' + rec_str(s['rid']))}
          <span class="ml">{s['ml']}</span><span class="pj">{('Final ' + f2(s['final'])) if final_preview else f"{s['proj']:.1f} proj"}</span></div>""" for s in (a, b))
    extra = ""
    if final_preview:
        fav = a if a["p"] >= 0.5 else b
        winner = a if a["final"] > b["final"] else b
        extra = f'<p class="q">{"Favorite held." if winner is fav else "Upset. The underdog won."}</p>'
    else:
        qs = a["q"] + b["q"]
        extra = f'<p class="q">Injury watch: {E(", ".join(qs))}</p>' if qs else ""
        empties = [team[s["rid"]]["name"] for s in (a, b) if s["empty"]]
        extra += f'<p class="q">Empty lineup spots: {E(", ".join(empties))}</p>' if empties else ""
        gaps = [(team[s["rid"]]["name"], R[s["rid"]]["strength"] - s["proj"]) for s in (a, b)]
        gaps = [f"{E(n)} has {d:.1f} more projected points available on the bench" for n, d in gaps if d >= 3]
        extra += f'<p class="q"><b>Lineup check:</b> {"; ".join(gaps)}.</p>' if gaps else ""
    n = note("preview", g["mid"])
    preview_html.append(f"""
    <article class="pick">
      {f'<p class="tag">{label}</p>' if label else ''}
      <div class="odds">{rows}</div>
      {f'<p>{E(n)}</p>' if n else ''}{extra}
    </article>""")

# ---------- week switcher ----------
os.makedirs("docs/weeks", exist_ok=True)
weeks = sorted({int(m) for f in glob.glob("docs/weeks/week-*.html") for m in re.findall(r"week-(\d+)\.html", f)} | {W})

def week_nav(prefix, latest_href):
    opts = "".join(f'<option value="{prefix}week-{w}.html"{" selected" if w == W else ""}>Week {w}</option>' for w in weeks)
    back = "" if IS_LATEST else f'<a class="latest" href="{latest_href}">Latest</a>'
    return f'<label class="wk"><span class="sr">Choose week</span><select onchange="location.href=this.value">{opts}</select></label>{back}'

# ---------- page ----------
updated = datetime.datetime.now(ZoneInfo("America/New_York")).strftime("%a %b %-d, %-I:%M %p ET")
intro = E(notes.get("intro", f"Week {COMPLETED} is in the books. Here's where everybody stands heading into Week {W}."))
base = open("template.html").read()
fill = {
    "{{TITLE}}": f"{E(league['name'])}: Week {W}", "{{LEAGUE}}": E(league["name"]), "{{WEEK}}": str(W),
    "{{DONE}}": str(COMPLETED), "{{INTRO}}": intro, "{{UPDATED}}": updated, "{{STORIES}}": stories_html,
    "{{RANKS}}": "".join(rank_html), "{{RECAP}}": "".join(recap_html), "{{AWARDS}}": awards_html,
    "{{PAIN}}": pain_html, "{{LUCK}}": "".join(luck_html), "{{PREVIEW}}": "".join(preview_html),
    "{{PREVIEW_SUB}}": (f"Moneylines use Sleeper's projections for each starting lineup, scored with league settings, then a normal curve with a {f2(MARGIN_SD)}-point spread on the margin, no vig. They move as lineups change."
                        if IS_LATEST else f"Lines rebuilt after the fact from Sleeper's Week {W} projections and the lineups that actually played, shown next to the final scores."), "{{PREVIEW_TITLE}}": f"Week {W} preview" if IS_LATEST else f"Week {W} lines and results",
}
def render(prefix, latest_href):
    page = base
    for k, v in {**fill, "{{WEEKNAV}}": week_nav(prefix, latest_href)}.items():
        page = page.replace(k, v)
    return page

with open(f"docs/weeks/week-{W}.html", "w") as f:
    f.write(render("", "../index.html"))
if IS_LATEST:
    with open("docs/index.html", "w") as f:
        f.write(render("weeks/", "index.html"))

with open(f"docs/weeks/week-{W}.json", "w") as f:
    json.dump({"preview_week": W, "completed_week": COMPLETED,
               "rankings": {team[r]["name"]: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in R[r].items()} for r in IDS},
               "optimal": {team[s["rid"]]["name"]: {"actual": s["pts"], "best": s["opt"]} for s in allsides},
               "preview": [{team[g[s]["rid"]]["name"]: {"proj": round(g[s]["proj"], 1), "ml": g[s]["ml"]} for s in ("a", "b")} for g in preview]},
              f, indent=2, ensure_ascii=False)
print(f"Built Week {W} ({'latest' if IS_LATEST else 'archive'}), recap of Week {COMPLETED}")

# Backfill any missing archive weeks, then rebuild the latest so its week picker lists them.
if IS_LATEST and not os.environ.get("NO_BACKFILL"):
    missing = [w for w in range(2, W) if not os.path.exists(f"docs/weeks/week-{w}.html")]
    for w in missing:
        subprocess.run([sys.executable, __file__], env={**os.environ, "WEEK": str(w), "NO_BACKFILL": "1"}, check=True)
    if missing:
        subprocess.run([sys.executable, __file__], env={**os.environ, "WEEK": "", "NO_BACKFILL": "1"}, check=True)
