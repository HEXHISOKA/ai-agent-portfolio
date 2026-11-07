from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # 后端项目的根路径


class Settings(BaseSettings):
    DATABASE_URL: str
    JWT_SECRET_KEY: str
    JWT_EXPIRE_HOURS: int = 24
    JWT_ALGORITHM: str = "HS256"

    LLM_BASE_URL: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL: str = ""
    LLM_TIMEOUT_SECONDS: float = 60.0  # 为一次模型请求设置 60 秒的网络等待超时。

    # 视觉模型独立配置，文本模型不必具备图片输入能力。
    VISION_BASE_URL: str = ""
    VISION_API_KEY: str = ""
    VISION_MODEL: str = ""
    VISION_TIMEOUT_SECONDS: float = 90.0

    EMBEDDING_BASE_URL: str = ""
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_MODEL: str = ""
    EMBEDDING_TIMEOUT_SECONDS: float = 15.0  # 限制网络等待，避免长时间无反馈。

    QUERY_DATABASE_URL: str = ""

    CHROMA_PERSIST_DIR: str = "data/chroma"
    CHROMA_COLLECTION: str = "ecom_texts"

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8"
    )


settings = Settings()

# 相对路径以 backend 为基准，避免从不同工作目录启动时读取不同索引。
CHROMA_DIR = BASE_DIR / settings.CHROMA_PERSIST_DIR

UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100M


ALLOWED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".zip",
}
