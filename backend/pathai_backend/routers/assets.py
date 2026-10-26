# -*- coding: utf-8 -*-
"""静态资源：ASR 模型文件与清单（API-05 之外的免鉴权下载端点）。

模型随包不可行（30MB 总包限），首启一次性下载到客户端用户目录，此后
识别零联网（docs/sherpa-onnx 方案）。免 Bearer：wx.downloadFile 不携带
Authorization 头。manifest 提供每文件 sha256，客户端下载后校验才落就绪标。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

#: backend/static/asr（不入 git，开发机从 result/asr-models 拷入）
ASR_DIR = Path(__file__).resolve().parents[2] / "static" / "asr"
#: 允许下载的文件白名单（防路径穿越）
ALLOWED_FILES = ("model.int8.onnx", "bbpe.model", "tokens.txt")

router = APIRouter(prefix="/static/asr", tags=["static"])


def _manifest() -> dict:
    files = {}
    for name in ALLOWED_FILES:
        path = ASR_DIR / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"模型文件未放置：{name}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files[name] = {"sha256": digest, "bytes": path.stat().st_size}
    return {
        "version": "zipformer2-ctc-zh-int8-2025-04-01-v1",
        "files": files,
    }


@router.get("/manifest")
def manifest() -> JSONResponse:
    """模型清单：版本 + 每文件 sha256/字节数（客户端据此校验下载）。"""
    try:
        return JSONResponse(_manifest())
    except HTTPException as err:
        # 模型未放置时返回 404 而非 500
        raise err


@router.get("/{name}")
def model_file(name: str) -> FileResponse:
    if name not in ALLOWED_FILES:
        raise HTTPException(status_code=404, detail="未知文件")
    path = ASR_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="模型文件未放置")
    return FileResponse(path, media_type="application/octet-stream", filename=name)
