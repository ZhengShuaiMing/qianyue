"""Embedding 工厂：把文字变成向量。模型在本机运行，材料不用发给任何服务商。"""

import threading
from pathlib import Path

from langchain_core.embeddings import Embeddings


class LocalEmbeddings(Embeddings):
    """用 fastembed 在本机跑 Embedding 模型。后端启动后在后台下载和加载（见 main.py）。"""

    def __init__(self, model_name: str, cache_dir: Path) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model = None
        self._lock = threading.Lock()

    def load(self):
        """第一次调用时下载并加载模型。后台准备还没完成时检索进来，就等它完成，不重复下载。"""
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding  # 首次加载本地模型时才导入

                self._model = TextEmbedding(self.model_name, cache_dir=str(self.cache_dir))
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self.load().passage_embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self.load().query_embed(text))).tolist()
