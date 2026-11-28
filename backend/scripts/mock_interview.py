"""在终端里做一场模拟面试。后端要先启动（uv run uvicorn app.main:app --port 8000）。

用法（在 backend/ 目录下）：

    uv run python scripts/mock_interview.py 1
    uv run python scripts/mock_interview.py 1 "技术面，重点练项目深挖"

第一个参数是岗位编号，第二个参数选填，写你想让面试官扮演谁、重点练什么。
每轮输入一行回答后回车。直接回车（不输入内容）表示结束，系统会复盘已经回答的部分。
中途按 Ctrl+C 退出不会丢进度，用同样的命令再运行一次就能接着面。
"""

import argparse
import json
import sys

import httpx
from websockets.sync.client import connect


def main() -> int:
    parser = argparse.ArgumentParser(description="在终端里做一场模拟面试")
    parser.add_argument("job_id", type=int, help="岗位编号")
    parser.add_argument("conditions", nargs="?", default="", help="想让面试官扮演谁、重点练什么")
    parser.add_argument("--server", default="127.0.0.1:8000", help="后端地址，默认 127.0.0.1:8000")
    args = parser.parse_args()

    # 同一个岗位、同样的要求还有一场没面完时，这里拿到的是那一场，所以再运行一次就是接着面。
    response = httpx.post(
        f"http://{args.server}/api/runs",
        json={"kind": "mock_interview", "job_ids": [args.job_id], "conditions": args.conditions},
        trust_env=False,
    )
    if response.status_code != 202:
        print(f"没有开始：{response.json().get('detail', response.text)}")
        return 1
    run_id = response.json()["id"]

    speaking = False  # 面试官的话是不是已经逐字显示过了
    reviewing = False
    with connect(f"ws://{args.server}/api/runs/{run_id}/interview", proxy=None) as websocket:
        for raw in websocket:
            message = json.loads(raw)
            kind = message["type"]
            if kind == "token":
                if not speaking:
                    print("\n面试官：", end="")
                    speaking = True
                print(message["text"], end="", flush=True)
            elif kind == "question":
                if not speaking:  # 连上之前这个问题已经问完了（刚开始，或者是接着上次的面试）
                    if message["transcript"]:
                        print(f"（接着上次的面试，已经问答了 {len(message['transcript'])} 轮）")
                    print(f"\n面试官：{message['question']}")
                speaking = False
                print(f"\n\n（第 {message['turn']}/{message['total']} 轮。直接回车结束并复盘）")
                _reply(websocket)
            elif kind == "error":
                print(f"\n{message['message']}")
                _reply(websocket)
            elif kind == "status" and message["label"] == "复盘" and not reviewing:
                reviewing = True
                print("\n正在复盘，大约半分钟……", flush=True)
            elif kind == "status" and "没有" in message["label"]:
                print(f"\n{message['label']}。")
            elif kind == "review":
                print(f"\n{message['content_md']}")
                print(f"复盘已保存，成果编号 {message['artifact_id']}。")
            elif kind == "done":
                if message["error"]:
                    print(f"\n面试没有完成：{message['error']}")
                return 0 if message["status"] == "succeeded" else 1
    return 0


def _reply(websocket) -> None:
    try:
        answer = input("你：").strip()
    except EOFError:
        answer = ""
    reply = {"type": "answer", "text": answer} if answer else {"type": "end"}
    websocket.send(json.dumps(reply, ensure_ascii=False))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已退出，进度保存了。用同样的命令再运行一次可以接着面。")
    except (httpx.ConnectError, ConnectionRefusedError):
        print("连不上后端。请先在 backend/ 目录下运行：uv run uvicorn app.main:app --port 8000")
        sys.exit(1)
