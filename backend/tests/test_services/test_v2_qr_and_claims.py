from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import io
from types import SimpleNamespace

import pytest

from app.models import InvoiceClaimTask, InvoiceProfile
import app.services.qr_receipt_decoder as qr_mod
from app.services.claim_form_agent import (
    BrowserClaimOutcome,
    FormFieldInfo,
    PageDiagnosis,
    _enrich_diagnosis_with_vlm,
    append_claim_log,
    build_page_excerpt,
    diagnose_non_form_page,
    evaluate_post_submit_text,
    execute_claim_task,
    extract_issued_invoice_no,
    format_copyable_profile_card,
    is_wechat_auth_blocked,
    match_submitted_claims,
    plan_deterministic_fills,
)
from app.services.qr_receipt_decoder import (
    ReceiptVisionSummary,
    analyze_receipt_photo,
    decode_qr_urls_from_image,
    is_wechat_only_url,
)


def _tiny_png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (20, 20), color=(255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def test_qr_decoder_helpers_and_edge_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    assert is_wechat_only_url("weixin://wxpay/bizpayurl?pr=abc") is True
    assert is_wechat_only_url("https://nnfp.jss.com.cn/abc") is False
    assert decode_qr_urls_from_image(b"") == []
    assert decode_qr_urls_from_image(b"not-an-image") == []

    # Simulate pyzbar returning non-URL + duplicate URLs + valid URL
    fake_pyzbar = SimpleNamespace(
        decode=lambda _img: [
            SimpleNamespace(data=b""),
            SimpleNamespace(data=b"plain-text-not-url"),
            SimpleNamespace(data=b"https://nnfp.jss.com.cn/inv/1"),
            SimpleNamespace(data=b"https://nnfp.jss.com.cn/inv/1"),
        ]
    )
    orig_import = qr_mod.importlib.import_module

    def fake_import(name: str):
        if name == "pyzbar.pyzbar":
            return fake_pyzbar
        return orig_import(name)

    monkeypatch.setattr(qr_mod.importlib, "import_module", fake_import)
    assert decode_qr_urls_from_image(_tiny_png_bytes()) == ["https://nnfp.jss.com.cn/inv/1"]

    # Simulate grayscale retry when RGB yields empty and grayscale yields URL
    calls = 0

    def grayscale_pyzbar(_img):
        nonlocal calls
        calls += 1
        return [] if calls == 1 else [SimpleNamespace(data=b"weixin://inv/2")]

    fake_pyzbar.decode = grayscale_pyzbar
    assert decode_qr_urls_from_image(_tiny_png_bytes()) == ["weixin://inv/2"]

    # Empty decode on both RGB and grayscale
    fake_pyzbar.decode = lambda _img: []
    assert decode_qr_urls_from_image(_tiny_png_bytes()) == []

    # ImportError graceful degradation
    def missing_import(name: str):
        raise ImportError(name)

    monkeypatch.setattr(qr_mod.importlib, "import_module", missing_import)
    assert decode_qr_urls_from_image(_tiny_png_bytes()) == []


@pytest.mark.asyncio
async def test_analyze_receipt_photo_with_vlm_and_fallback(db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qr_mod, "decode_qr_urls_from_image", lambda _b: ["https://nnfp.jss.com.cn/inv/9"])

    class OkVisionAI:
        async def _resolve_runtime_settings(self, _db):
            return {"llm_vision_model": "auto/gemini-4-neon"}

        async def _create_structured_with_fallback(self, **kwargs):
            del kwargs
            return ReceiptVisionSummary(
                is_invoice_qr_receipt=True,
                merchant_name="星巴克咖啡",
                amount=Decimal("38.00"),
                currency="cny",
                receipt_date=date(2026, 11, 27),
                summary="咖啡消费小票",
            )

    decoded = await analyze_receipt_photo(db, OkVisionAI(), _tiny_png_bytes())
    assert decoded.primary_qr_url == "https://nnfp.jss.com.cn/inv/9"
    assert decoded.merchant_name == "星巴克咖啡"
    assert decoded.amount == Decimal("38.00")
    assert decoded.currency == "CNY"

    class FailVisionAI(OkVisionAI):
        async def _create_structured_with_fallback(self, **kwargs):
            del kwargs
            raise RuntimeError("vision down")

    decoded2 = await analyze_receipt_photo(db, FailVisionAI(), _tiny_png_bytes())
    assert decoded2.primary_qr_url == "https://nnfp.jss.com.cn/inv/9"
    assert decoded2.merchant_name == ""

    monkeypatch.setattr(qr_mod, "decode_qr_urls_from_image", lambda _b: [])
    decoded3 = await analyze_receipt_photo(db, None, b"")
    assert decoded3.primary_qr_url == ""

    # VLM identifies WeChat Mini Program sunflower code when pyzbar returns []
    class WeChatCodeVisionAI(OkVisionAI):
        async def _create_structured_with_fallback(self, **kwargs):
            del kwargs
            return ReceiptVisionSummary(
                is_invoice_qr_receipt=True,
                code_type="wechat_miniprogram_code",
                merchant_name="某餐厅",
                amount=Decimal("188.00"),
            )

    decoded_wx = await analyze_receipt_photo(db, WeChatCodeVisionAI(), _tiny_png_bytes())
    assert decoded_wx.code_type == "wechat_miniprogram_code"
    assert decoded_wx.is_wechat_only is True

    # VLM identifies Alipay code (is_wechat_only remains False)
    class AlipayCodeVisionAI(OkVisionAI):
        async def _create_structured_with_fallback(self, **kwargs):
            del kwargs
            return ReceiptVisionSummary(is_invoice_qr_receipt=True, code_type="alipay_code")

    decoded_ali = await analyze_receipt_photo(db, AlipayCodeVisionAI(), _tiny_png_bytes())
    assert decoded_ali.code_type == "alipay_code"
    assert decoded_ali.is_wechat_only is False


def test_deterministic_form_fill_and_post_submit_evaluation() -> None:
    profile = InvoiceProfile(
        id=1,
        user_id=1,
        name="公司专票",
        title="上海测试科技有限公司",
        tax_id="91310000123456789X",
        email="inv@company.com",
        phone="13800138000",
        address="上海市浦东新区XX路1号",
        bank_name="招商银行上海分行",
        bank_account="6225880011223344",
    )
    card = format_copyable_profile_card(profile)
    assert "91310000123456789X" in card
    assert "招商银行上海分行" in card

    minimal_profile = InvoiceProfile(id=2, user_id=1, name="个人", title="张三", tax_id="", email="", phone="", address="", bank_name="", bank_account="")
    assert "抬头名称：张三" == format_copyable_profile_card(minimal_profile)

    fields = [
        FormFieldInfo(selector="#h", field_type="hidden"),
        FormFieldInfo(selector="#title", label="发票抬头"),
        FormFieldInfo(selector="#tax", placeholder="纳税人识别号"),
        FormFieldInfo(selector="#email", name="email"),
        FormFieldInfo(selector="#phone", placeholder="手机号"),
        FormFieldInfo(selector="#addr", label="地址"),
        FormFieldInfo(selector="#bank", label="开户行"),
        FormFieldInfo(selector="#acct", label="银行账号"),
        FormFieldInfo(selector="#unknown", label="备注其他"),
    ]
    fills, attn = plan_deterministic_fills(fields, profile)
    assert attn is None
    assert len(fills) == 7

    # Empty optional profile fields skip gracefully (including empty title)
    fills_min, attn_min = plan_deterministic_fills(fields, minimal_profile)
    assert attn_min is None
    assert fills_min == [("#title", "张三")]
    empty_all = InvoiceProfile(id=3, user_id=1, name="空", title="", tax_id="", email="", phone="", address="", bank_name="", bank_account="")
    assert plan_deterministic_fills(fields, empty_all) == ([], None)

    # CAPTCHA field escalates when no captcha_input given, fills when given
    cap_fields = [FormFieldInfo(selector="#cap", placeholder="请输入图形验证码")]
    _, attn_cap = plan_deterministic_fills(cap_fields, profile)
    assert attn_cap is not None and "验证码" in attn_cap
    fills_cap, attn_cap2 = plan_deterministic_fills(cap_fields, profile, captcha_input="ABCD")
    assert attn_cap2 is None
    assert fills_cap == [("#cap", "ABCD")]

    assert evaluate_post_submit_text("开票申请已受理，稍后发送")[0] == "submitted"
    assert evaluate_post_submit_text("验证码错误，请重试")[0] == "needs_attention"
    assert evaluate_post_submit_text("普通说明页")[0] == "needs_attention"
    assert evaluate_post_submit_text("")[0] == "needs_attention"

    # Rich diagnosis helpers
    assert extract_issued_invoice_no("") == ""
    assert extract_issued_invoice_no("发票号码：26312000006229050871") == "26312000006229050871"
    assert is_wechat_auth_blocked("http://www.bilalipay.com/fpwx/fp/goFP.php", "开票失败 获取用户信息异常") is True
    assert is_wechat_auth_blocked("http://example.com/fpwx/a", "开票失败") is True
    assert is_wechat_auth_blocked("https://invoice.meituan.com/a", "正常页面") is False
    assert build_page_excerpt("a" * 300, limit=10).endswith("…")

    k1, m1 = diagnose_non_form_page(url="http://www.bilalipay.com/fpwx/a", page_text="开票失败 获取用户信息异常", profile=profile, steps_taken=["打开链接"])
    assert k1 == "needs_attention" and "微信" in m1 and "91310000123456789X" in m1
    k2, m2 = diagnose_non_form_page(url="https://invoice.meituan.com/a", page_text="发票号码 26312000006229050871 查看发票", profile=profile)
    assert k2 == "already_issued" and "26312000006229050871" in m2
    k3, m3 = diagnose_non_form_page(url="https://x.com", page_text="超出开票期限，无法开具", profile=profile)
    assert k3 == "needs_attention" and "超出开票期限" in m3
    k4, m4 = diagnose_non_form_page(url="https://x.com", page_text="请联系门店前台", profile=profile)
    assert k4 == "needs_attention" and "请联系门店前台" in m4
    k5, m5 = diagnose_non_form_page(url="https://x.com", page_text="", profile=profile)
    assert k5 == "needs_attention" and "空白" in m5


@pytest.mark.asyncio
async def test_execute_claim_task_and_match_submitted_claims(
    db, settings, create_email_account, create_invoice, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = await create_email_account()
    profile = InvoiceProfile(
        user_id=account.user_id,
        name="公司",
        title="测试科技",
        tax_id="91310000123",
        email="inv@test.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    # Corrupted log_json edge case
    t_bad_log = InvoiceClaimTask(user_id=account.user_id, qr_url="", log_json="{bad", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    append_claim_log(t_bad_log, "ev", "d")
    t_bad_log2 = InvoiceClaimTask(user_id=account.user_id, qr_url="", log_json='{"not":"list"}', created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    append_claim_log(t_bad_log2, "ev", "d")

    # 1) Empty QR URL -> needs_attention
    t1 = InvoiceClaimTask(user_id=account.user_id, qr_url="", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    db.add(t1)
    await db.commit()
    await execute_claim_task(db, task=t1, profile=profile, settings=settings)
    assert t1.status == "needs_attention"

    # 2) WeChat-only URL -> needs_attention with copyable card
    t2 = InvoiceClaimTask(user_id=account.user_id, qr_url="weixin://wxpay/123", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    db.add(t2)
    await db.commit()
    await execute_claim_task(db, task=t2, profile=profile, settings=settings)
    assert t2.status == "needs_attention"
    assert "测试科技" in t2.attention_reason

    # 3) Browser returns needs_attention with screenshot + VLM diagnosis enrichment
    t3 = InvoiceClaimTask(user_id=account.user_id, qr_url="https://nnfp.jss.com.cn/cap", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    db.add(t3)
    await db.commit()

    async def runner_captcha(_u, _p, _c):
        return BrowserClaimOutcome(kind="needs_attention", detail="需要验证码", screenshot_bytes=b"\x89PNGfake")

    class VlmDiagAI:
        async def _resolve_runtime_settings(self, _db):
            return {"llm_vision_model": "auto/gemini-4-neon"}

        async def _create_structured_with_fallback(self, **kwargs):
            del kwargs
            return PageDiagnosis(summary="截图显示一个四位图形验证码弹窗。")

    await execute_claim_task(db, task=t3, profile=profile, settings=settings, ai=VlmDiagAI(), browser_runner=runner_captcha)
    assert t3.status == "needs_attention"
    assert "图形验证码弹窗" in t3.attention_reason
    assert t3.last_screenshot_path.endswith(f"claim_{t3.id}.png")

    # VLM diagnosis failure and empty-summary fallbacks
    class CrashDiagAI(VlmDiagAI):
        async def _create_structured_with_fallback(self, **kwargs):
            del kwargs
            raise RuntimeError("vlm down")

    class EmptyDiagAI(VlmDiagAI):
        async def _create_structured_with_fallback(self, **kwargs):
            del kwargs
            return PageDiagnosis(summary="")

    assert await _enrich_diagnosis_with_vlm(db, None, b"x", "orig") == "orig"
    assert await _enrich_diagnosis_with_vlm(db, CrashDiagAI(), b"x", "orig") == "orig"
    assert await _enrich_diagnosis_with_vlm(db, EmptyDiagAI(), b"x", "orig") == "orig"

    # 3b) Detected already-issued invoice number already archived in DB -> completed immediately
    archived_inv = await create_invoice(email_account=account, invoice_no="26312000006229050871", seller="虎丸烧肉", amount=Decimal("2628.00"))
    t3b = InvoiceClaimTask(user_id=account.user_id, qr_url="https://invoice.meituan.com/inv", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    db.add(t3b)
    await db.commit()

    async def runner_already_issued(_u, _p, _c):
        return BrowserClaimOutcome(
            kind="already_issued",
            detail="已开具",
            detected_invoice_no="26312000006229050871",
            page_text_excerpt="发票号码 26312000006229050871",
        )

    await execute_claim_task(db, task=t3b, profile=profile, settings=settings, browser_runner=runner_already_issued)
    assert t3b.status == "completed"
    assert t3b.matched_invoice_id == archived_inv.id

    # Detected invoice_no NOT yet in DB -> falls through to needs_attention
    t3c = InvoiceClaimTask(user_id=account.user_id, qr_url="https://invoice.meituan.com/inv2", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    db.add(t3c)
    await db.commit()

    async def runner_not_in_db(_u, _p, _c):
        return BrowserClaimOutcome(
            kind="already_issued",
            detail="已开具但未在库中",
            detected_invoice_no="26312000009999999999",
            page_text_excerpt="发票号码 26312000009999999999",
        )

    await execute_claim_task(db, task=t3c, profile=profile, settings=settings, browser_runner=runner_not_in_db)
    assert t3c.status == "needs_attention"

    # 4) Browser directly downloads PDF -> completed
    t4 = InvoiceClaimTask(user_id=account.user_id, qr_url="https://nnfp.jss.com.cn/pdf", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    db.add(t4)
    await db.commit()
    inv_direct = await create_invoice(email_account=account, invoice_no="DIRECT-001", seller="直接开票商户", amount=Decimal("55.00"))

    async def runner_pdf(_u, _p, _c):
        return BrowserClaimOutcome(kind="pdf_downloaded", detail="ok", pdf_bytes=b"%PDF-1.4")

    import app.services.manual_upload as mu_mod

    monkeypatch.setattr(
        mu_mod,
        "process_uploaded_invoice",
        lambda **kwargs: _coro(SimpleNamespace(invoice=inv_direct)),
    )
    await execute_claim_task(db, task=t4, profile=profile, settings=settings, ai=object(), file_mgr=object(), browser_runner=runner_pdf)
    assert t4.status == "completed"
    assert t4.matched_invoice_id == inv_direct.id

    # Direct PDF upload returned None invoice -> falls through to needs_attention
    t4b = InvoiceClaimTask(user_id=account.user_id, qr_url="https://nnfp.jss.com.cn/pdf2", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    db.add(t4b)
    await db.commit()
    monkeypatch.setattr(
        mu_mod,
        "process_uploaded_invoice",
        lambda **kwargs: _coro(SimpleNamespace(invoice=None)),
    )
    await execute_claim_task(db, task=t4b, profile=profile, settings=settings, ai=object(), file_mgr=object(), browser_runner=runner_pdf)
    assert t4b.status == "needs_attention"

    # 5) Auto-submitted -> matched later by match_submitted_claims
    assert await match_submitted_claims(db) == []
    t5 = InvoiceClaimTask(
        user_id=account.user_id,
        qr_url="https://nnfp.jss.com.cn/ok",
        merchant_name="星巴克",
        expected_amount=Decimal("38.00"),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    t6 = InvoiceClaimTask(
        user_id=account.user_id,
        qr_url="https://nnfp.jss.com.cn/ok2",
        merchant_name="滴滴出行",
        expected_amount=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    t7 = InvoiceClaimTask(
        user_id=account.user_id,
        qr_url="https://nnfp.jss.com.cn/unmatched",
        merchant_name="不存在商户",
        expected_amount=Decimal("999.00"),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add_all([t5, t6, t7])
    await db.commit()

    async def runner_ok(_u, _p, _c):
        return BrowserClaimOutcome(kind="submitted", detail="已自动提交")

    for t in (t5, t6, t7):
        await execute_claim_task(db, task=t, profile=profile, settings=settings, browser_runner=runner_ok)
        assert t.status == "submitted"

    await create_invoice(email_account=account, invoice_no="SB-001", seller="上海星巴克咖啡经营有限公司", amount=Decimal("38.00"))
    await create_invoice(email_account=account, invoice_no="DD-001", seller="滴滴出行科技有限公司", amount=Decimal("24.50"))

    # Also test merchant_ok=True with mismatched amount (does not match)
    t8 = InvoiceClaimTask(
        user_id=account.user_id,
        qr_url="https://nnfp.jss.com.cn/mismatch",
        merchant_name="星巴克",
        expected_amount=Decimal("777.00"),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(t8)
    await db.commit()
    await execute_claim_task(db, task=t8, profile=profile, settings=settings, browser_runner=runner_ok)

    pairs = await match_submitted_claims(db, user_id=account.user_id)
    assert len(pairs) == 2
    await db.refresh(t5)
    await db.refresh(t6)
    await db.refresh(t7)
    await db.refresh(t8)
    assert t5.status == "completed"
    assert t6.status == "completed"
    assert t7.status == "submitted"
    assert t8.status == "submitted"
    # Second call with only unmatched submitted tasks returns [] (covers empty matched_pairs branch)
    assert await match_submitted_claims(db, user_id=account.user_id) == []


async def _coro(val):
    return val
