import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUTPUT_FILE = "motogp.ics"

DATA_URL = (
    "https://cdn.jsdelivr.net/gh/"
    "vishwapramuditha/moto-db@main/"
    "data/motogp/{year}/schedule.json"
)


def fetch_schedule(year):
    url = DATA_URL.format(year=year)

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "MotoGP-Calendar/1.0"},
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def escape_ics(value):
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def parse_session(session):
    date = session.get("date")
    time = session.get("time")

    if not date or not time:
        return None

    value = f"{date}T{time}"

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    parsed = datetime.fromisoformat(value)

    if parsed.tzinfo is None:
        return None

    return parsed.astimezone(timezone.utc)


def get_events(data):
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("events", "races", "schedule", "rounds"):
            if isinstance(data.get(key), list):
                return data[key]

    return []


def get_location(event):
    circuit = event.get("circuit", event.get("track", ""))

    if isinstance(circuit, dict):
        return (
            circuit.get("name")
            or circuit.get("circuitName")
            or ""
        )

    return str(circuit or "")


def create_event(uid, title, start, duration, location):
    end = start + timedelta(minutes=duration)

    return [
        "BEGIN:VEVENT",
        f"UID:{uid}@motogp-calendar",
        f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
        f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}",
        f"SUMMARY:{escape_ics(title)}",
        f"LOCATION:{escape_ics(location)}",
        "BEGIN:VALARM",
        "TRIGGER:-PT30M",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{escape_ics(title)} starts in 30 minutes",
        "END:VALARM",
        "END:VEVENT",
    ]


def main():
    now = datetime.now(timezone.utc)
    current_year = now.year
    calendar_events = []
    seen_ids = set()

    for year in (current_year, current_year + 1):
        try:
            data = fetch_schedule(year)
        except Exception as error:
            print(f"Could not fetch {year} schedule: {error}")
            continue

        events = get_events(data)

        if not events:
            print(f"No recognizable events found for {year}.")
            continue

        for event in events:
            event_name = (
                event.get("name")
                or event.get("eventName")
                or event.get("grandPrix")
                or event.get("raceName")
                or f"MotoGP event {event.get('round', '')}"
            )

            location = get_location(event)

            sessions = event.get("sessions", [])

            if not isinstance(sessions, list):
                continue

            for session in sessions:
                session_type = str(
                    session.get("type", "")
                ).strip().lower()

                session_name = str(
                    session.get("name", "")
                ).strip().lower()

                # Include qualifying sessions, including Q1 and Q2.
                is_qualifying = (
                    session_type in ("qualifying", "q1", "q2")
                    or session_name in (
                        "qualifying",
                        "qualifying 1",
                        "qualifying 2",
                        "q1",
                        "q2",
                    )
                )

                # Include the main race, but never the Sprint race.
                is_main_race = (
                    session_type == "race"
                    or session_name in ("race", "motogp race")
                )

                if not (is_qualifying or is_main_race):
                    continue

                start = parse_session(session)

                if start is None or start <= now:
                    continue

                label = "Qualifying" if is_qualifying else "Grand Prix"

                session_id = (
                    session.get("id")
                    or f"{year}-{event_name}-{session_type}-{start.isoformat()}"
                )

                uid = f"{year}-{session_id}"

                if uid in seen_ids:
                    continue

                seen_ids.add(uid)

                duration = 60 if is_qualifying else 120

                calendar_events.extend(
                    create_event(
                        uid,
                        f"MotoGP {event_name} - {label}",
                        start,
                        duration,
                        location,
                    )
                )

    calendar = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//MotoGP Calendar//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:MotoGP - Qualifying & Grand Prix",
        *calendar_events,
        "END:VCALENDAR",
        "",
    ]

    Path(OUTPUT_FILE).write_text(
        "\r\n".join(calendar),
        encoding="utf-8",
    )

    print(f"Generated {OUTPUT_FILE}")
    print(f"Upcoming sessions included: {len(seen_ids)}")


if __name__ == "__main__":
    main()
