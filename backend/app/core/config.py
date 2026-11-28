from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent


# 联网搜索服务。每家的接口写在 app/tools/web.py 的 PROVIDERS 里
SearchProviderKey = Literal["ddgs", "bocha", "zhipu", "tavily", "brave", "serper", "exa"]


class Settings(BaseSettings):
    """运行配置。来源优先级：环境变量 > backend/.env > 默认值，变量名统一加 QIANYUE_ 前缀。

    模型的地址、模型名和 Key 在设置页保存过时，以设置页为准（见 app/db/app_settings.py）。
    """

    model_config = SettingsConfigDict(
        env_prefix="QIANYUE_",
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 任何 OpenAI 兼容接口都可以：DeepSeek、通义千问、智谱、本地 Ollama 等。
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"
    # SecretStr 在 repr 和日志里显示为 **********，取值必须显式调用 get_secret_value()。
    llm_api_key: SecretStr | None = None
    hide_personal_info: bool = True
    privacy_terms: str = ""
    # 一次评估允许送进模型的输入上限（估算值）。JD 加简历超过就不调用，补充资料超出部分会被裁掉。
    llm_input_token_budget: int = 30000
    # 批量评估时，一次运行里同时评估的岗位数上限。调太高容易触发模型服务商的限流。
    max_concurrent_jobs: int = 3

    # Embedding 和 Rerank 模型在本机运行，首次使用时下载到 data/models/。
    # 换了 Embedding 模型要删掉 data/vectors/，让向量库用新模型重建。
    embedding_model: str = "jinaai/jina-embeddings-v2-base-zh"
    rerank_model: str = "BAAI/bge-reranker-v2-m3-int8"

    # 联网搜索用哪家。ddgs 不用 Key（默认）；其余几家要在 search_api_key 里填 Key。
    search_provider: SearchProviderKey = "ddgs"
    search_api_key: SecretStr | None = None

    # 采集用的专用浏览器：系统里已安装的 Chrome，单独的用户目录在 data/browser/。
    # 找不到 Chrome 时用 chrome_path 指定它的位置。
    chrome_path: Path | None = None
    browser_port: int = 9339

    data_dir: Path = REPO_ROOT / "data"
    # 构建好的前端（frontend 里 npm run build 的输出）。
    # 存在时后端在 / 提供页面，只开一个服务就能用。
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"

    @field_validator("data_dir")
    @classmethod
    def from_repo_root(cls, value: Path) -> Path:
        """相对路径按仓库根算：start.sh 在 backend/ 里启动，QIANYUE_DATA_DIR=my-data
        指的仍是仓库根下的 my-data/。"""
        return value if value.is_absolute() else REPO_ROOT / value

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_api_key.get_secret_value().strip())
