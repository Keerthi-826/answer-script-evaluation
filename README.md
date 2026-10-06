# Exam Script Transcription

A local web application for transcribing handwritten exam scripts with
Ollama and Qwen.

## Requirements

- Python 3.10+
- Node.js 18+
- [Ollama](https://ollama.com/download/windows)
- Qwen vision model:

  ```powershell
  ollama pull qwen2.5vl:3b
  ```

## Setup

Create and activate a Python environment:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Install frontend dependencies:

```powershell
npm install
```

## Run

Start the API:

```powershell
uvicorn app:app --reload
```

In a second terminal, start the frontend:

```powershell
npm run dev
```

Open `http://localhost:5173`, upload a PDF, and start the transcription.
Completed pages appear as they are processed.
