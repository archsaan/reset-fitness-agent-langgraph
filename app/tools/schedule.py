"""Read-only ProfitConnect schedule tools (async). A 30s cache absorbs the
fan-out of check_availability and repeated questions about the same day."""

import asyncio
import time
from datetime import date, datetime, timedelta

from langchain_core.tools import tool

from app.config import FACILITY_ID, SCHEDULE_API_KEY, SCHEDULE_API_URL
from app.tools.http import post_with_retry

_CACHE_TTL = 30.0
_cache: dict[str, tuple[float, list]] = {}


async def fetch_schedule(target_date: str | None = None) -> list:
    target_date = target_date or date.today().isoformat()
    hit = _cache.get(target_date)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]

    headers = {"Authorization": f"Bearer {SCHEDULE_API_KEY}"} if SCHEDULE_API_KEY else {}
    resp = await post_with_retry(
        SCHEDULE_API_URL,
        json={"facility_id": FACILITY_ID, "room_id": "", "date": target_date},
        headers=headers,
    )
    resp.raise_for_status()
    data = resp.json()

    cleaned = []
    for cls in data.get("schedule", []):
        capacity, booked = int(cls["capacity"]), int(cls["booked"])
        cancelled = int(cls.get("cancelled", 0))
        coach = cls.get("coach")
        cleaned.append({
            "calendar_schedule_id": cls["id"],
            "class_name": cls["class_name"],
            "discipline": cls["discipline_name"],
            "start_time": cls["start_time"],
            "end_time": cls["end_time"],
            "coach": f'{coach[0]["firstname"]} {coach[0]["lastname"]}' if coach else None,
            "spots_left": max(capacity - (booked - cancelled), 0),
            "class_type": cls["class_type"],
        })
    _cache[target_date] = (time.monotonic(), cleaned)
    return cleaned


@tool
async def get_schedule(target_date: str = "") -> list:
    """Get the full class schedule for Reset Fitness on a given date
    (YYYY-MM-DD). If target_date is left blank, defaults to today."""
    return await fetch_schedule(target_date or None)


@tool
async def check_availability(class_name: str, target_date: str = "", max_days_ahead: int = 3) -> list:
    """Check if a specific class has open spots on a given date (YYYY-MM-DD),
    checking the next few days too if the requested date is full. Each result
    includes a 'day_label' field - always trust that label rather than
    calculating the day yourself."""
    start = datetime.strptime(target_date or date.today().isoformat(), "%Y-%m-%d")
    today = date.today()
    days = [(start + timedelta(days=i)).date() for i in range(max_days_ahead + 1)]
    schedules = await asyncio.gather(*(fetch_schedule(d.isoformat()) for d in days))

    results = []
    for d, schedule in zip(days, schedules):
        if d == today:
            label = "today"
        elif d == today + timedelta(days=1):
            label = "tomorrow"
        else:
            label = d.strftime("%A, %B %d")
        for cls in schedule:
            if class_name.lower() in cls["class_name"].lower():
                results.append({
                    "date": d.isoformat(), "day_label": label,
                    "calendar_schedule_id": cls["calendar_schedule_id"],
                    "class_name": cls["class_name"], "start_time": cls["start_time"],
                    "spots_left": cls["spots_left"], "coach": cls["coach"],
                })
    return results
