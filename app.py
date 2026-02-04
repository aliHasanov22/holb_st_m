#!/usr/bin/python3
import os
import re
import json
import secrets
from datetime import datetime
from typing import Optional, List
from typing import Tuple  
from typing import Tuple


from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

from passlib.context import CryptContext

# Optional AI (OpenAI). Works only if OPENAI_API_KEY is set.
USE_OPENAI = bool(os.getenv("OPENAI_API_KEY"))

try:
    from openai import OpenAI
    oai_client = OpenAI() if USE_OPENAI else None
except Exception:
    oai_client = None
    USE_OPENAI = False

APP_SECRET = os.getenv("APP_SECRET", "dev-secret-change-me")
DB_URL = os.getenv("DB_URL", "sqlite:///./study.db")

engine = create_engine(DB_URL, connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# -------------------- Models --------------------

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    subjects = relationship("Subject", back_populates="user")


class Subject(Base):
    __tablename__ = "subjects"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(120), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="subjects")
    notes = relationship("Note", back_populates="subject")


class Note(Base):
    __tablename__ = "notes"
    id = Column(Integer, primary_key=True)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    content = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    flashcards_json = Column(Text, nullable=True)  # store as JSON list
    created_at = Column(DateTime, default=datetime.utcnow)

    subject = relationship("Subject", back_populates="notes")


class QuizResult(Base):
    __tablename__ = "quiz_results"
    id = Column(Integer, primary_key=True)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    score = Column(Integer, nullable=False)
    total = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# -------------------- Simple Session (cookie token) --------------------
# For PLD demo: in-memory sessions. (Production: Redis/DB + secure settings.)
SESSIONS = {}  # token -> user_id

def set_session(response: RedirectResponse, user_id: int):
    token = secrets.token_urlsafe(24)
    SESSIONS[token] = user_id
    response.set_cookie("session", token, httponly=True, samesite="lax")
    return response

def get_user_id_from_request(request: Request) -> Optional[int]:
    token = request.cookies.get("session")
    if not token:
        return None
    return SESSIONS.get(token)

def require_login(request: Request) -> Optional[int]:
    return get_user_id_from_request(request)

# -------------------- Helpers --------------------

def hash_password(pw: str) -> str:
    return pwd_context.hash(pw)

def verify_password(pw: str, hashed: str) -> bool:
    return pwd_context.verify(pw, hashed)

def split_sentences(text: str) -> List[str]:
    # simple sentence splitting
    text = re.sub(r"\s+", " ", text.strip())
    sents = re.split(r"(?<=[.!?])\s+", text)
    sents = [s.strip() for s in sents if len(s.strip()) > 0]
    return sents

def offline_summary(text: str, max_sentences: int = 5) -> str:
    # naive extractive summary: pick first N "good length" sentences
    sents = split_sentences(text)
    picked = []
    for s in sents:
        if 40 <= len(s) <= 220:
            picked.append(s)
        if len(picked) >= max_sentences:
            break
    if not picked:
        picked = sents[:max_sentences]
    return "\n".join(f"- {s}" for s in picked)

def offline_flashcards(text: str, max_cards: int = 8) -> List[dict]:
    """
    Heuristic flashcards:
    - capture "X is ..." definitions
    - capture headings like "### Topic"
    """
    cards = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # headings -> question
    for ln in lines:
        if ln.startswith("#"):
            topic = ln.lstrip("#").strip()
            if topic:
                cards.append({"q": f"What is {topic}?", "a": f"{topic} is explained in your notes."})
        if len(cards) >= max_cards:
            return cards

    # "X is ..." definitions
    defs = re.findall(r"\b([A-Z][A-Za-z0-9 _-]{2,40})\s+is\s+([^.\n]{10,120})", text)
    for term, desc in defs:
        cards.append({"q": f"Define {term.strip()}.", "a": desc.strip()})
        if len(cards) >= max_cards:
            break

    if not cards:
        # fallback
        sents = split_sentences(text)
        for s in sents[:max_cards]:
            cards.append({"q": "Explain this:", "a": s})
    return cards[:max_cards]

def ai_summary_and_flashcards(text: str) -> Tuple[str, List[dict]]:
    # If OpenAI not set, fallback offline
    if not USE_OPENAI or oai_client is None:
        return offline_summary(text), offline_flashcards(text)

    prompt = f"""
You are a study assistant.
Given the NOTES below, produce:
1) A concise bullet summary (5-8 bullets).
2) 8 flashcards as JSON array of objects with keys "q" and "a".
Keep answers factual and based ONLY on the notes.

NOTES:
{text}
"""
    resp = oai_client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    content = resp.choices[0].message.content.strip()

    # Try to parse flashcards JSON if present; otherwise fallback
    # Expect summary first, then JSON.
    json_match = re.search(r"(\[\s*\{.*\}\s*\])", content, flags=re.S)
    flashcards = None
    if json_match:
        try:
            flashcards = json.loads(json_match.group(1))
        except Exception:
            flashcards = None

    # Summary = everything before JSON block
    if json_match:
        summary_part = content[:json_match.start()].strip()
    else:
        summary_part = content

    # Clean summary to bullets
    if not summary_part.startswith("-"):
        summary_part = "\n".join([f"- {ln.strip('- ').strip()}" for ln in summary_part.splitlines() if ln.strip()])

    if not flashcards or not isinstance(flashcards, list):
        flashcards = offline_flashcards(text)

    # Normalize card keys
    normalized = []
    for c in flashcards[:8]:
        q = (c.get("q") if isinstance(c, dict) else None) or "Question"
        a = (c.get("a") if isinstance(c, dict) else None) or "Answer"
        normalized.append({"q": str(q).strip(), "a": str(a).strip()})

    return summary_part, normalized

def build_quiz(flashcards: List[dict], limit: int = 5) -> List[dict]:
    # Simple quiz: ask question, user types answer (no MCQ)
    quiz = []
    for c in flashcards[:limit]:
        quiz.append({"q": c["q"], "expected": c["a"]})
    return quiz

def grade_quiz(quiz: List[dict], user_answers: List[str]) -> Tuple[int, int]:
    score = 0
    for item, ans in zip(quiz, user_answers):
        expected = item["expected"].lower()
        ans_l = (ans or "").lower()
        # loose match: keyword overlap
        exp_words = set(re.findall(r"[a-z0-9]+", expected))
        ans_words = set(re.findall(r"[a-z0-9]+", ans_l))
        if exp_words and (len(exp_words & ans_words) / max(1, len(exp_words))) >= 0.35:
            score += 1
    return score, len(quiz)

# -------------------- Routes --------------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user_id = require_login(request)
    if not user_id:
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse("/dashboard", status_code=302)

@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    return templates.TemplateResponse("signup.html", {"request": request, "error": None})

@app.post("/signup")
def signup(email: str = Form(...), password: str = Form(...)):
    db = SessionLocal()
    try:
        email = email.strip().lower()
        if db.query(User).filter(User.email == email).first():
            resp = RedirectResponse("/signup?err=Email+already+used", status_code=302)
            return resp
        u = User(email=email, password_hash=hash_password(password))
        db.add(u)
        db.commit()
        db.refresh(u)
        resp = RedirectResponse("/dashboard", status_code=302)
        return set_session(resp, u.id)
    finally:
        db.close()

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, err: Optional[str] = None):
    return templates.TemplateResponse("login.html", {"request": request, "error": err})

@app.post("/login")
def login(email: str = Form(...), password: str = Form(...)):
    db = SessionLocal()
    try:
        email = email.strip().lower()
        u = db.query(User).filter(User.email == email).first()
        if not u or not verify_password(password, u.password_hash):
            return RedirectResponse("/login?err=Invalid+credentials", status_code=302)
        resp = RedirectResponse("/dashboard", status_code=302)
        return set_session(resp, u.id)
    finally:
        db.close()

@app.get("/logout")
def logout(request: Request):
    token = request.cookies.get("session")
    if token and token in SESSIONS:
        del SESSIONS[token]
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("session")
    return resp

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user_id = require_login(request)
    if not user_id:
        return RedirectResponse("/login", status_code=302)

    db = SessionLocal()
    try:
        subjects = db.query(Subject).filter(Subject.user_id == user_id).order_by(Subject.created_at.desc()).all()
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "subjects": subjects,
            "use_openai": USE_OPENAI
        })
    finally:
        db.close()

@app.post("/subjects/create")
def create_subject(request: Request, name: str = Form(...)):
    user_id = require_login(request)
    if not user_id:
        return RedirectResponse("/login", status_code=302)
    db = SessionLocal()
    try:
        s = Subject(user_id=user_id, name=name.strip())
        db.add(s)
        db.commit()
        return RedirectResponse("/dashboard", status_code=302)
    finally:
        db.close()

@app.get("/subjects/{subject_id}", response_class=HTMLResponse)
def subject_page(request: Request, subject_id: int):
    user_id = require_login(request)
    if not user_id:
        return RedirectResponse("/login", status_code=302)

    db = SessionLocal()
    try:
        subject = db.query(Subject).filter(Subject.id == subject_id, Subject.user_id == user_id).first()
        if not subject:
            return RedirectResponse("/dashboard", status_code=302)

        notes = db.query(Note).filter(Note.subject_id == subject_id).order_by(Note.created_at.desc()).all()
        quiz_results = db.query(QuizResult).filter(QuizResult.subject_id == subject_id).order_by(QuizResult.created_at.desc()).limit(10).all()

        return templates.TemplateResponse("subject.html", {
            "request": request,
            "subject": subject,
            "notes": notes,
            "quiz_results": quiz_results,
            "use_openai": USE_OPENAI
        })
    finally:
        db.close()

@app.post("/subjects/{subject_id}/notes")
def add_note(request: Request, subject_id: int, content: str = Form(...)):
    user_id = require_login(request)
    if not user_id:
        return RedirectResponse("/login", status_code=302)

    db = SessionLocal()
    try:
        subject = db.query(Subject).filter(Subject.id == subject_id, Subject.user_id == user_id).first()
        if not subject:
            return RedirectResponse("/dashboard", status_code=302)

        summary, cards = ai_summary_and_flashcards(content)

        note = Note(
            subject_id=subject_id,
            content=content,
            summary=summary,
            flashcards_json=json.dumps(cards, ensure_ascii=False),
        )
        db.add(note)
        db.commit()

        return RedirectResponse(f"/subjects/{subject_id}", status_code=302)
    finally:
        db.close()

@app.post("/subjects/{subject_id}/quiz/start")
def start_quiz(request: Request, subject_id: int, note_id: int = Form(...)):
    user_id = require_login(request)
    if not user_id:
        return RedirectResponse("/login", status_code=302)

    db = SessionLocal()
    try:
        subject = db.query(Subject).filter(Subject.id == subject_id, Subject.user_id == user_id).first()
        if not subject:
            return RedirectResponse("/dashboard", status_code=302)

        note = db.query(Note).filter(Note.id == note_id, Note.subject_id == subject_id).first()
        if not note or not note.flashcards_json:
            return RedirectResponse(f"/subjects/{subject_id}", status_code=302)

        cards = json.loads(note.flashcards_json)
        quiz = build_quiz(cards, limit=5)

        # Store quiz temporarily in session cookie-less way (simple demo):
        token = request.cookies.get("session")
        if token:
            SESSIONS[f"quiz:{token}:{subject_id}"] = quiz

        return RedirectResponse(f"/subjects/{subject_id}?quiz=1", status_code=302)
    finally:
        db.close()

@app.post("/subjects/{subject_id}/quiz/submit")
def submit_quiz(request: Request, subject_id: int,
                a1: str = Form(""), a2: str = Form(""), a3: str = Form(""), a4: str = Form(""), a5: str = Form("")):
    user_id = require_login(request)
    if not user_id:
        return RedirectResponse("/login", status_code=302)

    token = request.cookies.get("session")
    quiz_key = f"quiz:{token}:{subject_id}"
    quiz = SESSIONS.get(quiz_key, [])
    answers = [a1, a2, a3, a4, a5]

    score, total = grade_quiz(quiz, answers)

    db = SessionLocal()
    try:
        # Ensure subject belongs to user
        subject = db.query(Subject).filter(Subject.id == subject_id, Subject.user_id == user_id).first()
        if not subject:
            return RedirectResponse("/dashboard", status_code=302)

        db.add(QuizResult(subject_id=subject_id, score=score, total=total))
        db.commit()

        # Clear quiz
        if quiz_key in SESSIONS:
            del SESSIONS[quiz_key]

        return RedirectResponse(f"/subjects/{subject_id}?sc={score}&tot={total}", status_code=302)
    finally:
        db.close()
