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


DEFAULT_MODEL = os.getenv("QWEN_MODEL", "qwen2.5vl:3b")
DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
PROMPT = """Transcribe all handwritten text in this image.

Rules:
- Return ONLY the transcription.
- Preserve the original wording.
- Do NOT summarize, explain, correct spelling, or correct grammar.
- Preserve paragraphs and line breaks where possible.
- Include handwritten question numbers and headings.
- Do not invent missing text.
- If a word cannot be read, write [UNCLEAR].
- Ignore page borders and unrelated marks.
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


def transcribe_page(image: Image.Image, model: str, ollama_url: str) -> str:
    response = requests.post(
        f"{ollama_url.rstrip('/')}/api/generate",
        json={
            "model": model,
            "prompt": PROMPT,
            "images": [image_as_base64(image)],
            "stream": False,
            "options": {"temperature": 0},
        },
        timeout=1800,
    )
    response.raise_for_status()
    result = response.json().get("response", "").strip()
    if not result:
        raise RuntimeError("Qwen returned an empty transcription.")
    return result


def check_ollama(ollama_url: str) -> None:
    try:
        response = requests.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=5)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(
            "Cannot connect to Ollama. Start Ollama and make sure it is listening "
            f"at {ollama_url}."
        ) from exc


def process_job(
    job: Job,
    pdf_bytes: bytes,
    model: str,
    ollama_url: str,
    max_width: int,
    max_height: int,
) -> None:
    try:
        job.publish("status", message="Checking Ollama connection…")
        check_ollama(ollama_url)
        job.publish("status", message="Calculating the number of pages…")

        with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
            page_count = len(document)
            job.publish("page_count", total=page_count)
            for index, source_page in enumerate(document):
                number = index + 1
                job.publish("page_started", page=number, total=page_count)
                pixmap = source_page.get_pixmap(
                    matrix=fitz.Matrix(1.5, 1.5),
                    alpha=False,
                )
                image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
                image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
                transcription = transcribe_page(image, model, ollama_url)
                job.publish(
                    "page_result",
                    page=number,
                    total=page_count,
                    image=f"data:image/png;base64,{image_as_base64(image)}",
                    transcription=transcription,
                )

        job.publish("complete", message="Transcription complete.")
    except (RuntimeError, requests.RequestException, fitz.FileDataError) as exc:
        job.publish("job_error", message=str(exc))
    finally:
        with job.condition:
            job.done = True
            job.condition.notify_all()


@app.post("/api/jobs")
async def create_job(
    file: UploadFile = File(...),
    model: str = Form(DEFAULT_MODEL),
    ollama_url: str = Form(DEFAULT_OLLAMA_URL),
    max_width: int = Form(1400),
    max_height: int = Form(1800),
) -> dict[str, str]:
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="The uploaded PDF is empty.")

    job_id = str(uuid.uuid4())
    job = Job()
    jobs[job_id] = job
    threading.Thread(
        target=process_job,
        args=(job, pdf_bytes, model, ollama_url, max_width, max_height),
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
