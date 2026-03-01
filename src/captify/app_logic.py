"""captify のアプリケーションロジック。"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator

LOGGER = logging.getLogger(__name__)


def stream_caption(stream_lines: Iterator[str], max_tokens: int) -> tuple[bool, str, str | None]:
    """ストリーミング応答から caption テキストを抽出して返す。

    概要:
        `/v1/chat/completions` のストリーミング行を順番に解釈し、
        最初の assistant message の text を連結して返す。

    引数:
        stream_lines: OpenAI 互換 API のストリーミング行イテレータ。
        max_tokens: UI で指定された max_tokens 値（検証は実施しない）。

    戻り値:
        `(成功可否, 抽出テキスト, エラーメッセージ)` のタプル。

    例外:
        本関数は例外を握りつぶさず、失敗時は `False` とエラーメッセージを返す。

    使用例:
        >>> ok, text, error = stream_caption(iter(["data: {\"choices\":[{\"delta\":{\"content\":\"hello\"}}]}", "data: [DONE]"]), 256)
        >>> ok
        True
        >>> text
        'hello'
    """
    del max_tokens  # max_tokens 超過確認は don't care

    chunks: list[str] = []
    for raw_line in stream_lines:
        line = raw_line.strip()
        if not line or not line.startswith("data:"):
            continue

        payload = line[5:].strip()
        if payload == "[DONE]":
            break

        try:
            data: dict[str, Any] = json.loads(payload)
        except json.JSONDecodeError as exc:
            LOGGER.error("ストリーミング JSON の解析に失敗しました: %s", exc)
            return False, "", f"stream_parse_error: {exc}"

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            continue

        delta = choices[0].get("delta", {})
        content = delta.get("content")
        if isinstance(content, str):
            chunks.append(content)

    final_text = "".join(chunks).strip()
    if not final_text:
        LOGGER.error("抽出テキストが空でした")
        return False, "", "empty_text"

    return True, final_text, None
