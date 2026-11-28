"""QR code decoding and multimodal receipt photo analysis for offline invoice claiming."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
import importlib
import io
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

WECHAT_ONLY_HINTS = (
    "weixin://",
    "wx.tenpay.com",
    "mp.weixin.qq.com/s",
    "open.weixin.qq.com",
)


class ReceiptVisionSummary(BaseModel):
    """Structured receipt metadata extracted by VLM from an offline consumption photo."""

    is_invoice_qr_receipt: bool = Field(
        default=False,
        description="True if photo shows a merchant receipt/QR code used to apply for an invoice.",
    )
    code_type: Literal["standard_qr", "wechat_miniprogram_code", "alipay_code", "no_code"] = Field(
        default="no_code",
        description=(
            "Visual type of scan code on the slip: "
            "'wechat_miniprogram_code' for circular sunflower-shaped WeChat Mini Program codes (微信小程序码/太阳码), "
            "'alipay_code' for Alipay-only codes, "
            "'standard_qr' for normal square QR codes, or 'no_code' if none visible."
        ),
    )
    merchant_name: str = Field(default="", description="Merchant / store name if visible.")
    amount: Decimal | None = Field(default=None, description="Consumption total amount if visible.")
    currency: str = Field(default="CNY", description="ISO 4217 currency code.")
    receipt_date: date | None = Field(default=None, description="Consumption date YYYY-MM-DD if visible.")
    summary: str = Field(default="", description="Short one-line summary of the receipt.")


@dataclass
class DecodedReceipt:
    qr_urls: list[str] = field(default_factory=list)
    code_type: str = "no_code"
    merchant_name: str = ""
    amount: Decimal | None = None
    currency: str = "CNY"
    receipt_date: date | None = None
    summary: str = ""
    is_wechat_only: bool = False

    @property
    def primary_qr_url(self) -> str:
        return self.qr_urls[0] if self.qr_urls else ""


def is_wechat_only_url(url: str) -> bool:
    lowered = (url or "").strip().lower()
    return any(hint in lowered for hint in WECHAT_ONLY_HINTS)


def decode_qr_urls_from_image(image_bytes: bytes) -> list[str]:
    """Extract URL QR codes from a receipt photo using Pillow + pyzbar with contrast/scale fallbacks."""
    if not image_bytes:
        return []
    try:
        pil_image = importlib.import_module("PIL.Image")
        pil_ops = importlib.import_module("PIL.ImageOps")
        pil_filter = importlib.import_module("PIL.ImageFilter")
        pyzbar_mod = importlib.import_module("pyzbar.pyzbar")
    except ImportError:
        logger.warning("PIL or pyzbar unavailable — QR photo decoding disabled")
        return []

    try:
        img = pil_image.open(io.BytesIO(image_bytes))
    except Exception as exc:
        logger.warning("Failed to open receipt image for QR decoding: %s", exc)
        return []

    urls: list[str] = []
    seen: set[str] = set()
    candidates = [img]
    try:
        gray = img.convert("L")
        min3 = gray.filter(pil_filter.MinFilter(3))
        candidates.extend([gray, pil_ops.autocontrast(gray), min3])
        # Thermal receipt printer dead-pixel vertical line repair + multi-scale/threshold passes
        if gray.width >= 320 and gray.height >= 320:
            half = gray.resize((gray.width // 2, gray.height // 2), pil_image.Resampling.LANCZOS)
            candidates.append(half.filter(pil_filter.MinFilter(3)))
        for thresh in (165, 130):
            candidates.append(min3.point(lambda p, t=thresh: 255 if p > t else 0, mode="1"))
    except Exception:  # pragma: no cover - defensive
        pass

    for candidate in candidates:
        for obj in pyzbar_mod.decode(candidate):
            raw = (obj.data or b"").decode("utf-8", errors="ignore").strip()
            if not raw:
                continue
            if raw.lower().startswith(("http://", "https://", "weixin://")) and raw not in seen:
                seen.add(raw)
                urls.append(raw)
        if urls:
            break

    return urls


async def analyze_receipt_photo(
    db: AsyncSession,
    ai: Any,
    image_bytes: bytes,
) -> DecodedReceipt:
    """Decode QR URL(s) and enrich merchant/amount/code_type metadata via VLM."""
    qr_urls = decode_qr_urls_from_image(image_bytes)
    result = DecodedReceipt(
        qr_urls=qr_urls,
        code_type="standard_qr" if qr_urls else "no_code",
        is_wechat_only=bool(qr_urls and is_wechat_only_url(qr_urls[0])),
    )

    if ai is not None and image_bytes and hasattr(ai, "_create_structured_with_fallback"):
        try:
            runtime_settings = await ai._resolve_runtime_settings(db)
            img_b64 = base64.b64encode(image_bytes).decode("ascii")
            vision_model = str(runtime_settings.get("llm_vision_model", "")).strip()
            vision: ReceiptVisionSummary = await ai._create_structured_with_fallback(
                runtime_settings=runtime_settings,
                response_model=ReceiptVisionSummary,
                preferred_first_model=vision_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Inspect the photo of a Chinese/international receipt or invoice slip. "
                            "Identify code_type carefully: circular sunflower-like radial dot codes are "
                            "'wechat_miniprogram_code' (微信小程序码/太阳码), normal square 3-corner-box codes are "
                            "'standard_qr', Alipay-branded codes are 'alipay_code', otherwise 'no_code'. "
                            "Also extract merchant_name, amount, currency, receipt_date, and a concise summary."
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Extract receipt metadata and code_type as JSON."},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                        ],
                    },
                ],
            )
            if not qr_urls:
                result.code_type = vision.code_type
                if vision.code_type == "wechat_miniprogram_code":
                    result.is_wechat_only = True
            result.merchant_name = (vision.merchant_name or "").strip()
            result.amount = vision.amount
            result.currency = (vision.currency or "CNY").strip().upper()[:8]
            result.receipt_date = vision.receipt_date
            result.summary = (vision.summary or "").strip()
        except Exception as exc:
            logger.warning("VLM receipt metadata analysis failed (continuing with QR result): %s", exc)

    return result
