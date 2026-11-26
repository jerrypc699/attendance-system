import base64
import io
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Optional
from PIL import Image, ImageOps
from app.config import settings

SAFE_PATH_RE = re.compile(r"^[0-9]{4}-[0-9]{2}/[a-f0-9\-]+\.jpg$")


def get_upload_root() -> str:
    root = os.path.abspath(settings.UPLOAD_DIR)
    os.makedirs(root, exist_ok=True)
    return root


def save_snapshot_from_base64(snapshot_data: Optional[str], prefix: str = "punch") -> Optional[str]:
    """
    Decodes a base64 image or data URL, applies EXIF orientation so phone camera photos
    are always upright, resizes to max 480x480 JPEG (quality=80), saves to disk under
    uploads/snapshots/YYYY-MM/<id>.jpg, and returns the protected API path.
    """
    if not snapshot_data:
        return None
    if snapshot_data.startswith("/api/v1/admin/snapshots/"):
        return snapshot_data
    if snapshot_data.startswith("http://") or snapshot_data.startswith("https://") or "sample_snapshot" in snapshot_data:
        return None

    try:
        raw_b64 = snapshot_data.split(",", 1)[1] if "," in snapshot_data else snapshot_data
        img_bytes = base64.b64decode(raw_b64)
        img = Image.open(io.BytesIO(img_bytes))
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((480, 480))

        month_dir = datetime.now(timezone.utc).strftime("%Y-%m")
        root = get_upload_root()
        target_dir = os.path.join(root, month_dir)
        os.makedirs(target_dir, exist_ok=True)

        filename = f"{uuid.uuid4().hex}.jpg"
        full_path = os.path.join(target_dir, filename)
        img.save(full_path, format="JPEG", quality=80, optimize=True)
        return f"/api/v1/admin/snapshots/{month_dir}/{filename}"
    except Exception:
        return None


def resolve_snapshot_file(rel_path: str) -> Optional[str]:
    """Safely resolves a relative snapshot path preventing directory traversal."""
    clean = rel_path.strip().lstrip("/").replace("\\", "/")
    if not SAFE_PATH_RE.match(clean):
        return None
    root = get_upload_root()
    full_path = os.path.abspath(os.path.join(root, clean))
    if not full_path.startswith(root):
        return None
    if not os.path.isfile(full_path):
        return None
    return full_path

