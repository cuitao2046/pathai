# -*- coding: utf-8 -*-
"""讯飞 WebSocket 签名 URL 签发（live 数据源 ASR/TTS 直连讯飞云）。

- 均要求 Bearer 令牌（对齐业务路由）；
- 响应 {url, expiresIn}：小程序拿 URL 直接 wx.connectSocket，
  讯飞校验 URL 中 date 与服务端时间偏差 ±300s，客户端缓存不得超过 expiresIn；
- 密钥缺失 → 503 XFYUN_NOT_CONFIGURED（请求时失败而非启动失败，
  本地未配讯飞密钥时服务照常可用）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from pathai_backend import auth, xfyun
from pathai_backend.schemas.voice import VoiceUrlResponse

router = APIRouter(
    prefix="/api/v1/voice",
    tags=["voice"],
    dependencies=[Depends(auth.get_current_user)],
)


def _settings(request: Request):
    return request.app.state.settings


@router.get("/asr-url", response_model=VoiceUrlResponse)
def get_asr_url(request: Request) -> VoiceUrlResponse:
    return VoiceUrlResponse(
        url=xfyun.build_iat_url(_settings(request)),
        expiresIn=xfyun.URL_EXPIRES_SECONDS,
    )


@router.get("/tts-url", response_model=VoiceUrlResponse)
def get_tts_url(request: Request) -> VoiceUrlResponse:
    return VoiceUrlResponse(
        url=xfyun.build_tts_url(_settings(request)),
        expiresIn=xfyun.URL_EXPIRES_SECONDS,
    )
