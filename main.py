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
    SQLALCHEMY_DATABASE_URL = "https://cdvsfvzoiojbblluifar.supabase.co"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False} if "sqlite" in SQLALCHEMY_DATABASE_URL else {}
)


# =============================================================================
# 2. LOGIC XỬ LÝ (PARSER)
# =============================================================================
def parse_exam_text(text: str):
    # --- BƯỚC 1: TÁCH PHẦN ĐÁP ÁN Ở CUỐI ĐỀ ---
    # Tìm vị trí của chữ "ĐÁP ÁN" hoặc "HƯỚNG DẪN GIẢI"
    answer_key_map = {}
    split_pattern = re.compile(r'(ĐÁP ÁN|HƯỚNG DẪN GIẢI)', re.IGNORECASE)
    parts = split_pattern.split(text)
    
    content_text = parts[0] # Phần nội dung câu hỏi
    remaining_text = "".join(parts[1:]) # Phần đáp án và giải chi tiết

    # Trích xuất tất cả các cặp số.chữ (Ví dụ: 1. B, 2.B)
    # Regex này tìm: [Số] [dấu chấm hoặc khoảng trắng] [Chữ cái A-D]
    ans_matches = re.findall(r'(\d+)\s*[\.\s]\s*([A-D])', remaining_text)
    for q_num, ans_char in ans_matches:
        answer_key_map[q_num] = ans_char

    # --- BƯỚC 2: TÁCH CÁC CÂU HỎI ---
    # Tách dựa trên cụm "Câu X." hoặc "Câu X:"
    question_blocks = re.split(r'(?=Câu\s*\d+[:.])', content_text)
    parsed_data = []

    for block in question_blocks:
        block = block.strip()
        if not block: continue

        # Lấy số câu để đối chiếu với bảng đáp án
        num_match = re.search(r'Câu\s*(\d+)', block)
        if not num_match: continue
        q_num = num_match.group(1)

        lines = block.split('\n')
        # Nội dung câu hỏi: bỏ phần "Câu X."
        content = re.sub(r'^Câu\s*\d+[:.]\s*', '', lines[0]).strip()

        # Tìm các lựa chọn A, B, C, D
        options = []
        # Regex tìm A. nội dung, B. nội dung... (xử lý cả khi nằm cùng 1 dòng)
        opt_matches = re.findall(r'([A-D])\.\s*([^A-D\n\r]*)', block)
        for label, opt_text in opt_matches:
            options.append({"label": label, "text": opt_text.strip()})

        # Xác định loại câu hỏi
        if options:
            # CÂU TRẮC NGHIỆM: Lấy đáp án từ bảng map đã trích xuất ở Bước 1
            correct_answer = answer_key_map.get(q_num)
            q_type = "MULTIPLE_CHOICE"
        else:
            # CÂU TỰ LUẬN: Không có options
            correct_answer = None 
            q_type = "ESSAY"

        parsed_data.append({
            "number": q_num,
            "content": content,
            "options": options,
            "correct_answer": correct_answer,
            "type": q_type
        })

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
