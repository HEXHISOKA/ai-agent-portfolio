from typing import Literal
from pydantic import BaseModel, Field


class FeedbackRequest(BaseModel):
    rating: Literal[1, -1]
    comment: str = Field(default="", max_length=500)
