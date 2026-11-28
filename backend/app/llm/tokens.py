"""Token 预算：估算文本大约占多少 Token，超出时裁剪。

不同模型的分词器不一样，这里不追求精确，只用来在调用前拦住明显过长的输入。
"""

import math

TRUNCATED_NOTE = "\n（内容过长，后面的部分已省略）"
# 一张截图大约占多少 Token。各家算法不同，这里按 OpenAI 公开的看图计费规则估：
# 一张 1080×2400 的手机截图缩到 768×1707，切成 8 块 512 像素的格子，8×170+85 = 1445。
IMAGE_TOKENS = 1500


def estimate_tokens(text: str) -> int:
    cjk = sum(1 for char in text if "一" <= char <= "鿿")
    return math.ceil(cjk * 0.7 + (len(text) - cjk) * 0.35)


def trim_to_tokens(text: str, budget: int) -> str:
    """把文本裁到预算以内，保留开头。没超就原样返回。"""
    tokens = estimate_tokens(text)
    if tokens <= budget:
        return text
    keep = int(len(text) * max(budget, 0) / tokens)
    return text[:keep].rstrip() + TRUNCATED_NOTE if keep else ""
