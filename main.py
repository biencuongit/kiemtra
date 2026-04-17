from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import create_engine, Column, Integer, String, Text, Float, ForeignKey, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from docx import Document # Thư viện đọc file Word
import io
import re
from datetime import datetime
from typing import List

# =============================================================================
# 1. DATABASE MODELS (Giữ nguyên cấu trúc cũ)
# =============================================================================
SQLALCHEMY_DATABASE_URL = "sqlite:///./azota_clone.db" 
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class ExamModel(Base):
    __tablename__ = "exams"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    duration = Column(Integer)
    questions = relationship("QuestionModel", back_populates="exam")

class QuestionModel(Base):
    __tablename__ = "questions"
    id = Column(Integer, primary_key=True, index=True)
    exam_id = Column(Integer, ForeignKey("exams.id"))
    content = Column(Text)
    correct_answer = Column(String)
    exam = relationship("ExamModel", back_populates="questions")
    options = relationship("OptionModel", back_populates="question")

class OptionModel(Base):
    __tablename__ = "options"
    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(Integer, ForeignKey("questions.id"))
    label = Column(String)
    text = Column(Text)
    question = relationship("QuestionModel", back_populates="options")

Base.metadata.create_all(bind=engine)

# =============================================================================
# 2. SIÊU BỘ PARSER (Xử lý văn bản từ file Word)
# =============================================================================
def parse_exam_text(text: str):
    # Tách phần đáp án ở cuối
    answer_key_map = {}
    split_pattern = re.compile(r'(ĐÁP ÁN|HƯỚNG DẪN GIẢI)', re.IGNORECASE)
    parts = split_pattern.split(text)
    content_text = parts[0]
    remaining_text = "".join(parts[1:])

    # Trích xuất bảng đáp án: 1. A 2. B...
    ans_matches = re.findall(r'(\d+)\s*[\.\s]\s*([A-D])', remaining_text)
    for q_num, ans_char in ans_matches:
        answer_key_map[q_num] = ans_char

    # Tách các câu hỏi
    question_blocks = re.split(r'(?=Câu\s*\d+[:.])', content_text)
    parsed_data = []
    for block in question_blocks:
        block = block.strip()
        if not block: continue
        
        num_match = re.search(r'Câu\s*(\d+)', block)
        if not num_match: continue
        q_num = num_match.group(1)
        
        lines = block.split('\n')
        content = re.sub(r'^Câu\s*\d+[:.]\s*', '', lines[0]).strip()
        
        options = []
        opt_matches = re.findall(r'([A-D])\.\s*([^A-D\n\r]*)', block)
        for label, opt_text in opt_matches:
            options.append({"label": label, "text": opt_text.strip()})
            
        correct_answer = answer_key_map.get(q_num) if options else None
        parsed_data.append({
            "content": content, 
            "options": options, 
            "correct_answer": correct_answer,
            "type": "MULTIPLE_CHOICE" if options else "ESSAY"
        })
    return parsed_data

# =============================================================================
# 3. API ENDPOINTS
# =============================================================================
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

@app.post("/upload-exam")
async def upload_exam(
    title: str = Form(...), 
    duration: int = Form(...), 
    file: UploadFile = File(...), 
    db: Session = Depends(get_db)
):
    # 1. Kiểm tra định dạng file
    if not file.filename.endswith('.docx'):
        raise HTTPException(status_code=400, detail="Chỉ chấp nhận file .docx")

    # 2. Đọc file Word chuyển thành văn bản
    file_content = await file.read()
    doc = Document(io.BytesIO(file_content))
    full_text = "\n".join([para.text for para in doc.paragraphs])

    # 3. Parse văn bản thành cấu trúc dữ liệu
    parsed_questions = parse_exam_text(full_text)
    if not parsed_questions:
        raise HTTPException(status_code=400, detail="Không nhận diện được câu hỏi nào trong file.")

    # 4. Đẩy lên Database
    new_exam = ExamModel(title=title, duration=duration)
    db.add(new_exam)
    db.commit()
    db.refresh(new_exam)

    for q_data in parsed_questions:
        new_q = QuestionModel(
            exam_id=new_exam.id, 
            content=q_data["content"], 
            correct_answer=q_data["correct_answer"]
        )
        db.add(new_q)
        db.commit()
        db.refresh(new_q)

        for opt in q_data["options"]:
            db.add(OptionModel(question_id=new_q.id, label=opt["label"], text=opt["text"]))
    
    db.commit()
    return {"message": "Đã upload và lưu đề thi thành công!", "exam_id": new_exam.id}

# (Giữ nguyên API /exam/{exam_id} và /submit-exam từ bài trước)
@app.get("/exam/{exam_id}")
async def get_exam(exam_id: int, db: Session = Depends(get_db)):
    exam = db.query(ExamModel).filter(ExamModel.id == exam_id).first()
    if not exam: raise HTTPException(status_code=404)
    return {
        "title": exam.title, "duration": exam.duration,
        "questions": [{"id": q.id, "content": q.content, "options": [{"label": o.label, "text": o.text} for o in q.options]} for q in exam.questions]
    }
