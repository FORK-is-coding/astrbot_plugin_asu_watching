from __future__ import annotations

import base64
import io
import json
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import qrcode

LOGIN_URL = "https://www.xiaohongshu.com"
QR_CREATE = "/api/sns/web/v1/login/qrcode/create"
QR_STATUS = "/api/sns/web/v1/login/qrcode/status"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
STATUS_TEXT = {
    0: "waiting",
    1: "scanned",
    2: "confirmed",
    3: "expired",
}


class XhsLoginError(RuntimeError):
    pass


class XhsLoginManager:
    def __init__(self, auth_path: Path):
        self.auth_path = auth_path
        self._sessions: dict[str, dict] = {}

    async def start(self) -> dict:
        a1 = secrets.token_hex(26)
        web_id = secrets.token_hex(16)
        cookies = {"a1": a1, "webId": web_id}
        headers = {
            "User-Agent": UA,
            "Referer": f"{LOGIN_URL}/",
            "Origin": LOGIN_URL,
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession(
            headers=headers, trust_env=True, timeout=aiohttp.ClientTimeout(total=20)
        ) as session:
            async with session.post(
                LOGIN_URL + QR_CREATE,
                json={"qr_type": 1},
                cookies=cookies,
            ) as resp:
                if resp.status != 200:
                    raise XhsLoginError(
                        f"小红书二维码创建失败（HTTP {resp.status}）：该接口需要浏览器签名，"
                        "请改用下方 Cookie 粘贴登录"
                    )
                payload = await resp.json()
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        qr_id = str(data.get("qr_id") or data.get("qrId") or "")
        code = str(data.get("code") or "")
        qr_url = str(data.get("url") or data.get("qr_url") or "")
        if not qr_id or not code or not qr_url:
            raise XhsLoginError("小红书二维码响应缺少 qr_id/code/url，接口可能已变更")

        image = qrcode.make(qr_url)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

        self._sessions[qr_id] = {
            "cookies": cookies,
            "code": code,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        return {"session": qr_id, "qr_image": data_url, "state": "waiting"}

    async def status(self, session_id: str) -> dict:
        session_data = self._sessions.get(session_id)
        if session_data is None:
            raise XhsLoginError("登录会话不存在或已过期，请重新获取二维码")

        headers = {
            "User-Agent": UA,
            "Referer": f"{LOGIN_URL}/",
        }
        params = {"qr_id": session_id, "code": session_data["code"]}
        async with aiohttp.ClientSession(
            headers=headers, trust_env=True, timeout=aiohttp.ClientTimeout(total=20)
        ) as session:
            async with session.get(
                LOGIN_URL + QR_STATUS,
                params=params,
                cookies=session_data["cookies"],
            ) as resp:
                if resp.status != 200:
                    raise XhsLoginError(f"小红书登录状态查询失败（HTTP {resp.status}）")
                payload = await resp.json()

        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        code_status = data.get("code_status", data.get("codeStatus"))
        try:
            code_status = int(code_status)
        except (TypeError, ValueError):
            raise XhsLoginError("小红书登录状态响应缺少 code_status")

        state = STATUS_TEXT.get(code_status, "waiting")
        if state != "confirmed":
            return {"state": state}

        login_info = data.get("login_info") or {}
        cookies = dict(session_data["cookies"])
        session_value = data.get("session") or login_info.get("session")
        secure_session = data.get("secure_session") or login_info.get("secure_session")
        if session_value:
            cookies["web_session"] = str(session_value)
        if secure_session:
            cookies["web_session_sec"] = str(secure_session)
        if not cookies.get("web_session"):
            raise XhsLoginError("小红书登录已确认，但没有返回 web_session")

        self.auth_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_auth(
            {
                "cookies": cookies,
                "user_id": data.get("user_id") or login_info.get("user_id") or "",
                "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        self._sessions.pop(session_id, None)
        return {"state": "confirmed"}

    def save_cookie_string(self, raw: str) -> None:
        cookies: dict[str, str] = {}
        for part in raw.replace("\n", ";").split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            key = key.strip()
            if key:
                cookies[key] = value.strip()
        if not cookies.get("web_session"):
            raise XhsLoginError("Cookie 里没有 web_session：请在已登录的小红书网页复制完整 Cookie")
        self.auth_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_auth(
            {
                "cookies": cookies,
                "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )

    async def logout(self) -> None:
        self.auth_path.unlink(missing_ok=True)

    def _write_auth(self, payload: dict) -> None:
        self.auth_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.auth_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.auth_path)

    def logged_in(self) -> bool:
        if not self.auth_path.exists():
            return False
        try:
            payload = json.loads(self.auth_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        return bool((payload.get("cookies") or {}).get("web_session"))

    @staticmethod
    def new_session_id() -> str:
        return uuid.uuid4().hex
