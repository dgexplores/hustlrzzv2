"""FastAPI app entrypoint for hustlrzzv2.

English-native AI mock interview coach. Merges:
  - hustlrzz       (prep workflow, live WebSocket interviewer, judge)
  - interview-skills (company profiles, JD-vs-resume, salary negotiation, modes)
  - AI-Interview-Coach (Next.js shell consumed by frontend)
"""

from __future__ import annotations

import secrets
import time
import zipfile
import math
from io import BytesIO
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlparse
from xml.etree import ElementTree

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from backend import config, db as dbc
from backend.ai import provider
from backend.career import analysis, company_profiles
from backend.rag import service as rag
from backend.resume import service as resume_analyzer
from backend.security import limiter, trusted_origin
from backend.session import registry
from backend.workflow.preparation import run_preparation_workflow

app = FastAPI(title="Hustlrzz V2", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_origin_regex=config.CORS_ORIGIN_REGEX or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

bearer = HTTPBearer(auto_error=False)
router = APIRouter()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


def _db_or_503():
    if not dbc.is_ready():
        raise HTTPException(
            status_code=503,
            detail="Backend not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.",
        )


def get_user(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)):
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    client = dbc.get_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    try:
        user = client.auth.get_user(credentials.credentials).user
        if not limiter.allow(f"user:{user.id}", config.API_RATE_LIMIT_PER_MINUTE):
            raise HTTPException(status_code=429, detail="Too many requests. Please wait a minute and try again.")
        meta = user.user_metadata or {}
        return {
            "uid": user.id,
            "email": user.email or "",
            "name": meta.get("full_name") or meta.get("name") or "",
            "picture": meta.get("avatar_url") or meta.get("picture") or "",
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def _enforce_costly_request(user_id: str) -> None:
    if not limiter.allow(f"costly:{user_id}", config.COSTLY_API_RATE_LIMIT_PER_MINUTE):
        raise HTTPException(status_code=429, detail="Too many AI requests. Please wait a minute and try again.")


def _valid_url(host: str) -> str:
    return host


@router.get("/workflows")
async def list_workflows(user: dict = Depends(get_user)):
    _db_or_503()
    rows = dbc.select_where("workflows", {"user_id": user["uid"]}, order="created_at")
    return {"success": True, "data": rows}


@router.get("/interviews")
async def list_interviews(user: dict = Depends(get_user)):
    _db_or_503()
    rows = dbc.select_where("interview_sessions", {"user_id": user["uid"]}, order="created_at")
    return {"success": True, "data": rows}


@router.get("/health")
def health():
    return {
        "status": "ok",
        "ai_configured": provider.is_configured(),
        "provider": config.AI_PROVIDER,
        "db_ready": dbc.is_ready(),
    }


# --------------------------------------------------------------------------- #
# Preparation workflow (resume + JD -> questions + match + salary + modes)
# --------------------------------------------------------------------------- #
@router.post("/workflows/start")
async def start_workflow(
    resume_text: str = Form(...),
    job_description: str = Form(...),
    company_name: str = Form(""),
    linkedin_link: str = Form(""),
    github_link: str = Form(""),
    portfolio_link: str = Form(""),
    additional_info: str = Form(""),
    num_questions: int = Form(config.DEFAULT_QUESTION_COUNT),
    user: dict = Depends(get_user),
):
    _enforce_costly_request(user["uid"])
    num_questions = max(1, min(num_questions, 50))
    t0 = time.time()
    rag_status = {"available": rag.is_ready(), "indexed": False}
    # Indexing is additive. An embedding outage must never prevent a candidate
    # from preparing for an interview with the configured chat provider.
    if rag_status["available"]:
        try:
            indexed = await rag.ingest_document(
                user_id=user["uid"],
                title="Resume context",
                source_type="resume",
                content=resume_text,
            )
            rag_status.update(indexed)
            rag_status["indexed"] = True
        except (ValueError, rag.RAGUnavailable) as exc:
            rag_status["warning"] = str(exc)
        except Exception:
            rag_status["warning"] = "Resume knowledge indexing is temporarily unavailable."
    result = await run_preparation_workflow(
        user_id=user["uid"],
        resume_text=resume_text,
        job_description=job_description,
        company_name=company_name,
        linkedin_link=linkedin_link,
        github_link=github_link,
        portfolio_link=portfolio_link,
        additional_info=additional_info,
        num_questions=num_questions,
    )
    result["processing_time"] = round(time.time() - t0, 2)
    result["knowledge"] = rag_status
    if not result.get("success"):
        err = str(result.get("error", "Workflow failed"))
        # Surface provider quota limits as a retryable 429, not a 500.
        if any(k in err for k in ("429", "Rate limit", "rate_limit")):
            raise HTTPException(status_code=429, detail=err)
        raise HTTPException(status_code=500, detail=err)
    # Persist workflow record (if db ready).
    if dbc.is_ready():
        try:
            persisted_match = {
                **(result.get("company_match") or {}),
                "company_research": result.get("company_research") or {},
            }
            dbc.insert("workflows", [{
                "workflow_id": result["workflow_id"],
                "user_id": user["uid"],
                "title": (result.get("company_match") or {}).get("summary", job_description[:80]),
                "company": company_name,
                "questions": result.get("questions", []),
                "answers": result.get("answers", []),
                "match": persisted_match,
                "created_at": _now(),
            }])
        except Exception as e:
            print("persist workflow failed:", e)
    return {"success": True, **result}


@router.post("/workflows/upload")
async def start_workflow_upload(
    file: UploadFile = File(...),
    job_description: str = Form(...),
    company_name: str = Form(""),
    linkedin_link: str = Form(""),
    github_link: str = Form(""),
    portfolio_link: str = Form(""),
    additional_info: str = Form(""),
    num_questions: int = Form(config.DEFAULT_QUESTION_COUNT),
    user: dict = Depends(get_user),
):
    """Upload a PDF or DOCX resume and run the same preparation workflow."""
    content = await file.read()
    if len(content) > config.MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large")
    resume_text = _extract_resume_text(file.filename or "", content)
    if not resume_text or len(resume_text.strip()) < config.MIN_RESUME_TEXT_LENGTH:
        raise HTTPException(status_code=400, detail="Could not extract enough text from this PDF or DOCX file.")
    return await start_workflow(
        resume_text=resume_text,
        job_description=job_description,
        company_name=company_name,
        linkedin_link=linkedin_link,
        github_link=github_link,
        portfolio_link=portfolio_link,
        additional_info=additional_info,
        num_questions=num_questions,
        user=user,
    )


def _extract_resume_text(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf_text(content)
    if suffix == ".docx":
        return _extract_docx_text(content)
    raise HTTPException(status_code=400, detail="Upload a PDF or DOCX resume, or paste the text instead.")


def _extract_pdf_text(content: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(content))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return ""


def _extract_docx_text(content: bytes) -> str:
    """Read the main DOCX document XML without adding a document-parser dependency."""
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs = []
        for node in root.iter(f"{namespace}p"):
            parts = [text.text or "" for text in node.iter(f"{namespace}t")]
            if parts:
                paragraphs.append("".join(parts))
        return "\n".join(paragraphs)
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return ""


# --------------------------------------------------------------------------- #
# Resume Analyzer (cost-aware, no raw-resume persistence)
# --------------------------------------------------------------------------- #
@router.get("/resume-analyzer/usage")
async def resume_analyzer_usage(user: dict = Depends(get_user)):
    _db_or_503()
    try:
        return {"success": True, "data": await resume_analyzer.usage(user["uid"])}
    except Exception:
        raise HTTPException(status_code=503, detail="Resume Analyzer usage is temporarily unavailable.")


@router.get("/resume-analyzer/analyses")
async def list_resume_analyses(user: dict = Depends(get_user)):
    _db_or_503()
    try:
        response = dbc.get_client().table("resume_analysis").select(
            "analysis_id,resume_score,extracted_skills,created_at"
        ).eq("user_id", user["uid"]).order("created_at", desc=True).limit(50).execute()
        return {"success": True, "data": response.data or []}
    except Exception:
        raise HTTPException(status_code=503, detail="Resume Analyzer history is temporarily unavailable.")


@router.get("/resume-analyzer/analyses/{analysis_id}")
async def get_resume_analysis(analysis_id: str, user: dict = Depends(get_user)):
    _db_or_503()
    try:
        response = dbc.get_client().table("resume_analysis").select("*").eq(
            "analysis_id", analysis_id
        ).eq("user_id", user["uid"]).limit(1).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Analysis not found")
        return {"success": True, "data": response.data[0]}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Resume Analyzer result is temporarily unavailable.")


@router.post("/resume-analyzer/analyze")
async def analyze_resume(
    file: UploadFile = File(...),
    job_description: str = Form(""),
    user: dict = Depends(get_user),
):
    """Analyze a PDF/DOCX in memory; raw upload bytes are discarded after parsing."""
    _db_or_503()
    _enforce_costly_request(user["uid"])
    filename = file.filename or ""
    if Path(filename).suffix.lower() not in {".pdf", ".docx"}:
        raise HTTPException(status_code=400, detail="Upload a PDF or DOCX resume.")
    content = await file.read(config.MAX_FILE_SIZE + 1)
    if len(content) > config.MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="Resume files must be 5 MB or smaller.")
    resume_text = _extract_resume_text(filename, content)
    # Ensure the file bytes are no longer retained by this request before the
    # model call; only extracted text is passed to the analysis service.
    del content
    if len(resume_text.strip()) < config.MIN_RESUME_TEXT_LENGTH:
        raise HTTPException(status_code=400, detail="Could not extract enough readable text from this resume.")
    if len(job_description) > config.RESUME_ANALYZER_MAX_JD_CHARS:
        raise HTTPException(status_code=422, detail="Job description is too long.")
    try:
        record, cached = await resume_analyzer.analyze(
            user_id=user["uid"], resume_text=resume_text, job_description=job_description,
        )
        return {"success": True, "data": record, "cached": cached}
    except PermissionError as exc:
        raise HTTPException(status_code=402, detail=str(exc))
    except provider.ProviderError as exc:
        raise HTTPException(status_code=503, detail="Resume analysis is temporarily unavailable. Please retry shortly.") from exc
    except Exception:
        raise HTTPException(status_code=503, detail="Resume analysis could not be completed. No quota was consumed; please retry.")


# --------------------------------------------------------------------------- #
# Company + salary + mode endpoints (from interview-skills, English)
# --------------------------------------------------------------------------- #
@router.get("/companies")
def list_companies():
    return {
        "success": True,
        "data": [
            {"name": name, **profile}
            for name, profile in company_profiles.COMPANY_PROFILES.items()
        ],
    }


class SalaryRequest(BaseModel):
    company: str = Field(min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=200)
    current_salary: str = Field(default="", max_length=200)
    target_range: str = Field(min_length=1, max_length=200)
    has_offer: str = Field(default="", max_length=2000)


class MatchAnalysisRequest(BaseModel):
    job_description: str = Field(min_length=80, max_length=60000)
    resume_text: str = Field(min_length=80, max_length=config.RAG_MAX_DOCUMENT_CHARS)


class CoachingPracticeRequest(BaseModel):
    scenario: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=10, max_length=2000)
    answer: str = Field(min_length=20, max_length=12000)
    presence_metrics: dict = Field(default_factory=dict)


class CoachingTurnMessage(BaseModel):
    role: Literal["candidate", "coach"]
    text: str = Field(min_length=1, max_length=4000)


class CoachingTurnRequest(BaseModel):
    scenario: str = Field(min_length=1, max_length=100)
    difficulty: Literal["supportive", "realistic", "challenging"] = "realistic"
    coach_style: Literal["recruiter", "hiring-manager", "negotiator"] = "recruiter"
    opening_prompt: str = Field(min_length=10, max_length=2000)
    history: list[CoachingTurnMessage] = Field(default_factory=list, max_length=10)
    candidate_answer: str = Field(min_length=10, max_length=4000)


@router.post("/coaching/salary")
def salary_script(payload: SalaryRequest, user: dict = Depends(get_user)):
    _enforce_costly_request(user["uid"])
    try:
        return {"success": True, "data": analysis.salary_script(**payload.model_dump())}
    except provider.ProviderError as exc:
        status = 429 if "429" in str(exc) or "rate" in str(exc).lower() else 503
        raise HTTPException(status_code=status, detail="The negotiation coach is temporarily busy. Please retry shortly.")


@router.post("/coaching/analyze")
async def analyze(payload: MatchAnalysisRequest, user: dict = Depends(get_user)):
    _enforce_costly_request(user["uid"])
    try:
        return {"success": True, "data": analysis.analyze_match(payload.job_description, payload.resume_text)}
    except provider.ProviderError as exc:
        status = 429 if "429" in str(exc) or "rate" in str(exc).lower() else 503
        raise HTTPException(status_code=status, detail="The role-fit coach is temporarily busy. Please retry shortly.")


@router.post("/coaching/practice")
def coaching_practice(payload: CoachingPracticeRequest, user: dict = Depends(get_user)):
    _enforce_costly_request(user["uid"])
    allowed_metrics: dict[str, float] = {}
    allowed_keys = {
            "handDetectionCounter", "handDetectionDuration", "notFacingCounter",
            "notFacingDuration", "badPostureDetectionCounter", "badPostureDuration",
            "sessionDurationSeconds", "eyeContactConsistency", "postureStability",
            "gestureRatePerMinute",
    }
    for key, value in payload.presence_metrics.items():
        if key not in allowed_keys or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        numeric = float(value)
        if math.isfinite(numeric):
            allowed_metrics[key] = min(max(0, numeric), 100_000)
    try:
        result = analysis.evaluate_coaching_practice(
            scenario=payload.scenario,
            prompt=payload.prompt,
            answer=payload.answer,
            presence_metrics=allowed_metrics,
        )
        if not result or result.get("error"):
            raise HTTPException(status_code=502, detail="The coach returned incomplete feedback. Please retry.")
        return {"success": True, "data": result}
    except provider.ProviderError as exc:
        status = 429 if "429" in str(exc) or "rate" in str(exc).lower() else 503
        raise HTTPException(status_code=status, detail="The practice coach is temporarily busy. Please retry shortly.")


@router.post("/coaching/practice/turn")
def coaching_practice_turn(payload: CoachingTurnRequest, user: dict = Depends(get_user)):
    _enforce_costly_request(user["uid"])
    try:
        result = analysis.coaching_practice_turn(
            scenario=payload.scenario,
            difficulty=payload.difficulty,
            coach_style=payload.coach_style,
            opening_prompt=payload.opening_prompt,
            history=[item.model_dump() for item in payload.history],
            candidate_answer=payload.candidate_answer,
        )
        if result.get("error"):
            raise HTTPException(status_code=502, detail="The coach returned an incomplete response. Please retry.")
        return {"success": True, "data": result}
    except provider.ProviderError as exc:
        status = 429 if "429" in str(exc) or "rate" in str(exc).lower() else 503
        raise HTTPException(status_code=status, detail="The live coach is temporarily busy. Your transcript remains available.")


# --------------------------------------------------------------------------- #
# Candidate knowledge base (RAG)
# --------------------------------------------------------------------------- #
class KnowledgeIngestRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=config.MIN_RESUME_TEXT_LENGTH, max_length=config.RAG_MAX_DOCUMENT_CHARS)
    source_type: str = Field(default="notes", pattern="^(resume|portfolio|notes|session_report)$")


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=10)


@router.get("/knowledge/status")
def knowledge_status(user: dict = Depends(get_user)):
    return {"success": True, "data": {"available": rag.is_ready()}}


@router.post("/knowledge/documents")
async def ingest_knowledge(payload: KnowledgeIngestRequest, user: dict = Depends(get_user)):
    try:
        data = await rag.ingest_document(
            user_id=user["uid"],
            title=payload.title,
            source_type=payload.source_type,
            content=payload.content,
        )
        return {"success": True, "data": data}
    except rag.RAGUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=503, detail="Knowledge indexing is temporarily unavailable.")


@router.post("/knowledge/search")
async def search_knowledge(payload: KnowledgeSearchRequest, user: dict = Depends(get_user)):
    try:
        chunks = await rag.retrieve(user_id=user["uid"], query=payload.query, top_k=payload.top_k)
        return {"success": True, "data": [{
            "content": item.content,
            "source_title": item.source_title,
            "source_type": item.source_type,
            "document_id": item.document_id,
            "similarity": item.similarity,
        } for item in chunks]}
    except rag.RAGUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=503, detail="Knowledge search is temporarily unavailable.")


# --------------------------------------------------------------------------- #
# Live interview (WebSocket)
# --------------------------------------------------------------------------- #
def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


class InterviewStart(BaseModel):
    workflow_id: str
    duration: int = Field(15, ge=5, le=60)
    is_audio: bool = False


def _fallback_interview_report() -> dict:
    return {
        "scores": {},
        "strengths": [],
        "improvements": ["Review the saved transcript and retry scoring from a future session."],
        "summary": "Your interview transcript was saved, but detailed AI scoring is temporarily unavailable.",
        "verdict": "Session captured successfully; scoring can be retried when the provider is available.",
    }


@router.post("/interviews/start")
async def start_interview(payload: InterviewStart, user: dict = Depends(get_user)):
    try:
        workflow = dbc.select_where("workflows", {"workflow_id": payload.workflow_id}) or []
    except Exception:
        workflow = []
    owned = [w for w in workflow if w.get("user_id") == user["uid"]]
    if not owned:
        raise HTTPException(status_code=404, detail="Workflow not found")
    session_id = secrets.token_urlsafe(16)
    ws_token = secrets.token_urlsafe(32)
    sess = await registry.create("hustlrzzv2", user["uid"], session_id)
    sess.state["ws_token"] = ws_token
    sess.state["workflow_id"] = payload.workflow_id
    sess.state["duration"] = payload.duration
    sess.state["is_audio"] = payload.is_audio
    qs = (
        f"?user_id={user['uid']}&workflow_id={payload.workflow_id}"
        f"&duration={payload.duration}&is_audio={str(payload.is_audio).lower()}&token={ws_token}"
    )
    return {"success": True, "data": {"session_id": session_id, "websocket_parameter": qs}}


@router.websocket("/ws/{session_id}")
async def interview_ws(
    websocket: WebSocket,
    session_id: str,
    user_id: str = "",
    workflow_id: str = "",
    token: str = "",
    duration: int = 15,
    is_audio: bool = False,
):
    sess = await registry.get("hustlrzzv2", user_id, session_id)
    expected = sess.state.get("ws_token", "") if sess else ""
    if (
        not sess
        or not token
        or not trusted_origin(websocket.headers.get("origin"))
        or not limiter.allow(f"ws:{user_id}", config.WEBSOCKET_RATE_LIMIT_PER_MINUTE)
        or not secrets.compare_digest(str(expected), str(token))
    ):
        await websocket.close(code=1008)
        return

    # Load prepared questions for this workflow so interviewer has a script.
    import json

    questions = []
    workflow_record: dict = {}
    try:
        rows = dbc.select_where("workflows", {"workflow_id": workflow_id, "user_id": user_id})
        for r in rows:
            workflow_record = r
            if isinstance(r.get("questions"), list):
                questions.extend(r["questions"])
    except Exception:
        pass

    stored_match = workflow_record.get("match") if isinstance(workflow_record.get("match"), dict) else {}
    system = build_interviewer_system(
        workflow_record.get("company") or "the target company",
        workflow_record.get("title") or "the target role",
        questions,
        duration,
        company_context=stored_match.get("company_research") if isinstance(stored_match, dict) else None,
    )
    transcript: list[dict] = []

    await websocket.accept()
    try:
        # Opening question.
        opener = {"question": questions[0]["question"] if questions else "Tell me about yourself.", "message": ""}
        transcript.append({"from": "interviewer", "text": opener["question"]})
        await websocket.send_json({"type": "question", "data": opener})
        while True:
            msg = await websocket.receive_json()
            if msg.get("type") == "message":
                text = str(msg.get("text", "")).strip()
                if not text:
                    await websocket.send_json({"type": "error", "data": {"message": "Please send an answer before continuing."}})
                    continue
                if len(text) > 12000:
                    await websocket.send_json({"type": "error", "data": {"message": "Please keep one answer under 12,000 characters."}})
                    continue
                retrieval_context = ""
                if rag.is_ready():
                    try:
                        chunks = await rag.retrieve(user_id=user_id, query=text, top_k=3)
                        retrieval_context = rag.format_context(chunks, max_chars=3500)
                    except Exception:
                        # A coaching session should continue if retrieval is slow
                        # or unavailable; the prepared question script remains.
                        retrieval_context = ""
                try:
                    reply = interviewer_turn(system, transcript, text, retrieval_context=retrieval_context)
                except provider.ProviderError:
                    await websocket.send_json({"type": "error", "data": {"message": "The interviewer is temporarily unavailable. Please try your answer again in a moment."}})
                    continue
                transcript.append({"from": "candidate", "text": text})
                transcript.append({"from": "interviewer", "text": reply.get("message") or reply.get("question") or ""})
                await websocket.send_json({"type": "message", "data": reply})
            elif msg.get("type") == "end":
                break
    except WebSocketDisconnect:
        pass
    finally:
        # Judge + persist session report.
        report = {}
        if transcript:
            try:
                report = judge_report(questions, transcript, "", "")
            except Exception as exc:
                print("judge failed:", exc)
                report = _fallback_interview_report()
        if dbc.is_ready() and transcript:
            try:
                dbc.insert("interview_sessions", [{
                    "session_id": session_id,
                    "user_id": user_id,
                    "workflow_id": workflow_id,
                    "transcript": transcript,
                    "report": report,
                    "is_audio": is_audio,
                    "created_at": _now(),
                }])
            except Exception as exc:
                print("persist interview failed:", exc)
        if report and rag.is_ready():
            try:
                await rag.ingest_document(
                    user_id=user_id,
                    title="Interview coaching report",
                    source_type="session_report",
                    content=json.dumps(report, ensure_ascii=False),
                )
            except Exception:
                # History persistence is already complete; RAG enrichment should
                # not affect the completed interview result.
                pass
        if report:
            try:
                await websocket.send_json({"type": "report", "data": report})
            except Exception:
                pass
        try:
            await websocket.close()
        except Exception:
            pass
        await registry.delete("hustlrzzv2", user_id, session_id)


from backend.agents.interviewer import build_interviewer_system, interviewer_turn, judge_report  # noqa: E402

app.include_router(router)
