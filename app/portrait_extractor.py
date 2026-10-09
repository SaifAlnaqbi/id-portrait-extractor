"""Face detection and portrait cropping logic.

Pipeline:
    decode bytes -> detect faces (MediaPipe) -> pick the largest face
    -> if none found, retry with the image rotated 90/180/270 degrees
    -> level the face using the eye keypoints (deskew)
    -> re-detect on the levelled image for a tight box
    -> frame a 3:4 ID-photo style crop around the face -> encode JPEG
"""

import math
import threading
from dataclasses import dataclass

import cv2
import mediapipe as mp
import numpy as np

_mp_face_detection = mp.solutions.face_detection

# Portrait framing: crop is 3:4 (w:h) and the face centre sits at this fraction
# of the crop height, leaving room for hair above and shoulders below.
PORTRAIT_ASPECT = 3 / 4
FACE_CENTER_FROM_TOP = 0.45
# Tilts smaller than this are left alone; larger ones are corrected.
MIN_DESKEW_DEGREES = 1.5
JPEG_QUALITY = 95

# The detector is expensive to build, so build it once. MediaPipe graphs are not
# thread-safe and FastAPI runs sync work in a thread pool, hence the lock.
_detector = None
_detector_lock = threading.Lock()


class NoFaceDetectedError(Exception):
    """Raised when no face could be found in the source image."""


class InvalidImageError(Exception):
    """Raised when the input bytes could not be decoded as an image."""


@dataclass
class DetectedFace:
    x: int
    y: int
    w: int
    h: int
    confidence: float
    right_eye: tuple[float, float] | None = None  # subject's right eye (image left), in pixels
    left_eye: tuple[float, float] | None = None

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2


@dataclass
class PortraitResult:
    jpeg_bytes: bytes
    face_count: int
    width: int
    height: int
    confidence: float
    rotation_degrees: float


def decode_image(image_bytes: bytes) -> np.ndarray:
    array = np.frombuffer(image_bytes, dtype=np.uint8)
    # IMREAD_COLOR honours EXIF orientation, so phone photos come out upright.
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        raise InvalidImageError("Could not decode image bytes; unsupported or corrupt format.")
    return image


def _get_detector():
    global _detector
    if _detector is None:
        _detector = _mp_face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.5)
    return _detector


def detect_faces(image: np.ndarray, min_confidence: float = 0.5) -> list[DetectedFace]:
    height, width = image.shape[:2]
    rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    with _detector_lock:
        result = _get_detector().process(rgb_image)

    faces: list[DetectedFace] = []
    for detection in result.detections or []:
        confidence = detection.score[0] if detection.score else 0.0
        if confidence < min_confidence:
            continue
        box = detection.location_data.relative_bounding_box
        keypoints = detection.location_data.relative_keypoints
        right_eye = left_eye = None
        if len(keypoints) >= 2:
            right_eye = (keypoints[0].x * width, keypoints[0].y * height)
            left_eye = (keypoints[1].x * width, keypoints[1].y * height)
        faces.append(
            DetectedFace(
                x=int(box.xmin * width),
                y=int(box.ymin * height),
                w=int(box.width * width),
                h=int(box.height * height),
                confidence=confidence,
                right_eye=right_eye,
                left_eye=left_eye,
            )
        )
    return faces


def _largest(faces: list[DetectedFace]) -> DetectedFace:
    # The main portrait is the biggest face; ghost images / holograms are smaller.
    return max(faces, key=lambda f: f.w * f.h)


def _eye_tilt_degrees(face: DetectedFace) -> float:
    if face.right_eye is None or face.left_eye is None:
        return 0.0
    dx = face.left_eye[0] - face.right_eye[0]
    dy = face.left_eye[1] - face.right_eye[1]
    return math.degrees(math.atan2(dy, dx))


def _rotate_about(image: np.ndarray, center: tuple[float, float], degrees: float) -> np.ndarray:
    matrix = cv2.getRotationMatrix2D(center, degrees, 1.0)
    h, w = image.shape[:2]
    return cv2.warpAffine(image, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


_COARSE_ROTATIONS = (
    (0, None),
    (90, cv2.ROTATE_90_COUNTERCLOCKWISE),
    (270, cv2.ROTATE_90_CLOCKWISE),
    (180, cv2.ROTATE_180),
)


def _find_face_any_orientation(image: np.ndarray, min_confidence: float):
    """Try the image as-is, then rotated by 90/270/180 degrees (sideways or upside-down scans)."""
    for degrees, rotate_code in _COARSE_ROTATIONS:
        candidate = image if rotate_code is None else cv2.rotate(image, rotate_code)
        faces = detect_faces(candidate, min_confidence=min_confidence)
        if faces:
            return candidate, faces, degrees
    return image, [], 0


def portrait_box(face: DetectedFace, margin: float, image_w: int, image_h: int) -> tuple[int, int, int, int]:
    """Return (x1, y1, x2, y2) of a 3:4 ID-photo framed crop around the face.

    `margin` is the horizontal padding on each side as a fraction of face width.
    The box is shifted (not shrunk) to stay inside the image where possible.
    """
    crop_w = face.w * (1 + 2 * margin)
    crop_h = crop_w / PORTRAIT_ASPECT
    cx, cy = face.center
    x1 = cx - crop_w / 2
    y1 = cy - FACE_CENTER_FROM_TOP * crop_h

    x1 = min(max(x1, 0), max(image_w - crop_w, 0))
    y1 = min(max(y1, 0), max(image_h - crop_h, 0))
    x2 = min(x1 + crop_w, image_w)
    y2 = min(y1 + crop_h, image_h)
    return int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))


def extract_portrait(image_bytes: bytes, margin: float = 0.3, min_confidence: float = 0.5) -> PortraitResult:
    """Detect the most prominent face in the source image and return an upright, framed crop.

    Raises InvalidImageError or NoFaceDetectedError on failure.
    """
    image = decode_image(image_bytes)

    image, faces, coarse_rotation = _find_face_any_orientation(image, min_confidence)
    if not faces:
        raise NoFaceDetectedError("No face detected in the provided image.")
    face_count = len(faces)
    face = _largest(faces)

    tilt = _eye_tilt_degrees(face)
    if abs(tilt) >= MIN_DESKEW_DEGREES:
        image = _rotate_about(image, face.center, tilt)
        relevelled = detect_faces(image, min_confidence=min_confidence)
        if relevelled:
            # Pick the re-detected face closest to where the original was.
            fx, fy = face.center
            face = min(relevelled, key=lambda f: (f.center[0] - fx) ** 2 + (f.center[1] - fy) ** 2)
    else:
        tilt = 0.0

    height, width = image.shape[:2]
    x1, y1, x2, y2 = portrait_box(face, margin, width, height)
    crop = image[y1:y2, x1:x2]

    success, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not success:
        raise InvalidImageError("Failed to encode extracted portrait.")

    crop_h, crop_w = crop.shape[:2]
    # Total counter-clockwise rotation applied to the source to make the face upright.
    rotation = (coarse_rotation + tilt) % 360
    return PortraitResult(
        jpeg_bytes=encoded.tobytes(),
        face_count=face_count,
        width=crop_w,
        height=crop_h,
        confidence=round(float(face.confidence), 4),
        rotation_degrees=round(rotation, 2),
    )
