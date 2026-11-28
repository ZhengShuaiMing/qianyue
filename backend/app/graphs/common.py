"""评估图和定制图共用的小工具。"""

from langgraph.config import get_stream_writer

MAX_RETRIES = 2  # 模型输出不合格时最多再问 2 次

RETRY_TEMPLATE = """上一次的输出不合格：{problem}

请重新输出完整的 JSON 对象，修正上面的问题，其余内容保持不变。只输出 JSON。"""


def announce(job_id: int, node: str) -> None:
    """把「哪个岗位进入了哪个节点」推给正在看进度的人。"""
    get_stream_writer()({"job_id": job_id, "node": node})


def job_result(job_id: int, status: str, reason: str = "", artifact_id: int | None = None) -> dict:
    """一个岗位的结论，写进 State 的 results。

    status：done 已生成、reused 复用已有成果、blocked 前置检查未过、failed 模型输出始终不合格、
    no_changes 模型认为不需要修改、declined 用户没有接受任何修改、empty 模拟面试一题都没答。
    """
    result = {"job_id": job_id, "status": status, "artifact_id": artifact_id, "reason": reason}
    return {"results": [result]}
