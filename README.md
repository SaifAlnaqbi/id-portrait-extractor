# ID Portrait Extractor

A FastAPI service that takes a photo or scan of an **ID card or passport**, extracts the holder's **portrait** and returns it as a **base64-encoded JPEG**. As a bonus, it reads the document's **Machine Readable Zone (MRZ)** to extract name, document number, nationality, date of birth and expiry date, validated with ICAO 9303 check digits.

| | |
|---|---|
| 🌐 **Live API** | https://id-portrait-extractor.onrender.com/docs *(free tier: first request after idle takes ~30–60 s)* |
| 🐳 **Docker image** | `ghcr.io/saifalnaqbi/id-portrait-extractor:latest` |
| 💻 **Source** | https://github.com/SaifAlnaqbi/id-portrait-extractor |
| 📐 **Design document** | [docs/DESIGN.md](docs/DESIGN.md) |
| 📖 **API documentation** | [docs/API.md](docs/API.md) |

## Features

- **Portrait extraction** with MediaPipe face detection + OpenCV
  - picks the main portrait (largest face), ignoring the small secondary "ghost" photo
  - **levels tilted documents** using the eye keypoints
  - recovers **sideways / upside-down** images
  - frames the result like an ID photo (**3:4**, head and shoulders)
- **MRZ field extraction** (bonus) for passports (TD3) and ID cards (TD1/TD2)
  - morphology-based MRZ localisation + Tesseract OCR with a bundled **MRZ-trained model**
  - check-digit validation and **automatic OCR error correction** (O/0, I/1, S/5, `<`/K …)
- Two input styles: **multipart upload** or **base64 JSON** (data-URLs accepted)
- Clear error codes (400 / 413 / 422 / 503), 10 MB upload limit, interactive Swagger docs
- Containerised (non-root, health check, `$PORT`-aware), CI with tests + image publishing

## Quick start

### Use the hosted API

```bash
curl -X POST https://id-portrait-extractor.onrender.com/extract-portrait \
  -F "file=@sample_images/passport_spain_specimen.jpg"

curl -X POST https://id-portrait-extractor.onrender.com/extract-fields \
  -F "file=@sample_images/passport_norway_specimen.jpg"
```

Or open **[/docs](https://id-portrait-extractor.onrender.com/docs)** and use *Try it out*.

### Run with Docker

```bash
# pre-built image
docker run -p 8000:8000 ghcr.io/saifalnaqbi/id-portrait-extractor:latest

# or build it yourself
docker build -t id-portrait-extractor .
docker run -p 8000:8000 id-portrait-extractor
```

Then open http://127.0.0.1:8000/docs.

### Run locally (without Docker)

Requires Python 3.11+ and, for the MRZ endpoints, [Tesseract OCR](https://github.com/tesseract-ocr/tesseract)
(Windows: `winget install UB-Mannheim.TesseractOCR`, macOS: `brew install tesseract`, Debian/Ubuntu: `apt install tesseract-ocr`).

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate on Linux/macOS)
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

### Run the tests

```bash
pytest -v
```

The MRZ end-to-end tests are skipped automatically if Tesseract is not installed.

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check (`{"status":"ok","ocr_available":true}`) |
| `POST` | `/extract-portrait` | Portrait from a multipart file upload |
| `POST` | `/extract-portrait/base64` | Portrait from `{"image_base64": "..."}` |
| `POST` | `/extract-fields` | MRZ fields from a multipart file upload |
| `POST` | `/extract-fields/base64` | MRZ fields from `{"image_base64": "..."}` |

Portrait response:

```json
{
  "portrait_base64": "/9j/4AAQSkZJRg...",
  "format": "jpeg",
  "face_count": 1,
  "width": 193,
  "height": 256,
  "confidence": 0.8736,
  "rotation_degrees": 0.0
}
```

Full request/response reference, error codes and Python/JS/PowerShell examples: **[docs/API.md](docs/API.md)**.

## Configuration

| Env var | Default | Description |
|---|---|---|
| `PORT` | `8000` | Port the server listens on (set automatically by most hosts) |
| `MAX_UPLOAD_MB` | `10` | Maximum accepted image size |
| `TESSERACT_CMD` | auto-detected | Path to the `tesseract` binary if it is not on `PATH` |

## Deployment

- **CI** — [.github/workflows/ci.yml](.github/workflows/ci.yml) runs the tests, builds the Docker image and publishes it to GitHub Container Registry on every push to `main`.
- **Hosting** — [render.yaml](render.yaml) is a Render Blueprint: *Render dashboard → New → Blueprint → select this repo*. Render builds the Dockerfile and redeploys on every push. The same image runs unchanged on Google Cloud Run, Azure Container Apps, AWS App Runner or Heroku (container stack).

## Project structure

```
app/
  main.py                FastAPI app, routes, input handling, error mapping
  schemas.py             Pydantic request/response models
  portrait_extractor.py  Face detection, orientation recovery, deskew, 3:4 framing
  mrz_reader.py          MRZ localisation + Tesseract OCR
  tessdata/              mrz.traineddata (MRZ OCR model, BSD-3, DoubangoTelecom/tesseractMRZ)
  mrz.py                 ICAO 9303 MRZ parser, check digits, OCR error correction
tests/                   pytest suite (API, portrait pipeline, MRZ parser)
sample_images/           Test images: Albanian ID card photos + public passport specimens
docs/                    Design document and API documentation
Dockerfile               Production image
render.yaml              Render deployment blueprint
.github/workflows/       CI pipeline
```

## Sample images

The passport images are official **specimen** documents (fictional holders) published on [Wikimedia Commons](https://commons.wikimedia.org/wiki/Category:Passport_data_pages): Norway, Netherlands, South Korea and Spain. The Albanian ID card photos show the same specimen card under different conditions (flat, tilted, in hand, on a keyboard).
