import json

# 关掉中间层缓存和缓冲，保证每一段都立刻送到浏览器。
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def sse(event: str, data: dict) -> str:
    """一条 SSE 消息：事件名一行、JSON 数据一行、空行结束。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
