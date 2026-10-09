import base64
import binascii
import logging
import os

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app.mrz_reader import NoMRZFoundError, OCRUnavailableError, read_mrz, tesseract_available
from app.portrait_extractor import InvalidImageError, NoFaceDetectedError, extract_portrait
from app.schemas import (
    ErrorResponse,
    ExtractFieldsRequest,
    ExtractFieldsResponse,
    ExtractPortraitRequest,
    ExtractPortraitResponse,
    HealthResponse,
)

logger = logging.getLogger("id_portrait_extractor")

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "10")) * 1024 * 1024

app = FastAPI(
    title="ID Portrait Extractor",
    description=(
        "Extracts the portrait photo from an ID card or passport image and returns it as base64. "
        "Also reads the Machine Readable Zone (MRZ) to extract name, document number, dates of birth and expiry."
    ),
    version="1.1.0",
)
# Public test API: allow browser clients from any origin.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_ERRORS = {
    400: {"model": ErrorResponse, "description": "Bad input: empty file, invalid base64 or undecodable image."},
    413: {"model": ErrorResponse, "description": "Image larger than the upload limit (10 MB by default)."},
}


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health():
    return HealthResponse(status="ok", ocr_available=tesseract_available())


# ---------- input helpers ----------

def _read_upload(file: UploadFile) -> bytes:
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"Image exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
    return data


def _decode_base64(value: str) -> bytes:
    if value.startswith("data:") and "," in value:  # accept data URLs like data:image/png;base64,....
        value = value.split(",", 1)[1]
    # Base64 inflates size by 4/3; reject before decoding huge payloads.
    if len(value) > MAX_UPLOAD_BYTES * 4 // 3 + 4:
        raise HTTPException(status_code=413, detail=f"Image exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="image_base64 is not valid base64.") from exc
    if not data:
        raise HTTPException(status_code=400, detail="Decoded image is empty.")
    return data


# ---------- portrait ----------

def _run_portrait(image_bytes: bytes, margin: float) -> ExtractPortraitResponse:
    try:
        result = extract_portrait(image_bytes, margin=margin)
    except InvalidImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NoFaceDetectedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ExtractPortraitResponse(
        portrait_base64=base64.b64encode(result.jpeg_bytes).decode("ascii"),
        format="jpeg",
        face_count=result.face_count,
        width=result.width,
        height=result.height,
        confidence=result.confidence,
        rotation_degrees=result.rotation_degrees,
    )


_PORTRAIT_ERRORS = {**_ERRORS, 422: {"model": ErrorResponse, "description": "No face detected (or request validation failed)."}}


@app.post(
    "/extract-portrait",
    response_model=ExtractPortraitResponse,
    responses=_PORTRAIT_ERRORS,
    tags=["portrait"],
    summary="Extract portrait from an uploaded image file",
)
def extract_portrait_upload(
    file: UploadFile = File(..., description="ID card / passport image (jpg, png, webp, bmp, tiff)."),
    margin: float = Form(0.3, ge=0.0, le=2.0, description="Horizontal padding each side, as a fraction of face width."),
):
    return _run_portrait(_read_upload(file), margin)


@app.post(
    "/extract-portrait/base64",
    response_model=ExtractPortraitResponse,
    responses=_PORTRAIT_ERRORS,
    tags=["portrait"],
    summary="Extract portrait from a base64-encoded image in a JSON body",
)
def extract_portrait_base64(payload: ExtractPortraitRequest):
    return _run_portrait(_decode_base64(payload.image_base64), payload.margin)


# ---------- MRZ fields (bonus) ----------

def _run_fields(image_bytes: bytes) -> ExtractFieldsResponse:
    try:
        result = read_mrz(image_bytes)
    except InvalidImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NoMRZFoundError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OCRUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ExtractFieldsResponse(
        valid=result.valid,
        mrz_format=result.format,
        document_type=result.document_type,
        issuing_country=result.issuing_country,
        surname=result.surname,
        given_names=result.given_names,
        document_number=result.document_number,
        nationality=result.nationality,
        date_of_birth=result.date_of_birth,
        sex=result.sex,
        expiry_date=result.expiry_date,
        optional_data=result.optional_data,
        checks=result.checks,
        raw_mrz=result.raw_lines,
    )


_FIELDS_ERRORS = {
    **_ERRORS,
    422: {"model": ErrorResponse, "description": "No MRZ found in the image (or request validation failed)."},
    503: {"model": ErrorResponse, "description": "OCR engine (Tesseract) not available on the server."},
}


@app.post(
    "/extract-fields",
    response_model=ExtractFieldsResponse,
    responses=_FIELDS_ERRORS,
    tags=["fields"],
    summary="Read the MRZ of an uploaded passport / ID card image",
)
def extract_fields_upload(file: UploadFile = File(..., description="Passport data page or ID card side showing the MRZ.")):
    return _run_fields(_read_upload(file))


@app.post(
    "/extract-fields/base64",
    response_model=ExtractFieldsResponse,
    responses=_FIELDS_ERRORS,
    tags=["fields"],
    summary="Read the MRZ of a base64-encoded passport / ID card image",
)
def extract_fields_base64(payload: ExtractFieldsRequest):
    return _run_fields(_decode_base64(payload.image_base64))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})
