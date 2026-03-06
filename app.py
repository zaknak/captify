"""captify アプリ起動エントリポイント。"""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    """起動オプションを解析する。

    概要:
        allowed_paths の指定方法（JSONファイル/直接指定）を受け取る。
    引数:
        なし。
    戻り値:
        解析済み引数。
    例外:
        SystemExit: 不正なオプション指定時。
    使用例:
        >>> _ = parse_args()
    """

    parser = argparse.ArgumentParser(description="captify launcher")
    parser.add_argument(
        "--allowed-paths-json",
        type=Path,
        default=Path("allowed_paths.json"),
        help="allowed_paths を記載した JSON ファイルパス（既定: allowed_paths.json）",
    )
    parser.add_argument(
        "--allowed-path",
        action="append",
        default=[],
        help="allowed_paths を直接追加指定（複数回指定可能）",
    )
    return parser.parse_args()


def main() -> None:
    """captifyアプリを起動する。

    概要:
        起動オプションを読み取り、UIを起動する。
    引数:
        なし。
    戻り値:
        なし。
    例外:
        Exception: 起動失敗時。
    使用例:
        >>> main()
    """

    args = parse_args()
    from captify.ui import launch

    launch(allowed_paths_json=args.allowed_paths_json, allowed_paths_cli=args.allowed_path)


if __name__ == "__main__":
    main()
