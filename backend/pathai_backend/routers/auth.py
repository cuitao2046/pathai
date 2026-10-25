# -*- coding: utf-8 -*-
"""API-01：POST /api/v1/auth/wechat（docs/24 §8）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from pathai_backend import auth
from pathai_backend.config import Settings
from pathai_backend.schemas.auth import AuthResponse, WechatLoginRequest
from pathai_backend.wechat import WechatAuthClient

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/wechat", response_model=AuthResponse)
def wechat_login(
    body: WechatLoginRequest,
    db: Session = Depends(auth.get_db_session),
    client: WechatAuthClient = Depends(auth.get_wechat_client),
    settings: Settings = Depends(auth.get_settings),
) -> AuthResponse:
    return auth.login(db, client, settings, body.code)
