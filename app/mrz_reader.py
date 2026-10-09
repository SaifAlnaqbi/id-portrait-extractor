"""Locate the MRZ in a document image and read it with Tesseract OCR.

Localisation (classic morphology approach):
    grayscale -> blackhat (dark text on light background) -> horizontal Scharr
    gradient -> closing with a wide kernel so characters merge into lines ->
    Otsu threshold -> closing with a square kernel so the 2-3 lines merge into
    one block -> contours filtered by aspect ratio/size.

Each candidate block is deskewed, upscaled and passed to Tesseract using a
model trained on the MRZ font (OCR-B, app/tessdata/mrz.traineddata from
DoubangoTelecom/tesseractMRZ, BSD-3) restricted to the MRZ alphabet
(A-Z, 0-9, '<'). Tesseract's generic English model misreads the '<' filler
as K/X/S and its errors vary between Tesseract versions; the MRZ model does not. Lines that look like MRZ lines are parsed
and validated with check digits; the candidate with the most passing checks wins.
If no candidate region works, the lower half and the whole image are tried.
"""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pytesseract

from app.mrz import MRZParseError, MRZResult, normalize_line, parse_mrz
from app.portrait_extractor import decode_image

WORK_WIDTH = 1200
OCR_WIDTH = 1600
OCR_LANG = "mrz"
TESSDATA_DIR = Path(__file__).resolve().parent / "tessdata"
_OCR_CONFIG = (
    "--oem 1 --psm 6 "
    "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789< "
    "-c load_system_dawg=0 -c load_freq_dawg=0"
)
_WINDOWS_DEFAULT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


class NoMRZFoundError(Exception):
    """Raised when no machine readable zone could be located or read."""


class OCRUnavailableError(Exception):
    """Raised when the Tesseract binary is not installed."""


def _configure_tesseract() -> None:
    cmd = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
    if not cmd and os.path.exists(_WINDOWS_DEFAULT):
        cmd = _WINDOWS_DEFAULT
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    # Point Tesseract at the bundled MRZ model. Done via the environment rather
    # than --tessdata-dir because pytesseract splits the config string on spaces,
    # which breaks paths like "C:/Users/.../Python Proj/...".
    os.environ["TESSDATA_PREFIX"] = str(TESSDATA_DIR)


_configure_tesseract()


def tesseract_available() -> bool:
    try:
        pytesseract.get_tesseract_version()
        return True
    except (pytesseract.TesseractNotFoundError, OSError):
        return False


@dataclass
class Region:
    box: tuple  # cv2.minAreaRect output, in WORK_WIDTH coordinates
    area: float


def _resize_to_width(image: np.ndarray, width: int) -> tuple[np.ndarray, float]:
    scale = width / image.shape[1]
    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=interpolation), scale


def find_mrz_regions(gray: np.ndarray, max_regions: int = 4) -> list[Region]:
    """Return candidate MRZ regions (largest first) in a WORK_WIDTH-wide grayscale image."""
    h, w = gray.shape
    rect_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 45, 9), max(w // 170, 3)))
    sq_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 35, 15), max(w // 35, 15)))

    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    blackhat = cv2.morphologyEx(blurred, cv2.MORPH_BLACKHAT, rect_kernel)
    grad = np.absolute(cv2.Sobel(blackhat, cv2.CV_32F, 1, 0, ksize=-1))
    grad = cv2.normalize(grad, None, 0, 255, cv2.NORM_MINMAX).astype("uint8")
    grad = cv2.morphologyEx(grad, cv2.MORPH_CLOSE, rect_kernel)
    _, thresh = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, sq_kernel)
    thresh = cv2.erode(thresh, None, iterations=2)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    regions = []
    for contour in contours:
        rect = cv2.minAreaRect(contour)
        (_, _), (rw, rh), _ = rect
        long_side, short_side = max(rw, rh), max(min(rw, rh), 1)
        if long_side / short_side >= 4 and long_side >= 0.25 * w:
            regions.append(Region(box=rect, area=rw * rh))
    regions.sort(key=lambda r: r.area, reverse=True)
    return regions[:max_regions]


def _crop_region(gray: np.ndarray, rect, pad: float = 0.08) -> np.ndarray:
    """Cut a rotated rectangle out of the image so the text runs horizontally."""
    (cx, cy), (rw, rh), angle = rect
    if rw < rh:  # make width the long side
        rw, rh = rh, rw
        angle += 90
    if angle > 45:
        angle -= 180
    rw, rh = rw * (1 + pad), rh * (1 + 4 * pad)
    matrix = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
    rotated = cv2.warpAffine(gray, matrix, gray.shape[::-1], flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return cv2.getRectSubPix(rotated, (int(rw), int(rh)), (cx, cy))


def _prepare_for_ocr(gray_crop: np.ndarray) -> np.ndarray:
    crop, _ = _resize_to_width(gray_crop, OCR_WIDTH)
    crop = cv2.GaussianBlur(crop, (3, 3), 0)
    _, binary = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    return cv2.copyMakeBorder(binary, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)


def _mrz_line_candidates(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = normalize_line(raw)
        if len(line) >= 26 and (line.count("<") >= 1 or len(line) >= 28):
            lines.append(line)
    return lines


def _parse_best(lines: list[str]) -> MRZResult | None:
    """Try every run of 2 or 3 consecutive MRZ-like lines and keep the best-validating parse."""
    best = None
    for size in (3, 2):
        for start in range(len(lines) - size + 1):
            group = lines[start : start + size]
            if size == 3 and not all(abs(len(l) - 30) <= 3 for l in group):
                continue
            try:
                result = parse_mrz(group)
            except (MRZParseError, IndexError):
                continue
            if best is None or result.check_score > best.check_score:
                best = result
    return best


def _ocr(image: np.ndarray) -> str:
    return pytesseract.image_to_string(image, lang=OCR_LANG, config=_OCR_CONFIG)


def read_mrz_from_image(image: np.ndarray) -> MRZResult:
    if not tesseract_available():
        raise OCRUnavailableError("Tesseract OCR is not installed on the server.")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    work, _ = _resize_to_width(gray, WORK_WIDTH)

    candidates = [_crop_region(work, region.box) for region in find_mrz_regions(work)]
    # Fallbacks for when localisation misses: the bottom half (where the MRZ usually is) and the full page.
    candidates.append(work[work.shape[0] // 2 :, :])
    candidates.append(work)

    best = None
    for crop in candidates:
        if crop.size == 0:
            continue
        result = _parse_best(_mrz_line_candidates(_ocr(_prepare_for_ocr(crop))))
        if result and (best is None or result.check_score > best.check_score):
            best = result
            if best.valid:
                break

    if best is None:
        raise NoMRZFoundError("No machine readable zone (MRZ) could be found in the image.")
    return best


def read_mrz(image_bytes: bytes) -> MRZResult:
    return read_mrz_from_image(decode_image(image_bytes))
