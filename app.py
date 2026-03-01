"""captify アプリ起動エントリポイント。"""

from __future__ import annotations

import sys


def main() -> None:
    """captifyアプリを起動する。

    概要:
        UI起動処理を実行し、依存不足時には原因を明示して終了する。
    引数:
        なし。
    戻り値:
        なし。
    例外:
        SystemExit: 必須依存が不足している場合。
    使用例:
        >>> main()
    """

    try:
        from src.captify.ui import launch
    except ModuleNotFoundError as error:
        missing = getattr(error, "name", "unknown")
        print(
            "ERROR: 必須依存の読み込みに失敗しました。"
            f" missing_module={missing} "
            "hint='venv を有効化し pip install -r requirements.txt を実行してください。'",
            file=sys.stderr,
        )
        raise SystemExit(1) from error

    launch()


if __name__ == "__main__":
    main()
