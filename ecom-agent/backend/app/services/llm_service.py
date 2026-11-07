from typing import Generator
from random import choice
from openai import OpenAI
from app.config import settings


def complete_text(system_prompt: str, user_prompt: str) -> str:
    """接收系统生成SQL的规则与当前用户的问题，调用大模型并返回完整的输出文本，失败的时候直接抛异常"""
    if not all((settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL)):
        raise ValueError("请完成大模型相关的配置")
    if settings.LLM_TIMEOUT_SECONDS <= 0:
        raise ValueError("大模型的超时时间配置必须大于0")

    with OpenAI(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        timeout=settings.LLM_TIMEOUT_SECONDS,
        max_retries=0,  # 失败直接反馈，便于定位问题
    ) as client:
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            stream=False,
        )
        if not response.choices:
            raise ValueError("模型没有返回结果")
        choice = response.choices[0]
        if choice.finish_reason != "stop":
            raise ValueError("模型未正常返回文本生成")
        content = choice.message.content
        if not isinstance(content, str) or not content.strip():
            raise ValueError("模型没有返回有效的文本")
        return content.strip()


# 逐段产出模型正文；没有正文或结束原因不是 stop 时抛出错误。
def stream_text(system_prompt: str, user_prompt: str) -> Generator[str, None, None]:
    # 必要配置缺失时直接拒绝，不发出无效网络请求。
    if not all((settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL)):
        raise ValueError("请完成大模型相关的配置")
    # 拒绝非正数的超时配置，客户端使用该值限制网络等待时间。
    if settings.LLM_TIMEOUT_SECONDS <= 0:
        raise ValueError("大模型的超时时间配置必须大于0")
    # with 负责关闭客户端与响应流，禁用自动重试以限制失败时的等待。
    with OpenAI(
        base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY,
        timeout=settings.LLM_TIMEOUT_SECONDS, max_retries=0,
    ) as client:
        with client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            stream=True,
        ) as stream:
            # has_text 记录是否收到非空白正文，finished 记录是否收到 stop 结束标记。
            has_text = False
            finished = False
            for chunk in stream:
                # 兼容只含使用量等信息、没有 choices 的分块。
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                content = choice.delta.content
                # 只转发正文增量，空片段和角色信息不进入页面回答。
                if content:
                    # 保留正文中的空格与换行，但只有非空白内容才能证明回答有效。
                    has_text = has_text or bool(content.strip())
                    yield content
                # 连接关闭不等于正常结束，必须检查模型返回的结束原因。
                if choice.finish_reason is not None:
                    # 截断、内容过滤等结束原因都不能标记为完整成功。
                    if choice.finish_reason != "stop":
                        raise ValueError("模型未正常完成回答")
                    finished = True
            # 流关闭后仍缺少正常结束或正文时，通知 SSE 层按失败处理。
            if not finished or not has_text:
                raise ValueError("模型未返回完整有效的回答")
