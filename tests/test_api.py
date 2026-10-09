import base64
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.main as main
from app.main import app
from app.mrz_reader import tesseract_available

client = TestClient(app)
SAMPLES = Path(__file__).resolve().parent.parent / "sample_images"

requires_tesseract = pytest.mark.skipif(not tesseract_available(), reason="Tesseract OCR not installed")


def _blank_jpeg_bytes(width=200, height=200) -> bytes:
    image = Image.new("RGB", (width, height), color=(255, 255, 255))
    buf = io.BytesIO()
    image.save(buf, format="JPEG")
    return buf.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "ocr_available" in response.json()


def test_root_redirects_to_docs():
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/docs"


# ---------- portrait ----------

def test_extract_portrait_upload_success():
    sample = (SAMPLES / "alb_id_clean_template.jpg").read_bytes()
    response = client.post("/extract-portrait", files={"file": ("id.jpg", sample, "image/jpeg")})
    assert response.status_code == 200
    body = response.json()
    portrait = Image.open(io.BytesIO(base64.b64decode(body["portrait_base64"])))
    assert portrait.format == "JPEG"
    assert portrait.size == (body["width"], body["height"])
    assert body["face_count"] >= 1
    assert 0 < body["confidence"] <= 1


def test_extract_portrait_base64_success_with_data_url():
    sample = (SAMPLES / "passport_spain_specimen.jpg").read_bytes()
    response = client.post(
        "/extract-portrait/base64",
        json={"image_base64": "data:image/jpeg;base64," + _b64(sample), "margin": 0.5},
    )
    assert response.status_code == 200
    assert response.json()["format"] == "jpeg"


def test_extract_portrait_upload_no_face_returns_422():
    response = client.post("/extract-portrait", files={"file": ("blank.jpg", _blank_jpeg_bytes(), "image/jpeg")})
    assert response.status_code == 422


def test_extract_portrait_upload_empty_file_returns_400():
    response = client.post("/extract-portrait", files={"file": ("empty.jpg", b"", "image/jpeg")})
    assert response.status_code == 400


def test_extract_portrait_base64_invalid_base64_returns_400():
    response = client.post("/extract-portrait/base64", json={"image_base64": "not-valid-base64!!"})
    assert response.status_code == 400


def test_extract_portrait_base64_corrupt_image_returns_400():
    response = client.post("/extract-portrait/base64", json={"image_base64": _b64(b"this is not an image")})
    assert response.status_code == 400


def test_extract_portrait_margin_out_of_range_returns_422():
    response = client.post("/extract-portrait/base64", json={"image_base64": _b64(_blank_jpeg_bytes()), "margin": 5})
    assert response.status_code == 422


def test_upload_too_large_returns_413(monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 1000)
    response = client.post("/extract-portrait", files={"file": ("big.jpg", b"x" * 2000, "image/jpeg")})
    assert response.status_code == 413
    response = client.post("/extract-portrait/base64", json={"image_base64": _b64(b"x" * 2000)})
    assert response.status_code == 413


# ---------- MRZ fields ----------

@requires_tesseract
@pytest.mark.parametrize(
    "filename, surname, given_names, document_number, dob, expiry",
    [
        ("passport_norway_specimen.jpg", "OESTENBYEN", "AASAMUND SPECIMEN", "CCC002251", "1956-04-23", "2030-04-15"),
        ("passport_netherlands_specimen.jpg", "DE BRUIJN", "WILLEKE LISELOTTE", "SPECI2014", "1965-03-10", "2024-03-09"),
        ("passport_south_korea_specimen.jpg", "LEE", "SUYEON", "M70689098", "1985-07-02", "2024-04-15"),
        ("passport_spain_specimen.jpg", "ESPANOLA ESPANOLA", "CARMEN", "ZAB000254", "1980-01-01", "2025-01-01"),
    ],
)
def test_extract_fields_from_passports(filename, surname, given_names, document_number, dob, expiry):
    sample = (SAMPLES / filename).read_bytes()
    response = client.post("/extract-fields", files={"file": (filename, sample, "image/jpeg")})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valid"] is True
    assert body["mrz_format"] == "TD3"
    assert body["surname"] == surname
    assert body["given_names"] == given_names
    assert body["document_number"] == document_number
    assert body["date_of_birth"] == dob
    assert body["expiry_date"] == expiry


@requires_tesseract
def test_extract_fields_base64():
    sample = (SAMPLES / "passport_norway_specimen.jpg").read_bytes()
    response = client.post("/extract-fields/base64", json={"image_base64": _b64(sample)})
    assert response.status_code == 200
    assert response.json()["nationality"] == "NOR"


@requires_tesseract
def test_extract_fields_no_mrz_returns_422():
    response = client.post("/extract-fields", files={"file": ("blank.jpg", _blank_jpeg_bytes(600, 400), "image/jpeg")})
    assert response.status_code == 422


def test_extract_fields_corrupt_image_returns_400():
    response = client.post("/extract-fields/base64", json={"image_base64": _b64(b"not an image")})
    assert response.status_code in (400, 503)
