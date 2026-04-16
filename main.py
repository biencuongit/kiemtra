from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Text, Float, ForeignKey, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
import re
from datetime import datetime
from typing import List
import os
from sqlalchemy import create_engine
# =============================================================================
# 1. CẤU HÌNH DATABASE & MODELS
# =============================================================================
# Lấy đường dẫn DB từ biến môi trường Render cung cấp
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL") 

if not SQLALCHEMY_DATABASE_URL:
    # Dòng này để bạn vẫn chạy được local nếu chưa cài biến môi trường
    SQLALCHEMY_DATABASE_URL = "postgresql://postgres:acd5Y63xNzayPGAI@db.cdvsfvzoiojbblluifar.supabase.co:5432/postgres"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False} if "sqlite" in SQLALCHEMY_DATABASE_URL else {}
)


# =============================================================================
# 2. LOGIC XỬ LÝ (PARSER)
# =============================================================================
def parse_exam_text(text: str):
    question_blocks = re.split(r'(?=Câu\s*\d+[:.])', text)
    parsed_data = []
    for block in question_blocks:
        if not block.strip(): continue
        lines = block.strip().split('\n')
        content = re.sub(r'^Câu\s*\d+[:.]\s*', '', lines[0])
        options = []
        for line in lines[1:]:
            match = re.match(r'^([A-D])\.\s*(.*)', line.strip())
            if match: options.append({"label": match.group(1), "text": match.group(2)})
        answer_match = re.search(r'Đáp án:\s*([A-D])', block)
        correct_answer = answer_match.group(1) if answer_match else None
        parsed_data.append({"content": content, "options": options, "correct_answer": correct_answer})
    return parsed_data

# =============================================================================
# 3. API ENDPOINTS
# =============================================================================
app = FastAPI()

# CẤU HÌNH CORS - Cho phép Frontend gọi API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

class ExamCreate(BaseModel):
    title: str
    duration: int
    raw_text: str

class SubmitExam(BaseModel):
    exam_id: int
    student_name: str
    answers: List[dict] # [{ "question_id": 1, "selected": "A" }]

@app.post("/preview-parse")
async def preview_parse(data: dict):
    return {"parsed_result": parse_exam_text(data.get("text", ""))}

@app.post("/save-exam")
async def save_exam(exam_data: ExamCreate, db: Session = Depends(get_db)):
    parsed_questions = parse_exam_text(exam_data.raw_text)
    new_exam = ExamModel(title=exam_data.title, duration=exam_data.duration)
    db.add(new_exam)
    db.commit(); db.refresh(new_exam)
    for q_data in parsed_questions:
        new_q = QuestionModel(exam_id=new_exam.id, content=q_data["content"], correct_answer=q_data["correct_answer"])
        db.add(new_q); db.commit(); db.refresh(new_q)
        for opt in q_data["options"]:
            db.add(OptionModel(question_id=new_q.id, label=opt["label"], text=opt["text"]))
    db.commit()
    return {"exam_id": new_exam.id}

@app.get("/exam/{exam_id}")
async def get_exam(exam_id: int, db: Session = Depends(get_db)):
    exam = db.query(ExamModel).filter(ExamModel.id == exam_id).first()
    if not exam: raise HTTPException(status_code=404)
    return {
        "title": exam.title,
        "duration": exam.duration,
        "questions": [{"id": q.id, "content": q.content, "options": [{"label": o.label, "text": o.text} for o in q.options]} for q in exam.questions]
    }

@app.post("/submit-exam")
async def submit_exam(submit: SubmitExam, db: Session = Depends(get_db)):
    # Lấy đáp án đúng từ DB
    questions = db.query(QuestionModel).filter(QuestionModel.exam_id == submit.exam_id).all()
    correct_map = {q.id: q.correct_answer for q in questions}
    
    # Chấm điểm
    score = 0
    total_q = len(questions)
    for ans in submit.answers:
        if correct_map.get(ans["question_id"]) == ans["selected"]:
            score += 1
            
    # Lưu kết quả
    submission = SubmissionModel(exam_id=submit.exam_id, student_name=submit.student_name, total_score=score)
    db.add(submission)
    db.commit()
    
    return {"score": score, "total": total_q, "percentage": (score/total_q)*100 if total_q > 0 else 0}
