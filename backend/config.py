import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")

# Preferred provider: "groq" (free tier, no card) or "gemini".
AI_PROVIDER = os.getenv("AI_PROVIDER", "groq").lower()

GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Retrieval is intentionally independent of chat-provider selection. Gemini's
# embedding endpoint is used only when a key is configured; the core interview
# workflow stays available without knowledge-base search.
RAG_EMBEDDING_MODEL = os.getenv("RAG_EMBEDDING_MODEL", "models/text-embedding-004")
RAG_EMBEDDING_DIMENSIONS = int(os.getenv("RAG_EMBEDDING_DIMENSIONS", "768"))
RAG_MAX_DOCUMENT_CHARS = int(os.getenv("RAG_MAX_DOCUMENT_CHARS", "200000"))
RAG_CHUNK_CHARS = int(os.getenv("RAG_CHUNK_CHARS", "900"))
RAG_CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "120"))
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "5"))

CORS_ORIGINS = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,https://hustlrzzv2.vercel.app",
).split(",")
# Vercel creates a unique Preview hostname for every deployment. Keep the
# expression narrow: it permits only this team's `frontend` Vercel hosts.
CORS_ORIGIN_REGEX = os.getenv(
    "CORS_ORIGIN_REGEX",
    r"https://frontend(?:-[a-z0-9-]+)?-deepaklearn7878-6255s-projects\.vercel\.app",
)

MIN_RESUME_TEXT_LENGTH = int(os.getenv("MIN_RESUME_TEXT_LENGTH", "120"))
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", "5242880"))  # 5 MB

DEFAULT_QUESTION_COUNT = int(os.getenv("DEFAULT_QUESTION_COUNT", "12"))
ENABLE_WEB_SEARCH = os.getenv("ENABLE_WEB_SEARCH", "true").lower() in {"1", "true", "yes"}
AI_REQUEST_TIMEOUT_SECONDS = int(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "60"))
WEB_SEARCH_TIMEOUT_SECONDS = int(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS", "15"))
API_RATE_LIMIT_PER_MINUTE = int(os.getenv("API_RATE_LIMIT_PER_MINUTE", "90"))
COSTLY_API_RATE_LIMIT_PER_MINUTE = int(os.getenv("COSTLY_API_RATE_LIMIT_PER_MINUTE", "12"))
WEBSOCKET_RATE_LIMIT_PER_MINUTE = int(os.getenv("WEBSOCKET_RATE_LIMIT_PER_MINUTE", "10"))

# Resume Analyzer safeguards.  Quotas are ultimately enforced by the Supabase
# RPC; these values only define product policy and request bounds.
RESUME_ANALYZER_FREE_DAILY_LIMIT = int(os.getenv("RESUME_ANALYZER_FREE_DAILY_LIMIT", "3"))
RESUME_ANALYZER_MAX_JD_CHARS = int(os.getenv("RESUME_ANALYZER_MAX_JD_CHARS", "60000"))
