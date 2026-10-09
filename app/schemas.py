from pydantic import BaseModel, Field


class ExtractPortraitRequest(BaseModel):
    """Request body for the base64 JSON variant of /extract-portrait."""

    image_base64: str = Field(..., description="Base64-encoded source image (ID card / passport). A data-URL prefix is accepted.")
    margin: float = Field(
        0.3,
        ge=0.0,
        le=2.0,
        description="Horizontal padding on each side of the face, as a fraction of face width. The crop is always 3:4.",
    )


class ExtractPortraitResponse(BaseModel):
    portrait_base64: str = Field(..., description="Base64-encoded extracted portrait image (JPEG).")
    format: str = Field("jpeg", description="Image format of the returned portrait.")
    face_count: int = Field(..., description="Number of faces detected in the source image (ghost images included).")
    width: int = Field(..., description="Width of the returned portrait in pixels.")
    height: int = Field(..., description="Height of the returned portrait in pixels.")
    confidence: float = Field(..., description="Face detector confidence for the chosen face (0-1).")
    rotation_degrees: float = Field(
        ..., description="Counter-clockwise rotation applied to the source to make the portrait upright."
    )


class ExtractFieldsRequest(BaseModel):
    """Request body for the base64 JSON variant of /extract-fields."""

    image_base64: str = Field(..., description="Base64-encoded passport / ID card image showing the MRZ.")


class MRZChecks(BaseModel):
    document_number: bool
    date_of_birth: bool
    expiry_date: bool
    composite: bool
    optional_data: bool | None = Field(None, description="Only present for TD3 passports with a personal number check digit.")


class ExtractFieldsResponse(BaseModel):
    valid: bool = Field(..., description="True when every MRZ check digit passed - the fields can be trusted.")
    mrz_format: str = Field(..., description="TD1 (ID card, 3x30), TD2 (2x36) or TD3 (passport, 2x44).")
    document_type: str = Field(..., description="Document code, e.g. P (passport), I / ID (identity card).")
    issuing_country: str = Field(..., description="ICAO 3-letter issuing state code.")
    surname: str
    given_names: str
    document_number: str
    nationality: str = Field(..., description="ICAO 3-letter nationality code.")
    date_of_birth: str | None = Field(None, description="ISO date YYYY-MM-DD, null if unreadable.")
    sex: str = Field(..., description="M, F or X (unspecified).")
    expiry_date: str | None = Field(None, description="ISO date YYYY-MM-DD, null if unreadable.")
    optional_data: str = Field(..., description="Personal number / optional data, if any.")
    checks: MRZChecks
    raw_mrz: list[str] = Field(..., description="The MRZ lines as read by OCR.")


class HealthResponse(BaseModel):
    status: str
    ocr_available: bool


class ErrorResponse(BaseModel):
    detail: str
