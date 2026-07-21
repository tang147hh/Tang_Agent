from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_deepseek import ChatDeepSeek

from app.core.config import load_settings
from app.core.model import (
    ModelConfigurationError,
    make_main_model,
)


def test_model_settings_are_loaded_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TANG_AGENT_MODEL_PROVIDER",
        "deepseek",
    )
    monkeypatch.setenv(
        "TANG_AGENT_MODEL_NAME",
        "deepseek-chat",
    )
    monkeypatch.setenv(
        "TANG_AGENT_MODEL_API_KEY",
        "test-secret-key",
    )

    settings = load_settings()

    assert settings.model_provider == "deepseek"
    assert settings.model_name == "deepseek-chat"
    assert "test-secret-key" not in repr(settings)


def test_make_main_model_does_not_call_network() -> None:
    settings = replace(
        load_settings(),
        model_provider="deepseek",
        model_name="deepseek-chat",
        model_api_key="test-key",
        model_base_url="https://api.deepseek.com",
        model_temperature=0.2,
        model_max_tokens=8192,
        model_timeout=60,
        model_max_retries=2,
    )

    model = make_main_model(settings)

    assert isinstance(model, ChatDeepSeek)
    assert model.model_name == "deepseek-chat"
    assert model.temperature == 0.2
    assert model.max_tokens == 8192
    assert model.streaming is True


def test_missing_model_api_key_fails_early() -> None:
    settings = replace(
        load_settings(),
        model_api_key="",
    )

    with pytest.raises(
        ModelConfigurationError,
        match="TANG_AGENT_MODEL_API_KEY",
    ):
        make_main_model(settings)


def test_unsupported_provider_fails_early() -> None:
    settings = replace(
        load_settings(),
        model_provider="unknown",
        model_api_key="test-key",
    )

    with pytest.raises(
        ModelConfigurationError,
        match="不支持的模型供应商",
    ):
        make_main_model(settings)