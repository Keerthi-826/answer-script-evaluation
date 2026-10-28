from __future__ import annotations

import base64
import io
import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

import fitz
import requests
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from PIL import Image


# Same model as the notebook (Qwen/Qwen3-VL-4B-Instruct), served by Ollama.
DEFAULT_MODEL = os.getenv("QWEN_MODEL", "qwen3-vl:4b-instruct")
DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
PDF_RENDER_SCALE = 1.3
OCR_MAX_NEW_TOKENS = 768
MATCH_MAX_NEW_TOKENS = 4096
MATCH_CONTEXT_TOKENS = int(os.getenv("QWEN_NUM_CTX", "32768"))
PROMPT = """
Transcribe all handwritten and printed question/answer text visible in this page.

Rules:
- Return ONLY the transcription.
- Preserve the original wording; do not summarize or rewrite.
- Do not correct spelling or grammar.
- Preserve paragraphs, line breaks, headings, question numbers and subquestion labels.
- Preserve mathematical expressions, equations, symbols, lists and examples as accurately as possible.
- Do not invent missing text.
- If a word or short section cannot be read, write [UNCLEAR].
- Include crossed-out text only if it is still clearly intended as content.
- Ignore page borders and unrelated marks.
- Completely ignore institutional watermarks, college names, logos, and boilerplate
  text that is pre-printed on the answer booklet pages. In particular, omit any
  occurrence of: "VIGNANA JYOTHI", "TAMASOMA JYOTIRGAMAYA", "JYOTHI",
  "START FROM HERE", "WRITE ON BOTH SIDES OF EACH SHEET", and similar
  header/footer/watermark text that is not part of the student's handwriting or
  the question paper content.
"""


def matching_prompt(question_text: str, answer_text: str) -> str:
    return f"""
You are an examination-paper and handwritten-answer matching assistant.

OBJECTIVE
Match each question in the QUESTION PAPER with the student's corresponding
complete answer in the ANSWER BOOKLET. The final document must be ordered
according to the question paper, even if answers are written in another order.

QUESTION PAPER TRANSCRIPTION
<QUESTION_PAPER>
{question_text}
</QUESTION_PAPER>

ANSWER BOOKLET TRANSCRIPTION
<ANSWER_BOOKLET>
{answer_text}
</ANSWER_BOOKLET>

MATCHING RULES
1. Include every question and subquestion from the question paper in its original order.
2. ALWAYS match answers to questions by the question/subquestion number the student
   wrote (e.g. "1 a)", "4b)", "3. a)"). The student's numbering is the primary and
   most reliable indicator of which question they intended to answer. Use content-based
   matching only as a fallback when the student did not write any question number.
3. Even when two questions have similar or overlapping topics, each answer must be
   assigned ONLY to the question number the student labelled it with. Never duplicate
   an answer across multiple questions because of topic similarity.
4. Use the answer booklet as the source of answers. Never write a new/model answer
   from your own knowledge.
5. Preserve the full answer as transcribed. Do not summarize, shorten, paraphrase,
   or omit explanations, examples, equations, lists, or conclusion paragraphs.
6. If an answer continues across pages, combine its parts in their logical order.
7. Keep answers to subquestions separate when the paper has subparts.
8. If no corresponding answer can be identified, use:
   [ANSWER NOT FOUND]
9. If more than one answer could match and you cannot confidently decide, use:
   [MATCH UNCERTAIN - MANUAL REVIEW REQUIRED]
   You may briefly state the ambiguity in a separate match note.
10. Preserve [UNCLEAR] and [OCR FAILED] markers. Do not guess missing text.
11. Do not invent question text. If the OCR shows a page marker, do not treat it as
    an examination question.
12. Ignore institutional watermarks, college names, and boilerplate text (e.g.
    "VIGNANA JYOTHI", "TAMASOMA JYOTIRGAMAYA", "START FROM HERE",
    "WRITE ON BOTH SIDES OF EACH SHEET"). These are not answers or questions.
13. If the answer booklet contains extra answers that do not match a question, list
    them at the end under "Unmatched answer-booklet content" instead of discarding them.
14. If a question has internal subparts, reproduce the complete question and map each
    subpart to its corresponding answer where possible.

OUTPUT FORMAT
# Questions and Matched Answers

## Question 1
[Full question text, including its subparts]

**Answer:**
[Complete corresponding answer from the answer booklet]

**Match status:** Matched / Answer not found / Manual review required

Continue in exact question-paper order for all questions.

At the end, include "## Unmatched answer-booklet content" only if there is answer content
that could not be assigned to any question. Quote that content faithfully.

Return only the matched document. Do not add an introduction or summary.
"""


@dataclass
class Job:
    events: list[dict[str, Any]] = field(default_factory=list)
    condition: threading.Condition = field(default_factory=threading.Condition)
    done: bool = False

    def publish(self, event: str, **data: Any) -> None:
        with self.condition:
            self.events.append({"event": event, "data": data})
            self.condition.notify_all()


jobs: dict[str, Job] = {}
app = FastAPI(title="Qwen HTR API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def image_as_base64(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def generate(
    prompt: str,
    model: str,
    ollama_url: str,
    max_new_tokens: int,
    image: Image.Image | None = None,
    num_ctx: int | None = None,
) -> str:
    options: dict[str, Any] = {"temperature": 0, "num_predict": max_new_tokens}
    if num_ctx:
        options["num_ctx"] = num_ctx
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": options,
    }
    if image is not None:
        payload["images"] = [image_as_base64(image)]
    response = requests.post(
        f"{ollama_url.rstrip('/')}/api/generate",
        json=payload,
        timeout=1800,
    )
    response.raise_for_status()
    return response.json().get("response", "").strip()


def transcribe_page(image: Image.Image, model: str, ollama_url: str) -> str:
    result = generate(PROMPT, model, ollama_url, OCR_MAX_NEW_TOKENS, image=image)
    if not result:
        raise RuntimeError("Qwen returned an empty transcription.")
    return result


def match_questions_and_answers(
    question_text: str, answer_text: str, model: str, ollama_url: str
) -> str:
    result = generate(
        matching_prompt(question_text, answer_text),
        model,
        ollama_url,
        MATCH_MAX_NEW_TOKENS,
        num_ctx=MATCH_CONTEXT_TOKENS,
    )
    if not result:
        raise RuntimeError("Qwen returned an empty matching result.")
    return result


def combine_pages(page_texts: list[str], label: str) -> str:
    return "\n\n".join(
        f"--- {label} PAGE {i} ---\n{text}"
        for i, text in enumerate(page_texts, start=1)
    )


def check_ollama(ollama_url: str) -> None:
    try:
        response = requests.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=5)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(
            "Cannot connect to Ollama. Start Ollama and make sure it is listening "
            f"at {ollama_url}."
        ) from exc


def ocr_pdf(
    job: Job,
    pdf_bytes: bytes,
    document: str,
    model: str,
    ollama_url: str,
    max_width: int,
    max_height: int,
) -> list[str]:
    texts = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        page_count = len(pdf)
        if not page_count:
            raise RuntimeError(f"The {document} PDF has no pages.")
        job.publish("page_count", document=document, total=page_count)
        for index, source_page in enumerate(pdf):
            number = index + 1
            job.publish(
                "page_started", document=document, page=number, total=page_count
            )
            pixmap = source_page.get_pixmap(
                matrix=fitz.Matrix(PDF_RENDER_SCALE, PDF_RENDER_SCALE),
                alpha=False,
            )
            image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
            image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            try:
                transcription = transcribe_page(image, model, ollama_url)
            except (RuntimeError, requests.RequestException):
                transcription = "[OCR FAILED]"
            texts.append(transcription)
            job.publish(
                "page_result",
                document=document,
                page=number,
                total=page_count,
                image=f"data:image/png;base64,{image_as_base64(image)}",
                transcription=transcription,
            )
    return texts


def process_job(
    job: Job,
    question_pdf: bytes,
    answer_pdf: bytes,
    model: str,
    ollama_url: str,
    max_width: int,
    max_height: int,
) -> None:
    try:
        job.publish("status", message="Checking Ollama connection…")
        check_ollama(ollama_url)

        question_ocr = ocr_pdf(
            job, question_pdf, "question", model, ollama_url, max_width, max_height
        )
        answer_ocr = ocr_pdf(
            job, answer_pdf, "answer", model, ollama_url, max_width, max_height
        )
        question_text = combine_pages(question_ocr, "QUESTION PAPER")
        answer_text = combine_pages(answer_ocr, "ANSWER BOOKLET")

        job.publish("status", message="Matching questions to answers…")
        matched = match_questions_and_answers(
            question_text, answer_text, model, ollama_url
        )
        job.publish(
            "matched",
            markdown=matched,
            question_text=question_text,
            answer_text=answer_text,
        )
        job.publish("complete", message="Matching complete.")
    except (RuntimeError, requests.RequestException, fitz.FileDataError) as exc:
        job.publish("job_error", message=str(exc))
    finally:
        with job.condition:
            job.done = True
            job.condition.notify_all()


@app.post("/api/jobs")
async def create_job(
    question_file: UploadFile = File(...),
    answer_file: UploadFile = File(...),
    model: str = Form(DEFAULT_MODEL),
    ollama_url: str = Form(DEFAULT_OLLAMA_URL),
    max_width: int = Form(1400),
    max_height: int = Form(1800),
) -> dict[str, str]:
    pdfs = []
    for upload, label in (
        (question_file, "question paper"),
        (answer_file, "answer booklet"),
    ):
        if upload.content_type != "application/pdf":
            raise HTTPException(
                status_code=400, detail=f"The {label} must be a PDF file."
            )
        data = await upload.read()
        if not data:
            raise HTTPException(status_code=400, detail=f"The {label} PDF is empty.")
        pdfs.append(data)

    job_id = str(uuid.uuid4())
    job = Job()
    jobs[job_id] = job
    threading.Thread(
        target=process_job,
        args=(job, *pdfs, model, ollama_url, max_width, max_height),
        daemon=True,
    ).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}/events")
def stream_events(job_id: str) -> StreamingResponse:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    def event_stream():
        cursor = 0
        while True:
            with job.condition:
                while cursor >= len(job.events) and not job.done:
                    job.condition.wait(timeout=15)
                new_events = job.events[cursor:]
                cursor = len(job.events)
                finished = job.done and not new_events
            for item in new_events:
                yield f"event: {item['event']}\ndata: {json.dumps(item['data'])}\n\n"
            if finished:
                break
            if not new_events:
                yield ": keep-alive\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
