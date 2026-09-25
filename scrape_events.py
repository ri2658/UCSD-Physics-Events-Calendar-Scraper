"""
Scrape UCSD Physics seminars/colloquia into an .ics calendar feed.

Data source: the JSON API that powers https://physics.ucsd.edu/events/seminars-colloquia
    https://physics.ucsd.edu/api/search-events?start=0&count=10&event_start=&search_terms=

The API only returns *upcoming* events, so each run merges them into
events_archive.csv. That way past talks stay on your calendar instead of
vanishing the day after they happen.

Usage:
    pip install requests pandas beautifulsoup4 icalendar
    python scrape_events.py            # writes physics.ics + events_archive.csv
"""

import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import bs4
import pandas as pd
import requests
from icalendar import Calendar, Event

API_URL = "https://physics.ucsd.edu/api/search-events"
PAGE_SIZE = 50
TZ = ZoneInfo("America/Los_Angeles")
ARCHIVE = Path("events_archive.csv")
OUTPUT = Path("physics.ics")

# Best practice from lecture: be upfront about who you are.
HEADERS = {"User-Agent": "ucsd-physics-calendar (student project; contact: ride@ucsd.edu)"}


# ---------------------------------------------------------------- 1. Download
def download_page(start):
    """One page of the API, like download_page(i) in the quotes example."""
    params = {"start": start, "count": PAGE_SIZE, "event_start": "", "search_terms": ""}
    resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()["data"]


def download_all():
    rows, start = [], 0
    while True:
        page = download_page(start)
        rows.extend(page)
        if len(page) < PAGE_SIZE:      # last page
            break
        start += PAGE_SIZE
        time.sleep(1)                  # send requests slowly
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 2. Clean
def html_to_text(html):
    """event_location / event_abstract come back as HTML fragments like '<p>Mayer 4322</p>'."""
    if not isinstance(html, str) or not html.strip():
        return ""
    return bs4.BeautifulSoup(html, "html.parser").get_text("\n").strip()


def parse_when(date_str, time_str):
    """'September 28, 2026' + '12:00 PM' -> timezone-aware datetime."""
    if not isinstance(date_str, str) or not isinstance(time_str, str) or not time_str.strip():
        return None
    naive = datetime.strptime(f"{date_str} {time_str}", "%B %d, %Y %I:%M %p")
    return naive.replace(tzinfo=TZ)


def speaker(row):
    name = " ".join(x for x in [row.get("speaker_first_name"), row.get("speaker_last_name")] if isinstance(x, str))
    inst = row.get("speaker_institution")
    return f"{name} ({inst})" if name and isinstance(inst, str) else name


def process_event(row):
    """One API record -> one flat dict, like process_quote(div) in lecture."""
    start = parse_when(row["start_date"], row["start_time"])
    end = parse_when(row.get("end_date") or row["start_date"], row.get("end_time"))
    if start is not None and (end is None or end <= start):
        end = start + timedelta(hours=1)
    return {
        "event_id": int(row["event_id"]),
        "title": (row.get("event_title") or "").strip(),
        "series": row.get("event_series") or "",
        "speaker": speaker(row),
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "location": html_to_text(row.get("event_location")),
        "abstract": html_to_text(row.get("event_abstract")),
        "updated_at": row.get("updated_at") or "",
    }


# ---------------------------------------------------------------- 3. Merge with archive
def merge_with_archive(new):
    if ARCHIVE.exists():
        old = pd.read_csv(ARCHIVE, keep_default_na=False)
        combined = pd.concat([old, new], ignore_index=True)
    else:
        combined = new
    # Newest scrape wins if an event was edited (e.g. room change).
    combined = combined.drop_duplicates("event_id", keep="last")
    return combined.sort_values("start").reset_index(drop=True)


# ---------------------------------------------------------------- 4. Write .ics
def build_calendar(events):
    cal = Calendar()
    cal.add("prodid", "-//UCSD Physics seminars (unofficial scrape)//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "UCSD Physics Seminars")
    cal.add("x-wr-timezone", "America/Los_Angeles")

    for _, ev in events.iterrows():
        if not ev["start"]:
            continue
        summary = ev["title"]
        # Many titles are just the series name ("Condensed Matter Seminar"),
        # so put the speaker in the title to make the calendar readable at a glance.
        if ev["speaker"] and ev["speaker"].split(" (")[0] not in summary:
            summary = f"{summary} — {ev['speaker']}"
        if ev["series"] and ev["series"] not in summary:
            summary = f"[{ev['series']}] {summary}"

        e = Event()
        e.add("uid", f"{ev['event_id']}@physics.ucsd.edu")   # stable -> updates, not duplicates
        e.add("summary", summary)
        # .astimezone(TZ) restores the real America/Los_Angeles zone (handles PDT -> PST)
        e.add("dtstart", datetime.fromisoformat(ev["start"]).astimezone(TZ))
        e.add("dtend", datetime.fromisoformat(ev["end"]).astimezone(TZ))
        # Deterministic DTSTAMP (from the site's updated_at) so the file only changes
        # when an event actually changes -> no pointless daily commits.
        try:
            stamp = datetime.strptime(ev["updated_at"], "%m/%d/%Y").replace(tzinfo=TZ)
        except (TypeError, ValueError):
            stamp = datetime.fromisoformat(ev["start"]).astimezone(TZ)
        e.add("dtstamp", stamp)
        e.add("location", ev["location"])
        desc = "\n\n".join(x for x in [f"Speaker: {ev['speaker']}" if ev["speaker"] else "",
                                         ev["abstract"],
                                         "https://physics.ucsd.edu/events/seminars-colloquia"] if x)
        e.add("description", desc)
        cal.add_component(e)
    cal.add_missing_timezones()   # embeds a VTIMEZONE block so every calendar app agrees
    return cal


def main():
    raw = download_all()
    print(f"Downloaded {len(raw)} upcoming events")
    new = pd.DataFrame([process_event(r) for r in raw.to_dict("records")]) if len(raw) else \
        pd.DataFrame(columns=["event_id", "title", "series", "speaker", "start", "end", "location", "abstract", "updated_at"])
    events = merge_with_archive(new)
    events.to_csv(ARCHIVE, index=False)
    OUTPUT.write_bytes(build_calendar(events).to_ical())
    print(f"Wrote {len(events)} events to {OUTPUT}")


if __name__ == "__main__":
    main()