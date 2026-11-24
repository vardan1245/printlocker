"""Live job status for the user app: ws://<host>/api/v1/ws/user?token=<access token>."""
import asyncio

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from ..realtime import hub
from ..security import decode_token

router = APIRouter(tags=["live"])


@router.websocket("/ws/user")
async def user_socket(websocket: WebSocket, token: str = ""):
    try:
        user_id = int(decode_token(token, "access")["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    hub.bind_loop(asyncio.get_running_loop())
    queue = hub.subscribe(user_id)
    try:
        await websocket.send_json({"type": "hello", "user_id": user_id})
        while True:
            message = await queue.get()
            await websocket.send_json(message)
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(user_id, queue)
