"""
FastAPI Server — Universal Financial Document Underwriting Engine
"""

import asyncio
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.gateway import UniversalLLMGateway
from app.pipeline import PipelineOrchestrator
from app.config import SUPPORTED_PROVIDERS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

app = FastAPI(title="Universal Financial Statement & Underwriting Engine")

allowed_origins = [origin.strip() for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins or ["*"],
    allow_credentials=bool(allowed_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

PROGRESS_STREAMS = {}


@app.get("/api/health")
def health_check():
    return {"status": "ok"}


@app.get("/api/providers")
def get_providers():
    return SUPPORTED_PROVIDERS


@app.post("/api/test-connection")
def test_connection(
    x_provider: str = Header(...),
    x_api_key: str = Header(...),
    x_model: str = Header(...),
    x_base_url: str = Header(None)
):
    try:
        gateway = UniversalLLMGateway(x_provider, x_api_key, x_model, x_base_url)
        gateway.test_handshake()
        return {"status": "ok", "message": f"Successfully connected to {x_model} ({x_provider})!"}
    except Exception as e:
        detail = str(e)
        if hasattr(e, "__cause__") and e.__cause__:
            detail = f"{detail} (Underlying: {e.__cause__})"
        logger.error(f"Handshake failed: {detail}")
        raise HTTPException(status_code=400, detail=detail)


@app.get("/api/progress/{job_id}")
async def get_progress(job_id: str):
    async def event_generator():
        last_pct = -1
        while True:
            if job_id in PROGRESS_STREAMS:
                state = PROGRESS_STREAMS[job_id]
                if state.get("pct") != last_pct or state.get("done"):
                    last_pct = state.get("pct")
                    yield f"data: {json.dumps(state)}\n\n"
                    if state.get("done"):
                        break
            await asyncio.sleep(0.3)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/extract")
async def extract_document(
    file: UploadFile = File(...),
    job_id: str = Form(...),
    x_provider: str = Header(...),
    x_api_key: str = Header(...),
    x_model: str = Header(...),
    x_base_url: str = Header(None)
):
    PROGRESS_STREAMS[job_id] = {"msg": "Initializing extraction...", "pct": 5, "done": False}

    temp_dir = tempfile.mkdtemp(prefix="stmt_")
    suffix = Path(file.filename or "document").suffix.lower()
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg"}:
        raise HTTPException(status_code=400, detail="Only PDF, PNG, and JPG files are supported.")
    file_path = os.path.join(temp_dir, f"upload{suffix}")

    try:
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        def progress_cb(msg, pct):
            PROGRESS_STREAMS[job_id] = {"msg": msg, "pct": pct, "done": False}

        gateway = UniversalLLMGateway(x_provider, x_api_key, x_model, x_base_url)
        orchestrator = PipelineOrchestrator(gateway, progress_callback=progress_cb)

        result = await asyncio.to_thread(orchestrator.run, file_path)

        PROGRESS_STREAMS[job_id] = {"msg": "Complete!", "pct": 100, "done": True}
        return JSONResponse(content={"status": "success", "result": result})

    except Exception as e:
        logger.exception("Extraction failed")
        PROGRESS_STREAMS[job_id] = {"msg": f"Failed: {str(e)}", "pct": 100, "done": True, "error": True}
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


os.makedirs("static", exist_ok=True)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
