"""
Revision Timetable Manager  -  run with:  streamlit run revision_timetable.py

Features
- Add as many subjects as you like, each with its own colour
- Add blocked activities (football, music, etc.) on one or more days, with colours
- Pick your own start and finish time for every day (00:00 - 24:00)
- Daily dinner break (time and length adjustable)
- Choose the app colour palette (red / green / blue) and the break block colour
- Timetable is generated automatically in 30-minute chunks:
  revision, break, revision, break ...  (always 1:1 revision to break)
- Subjects are shared out equally and placed randomly through the week
- "Shuffle" button gives you a fresh random layout
- On a phone it automatically shows just today (swipe through the other days)
- Save your setup to a file and load it again next time
- If a saved setup file is found when the app starts, it offers to load it
"""

import html
import json
import random
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

# ----------------------------------------------------------------------------
# Constants / helpers
# ----------------------------------------------------------------------------
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
PALETTE = [
    "#FF8A8A", "#FFB020", "#8BC34A", "#4F7CBA", "#1AAEE0",
    "#B07CD8", "#F06FA8", "#2EC4B6", "#E8C547", "#FF7F50",
]
# app colour themes: accent = title, head = day header cells, time = time column
THEMES = {
    "Red":   {"accent": "#FF8A8A", "head": "#FF9999", "time": "#FFDDDD"},
    "Green": {"accent": "#5DB075", "head": "#8FD19E", "time": "#DFF3E3"},
    "Blue":  {"accent": "#5B9BE0", "head": "#8EBBF0", "time": "#DCEBFB"},
}
VIEWS = ["Auto-detect", "Phone (one day)", "Full week"]
SLOT_MIN = 30
START_OPTS = list(range(0, 48))   # 00:00 ... 23:30
END_OPTS = list(range(1, 49))     # 00:30 ... 24:00
DINNER_LENGTHS = [1, 2, 3, 4]     # in half-hour slots
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Every setting lives in st.session_state under its widget key, so it can be
# saved to / loaded from a file.
SETTING_DEFAULTS = {
    "same_hours": False,
    "same_days": DAYS[:],
    "all_start": 32,            # 16:00
    "all_end": 42,              # 21:00
    "max_hours": 6.0,
    "dinner_on": True,
    "dinner_start": 36,         # 18:00
    "dinner_len": 2,            # 1 hour
    "dinner_col": "#8FBF8F",
    "palette": "Red",
    "break_match": True,
    "break_col": "#E6E6E6",
    "view": VIEWS[0],
}
for _d in range(7):
    SETTING_DEFAULTS[f"on_{_d}"] = True
    SETTING_DEFAULTS[f"start_{_d}"] = 20 if _d >= 5 else 32    # weekend 10:00, weekday 16:00
    SETTING_DEFAULTS[f"end_{_d}"] = 34 if _d >= 5 else 42      # weekend 17:00, weekday 21:00

# The "add subject" / "add activity" form fields (not saved to the setup file).
FORM_DEFAULTS = {
    "new_subject_name": "",
    "new_subject_colour": PALETTE[0],
    "act_name": "",
    "act_days": ["Tuesday"],
    "act_from": 36,             # 18:00
    "act_to": 39,               # 19:30
    "act_colour": "#7FB77E",
}


def label(slot: int) -> str:
    h, m = divmod(slot * SLOT_MIN, 60)
    return f"{h:02d}:{m:02d}"


def length_label(slots: int) -> str:
    mins = slots * SLOT_MIN
    if mins < 60:
        return f"{mins} min"
    hrs = mins / 60
    return f"{hrs:g} hour" + ("" if hrs == 1 else "s")


def text_colour(hex_colour: str) -> str:
    """Black or white text depending on how light the background is."""
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#111111" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#FFFFFF"


# ----------------------------------------------------------------------------
# Phone detection + "what day is it for this visitor"
# ----------------------------------------------------------------------------
def looks_like_phone(user_agent: str) -> bool:
    ua = (user_agent or "").lower()
    if "ipad" in ua or "tablet" in ua:
        return False
    return bool(re.search(r"iphone|ipod|android.+mobile|windows phone|blackberry|opera mini|mobile", ua))


def visitor_is_phone() -> bool:
    try:
        return looks_like_phone(st.context.headers.get("User-Agent", ""))
    except Exception:
        return False


def today_index() -> int:
    """Monday=0 ... Sunday=6, in the visitor's own timezone when we can find it."""
    now = None
    try:
        tz = st.context.timezone
        if tz:
            now = datetime.now(ZoneInfo(tz))
    except Exception:
        now = None
    return (now or datetime.now()).weekday()


# ----------------------------------------------------------------------------
# Session state
# ----------------------------------------------------------------------------
def init_state():
    ss = st.session_state
    ss.setdefault("subjects", [])      # {id, name, color}
    ss.setdefault("activities", [])    # {id, name, day, start, end, color}
    ss.setdefault("next_id", 1)
    ss.setdefault("seed", random.randint(0, 10**9))
    for k, v in {**SETTING_DEFAULTS, **FORM_DEFAULTS}.items():
        ss.setdefault(k, v[:] if isinstance(v, list) else v)


def new_id() -> int:
    st.session_state.next_id += 1
    return st.session_state.next_id


def add_subject(name: str, colour: str):
    st.session_state.subjects.append({"id": new_id(), "name": name.strip(), "color": colour})


def remove_subject(sid: int):
    st.session_state.subjects = [s for s in st.session_state.subjects if s["id"] != sid]


def remove_activity(aid: int):
    st.session_state.activities = [a for a in st.session_state.activities if a["id"] != aid]


def shuffle():
    st.session_state.seed = random.randint(0, 10**9)


# ----------------------------------------------------------------------------
# Save / load a setup file
# ----------------------------------------------------------------------------
def _is_int_in(opts):
    return lambda x: type(x) is int and x in opts


def _is_hex(x):
    return isinstance(x, str) and HEX_RE.match(x) is not None


def _is_hours(x):
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and 0.5 <= x <= 12 and float(x) * 2 == int(float(x) * 2))


SETTING_RULES = {
    "same_hours": lambda x: isinstance(x, bool),
    "same_days": lambda x: isinstance(x, list) and len(x) <= 7 and all(i in DAYS for i in x),
    "all_start": _is_int_in(START_OPTS),
    "all_end": _is_int_in(END_OPTS),
    "max_hours": _is_hours,
    "dinner_on": lambda x: isinstance(x, bool),
    "dinner_start": _is_int_in(START_OPTS),
    "dinner_len": _is_int_in(DINNER_LENGTHS),
    "dinner_col": _is_hex,
    "palette": lambda x: x in THEMES,
    "break_match": lambda x: isinstance(x, bool),
    "break_col": _is_hex,
    "view": lambda x: x in VIEWS,
}
for _d in range(7):
    SETTING_RULES[f"on_{_d}"] = lambda x: isinstance(x, bool)
    SETTING_RULES[f"start_{_d}"] = _is_int_in(START_OPTS)
    SETTING_RULES[f"end_{_d}"] = _is_int_in(END_OPTS)


def export_setup() -> str:
    ss = st.session_state
    data = {
        "app": "revision-timetable",
        "version": 1,
        "seed": ss.seed,
        "subjects": [{"name": s["name"], "color": s["color"]} for s in ss.subjects],
        "activities": [
            {"name": a["name"], "day": a["day"], "start": a["start"], "end": a["end"], "color": a["color"]}
            for a in ss.activities
        ],
        "settings": {k: ss[k] for k in SETTING_DEFAULTS if k in ss},
    }
    return json.dumps(data, indent=2)


def parse_setup(text: str):
    """Returns (pending_dict, None) or (None, error_message). Everything is validated."""
    if len(text) > 200_000:
        return None, "That file is too big to be a setup file."
    try:
        data = json.loads(text)
    except ValueError:
        return None, "Couldn't read that file. Is it a setup file you saved from this app?"
    if not isinstance(data, dict) or data.get("app") != "revision-timetable":
        return None, "That doesn't look like a setup file from this app."

    counter = 1

    def nid():
        nonlocal counter
        counter += 1
        return counter

    subjects, seen = [], set()
    raw_subjects = data.get("subjects", [])
    for s in (raw_subjects if isinstance(raw_subjects, list) else [])[:100]:
        if not isinstance(s, dict):
            continue
        name = s.get("name")
        if not (isinstance(name, str) and 1 <= len(name.strip()) <= 40 and _is_hex(s.get("color"))):
            continue
        if name.strip().lower() in seen:
            continue
        seen.add(name.strip().lower())
        subjects.append({"id": nid(), "name": name.strip(), "color": s["color"]})

    activities = []
    raw_acts = data.get("activities", [])
    for a in (raw_acts if isinstance(raw_acts, list) else [])[:200]:
        if not isinstance(a, dict):
            continue
        name, day, st_, en, col = (a.get(k) for k in ("name", "day", "start", "end", "color"))
        if not (isinstance(name, str) and 1 <= len(name.strip()) <= 40 and _is_hex(col)):
            continue
        if not (type(day) is int and 0 <= day <= 6 and type(st_) is int and type(en) is int
                and 0 <= st_ < en <= 48):
            continue
        if any(x["day"] == day and st_ < x["end"] and x["start"] < en for x in activities):
            continue
        activities.append({"id": nid(), "name": name.strip(), "day": day,
                           "start": st_, "end": en, "color": col})

    settings = {}
    raw_settings = data.get("settings", {})
    for k, v in (raw_settings.items() if isinstance(raw_settings, dict) else []):
        if k in SETTING_RULES and SETTING_RULES[k](v):
            settings[k] = float(v) if k == "max_hours" else (list(dict.fromkeys(v)) if k == "same_days" else v)

    seed = data.get("seed")
    return {
        "subjects": subjects,
        "activities": activities,
        "settings": settings,
        "next_id": counter,
        "seed": seed if type(seed) is int and 0 <= seed <= 10**9 else random.randint(0, 10**9),
    }, None


def apply_pending():
    """Runs at the very start of a run, before any widget exists, so keys can be set safely."""
    p = st.session_state.pop("_pending", None)
    if p is None:
        return
    for k in list(st.session_state.keys()):
        if k in SETTING_DEFAULTS or k.startswith(("col_", "actcol_", "start_", "end_", "on_")):
            del st.session_state[k]
    st.session_state.subjects = p["subjects"]
    st.session_state.activities = p["activities"]
    st.session_state.next_id = p["next_id"]
    st.session_state.seed = p["seed"]
    for k, v in p["settings"].items():
        st.session_state[k] = v
    st.session_state["_offer_done"] = True
    st.toast("Setup loaded")


# ----------------------------------------------------------------------------
# Startup offer: "found a saved setup, want to load it?"
# ----------------------------------------------------------------------------
SETUP_GLOB = "revision_setup*.json"      # revision_setup.json, "revision_setup (1).json", ...


def running_locally() -> bool:
    """True only when the page is opened on the same machine that runs the app (localhost)."""
    try:
        host = st.context.headers.get("Host", "")
    except Exception:
        return False
    return host.startswith(("localhost", "127.0.0.1", "[::1]"))


def candidate_folders():
    folders = []
    try:
        folders.append(Path(__file__).resolve().parent)     # next to this script
    except NameError:
        pass
    folders.append(Path.cwd())
    if running_locally():
        folders.append(Path.home() / "Downloads")           # where "Save my setup" lands
    return list(dict.fromkeys(folders))


def find_saved_setup():
    """Newest valid saved-setup file in the usual places, or None. Only ever reads."""
    best = None
    for folder in candidate_folders():
        try:
            files = list(folder.glob(SETUP_GLOB))
        except OSError:
            continue
        for f in files:
            try:
                info = f.stat()
                if not f.is_file() or info.st_size > 200_000:
                    continue
                if best is None or info.st_mtime > best[0]:
                    pending, err = parse_setup(f.read_text(encoding="utf-8"))
                    if err is None and (pending["subjects"] or pending["activities"]):
                        best = (info.st_mtime, f, pending)
            except (OSError, UnicodeDecodeError):
                continue
    if best is None:
        return None
    mtime, f, pending = best
    return {"name": f.name, "folder": f.parent.name, "mtime": mtime, "pending": pending}


def _accept_offer():
    st.session_state["_pending"] = st.session_state["_offer"]["pending"]


def _decline_offer():
    st.session_state["_offer_done"] = True


def startup_offer():
    """On a fresh session, if a saved setup file is around, offer to load it."""
    ss = st.session_state
    if ss.get("_offer_done") or ss.subjects or ss.activities:
        return
    if "_offer" not in ss:
        ss["_offer"] = find_saved_setup()
    offer = ss["_offer"]
    if not offer:
        return
    p = offer["pending"]
    name = re.sub(r"[^\w .()\-]", "", offer["name"])
    folder = re.sub(r"[^\w .()\-]", "", offer["folder"])
    when = datetime.fromtimestamp(offer["mtime"]).strftime("%d %b, %H:%M")
    with st.container(border=True):
        st.markdown(
            f"**Found a saved setup:** `{name}` (in *{folder}*, saved {when}) with "
            f"{len(p['subjects'])} subject{'s' if len(p['subjects']) != 1 else ''} and "
            f"{len(p['activities'])} activit{'ies' if len(p['activities']) != 1 else 'y'}. Load it?"
        )
        c1, c2, _ = st.columns([1, 1, 3])
        c1.button("Load it", key="offer_load", type="primary", on_click=_accept_offer, use_container_width=True)
        c2.button("Start fresh", key="offer_skip", on_click=_decline_offer, use_container_width=True)


# ----------------------------------------------------------------------------
# Dinner
# ----------------------------------------------------------------------------
def dinner_blocks(cfg, activities):
    """
    One 'Dinner' block per revision day, as long as it starts inside that day's
    study hours. If it clashes with one of your own activities that day, it is
    skipped for that day. Returns (blocks, skipped_day_numbers).
    """
    dn = cfg["dinner"]
    if not dn["on"]:
        return [], []
    s, e = dn["start"], min(48, dn["start"] + dn["len"])
    blocks, skipped = [], []
    for d, (ws, we) in cfg["hours"].items():
        if not (ws <= s < we):
            continue
        if any(a["day"] == d and s < a["end"] and a["start"] < e for a in activities):
            skipped.append(d)
            continue
        blocks.append({"id": f"dinner{d}", "name": "Dinner", "day": d,
                       "start": s, "end": e, "color": dn["color"]})
    return blocks, sorted(skipped)


# ----------------------------------------------------------------------------
# Timetable generation
# ----------------------------------------------------------------------------
def spread(items, k, rng):
    """Pick k items spread evenly across the list (one random pick per bin)."""
    n = len(items)
    if k >= n:
        return items
    chosen = []
    for i in range(k):
        lo = -((-i * n) // k)            # ceil(i*n/k)
        hi = -((-(i + 1) * n) // k)      # ceil((i+1)*n/k), exclusive
        chosen.append(items[rng.randrange(lo, max(hi, lo + 1))])
    return chosen


def generate(subjects, blocks, cfg, seed):
    """
    Returns ({(day, slot): ("rev", subject) | ("brk", None)}, pairs)

    1. Work out the free half-hour slots each day (inside that day's study
       hours, outside any activity / dinner).
    2. In every free run, lay down  revision + break  pairs, so the revision
       to break ratio is exactly 1:1.
    3. If a day has more pairs than the daily maximum, the ones kept are spread
       evenly through the day (not bunched at the start).
    4. Share the revision slots out between subjects as evenly as possible
       (counts never differ by more than 1) and shuffle them randomly.
    """
    rng = random.Random(seed)

    blocked = {d: set() for d in range(7)}
    for a in blocks:
        blocked[a["day"]].update(range(a["start"], a["end"]))

    pairs = []  # (day, first_slot_of_pair)
    for d in range(7):
        if d not in cfg["hours"]:
            continue
        ws, we = cfg["hours"][d]
        free = [s for s in range(ws, we) if s not in blocked[d]]

        runs = []
        for s in free:
            if runs and runs[-1][-1] == s - 1:
                runs[-1].append(s)
            else:
                runs.append([s])

        day_pairs = []
        for run in runs:
            offset = rng.choice([0, 1]) if len(run) % 2 else 0
            for i in range(offset, len(run) - 1, 2):
                day_pairs.append(run[i])

        day_pairs = spread(day_pairs, cfg["max_pairs"], rng)
        pairs += [(d, s) for s in day_pairs]

    if not subjects or not pairs:
        return {}, pairs

    # equal share of subjects
    pool = []
    while len(pool) < len(pairs):
        cycle = subjects[:]
        rng.shuffle(cycle)
        pool += cycle
    pool = pool[:len(pairs)]

    # random order, trying to avoid the same subject twice in a row on a day
    def clashes(seq):
        return sum(
            1 for i in range(len(seq) - 1)
            if seq[i]["id"] == seq[i + 1]["id"] and pairs[i][0] == pairs[i + 1][0]
        )

    best = None
    for _ in range(300):
        rng.shuffle(pool)
        score = clashes(pool)
        if best is None or score < best[0]:
            best = (score, pool[:])
        if score == 0:
            break
    pool = best[1]

    cells = {}
    for (d, s), subj in zip(pairs, pool):
        cells[(d, s)] = ("rev", subj)
        cells[(d, s + 1)] = ("brk", None)
    return cells, pairs


# ----------------------------------------------------------------------------
# HTML rendering  (CSS grid with a real gap between every block)
# ----------------------------------------------------------------------------
def css(theme) -> str:
    return (
        "<style>"
        ".tt{display:grid;gap:6px;width:100%;font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:12px;}"
        ".tt.phone{font-size:14px;}"
        ".tt>div{box-sizing:border-box;border-radius:8px;display:flex;flex-direction:column;"
        "align-items:center;justify-content:center;text-align:center;overflow:hidden;"
        "padding:2px 6px;line-height:1.2;min-width:0;}"
        f".tt .h{{background:{theme['head']};color:#222;font-weight:700;font-size:13px;padding:8px 4px;}}"
        f".tt .t{{background:{theme['time']};color:#222;font-weight:600;font-size:11px;}}"
        ".tt .e{background:rgba(128,128,128,0.08);}"
        ".tt .b{background:rgba(128,128,128,0.08);color:#888;font-size:11px;}"
        ".tt .n{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%;font-weight:700;}"
        "</style>"
    )


def render(cells, blocks, cfg, days=None, phone=False) -> str:
    """days = which day numbers to draw (default: all seven). phone = bigger, single column."""
    days = list(range(7)) if days is None else list(days)
    theme = THEMES[cfg["palette"]]
    starts = [cfg["hours"][d][0] for d in days if d in cfg["hours"]] + \
             [a["start"] for a in blocks if a["day"] in days]
    ends = [cfg["hours"][d][1] for d in days if d in cfg["hours"]] + \
           [a["end"] for a in blocks if a["day"] in days]
    if not starts:
        return ""
    lo, hi = min(starts), max(ends)
    n = hi - lo
    col_of = {d: i + 2 for i, d in enumerate(days)}

    time_w, row_h = ("54px", 44) if phone else ("62px", 38)
    grid = (
        f'<div class="tt{" phone" if phone else ""}" '
        f'style="grid-template-columns:{time_w} repeat({len(days)},minmax(0,1fr));'
        f'grid-template-rows:auto repeat({n},{row_h}px);">'
    )
    out = [css(theme), grid, '<div class="h" style="grid-column:1;grid-row:1;">Time</div>']
    for d in days:
        out.append(f'<div class="h" style="grid-column:{col_of[d]};grid-row:1;">{DAYS[d]}</div>')

    for s in range(lo, hi):
        out.append(f'<div class="t" style="grid-column:1;grid-row:{s - lo + 2};">{label(s)}</div>')

    occupied = set()

    # activities + dinner (can span several rows)
    for a in blocks:
        if a["day"] not in col_of:
            continue
        span = a["end"] - a["start"]
        for s in range(a["start"], a["end"]):
            occupied.add((a["day"], s))
        fg = text_colour(a["color"])
        times = f'<span>{label(a["start"])}-{label(a["end"])}</span>' if span >= 2 else ""
        out.append(
            f'<div style="grid-column:{col_of[a["day"]]};grid-row:{a["start"] - lo + 2} / span {span};'
            f'background:{a["color"]};color:{fg};">'
            f'<span class="n">{html.escape(a["name"])}</span>{times}</div>'
        )

    # revision + breaks
    brk_colour = cfg["break_color"]   # None -> match the empty squares
    for (d, s), (kind, subj) in cells.items():
        if d not in col_of or (d, s) in occupied or not (lo <= s < hi):
            continue
        occupied.add((d, s))
        pos = f"grid-column:{col_of[d]};grid-row:{s - lo + 2};"
        if kind == "rev":
            fg = text_colour(subj["color"])
            out.append(
                f'<div style="{pos}background:{subj["color"]};color:{fg};">'
                f'<span class="n">{html.escape(subj["name"])}</span></div>'
            )
        elif brk_colour:
            out.append(f'<div class="b" style="{pos}background:{brk_colour};color:{text_colour(brk_colour)};">Break</div>')
        else:
            out.append(f'<div class="b" style="{pos}">Break</div>')

    # faint placeholders for everything else so the grid looks complete
    for d in days:
        for s in range(lo, hi):
            if (d, s) not in occupied:
                out.append(f'<div class="e" style="grid-column:{col_of[d]};grid-row:{s - lo + 2};"></div>')

    out.append("</div>")
    return "".join(out)


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
def _submit_subject():
    """Button callback: runs before the page redraws, so no st.rerun() is needed."""
    ss = st.session_state
    name = ss["new_subject_name"].strip()
    if not name:
        ss["_subject_msg"] = "Give the subject a name."
    elif name.lower() in [s["name"].lower() for s in ss.subjects]:
        ss["_subject_msg"] = "You already added that one."
    else:
        add_subject(name, ss["new_subject_colour"])
        ss["new_subject_name"] = ""
        ss["new_subject_colour"] = PALETTE[len(ss.subjects) % len(PALETTE)]   # next colour along


def sidebar_subjects():
    st.subheader("Subjects")
    with st.form("add_subject"):
        st.text_input("Subject name", placeholder="e.g. Chemistry", key="new_subject_name")
        st.color_picker("Colour", key="new_subject_colour")
        st.form_submit_button("Add subject", use_container_width=True, on_click=_submit_subject)
    msg = st.session_state.pop("_subject_msg", "")
    if msg:
        st.warning(msg)

    for s in st.session_state.subjects:
        c1, c2, c3 = st.columns([5, 2, 2])
        c1.markdown(f"**{html.escape(s['name'])}**")
        s["color"] = c2.color_picker("c", s["color"], key=f"col_{s['id']}", label_visibility="collapsed")
        c3.button("✕", key=f"delsub_{s['id']}", on_click=remove_subject, args=(s["id"],))


def sidebar_activities():
    st.subheader("Activities / blocked time")
    st.caption("Football, clubs, work... revision is placed around these.")
    with st.form("add_activity", clear_on_submit=False):
        name = st.text_input("Activity", placeholder="e.g. Football")
        days = st.multiselect("Day(s)", DAYS, default=["Tuesday"])
        c1, c2 = st.columns(2)
        s = c1.selectbox("From", START_OPTS, index=36, format_func=label)   # 18:00
        e = c2.selectbox("To", END_OPTS, index=38, format_func=label)       # 19:30
        colour = st.color_picker("Colour", "#7FB77E")
        if st.form_submit_button("Add activity", use_container_width=True):
            if not name.strip() or not days:
                st.warning("Needs a name and at least one day.")
            elif e <= s:
                st.warning("End time must be after the start time.")
            else:
                clash = False
                for dn in days:
                    d = DAYS.index(dn)
                    for a in st.session_state.activities:
                        if a["day"] == d and s < a["end"] and a["start"] < e:
                            st.warning(f"Clashes with '{a['name']}' on {dn}.")
                            clash = True
                if not clash:
                    for dn in days:
                        st.session_state.activities.append({
                            "id": new_id(), "name": name.strip(), "day": DAYS.index(dn),
                            "start": s, "end": e, "color": colour,
                        })
                    st.rerun()

    # one colour picker per activity name (changes every day it appears on)
    groups = {}
    for a in sorted(st.session_state.activities, key=lambda x: (x["name"].lower(), x["day"], x["start"])):
        groups.setdefault(a["name"], []).append(a)

    for gname, items in groups.items():
        c1, c2 = st.columns([6, 3])
        c1.markdown(f"**{html.escape(gname)}**")
        colour = c2.color_picker("c", items[0]["color"], key=f"actcol_{gname}", label_visibility="collapsed")
        for a in items:
            a["color"] = colour
        for a in items:
            d1, d2 = st.columns([7, 2])
            d1.caption(f"{DAYS[a['day']]} {label(a['start'])}-{label(a['end'])}")
            d2.button("✕", key=f"delact_{a['id']}", on_click=remove_activity, args=(a["id"],))


def sidebar_hours():
    st.subheader("Study hours")
    st.caption("Choose when you're available to revise. Any start and finish time, 00:00-24:00.")

    same = st.checkbox("Same hours every day", key="same_hours")
    hours = {}

    if same:
        days = st.multiselect("Days to revise", DAYS, key="same_days")
        c1, c2 = st.columns(2)
        s = c1.selectbox("Start", START_OPTS, format_func=label, key="all_start")
        e = c2.selectbox("Finish", END_OPTS, format_func=label, key="all_end")
        if e <= s:
            st.error("Finish must be after start.")
        else:
            for dn in days:
                hours[DAYS.index(dn)] = (s, e)
    else:
        h1, h2, h3 = st.columns([2, 3, 3])
        h1.caption("Day")
        h2.caption("Start")
        h3.caption("Finish")
        for d, dn in enumerate(DAYS):
            c0, c1, c2 = st.columns([2, 3, 3])
            on = c0.checkbox(dn[:3], key=f"on_{d}")
            s = c1.selectbox("s", START_OPTS, format_func=label, key=f"start_{d}",
                             label_visibility="collapsed", disabled=not on)
            e = c2.selectbox("e", END_OPTS, format_func=label, key=f"end_{d}",
                             label_visibility="collapsed", disabled=not on)
            if on:
                if e <= s:
                    st.error(f"{dn}: finish must be after start.")
                else:
                    hours[d] = (s, e)

    max_hours = st.slider("Max revision per day (hours)", 0.5, 12.0, step=0.5, key="max_hours",
                          help="If a day has more free time than this, the revision "
                               "sessions are spread out evenly through the day.")

    st.divider()
    st.subheader("Dinner")
    on = st.checkbox("Include a dinner break", key="dinner_on")
    c1, c2 = st.columns(2)
    d_start = c1.selectbox("Starts", START_OPTS, format_func=label, key="dinner_start", disabled=not on)
    d_len = c2.selectbox("Length", DINNER_LENGTHS, format_func=length_label, key="dinner_len", disabled=not on)
    d_col = st.color_picker("Dinner colour", key="dinner_col", disabled=not on)

    return {
        "hours": hours,
        "max_pairs": int(max_hours * 2),
        "dinner": {"on": on, "start": d_start, "len": d_len, "color": d_col},
    }


def sidebar_style():
    st.subheader("Colours")
    palette = st.radio("App colour palette", list(THEMES), horizontal=True, key="palette")
    st.caption("Changes the title, the day headers and the time column.")

    st.markdown("**Break blocks**")
    match = st.checkbox("Same colour as the empty squares", key="break_match")
    brk = None
    if not match:
        brk = st.color_picker("Break colour", key="break_col")

    st.divider()
    st.subheader("Layout")
    st.selectbox("View", VIEWS, key="view",
                 help="Auto-detect shows just today's column on a phone and the whole week otherwise.")
    return {"palette": palette, "break_color": brk}


def sidebar_save():
    st.subheader("Save & load")
    st.caption("Your choices aren't remembered between visits. Download your setup, "
               "then load the file next time.")
    st.download_button("⬇ Save my setup", data=export_setup(), file_name="revision_setup.json",
                       mime="application/json", use_container_width=True)

    up = st.file_uploader("Load a saved setup", key="setup_file")   # no type filter: iOS can grey out .json
    if up is not None and st.button("Load this setup", use_container_width=True):
        try:
            text = up.getvalue().decode("utf-8")
        except UnicodeDecodeError:
            st.error("That file isn't a setup file.")
            return
        pending, err = parse_setup(text)
        if err:
            st.error(err)
        else:
            st.session_state["_pending"] = pending
            st.rerun()


def main():
    st.set_page_config(page_title="Revision Timetable", page_icon="📚", layout="wide")
    apply_pending()
    init_state()

    with st.sidebar:
        st.title("📚 Setup")
        tab1, tab2, tab3, tab4, tab5 = st.tabs(["Subjects", "Activities", "Hours", "Style", "Save"])
        with tab1:
            sidebar_subjects()
        with tab2:
            sidebar_activities()
        with tab3:
            cfg = sidebar_hours()
        with tab4:
            cfg.update(sidebar_style())
        with tab5:
            sidebar_save()

    view = st.session_state.view
    phone = view == VIEWS[1] or (view == VIEWS[0] and visitor_is_phone())

    theme = THEMES[cfg["palette"]]
    st.markdown(f"<h1 style='color:{theme['accent']};margin-bottom:0'>Revision Timetable</h1>",
                unsafe_allow_html=True)
    if phone:
        st.caption("Tap the arrow at the top left for your settings.")
    else:
        st.caption("Add your subjects in the sidebar and the timetable builds itself: "
                   "30 min revision, 30 min break, repeat.")

    startup_offer()

    subjects = st.session_state.subjects
    activities = st.session_state.activities
    dinners, skipped = dinner_blocks(cfg, activities)
    blocks = activities + dinners

    top = st.columns([1, 5])
    top[0].button("🔀 Shuffle", on_click=shuffle, use_container_width=True,
                  help="Generate a new random layout")

    cells, pairs = generate(subjects, blocks, cfg, st.session_state.seed)

    if not cfg["hours"]:
        st.warning("Pick at least one day with valid start and finish times in the Hours tab.")
    elif not subjects:
        st.info("Add at least one subject in the sidebar to generate your timetable. "
                "Your activities are shown below in the meantime.")
    elif not pairs:
        st.warning("No free time to revise in! Widen your hours or remove some activities.")

    full_html = render(cells, blocks, cfg)
    if phone:
        st.session_state.setdefault("phone_day", today_index())
        day = st.radio("Day", list(range(7)), format_func=lambda i: DAYS[i][:3],
                       horizontal=True, key="phone_day", label_visibility="collapsed")
        shown = render(cells, blocks, cfg, days=[day], phone=True)
        if shown:
            st.markdown(shown, unsafe_allow_html=True)
        else:
            st.info(f"Nothing planned for {DAYS[day]}.")
    elif full_html:
        st.markdown(full_html, unsafe_allow_html=True)

    if full_html:
        if skipped:
            names = ", ".join(DAYS[d] for d in skipped)
            st.caption(f"Dinner skipped on {names}: it clashes with an activity.")
        st.download_button(
            "⬇ Download timetable (HTML)",
            data=f"<!doctype html><meta charset='utf-8'><title>Revision Timetable</title>"
                 f"<body style='margin:16px'>{full_html}</body>",
            file_name="revision_timetable.html",
            mime="text/html",
        )

    if subjects and pairs:
        st.markdown("#### Sessions per subject")
        counts = {s["id"]: 0 for s in subjects}
        for kind, subj in cells.values():
            if kind == "rev":
                counts[subj["id"]] += 1
        chips = "".join(
            f'<span style="background:{s["color"]};color:{text_colour(s["color"])};'
            f'padding:4px 10px;border-radius:12px;margin:3px;display:inline-block;">'
            f'{html.escape(s["name"])}: {counts[s["id"]]} × 30 min ({counts[s["id"]] / 2:g} h)</span>'
            for s in subjects
        )
        st.markdown(chips, unsafe_allow_html=True)
        total = sum(counts.values())
        st.caption(f"{total} revision chunks and {total} breaks across the week "
                   f"({total / 2:g} h revision, {total / 2:g} h break).")


main()