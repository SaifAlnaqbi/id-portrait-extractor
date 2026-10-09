# API Documentation — ID Portrait Extractor

**Base URL (hosted):** `https://id-portrait-extractor.onrender.com`
**Base URL (local):** `http://127.0.0.1:8000`

Interactive docs are generated from the code and always up to date:

* Swagger UI — `GET /docs` (try requests in the browser)
* ReDoc — `GET /redoc`
* OpenAPI schema — `GET /openapi.json` (import into Postman/Insomnia)

> The free hosting tier sleeps after ~15 minutes of inactivity. The first request after that can take 30–60 seconds while the container starts; later requests are fast.

All responses are JSON. There is no authentication (public test service). CORS is open to all origins.

---

## Endpoints overview

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `POST` | `/extract-portrait` | Extract the portrait — **multipart file upload** |
| `POST` | `/extract-portrait/base64` | Extract the portrait — **base64 image in JSON** |
| `POST` | `/extract-fields` | Read MRZ fields — multipart file upload *(bonus)* |
| `POST` | `/extract-fields/base64` | Read MRZ fields — base64 image in JSON *(bonus)* |

**Accepted image formats:** JPEG, PNG, WebP, BMP, TIFF. **Max size:** 10 MB (configurable with the `MAX_UPLOAD_MB` environment variable).

---

## `GET /health`

```bash
curl https://id-portrait-extractor.onrender.com/health
```

**200 OK**
```json
{ "status": "ok", "ocr_available": true }
```

| Field | Type | Description |
|---|---|---|
| `status` | string | Always `"ok"` when the service is up |
| `ocr_available` | bool | Whether Tesseract is installed (required by `/extract-fields`) |

---

## `POST /extract-portrait`

Upload an image of an ID card or passport; receive the holder's portrait, levelled and framed 3:4.

**Request** — `Content-Type: multipart/form-data`

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `file` | file | yes | — | The document image |
| `margin` | float `0.0–2.0` | no | `0.3` | Horizontal padding each side of the face, as a fraction of face width. Larger = looser crop. The crop is always 3:4 |

```bash
curl -X POST https://id-portrait-extractor.onrender.com/extract-portrait \
  -F "file=@sample_images/passport_spain_specimen.jpg" \
  -F "margin=0.3"
```

**200 OK**
```json
{
  "portrait_base64": "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a...",
  "format": "jpeg",
  "face_count": 1,
  "width": 193,
  "height": 256,
  "confidence": 0.8736,
  "rotation_degrees": 0.0
}
```

| Field | Type | Description |
|---|---|---|
| `portrait_base64` | string | Base64-encoded JPEG of the portrait (no `data:` prefix) |
| `format` | string | Always `"jpeg"` |
| `face_count` | int | Faces detected in the source (secondary "ghost" portraits included). The **largest** is returned |
| `width`, `height` | int | Portrait size in pixels |
| `confidence` | float | Face detector confidence for the returned face, 0–1 |
| `rotation_degrees` | float | Counter-clockwise rotation applied to the source to make the face upright (e.g. `90` for a sideways scan, `10.35` for a tilted photo) |

To display the result in HTML: `<img src="data:image/jpeg;base64,{portrait_base64}">`.

---

## `POST /extract-portrait/base64`

Same as above, with the image sent as base64 inside a JSON body.

**Request** — `Content-Type: application/json`

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `image_base64` | string | yes | — | Base64 image. A data-URL prefix (`data:image/jpeg;base64,...`) is accepted |
| `margin` | float `0.0–2.0` | no | `0.3` | As above |

```json
{
  "image_base64": "/9j/4AAQSkZJRgABAQEASABIAAD...",
  "margin": 0.3
}
```

```bash
# Linux / macOS / Git Bash
curl -X POST https://id-portrait-extractor.onrender.com/extract-portrait/base64 \
  -H "Content-Type: application/json" \
  -d "{\"image_base64\": \"$(base64 -w0 sample_images/passport_spain_specimen.jpg)\"}"
```

**Response** — identical to `/extract-portrait`.

---

## `POST /extract-fields` *(bonus)*

Reads the **Machine Readable Zone** (the `<<<` lines at the bottom of a passport data page or the back of an ID card) and returns the holder's data, validated with the ICAO 9303 check digits.

**Request** — `multipart/form-data` with a single `file` field.

```bash
curl -X POST https://id-portrait-extractor.onrender.com/extract-fields \
  -F "file=@sample_images/passport_norway_specimen.jpg"
```

**200 OK**
```json
{
  "valid": true,
  "mrz_format": "TD3",
  "document_type": "P",
  "issuing_country": "NOR",
  "surname": "OESTENBYEN",
  "given_names": "AASAMUND SPECIMEN",
  "document_number": "CCC002251",
  "nationality": "NOR",
  "date_of_birth": "1956-04-23",
  "sex": "M",
  "expiry_date": "2030-04-15",
  "optional_data": "",
  "checks": {
    "document_number": true,
    "date_of_birth": true,
    "expiry_date": true,
    "composite": true,
    "optional_data": null
  },
  "raw_mrz": [
    "P<NOROESTENBYEN<<AASAMUND<SPECIMEN<<<<<<<<<<",
    "CCC0022514N0R5604230M3004157<<<<<<<<<<<<<<04"
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `valid` | bool | `true` only if **every** check digit passed. Treat fields as unverified when `false` |
| `mrz_format` | string | `TD1` (ID card, 3×30), `TD2` (2×36) or `TD3` (passport, 2×44) |
| `document_type` | string | `P` passport, `I`/`ID`/`AC`… identity documents |
| `issuing_country`, `nationality` | string | ICAO 3-letter codes (`NOR`, `NLD`, `D` for Germany, …) |
| `surname`, `given_names` | string | As written in the MRZ (transliterated, upper case: `Ø` → `OE`) |
| `document_number` | string | Document number, OCR-corrected using its check digit |
| `date_of_birth`, `expiry_date` | string \| null | ISO `YYYY-MM-DD`; `null` if unreadable |
| `sex` | string | `M`, `F` or `X` (unspecified) |
| `optional_data` | string | Personal number / optional data, if present |
| `checks` | object | Result of each individual check digit. `optional_data` is `null` when not applicable |
| `raw_mrz` | string[] | The MRZ lines as read by OCR (before field-level correction) — useful for debugging |

---

## `POST /extract-fields/base64` *(bonus)*

```json
{ "image_base64": "/9j/4AAQSkZJRgABAQEASABIAAD..." }
```

Response identical to `/extract-fields`.

---

## Errors

Every error returns the same shape:

```json
{ "detail": "No face detected in the provided image." }
```

| Status | Endpoints | Cause | Example `detail` |
|---|---|---|---|
| `400 Bad Request` | all POST | Empty file; invalid base64; bytes are not an image | `"Could not decode image bytes; unsupported or corrupt format."` |
| `413 Payload Too Large` | all POST | Image larger than the limit | `"Image exceeds the 10 MB limit."` |
| `422 Unprocessable Entity` | portrait | No face found in the image | `"No face detected in the provided image."` |
| `422 Unprocessable Entity` | fields | No MRZ found / readable | `"No machine readable zone (MRZ) could be found in the image."` |
| `422 Unprocessable Entity` | all POST | Request validation (missing `file`, `margin` out of range…). FastAPI returns `detail` as a list of field errors | `[{"loc": ["body","margin"], "msg": "Input should be less than or equal to 2", ...}]` |
| `503 Service Unavailable` | fields | Tesseract not installed on the server | `"Tesseract OCR is not installed on the server."` |
| `500 Internal Server Error` | all | Unexpected failure (logged server-side) | `"Internal server error."` |

---

## Usage examples

### Python

```python
import base64
import requests

BASE = "https://id-portrait-extractor.onrender.com"

# 1. Portrait via file upload
with open("passport.jpg", "rb") as f:
    r = requests.post(f"{BASE}/extract-portrait", files={"file": f}, data={"margin": 0.3}, timeout=90)
r.raise_for_status()
with open("portrait.jpg", "wb") as out:
    out.write(base64.b64decode(r.json()["portrait_base64"]))

# 2. Portrait via base64 JSON
with open("passport.jpg", "rb") as f:
    payload = {"image_base64": base64.b64encode(f.read()).decode()}
r = requests.post(f"{BASE}/extract-portrait/base64", json=payload, timeout=90)

# 3. MRZ fields
with open("passport.jpg", "rb") as f:
    fields = requests.post(f"{BASE}/extract-fields", files={"file": f}, timeout=90).json()
if fields.get("valid"):
    print(fields["surname"], fields["given_names"], fields["date_of_birth"], fields["expiry_date"])
```

### JavaScript (browser)

```javascript
const form = new FormData();
form.append("file", fileInput.files[0]);
const res = await fetch("https://id-portrait-extractor.onrender.com/extract-portrait", { method: "POST", body: form });
const { portrait_base64 } = await res.json();
img.src = `data:image/jpeg;base64,${portrait_base64}`;
```

### PowerShell

```powershell
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("sample_images\passport_spain_specimen.jpg"))
$body = @{ image_base64 = $b64 } | ConvertTo-Json
$r = Invoke-RestMethod -Method Post -Uri "https://id-portrait-extractor.onrender.com/extract-portrait/base64" -ContentType "application/json" -Body $body
[IO.File]::WriteAllBytes("portrait.jpg", [Convert]::FromBase64String($r.portrait_base64))
```

### Postman

Import `https://id-portrait-extractor.onrender.com/openapi.json` (*Import → Link*) to get a ready-made collection of all endpoints.

---

## Tips for best results

* Photograph the document flat, in good light, filling most of the frame.
* Small tilts and 90°/180° rotations are corrected automatically; strong perspective (steep camera angle) reduces accuracy.
* For `/extract-fields`, the MRZ must be in the image — for ID cards that is usually the **back** side.
* Use `valid` and `checks` to decide whether MRZ fields can be trusted.
