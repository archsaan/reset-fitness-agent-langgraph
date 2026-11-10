"""All environment-derived settings. The only module that reads os.environ."""

import os

from dotenv import load_dotenv

load_dotenv()


def _csv(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


DATABASE_URL = os.environ.get("DATABASE_URL", "")
DIRECT_DATABASE_URL = os.environ.get("DIRECT_DATABASE_URL", "") or DATABASE_URL

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
# Token verification is provider-neutral. On Render/Neon today: set JWT_SECRET
# to a secret shared with whatever service mints member tokens (HS256).
# On Supabase later: leave JWT_SECRET blank and set SUPABASE_URL (JWKS), or
# set the project's legacy JWT secret in JWT_SECRET.
SUPABASE_JWT_SECRET = os.environ.get("JWT_SECRET") or os.environ.get("SUPABASE_JWT_SECRET", "")
JWT_AUDIENCE = os.environ.get("JWT_AUDIENCE", "authenticated")

CORS_ORIGINS = _csv("CORS_ORIGINS", "http://localhost:3000")

RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60"))
RATE_LIMIT_MAX = int(os.environ.get("RATE_LIMIT_MAX", "20"))

# ProfitConnect
SCHEDULE_API_URL = "https://crmapi.profitconnect.co/calendar/get/dayschedule"
BOOKING_VALIDATE_API_URL = "https://crmapi.profitconnect.co/member/booking/validate"
BOOKING_CREATE_API_URL = "https://crmapi.profitconnect.co/member/booking/book"
SCHEDULE_API_KEY = os.environ.get("SCHEDULE_API_KEY", "")
FACILITY_ID = int(os.environ.get("FACILITY_ID", "2"))

# RAG
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")
EMBEDDING_MODEL = "voyage-3-lite"
EMBEDDING_DIM = 512
KB_RETRIEVAL_TOP_K = 4

# Models
TRIBE_APP_MODEL = os.environ.get("TRIBE_APP_MODEL", "gemini-3.5-flash-lite")

# Conversation memory: once a thread exceeds TRIGGER messages, everything
# older than the last RECENT messages is folded into a running summary.
ROLLING_SUMMARY_RECENT_MESSAGES = int(os.environ.get("ROLLING_SUMMARY_RECENT_MESSAGES", "12"))
ROLLING_SUMMARY_TRIGGER_MESSAGES = int(os.environ.get("ROLLING_SUMMARY_TRIGGER_MESSAGES", "20"))

# How long admin-edited prompts may be served from memory before re-reading.
AGENT_CONFIG_TTL_SECONDS = int(os.environ.get("AGENT_CONFIG_TTL_SECONDS", "30"))
