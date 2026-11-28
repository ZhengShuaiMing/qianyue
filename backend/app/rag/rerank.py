"""Rerank：对召回的片段重新打分排序。

向量召回快但粗，只看「大致相近」。交叉编码器把查询和片段放在一起读，判断更准但更慢，
所以只用它给召回的少量候选重新排序。
"""

import math
import threading
from pathlib import Path
from typing import Protocol


class Reranker(Protocol):
    def score(self, query: str, texts: list[str]) -> list[float]:
        """每个片段与查询的相关度，0 到 1。"""
        ...


class LocalReranker:
    """用 fastembed 在本机跑交叉编码器。后端启动后在后台下载和加载（见 main.py）。"""

    def __init__(self, model_name: str, cache_dir: Path) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model = None
        self._lock = threading.Lock()

    def load(self):
        """第一次调用时下载并加载模型。后台准备还没完成时检索进来，就等它完成，不重复下载。"""
        with self._lock:
            if self._model is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                self._model = TextCrossEncoder(self.model_name, cache_dir=str(self.cache_dir))
        return self._model

    def score(self, query: str, texts: list[str]) -> list[float]:
        # 模型输出的是未归一化的分数，用 sigmoid 压到 0–1，方便设统一的门槛。
        return [1 / (1 + math.exp(-logit)) for logit in self.load().rerank(query, texts)]
