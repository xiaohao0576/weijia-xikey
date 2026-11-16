"""Validate original visit images before sending them to Odoo."""
import base64
import binascii
import io
import re
import warnings
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

MAX_PHOTOS = 3
MAX_PHOTO_BYTES = 20 * 1024 * 1024
MAX_BASE64_LENGTH = 4 * ((MAX_PHOTO_BYTES + 2) // 3)
FORMATS = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"), "WEBP": ("webp", "image/webp")}
TIMEMARK_SOFTWARE = re.compile(r"timemark", re.IGNORECASE)


class PhotoValidationError(ValueError):
    pass


@dataclass(frozen=True)
class VisitPhoto:
    encoded: str
    extension: str
    mimetype: str


def validate_visit_photos(photos) -> list[VisitPhoto]:
    if not isinstance(photos, list) or not 1 <= len(photos) <= MAX_PHOTOS:
        raise PhotoValidationError("Select 1 to 3 TimeMark photos.")
    result = []
    for index, encoded in enumerate(photos, start=1):
        prefix = f"Photo {index}: "
        if not isinstance(encoded, str) or not encoded or len(encoded) > MAX_BASE64_LENGTH:
            raise PhotoValidationError(prefix + "invalid image or larger than 20 MB.")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise PhotoValidationError(prefix + "invalid image data.") from None
        if not content or len(content) > MAX_PHOTO_BYTES:
            raise PhotoValidationError(prefix + "invalid image or larger than 20 MB.")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                # verify() must run on a freshly opened image; read metadata from a second open.
                with Image.open(io.BytesIO(content)) as image:
                    image.verify()
                with Image.open(io.BytesIO(content)) as image:
                    image_format = image.format
                    software = image.getexif().get(305, "")
                    image.load()
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError, RuntimeError,
                Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise PhotoValidationError(prefix + "unreadable or unsupported image.") from None
        if image_format not in FORMATS:
            raise PhotoValidationError(prefix + "use a JPEG, PNG, or WebP image.")
        if not isinstance(software, str) or not TIMEMARK_SOFTWARE.search(software):
            raise PhotoValidationError(prefix + "សូមថតរូបដោយប្រើកម្មវិធី TimeMark រួចបង្ហោះរូបទាំងនោះ។")
        extension, mimetype = FORMATS[image_format]
        result.append(VisitPhoto(encoded, extension, mimetype))
    return result
