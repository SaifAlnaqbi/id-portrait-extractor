from pathlib import Path

import cv2
import numpy as np
import pytest

from app.portrait_extractor import DetectedFace, extract_portrait, portrait_box

SAMPLES = Path(__file__).resolve().parent.parent / "sample_images"
SAMPLE_FILES = sorted(SAMPLES.glob("*.jpg"))


def _decode(jpeg_bytes: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)


@pytest.mark.parametrize("path", SAMPLE_FILES, ids=lambda p: p.name)
def test_portrait_extracted_from_every_sample(path):
    result = extract_portrait(path.read_bytes())
    image = _decode(result.jpeg_bytes)
    assert image is not None
    assert image.shape[:2] == (result.height, result.width)
    # Framed as a portrait (3:4), allowing for rounding / clamping at image edges.
    assert 1.2 < result.height / result.width < 1.45


def test_tilted_card_is_levelled():
    result = extract_portrait((SAMPLES / "alb_id_on_table.jpg").read_bytes())
    assert result.rotation_degrees != 0


def test_sideways_image_is_recovered():
    image = cv2.imread(str(SAMPLES / "passport_spain_specimen.jpg"))
    sideways = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    ok, encoded = cv2.imencode(".jpg", sideways)
    result = extract_portrait(encoded.tobytes())
    assert 80 <= result.rotation_degrees <= 100
    assert result.height > result.width


def test_portrait_box_is_3_by_4_and_clamped():
    face = DetectedFace(x=100, y=100, w=100, h=100, confidence=1.0)
    x1, y1, x2, y2 = portrait_box(face, margin=0.3, image_w=1000, image_h=1000)
    assert (x2 - x1, y2 - y1) == (160, 213)

    corner_face = DetectedFace(x=0, y=0, w=100, h=100, confidence=1.0)
    x1, y1, x2, y2 = portrait_box(corner_face, margin=0.3, image_w=1000, image_h=1000)
    assert x1 == 0 and y1 == 0
    assert (x2 - x1, y2 - y1) == (160, 213)  # shifted, not shrunk
