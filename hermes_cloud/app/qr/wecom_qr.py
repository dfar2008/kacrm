"""WeCom AI Bot 扫码开通 — 云端 device flow 原语。

驱动企业微信官方 AI Bot 控制台开通端点
``work.weixin.qq.com/ai/qc/{generate,query_result}``(由 ``source`` 区分
接入方;腾讯云轻量应用服务器自身的管理界面亦用此端点)。用户用企业微信
手机端扫码后,后台创建 AI 机器人并返回 ``bot_id`` + ``secret``。

对应 hermes ``gateway/platforms/wecom.py::qr_scan_for_bot_info``,但拆成
start/poll 两个原语供云端 qr_start/qr_poll 端点调用(无终端渲染、无阻塞轮询)。
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Dict

import httpx

logger = logging.getLogger(__name__)

_QR_GENERATE_URL = "https://work.weixin.qq.com/ai/qc/generate"
_QR_QUERY_URL = "https://work.weixin.qq.com/ai/qc/query_result"
_QR_SOURCE = "hermes"
_UA = "HermesCloud/1.0"
_QR_EXPIRY_SECONDS = 300  # WeCom AI Bot 扫码页有效期约 5 分钟


class WeComQRError(Exception):
    """企微扫码开通 API 返回异常时抛出。"""


async def qr_start() -> Dict[str, Any]:
    """发起一次企微 AI Bot 扫码开通。

    返回 ``{"scode": str, "qr_url": str}``。``qr_url`` 用 generate 返回的
    ``auth_url``(``…/ai/qc/c?s=<scode>&for_native=true``)——这是企业微信
    App **直扫即授权**的原生深链,由 SPA 渲染为二维码。注意不能用 ``…/ai/qc/gen``
    那个网页地址:扫它只会打开一个再显示二维码的网页(码套码)。
    """
    url = f"{_QR_GENERATE_URL}?source={_QR_SOURCE}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers={"User-Agent": _UA})
            resp.raise_for_status()
            raw = resp.json()
    except Exception as exc:
        raise WeComQRError(f"failed to fetch WeCom QR: {exc}") from exc
    data = raw.get("data") or {}
    scode = str(data.get("scode") or "").strip()
    auth_url = str(data.get("auth_url") or "").strip()
    if not scode or not auth_url:
        raise WeComQRError(f"unexpected generate response: {raw}")
    return {"scode": scode, "qr_url": auth_url, "expires_in": _QR_EXPIRY_SECONDS}


async def qr_poll_once(scode: str) -> Dict[str, Any]:
    """轮询一次。返回状态联合:

    - ``{"status": "pending"}``
    - ``{"status": "completed", "creds": {"bot_id":…, "secret":…}}``
    - ``{"status": "error", "message": …}``

    网络瞬断按 pending 处理,让上层继续轮询。
    """
    url = f"{_QR_QUERY_URL}?scode={urllib.parse.quote(scode)}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"User-Agent": _UA})
            resp.raise_for_status()
            result = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("wecom qr poll http error %s: %s", exc.response.status_code, exc)
        return {"status": "error", "message": f"企业微信接口返回 {exc.response.status_code}"}
    except Exception as exc:
        logger.debug("wecom qr poll transient error: %s", exc)
        return {"status": "pending"}
    data = result.get("data") or {}
    status = str(data.get("status") or "").lower()
    if status == "success":
        bot = data.get("bot_info") or {}
        bot_id = str(bot.get("botid") or bot.get("bot_id") or "").strip()
        secret = str(bot.get("secret") or "").strip()
        if bot_id and secret:
            return {"status": "completed",
                    "creds": {"bot_id": bot_id, "secret": secret}}
        return {"status": "error", "message": "扫码成功但未返回机器人凭证"}
    return {"status": "pending"}
