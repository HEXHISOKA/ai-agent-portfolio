from pydantic import BaseModel, Field, field_validator


class SemanticDebugRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20, strict=True)

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("检索内容不能为空")
        return value
