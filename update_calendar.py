#!/usr/bin/env python3
"""Build motogp.ics from the official MotoGP (PulseLive) API.

- Only Qualifying (Q1/Q2) and the Sunday Grand Prix race are included
  (Sprint is optional, see INCLUDE_SPRINT).
- All times are converted to IST (Asia/Kolkata).
- Every event gets an alert 30 minutes before the start.
- If the API fails or returns nothing, the existing .ics is left untouched.
"""
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

# ----------------------------- settings ------------------------------------
BASE = "https://api.motogp.pulselive.com/motogp/v1"
OUTPUT = "motogp.ics"
CAL_NAME = "MotoGP (Qualifying + Race)"
IST = ZoneInfo("Asia/Kolkata")
ALARM_MINUTES = 30

INCLUDE_Q1 = True        # Q1 is the 15-min session for riders outside the top 10
INCLUDE_SPRINT = False   # Saturday sprint race; you asked for GP races only

# calendar block length in minutes (just for how long it shows in your calendar)
DURATION = {"Q1": 30, "Q2": 30, "RAC": 60, "SPR": 45}

LABEL = {
    "Q1": "Qualifying Q1",
    "Q2": "Qualifying Q2",
    "RAC": "Grand Prix Race",
    "SPR": "Sprint",
}
EMOJI = {"Q1": "⏱️", "Q2": "⏱️", "RAC": "🏁", "SPR": "⚡"}
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": "Mozilla/5.0 (motogp-calendar-bot)",
    "Accept": "application/json",
    "Origin": "https://www.motogp.com",
    "Referer": "https://www.motogp.com/",
}


def api_get(path, **params):
    """GET with a few retries. Returns parsed JSON (list or dict)."""
    url = f"{BASE}{path}"
    last = None
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                for key in ("results", "data", "items"):
                    if isinstance(data.get(key), list):
                        return data[key]
            return data
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def get_motogp_category(season_uuid):
    for c in api_get("/results/categories", seasonUuid=season_uuid):
        name = (c.get("name") or "").lower()
        if name.startswith("motogp") and not any(x in name for x in ("moto2", "moto3", "e-")):
            return c["id"]
    return None


def classify(s):
    """Return 'Q1' | 'Q2' | 'RAC' | 'SPR' or None for sessions we skip."""
    t = (s.get("type") or "").upper()
    short = (s.get("session_shortname") or "").upper()
    name = (s.get("name") or "").lower()

    if t == "SPR" or "sprint" in name or short == "SPR":
        return "SPR"
    if t == "RAC" or short == "RAC" or name.strip() in ("race", "grand prix", "grand prix race"):
        return "RAC"
    if t == "Q" or "qualifying" in name:
        if "Q1" in short or "q1" in name or "nr. 1" in name or s.get("number") == 1:
            return "Q1"
        return "Q2"
    return None  # practice, warm-up, etc.


def parse_dt(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def esc(text):
    return (
        str(text).replace("\\", "\\\\").replace(";", "\\;")
        .replace(",", "\\,").replace("\n", "\\n")
    )


def fold(line):
    """RFC 5545 line folding at 75 octets."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        limit = 75 if not out else 74
        if len(cur) + len(b) > limit:
            out.append(cur.decode("utf-8"))
            cur = b
        else:
            cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


def collect_sessions():
    now = datetime.now(IST)
    seasons = api_get("/results/seasons")
    wanted_years = {now.year, now.year + 1}  # next season appears once published
    rows = []

    for season in seasons:
        if season.get("year") not in wanted_years:
            continue
        cat = get_motogp_category(season["id"])
        if not cat:
            continue
        events = api_get("/results/events", seasonUuid=season["id"], isFinished="false")
        events += api_get("/results/events", seasonUuid=season["id"], isFinished="true")
        seen = set()
        for ev in events:
            if ev["id"] in seen or ev.get("test") or (ev.get("kind") or "").upper() == "TEST":
                continue
            seen.add(ev["id"])
            for s in api_get("/results/sessions", eventUuid=ev["id"], categoryUuid=cat):
                kind = classify(s)
                if kind is None or not s.get("date"):
                    continue
                if kind == "Q1" and not INCLUDE_Q1:
                    continue
                if kind == "SPR" and not INCLUDE_SPRINT:
                    continue
                rows.append((ev, s, kind))
    return rows


def build_ics(rows):
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//motogp-calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{esc(CAL_NAME)}",
        "X-WR-TIMEZONE:Asia/Kolkata",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
        # IST has no DST, so one fixed observance is enough
        "BEGIN:VTIMEZONE",
        "TZID:Asia/Kolkata",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0530",
        "TZOFFSETTO:+0530",
        "TZNAME:IST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]

    for ev, s, kind in sorted(rows, key=lambda r: r[1]["date"]):
        start = parse_dt(s["date"])
        end = start + timedelta(minutes=DURATION[kind])
        gp_name = ev.get("short_name") or ev.get("name") or "MotoGP"
        circuit = ev.get("circuit") or {}
        country = (ev.get("country") or {}).get("name") or circuit.get("nation") or ""
        location = ", ".join(x for x in (circuit.get("name"), circuit.get("place"), country) if x)

        summary = f"{EMOJI[kind]} MotoGP {LABEL[kind]} – {gp_name}"
        desc = (
            f"{ev.get('name', gp_name)}\n{LABEL[kind]}\n"
            f"Starts {start.strftime('%a %d %b %Y, %I:%M %p')} IST"
        )

        lines += [
            "BEGIN:VEVENT",
            f"UID:{s['id']}@motogp-calendar",
            f"DTSTAMP:{now_utc}",
            f"DTSTART;TZID=Asia/Kolkata:{start.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Asia/Kolkata:{end.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{esc(summary)}",
            f"LOCATION:{esc(location)}",
            f"DESCRIPTION:{esc(desc)}",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{esc(summary)} starts in {ALARM_MINUTES} minutes",
            f"TRIGGER:-PT{ALARM_MINUTES}M",
            "END:VALARM",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


def main():
    try:
        rows = collect_sessions()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not rows:
        print("ERROR: no sessions found, keeping existing calendar", file=sys.stderr)
        return 1

    ics = build_ics(rows)
    with open(OUTPUT, "w", encoding="utf-8", newline="") as f:
        f.write(ics)
    print(f"Wrote {len(rows)} sessions to {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
