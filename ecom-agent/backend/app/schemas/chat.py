from pydantic import BaseModel, Field, StrictBool


class ChatTurn(BaseModel):
    """前端提交的一条历史消息。"""

    role: str = Field(pattern="^(user|assistant)$", description="消息来源")
    content: str = Field(max_length=2000, description="历史文本")


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000, description="用户问题")
    history: list[ChatTurn] = Field(
        default_factory=list, max_length=10, description="最近对话"
    )


class ConfirmRequest(BaseModel):
    # 校验32位小写十六进制格式；存在性与归属由确认服务检查。
    thread_id: str = Field(pattern="^[0-9a-f]{32}$")
    approved: StrictBool
