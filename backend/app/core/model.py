from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek

from app.core.config import Settings, load_settings


class ModelConfigurationError(RuntimeError):
    """模型配置不完整或不受支持。"""


def make_main_model(
    settings: Settings | None = None,
) -> BaseChatModel:
    """创建 Tang Agent 使用的主模型。

    业务代码只依赖 BaseChatModel，不直接依赖具体供应商。
    创建模型对象不会发送网络请求。
    """

    current = settings or load_settings()

    if current.model_provider != "deepseek":
        raise ModelConfigurationError(
            f"不支持的模型供应商：{current.model_provider}"
        )

    if not current.model_name:
        raise ModelConfigurationError(
            "TANG_AGENT_MODEL_NAME 不能为空"
        )

    if not current.model_api_key:
        raise ModelConfigurationError(
            "缺少 TANG_AGENT_MODEL_API_KEY"
        )

    model_options: dict[str, object] = {}

    if current.model_base_url:
        model_options["api_base"] = current.model_base_url

    return ChatDeepSeek(
        model=current.model_name,
        api_key=current.model_api_key,
        temperature=current.model_temperature,
        max_tokens=current.model_max_tokens,
        timeout=current.model_timeout,
        max_retries=current.model_max_retries,
        streaming=True,
        **model_options,
    )