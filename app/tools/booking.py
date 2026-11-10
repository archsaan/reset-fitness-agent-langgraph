"""Booking. Two separate things on purpose:

  book_class       - the tool the MODEL sees. It only declares the schema;
                     the graph never executes it. The confirm_booking node
                     intercepts the call and asks the member first.
  execute_booking  - the real validate -> book flow. Called only by
                     confirm_booking, only after an explicit "yes", with a
                     member_id taken from the verified JWT.

Idempotent: a (member_id, calendar_schedule_id) row in booking_attempts is
claimed before the POST, so a repeated "yes", a client retry, or two
instances racing cannot book the same class twice. The booking POST itself
is never auto-retried.
"""

import logging

import httpx
from langchain_core.tools import tool

from app.config import BOOKING_CREATE_API_URL, BOOKING_VALIDATE_API_URL, FACILITY_ID, SCHEDULE_API_KEY
from app.db import pool
from app.tools.http import client, post_with_retry

logger = logging.getLogger("reset_fitness.booking")


@tool
def book_class(calendar_schedule_id: int, class_name: str, target_date: str, start_time: str, lead_name: str) -> str:
    """Propose booking a member into a class. Only call after check_availability
    has confirmed there is space - pass the exact calendar_schedule_id it
    returned. This does NOT book yet: the member is asked to confirm first."""
    return "Not executed here - handled by the confirmation step."


def _headers() -> dict:
    return {"Authorization": f"Bearer {SCHEDULE_API_KEY}"} if SCHEDULE_API_KEY else {}


def _failure_reason(data: dict) -> str:
    for key in ("message", "error", "reason", "detail"):
        if data.get(key):
            return str(data[key])
    return "The booking system declined this booking."


async def _claim(member_id: int, schedule_id: int) -> str:
    """Returns 'claimed', or 'booked' / 'pending' if a row already exists."""
    async with pool().connection() as conn:
        cur = await conn.execute(
            "INSERT INTO booking_attempts (member_id, calendar_schedule_id) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING RETURNING 1", (member_id, schedule_id))
        if await cur.fetchone():
            return "claimed"
        cur = await conn.execute(
            "SELECT status FROM booking_attempts WHERE member_id = %s AND calendar_schedule_id = %s",
            (member_id, schedule_id))
        row = await cur.fetchone()
        return row["status"] if row else "claimed"


async def _release(member_id: int, schedule_id: int) -> None:
    async with pool().connection() as conn:
        await conn.execute(
            "DELETE FROM booking_attempts WHERE member_id = %s AND calendar_schedule_id = %s AND status = 'pending'",
            (member_id, schedule_id))


async def _mark_booked(member_id: int, schedule_id: int) -> None:
    async with pool().connection() as conn:
        await conn.execute(
            "UPDATE booking_attempts SET status = 'booked' WHERE member_id = %s AND calendar_schedule_id = %s",
            (member_id, schedule_id))


async def execute_booking(
    member_id: int, calendar_schedule_id: int, class_name: str, target_date: str, start_time: str, lead_name: str
) -> dict:
    state = await _claim(member_id, calendar_schedule_id)
    if state == "booked":
        return {"success": False, "reason": "You're already booked into that class."}
    if state == "pending":
        return {"success": False, "reason": "That booking is already being processed."}

    try:
        try:
            v = await post_with_retry(
                BOOKING_VALIDATE_API_URL, headers=_headers(),
                json={"calendar_schedule_id": calendar_schedule_id, "facility_id": FACILITY_ID, "member_id": member_id})
            v.raise_for_status()
            vdata = v.json()
        except httpx.HTTPError as exc:
            await _release(member_id, calendar_schedule_id)
            return {"success": False, "reason": f"Couldn't reach the booking system to validate: {exc}"}

        if vdata.get("valid") is False or vdata.get("success") is False:
            await _release(member_id, calendar_schedule_id)
            return {"success": False, "reason": _failure_reason(vdata)}

        try:
            b = await client().post(
                BOOKING_CREATE_API_URL, headers=_headers(),
                json={"calendar_schedule_id": calendar_schedule_id, "facility_id": FACILITY_ID,
                      "member_id": [member_id], "planStatus": "Active", "status": "Booked",
                      "validateBy": "Membership"})
            b.raise_for_status()
            bdata = b.json()
        except httpx.TimeoutException:
            # Outcome unknown: the request may have gone through. Keep the
            # claim so a retry cannot double-book; staff can reconcile.
            logger.error("Booking POST timed out member=%s schedule=%s - claim kept", member_id, calendar_schedule_id)
            return {"success": False, "reason": "The booking system is slow to respond. Please check your bookings before trying again."}
        except httpx.HTTPError as exc:
            await _release(member_id, calendar_schedule_id)
            return {"success": False, "reason": f"Validated but couldn't complete the booking: {exc}"}

        if bdata.get("success") is False:
            await _release(member_id, calendar_schedule_id)
            return {"success": False, "reason": _failure_reason(bdata)}

        await _mark_booked(member_id, calendar_schedule_id)
        return {"success": True, "booking": {
            "class_name": class_name, "date": target_date, "start_time": start_time,
            "lead_name": lead_name, "calendar_schedule_id": calendar_schedule_id}}
    except Exception:
        logger.exception("Unexpected booking failure member=%s schedule=%s", member_id, calendar_schedule_id)
        await _release(member_id, calendar_schedule_id)
        return {"success": False, "reason": "Something went wrong reaching the booking system. Please try again in a moment."}
