"""QR code generation for dashboard deep links."""

from __future__ import annotations

import io

import qrcode
from qrcode.constants import ERROR_CORRECT_M

from insight_engine.core.errors import RenderError

MAX_URL_LENGTH = 2048


def build_qr_png(url: str, *, size_px: int = 720, border: int = 3) -> bytes:
    """Render ``url`` as a PNG.

    Medium error correction is the right trade here: high correction shrinks the
    data capacity and inflates module count for no benefit when the code is
    printed on a slide rather than a coffee cup.
    """
    if not url:
        raise RenderError("Cannot build a QR code for an empty URL")
    if len(url) > MAX_URL_LENGTH:
        raise RenderError("URL is too long to encode in a QR code", context={"length": len(url)})

    code = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=10, border=border)
    code.add_data(url)
    code.make(fit=True)

    image = code.make_image(fill_color="#10367D", back_color="white").convert("RGB")
    if image.size[0] != size_px:
        from PIL import Image

        image = image.resize((size_px, size_px), Image.Resampling.NEAREST)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
