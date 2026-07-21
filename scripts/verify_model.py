from __future__ import annotations

from dataclasses import replace

from app.core.config import load_settings
from app.core.model import make_main_model


def main() -> None:
    settings = replace(
        load_settings(),
        model_temperature=0,
        model_max_tokens=256,
    )

    print(f"provider: {settings.model_provider}")
    print(f"model: {settings.model_name}")
    print("正在进行真实 DeepSeek API 调用……")

    model = make_main_model(settings)

    try:
        response = model.invoke(
            [
                (
                    "system",
                    "你是 Tang Agent 的模型连接验证助手。",
                ),
                (
                    "human",
                    "请只回复：Tang Agent DeepSeek-V4-Pro 连接成功",
                ),
            ]
        )
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)

        print(
            "模型调用失败："
            f"type={type(exc).__name__}, "
            f"status_code={status_code}"
        )
        raise SystemExit(1) from None

    print("response:")
    print(response.content)

    if response.usage_metadata:
        print("usage:")
        print(response.usage_metadata)


if __name__ == "__main__":
    main()