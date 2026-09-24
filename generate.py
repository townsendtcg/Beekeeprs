#!/usr/bin/env python3
"""Builds the weekly Beekeeprs report from Sleeper's public API.

Usage:  python generate.py            (auto-detects weeks)
        WEEK=3 python generate.py     (force the preview week)
Writes docs/index.html and docs/weeks/week-N.html. Stdlib only.
"""
import json, os, math, html, statistics as st, urllib.request, datetime
from collections import defaultdict

LEAGUE_ID = "1326441943491710976"
API = "https://api.sleeper.app"
MARGIN_SD = 34.0  # std dev of the scoring margin used for win probability


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

W = int(os.environ.get("WEEK", 0)) or state["week"]
if not os.environ.get("WEEK") and done(matchups(W)):
    W += 1  # state hasn't flipped yet; that week is already final
COMPLETED = W - 1
M = {w: matchups(w) for w in range(1, W + 1)}

team = {}
for r in rosters:
    u = users.get(r["owner_id"], {})
    name = ((u.get("metadata") or {}).get("team_name") or u.get("display_name") or f"Team {r['roster_id']}").strip()
    team[r["roster_id"]] = {"name": name, "mgr": u.get("display_name", "")}
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
    recent = [w for w in range(max(1, thru - 2), thru + 1)]
    for r in IDS:
        wk = range(1, thru + 1)
        wins = sum(res[w][r][0] > res[w][r][1] for w in wk)
        ties = sum(res[w][r][0] == res[w][r][1] for w in wk)
        pf = sum(res[w][r][0] for w in wk)
        pa = sum(res[w][r][1] for w in wk)
        apw = sum(res[w][r][0] > res[w][o][0] for w in wk for o in IDS if o != r)
        apn = (len(IDS) - 1) * thru
        # recency: last 3 weeks weight 2, older weeks weight 1
        wts = {w: (2 if w in recent else 1) for w in wk}
        form = sum(res[w][r][0] * wts[w] for w in wk) / sum(wts.values())
        score = (0.25 * (wins + 0.5 * ties) / thru + 0.30 * apw / apn
                 + 0.30 * (0.5 + 0.2 * (form - mu) / sd)
                 + 0.15 * (0.5 + 0.2 * (pa / thru - mu) / sd))
        rows[r] = dict(w=wins, l=thru - wins - ties, t=ties, pf=pf, pa=pa,
                       apw=apw, apl=apn - apw, score=score)
    for i, r in enumerate(sorted(IDS, key=lambda r: -rows[r]["score"])):
        rows[r]["rank"] = i + 1
    return rows

R = ranking(COMPLETED)
PREV = ranking(COMPLETED - 1) if COMPLETED > 1 else None
pf_rank = {r: i + 1 for i, r in enumerate(sorted(IDS, key=lambda r: -R[r]["pf"]))}
pa_rank = {r: i + 1 for i, r in enumerate(sorted(IDS, key=lambda r: -R[r]["pa"]))}

def optimal(m):
    """Best legal lineup from the full roster (greedy, strict slots first)."""
    pp = m["players_points"]
    order = sorted(SLOTS, key=lambda s: len(POSOK.get(s, {s})))
    avail = sorted(m["players"], key=lambda p: -pp.get(p, 0))
    used, tot = set(), 0.0
    for s in order:
        for p in avail:
            if p not in used and pos(p) in POSOK.get(s, {s}):
                used.add(p); tot += pp.get(p, 0); break
    return round(tot, 2)

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

# ---------- last week ----------
recap = []
for a, b in pairs(COMPLETED):
    win, lose = (a, b) if a["points"] >= b["points"] else (b, a)
    sides = []
    for x in (win, lose):
        top = max(x["starters"], key=lambda p: x["players_points"].get(p, 0))
        sides.append(dict(rid=x["roster_id"], pts=x["points"], top=pname(top),
                          topv=x["players_points"].get(top, 0), opt=optimal(x),
                          swap=worst_bench_swap(x), m=x))
    recap.append(dict(mid=a["matchup_id"], w=sides[0], l=sides[1],
                      margin=round(win["points"] - lose["points"], 2)))

scores = [(s["pts"], s["rid"]) for g in recap for s in (g["w"], g["l"])]
hi, lo = max(scores), min(scores)
closest = min(recap, key=lambda g: g["margin"])
blowout = max(recap, key=lambda g: g["margin"])

# ---------- preview ----------
proj = {p["player_id"]: p.get("stats", {}) for p in get(
    f"/projections/nfl/{season}/{W}?season_type=regular&position[]=QB&position[]=RB"
    "&position[]=WR&position[]=TE&position[]=K&position[]=DEF")}

def proj_pts(pid):
    return sum(v * S[k] for k, v in proj.get(pid, {}).items() if k in S and isinstance(v, (int, float)))

def phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))

def moneyline(p):
    p = min(max(p, 0.01), 0.99)
    return f"-{round(100 * p / (1 - p))}" if p >= 0.5 else f"+{round(100 * (1 - p) / p)}"

preview = []
for a, b in pairs(W):
    sides = []
    for x in (a, b):
        st_ = [p for p in x["starters"] if p and p != "0"]
        tot = sum(proj_pts(p) for p in st_)
        q = [f"{pname(p)} ({(players.get(p) or {}).get('injury_status')})" for p in st_
             if (players.get(p) or {}).get("injury_status")]
        empty = len(x["starters"]) - len(st_)
        sides.append(dict(rid=x["roster_id"], proj=tot, q=q, empty=empty))
    p = phi((sides[0]["proj"] - sides[1]["proj"]) / MARGIN_SD)
    sides[0]["p"], sides[1]["p"] = p, 1 - p
    sides[0]["ml"], sides[1]["ml"] = moneyline(p), moneyline(1 - p)
    preview.append(dict(mid=a["matchup_id"], a=sides[0], b=sides[1], closeness=abs(p - 0.5)))

# ---------- notes (optional commish writing) ----------
notes = {}
npath = f"notes/week-{W}.json"
if os.path.exists(npath):
    with open(npath) as f:
        notes = json.load(f)

def note(section, key):
    return (notes.get(section) or {}).get(str(key))

# ---------- render ----------
E = html.escape
def f2(x): return f"{x:.2f}".rstrip("0").rstrip(".")
def rec_str(r): return f"{R[r]['w']}-{R[r]['l']}" + (f"-{R[r]['t']}" if R[r]["t"] else "")
def tname(r): return E(team[r]["name"])
def mgr(r): return E(team[r]["mgr"])

def movement(r):
    if not PREV: return '<span class="mv same">New</span>'
    d = PREV[r]["rank"] - R[r]["rank"]
    if d > 0: return f'<span class="mv up">Up {d}</span>'
    if d < 0: return f'<span class="mv down">Down {-d}</span>'
    return '<span class="mv same">No change</span>'

def auto_blurb(r):
    x = R[r]
    return (f"{rec_str(r)} with {f2(x['pf'])} points for, number {pf_rank[r]} in the league. "
            f"All-play record of {x['apw']}-{x['apl']}, and {f2(x['pa'])} points against "
            f"(number {pa_rank[r]} toughest).")

rank_html = []
for r in sorted(IDS, key=lambda r: R[r]["rank"]):
    x = R[r]
    rank_html.append(f"""
    <li class="rank">
      <div class="cell"><span>{x['rank']}</span></div>
      <div class="rbody">
        <div class="rhead"><h3>{tname(r)}</h3><span class="mgr">{mgr(r)}</span>{movement(r)}</div>
        <p class="stats"><b>{rec_str(r)}</b> record, <b>{f2(x['pf'])}</b> PF, {f2(x['pa'])} PA, all-play {x['apw']}-{x['apl']}</p>
        <p>{E(note('blurbs', r) or auto_blurb(r))}</p>
      </div>
    </li>""")

def auto_recap(g):
    w, l = g["w"], g["l"]
    s = f"{E(team[w['rid']]['name'])} by {f2(g['margin'])}."
    if l["opt"] > w["pts"]:
        s += f" {E(team[l['rid']]['name'])}'s best possible lineup ({f2(l['opt'])}) would have won it."
    return s

recap_html = []
for g in recap:
    w, l = g["w"], g["l"]
    recap_html.append(f"""
    <article class="game">
      <div class="line win"><span class="tn">{tname(w['rid'])} <small>{mgr(w['rid'])}</small></span><span class="sc">{f2(w['pts'])}</span></div>
      <div class="line"><span class="tn">{tname(l['rid'])} <small>{mgr(l['rid'])}</small></span><span class="sc">{f2(l['pts'])}</span></div>
      <p class="tops">Top scorers: {E(w['top'])} {f2(w['topv'])} / {E(l['top'])} {f2(l['topv'])}</p>
      <p>{E(note('recap', g['mid']) or '') or auto_recap(g)}</p>
    </article>""")

pain = []
for g in recap:
    for side, other in ((g["l"], g["w"]), (g["w"], g["l"])):
        sw = side["swap"]
        if not sw: continue
        bp, sp, gap = sw
        flipped = side is g["l"] and side["pts"] + gap > other["pts"]
        if gap < 5 and not flipped: continue
        tag = "Cost them the game." if flipped else ("Lost anyway." if side is g["l"] else "Won anyway.")
        pain.append((flipped, gap, f"<li><b>{tname(side['rid'])}</b>: {E(pname(bp))} scored "
                     f"{f2(side['m']['players_points'].get(bp,0))} on the bench while {E(pname(sp))} started and scored "
                     f"{f2(side['m']['players_points'].get(sp,0))}. {tag}</li>"))
pain.sort(key=lambda z: (not z[0], -z[1]))

pv = sorted(preview, key=lambda g: g["closeness"])
preview_html = []
for i, g in enumerate(pv):
    a, b = g["a"], g["b"]
    label = "Game of the week" if i == 0 else ("Lock of the week" if i == len(pv) - 1 else "")
    qs = a["q"] + b["q"]
    qline = f'<p class="q">Injury watch: {E(", ".join(qs))}</p>' if qs else ""
    empties = [team[s["rid"]]["name"] for s in (a, b) if s["empty"]]
    eline = f'<p class="q">Empty lineup spots: {E(", ".join(empties))}</p>' if empties else ""
    preview_html.append(f"""
    <article class="pick">
      {f'<p class="tag">{label}</p>' if label else ''}
      <div class="odds">
        <div><span class="tn">{tname(a['rid'])} <small>{mgr(a['rid'])} &middot; {rec_str(a['rid'])}</small></span><span class="ml">{a['ml']}</span><span class="pj">{a['proj']:.1f} proj</span></div>
        <div><span class="tn">{tname(b['rid'])} <small>{mgr(b['rid'])} &middot; {rec_str(b['rid'])}</small></span><span class="ml">{b['ml']}</span><span class="pj">{b['proj']:.1f} proj</span></div>
      </div>
      <p>{E(note('preview', g['mid']) or '')}</p>{qline}{eline}
    </article>""")

updated = datetime.datetime.now(datetime.timezone.utc).strftime("%b %d, %Y")
title = f"{E(league['name'])}: Week {W}"
intro = E(notes.get("intro", f"Week {COMPLETED} is in the books. Here's where everybody stands heading into Week {W}."))

page = open("template.html").read()
for k, v in {
    "{{TITLE}}": title, "{{LEAGUE}}": E(league["name"]), "{{WEEK}}": str(W),
    "{{DONE}}": str(COMPLETED), "{{INTRO}}": intro, "{{UPDATED}}": updated,
    "{{RANKS}}": "".join(rank_html), "{{RECAP}}": "".join(recap_html),
    "{{HI}}": f"{tname(hi[1])}, {f2(hi[0])}", "{{LO}}": f"{tname(lo[1])}, {f2(lo[0])}",
    "{{CLOSE}}": f"{tname(closest['w']['rid'])} over {tname(closest['l']['rid'])} by {f2(closest['margin'])}",
    "{{BLOW}}": f"{tname(blowout['w']['rid'])} over {tname(blowout['l']['rid'])} by {f2(blowout['margin'])}",
    "{{PAIN}}": "".join(p[2] for p in pain) or "<li>Clean week. Nobody got burned by their bench.</li>",
    "{{PREVIEW}}": "".join(preview_html), "{{SD}}": f2(MARGIN_SD),
}.items():
    page = page.replace(k, v)

os.makedirs("docs/weeks", exist_ok=True)
for path in ("docs/index.html", f"docs/weeks/week-{W}.html"):
    with open(path, "w") as f:
        f.write(page)

# machine-readable dump, handy for checking numbers
with open(f"docs/weeks/week-{W}.json", "w") as f:
    json.dump({"preview_week": W, "completed_week": COMPLETED,
               "rankings": {team[r]["name"]: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in R[r].items()} for r in IDS},
               "preview": [{team[g[s]["rid"]]["name"]: {"proj": round(g[s]["proj"], 1), "ml": g[s]["ml"]} for s in ("a", "b")} for g in preview]},
              f, indent=2, ensure_ascii=False)
print(f"Built Week {W} preview (Week {COMPLETED} recap)")
