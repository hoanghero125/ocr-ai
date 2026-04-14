"""
Minimal local OCR testing server.

No AWS. No database. No mocking.
Just upload a PDF and get results back immediately.

Usage:
    python scripts/local_server.py
    # open http://localhost:8000/docs
"""

import base64
import json
import os
import sys
from pathlib import Path

# ── Project root on sys.path ──────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── Load .env ─────────────────────────────────────────────────────────────────
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(env_path)
else:
    print("[local] No .env found — copy .env.example to .env and set MISTRAL_API_KEY")
    sys.exit(1)

os.environ.setdefault("ENVIRONMENT", "local")

# ── Wire up OCR + extraction stages directly (no Container, no AWS) ───────────
from src.shared.config import get_settings
from src.mistral.client import MistralClient
from src.mistral.ocr import OCRStage
from src.mistral.extraction import ExtractionStage

get_settings.cache_clear()
_settings = get_settings()

_client = MistralClient(
    api_key=_settings.mistral.api_key,
    ocr_model=_settings.mistral.ocr_model,
    chat_model=_settings.mistral.chat_model,
    table_format=_settings.mistral.table_format,
    base_url=_settings.mistral.base_url,
    timeout_s=_settings.mistral.timeout_s,
    max_retries=_settings.mistral.max_retries,
)

_ocr = OCRStage(client=_client)
_extraction = ExtractionStage(
    client=_client,
    max_concurrent_pages=_settings.processing.max_concurrent_pages,
    max_retries_per_page=_settings.processing.extract_max_retries_per_page,
)

# ── FastAPI app ───────────────────────────────────────────────────────────────
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI(
    title="OCR Local",
    version="1.0.0",
    description="Upload a PDF — get OCR markdown or extracted fields back.",
)


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/ocr", summary="OCR only — returns raw markdown per page")
async def ocr(file: UploadFile = File(..., description="PDF file")):
    data = await file.read()
    pdf_url = f"data:application/pdf;base64,{base64.b64encode(data).decode()}"

    pages = await _ocr.run(pdf_url=pdf_url)

    return {
        "total_pages": len(pages),
        "pages": [
            {
                "page_number": p.page_number,
                "markdown": p.markdown,
                "tables": len(p.tables),
            }
            for p in pages
        ],
    }


@app.post("/extract", summary="OCR + field extraction — returns structured fields per page")
async def extract(
    file: UploadFile = File(..., description="PDF file"),
    fields: str = Form(
        ...,
        description='JSON array of fields to extract. Example: [{"key": "ho_ten", "label": "Ho ten"}, {"key": "ngay_ky", "label": "Ngay ky"}]',
    ),
):
    try:
        field_list = json.loads(fields)
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "fields must be a valid JSON array"})

    from src.models.job import FieldInstruction

    field_instructions = tuple(
        FieldInstruction(
            key=f["key"],
            label=f["label"],
            description=f.get("description", ""),
            min_confidence=f.get("min_confidence", 0.0),
        )
        for f in field_list
    )

    data = await file.read()
    pdf_url = f"data:application/pdf;base64,{base64.b64encode(data).decode()}"

    pages = await _ocr.run(pdf_url=pdf_url)
    pages = await _extraction.run(
        pages=pages,
        field_instructions=field_instructions,
    )

    return {
        "total_pages": len(pages),
        "pages": [
            {
                "page_number": p.page_number,
                "fields": [
                    {"key": f.key, "label": f.label, "value": f.value, "confidence": f.confidence}
                    for f in p.fields
                ],
                "error": p.error,
            }
            for p in pages
        ],
    }


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
