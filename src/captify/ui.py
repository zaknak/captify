"""captify Gradio UI 定義。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import gradio as gr

from .app_logic import (
    DEFAULT_ENDPOINT,
    available_preset_names,
    ensure_presets_file,
    execute_batch,
    execute_test,
    first_preset,
    model_fetch_handler,
    preset_change_handler,
    preview_images,
    setup_logging,
)

LOGGER = logging.getLogger("captify")


def build_app() -> gr.Blocks:
    """Gradioアプリを構築する。

    概要:
        主機能1のUI要素とイベント配線を生成する。
    引数:
        なし。
    戻り値:
        構築済みGradio Blocks。
    例外:
        Exception: プリセット読み込み失敗時など。
    使用例:
        >>> app = build_app()
    """

    setup_logging()
    presets: dict[str, str] = ensure_presets_file()
    first_name, first_prompt = first_preset(presets)

    with gr.Blocks(title="captify") as demo:
        gr.Markdown("# captify - 主機能1（一括生成）")

        with gr.Row():
            endpoint = gr.Textbox(label="エンドポイント", value=DEFAULT_ENDPOINT)
            fetch_models_btn = gr.Button("モデル取得")

        with gr.Row():
            model_dropdown = gr.Dropdown(
                label="モデル選択",
                choices=[],
                value=None,
                interactive=False,
                allow_custom_value=False,
                info="モデル未取得",
            )

        folder_input = gr.Textbox(label="入力フォルダ", placeholder="画像フォルダを指定")
        preview_btn = gr.Button("フォルダ内画像プレビュー更新")
        gallery = gr.Gallery(label="フォルダ内画像プレビュー", columns=4, height=260)

        with gr.Row():
            preset_dropdown = gr.Dropdown(
                label="プロンプトプリセット",
                choices=list(available_preset_names(presets)),
                value=first_name,
                interactive=True,
            )
            prompt = gr.Textbox(label="プロンプト", lines=5, value=first_prompt)

        with gr.Row():
            max_tokens = gr.Slider(label="max_tokens", minimum=1, maximum=4096, step=1, value=256)
            temperature = gr.Slider(label="temperature", minimum=0.0, maximum=2.0, step=0.1, value=0.2)
            top_p = gr.Slider(label="top_p", minimum=0.0, maximum=1.0, step=0.05, value=0.9)

        with gr.Row():
            run_btn = gr.Button("実行", variant="primary")
            test_btn = gr.Button("テスト")

        model_response = gr.Textbox(label="モデル応答", lines=10)
        log_output = gr.Textbox(label="ログ表示", lines=12)

        fetch_models_btn.click(
            fn=model_fetch_handler,
            inputs=[endpoint],
            outputs=[model_dropdown, model_dropdown, log_output],
        )

        preview_btn.click(
            fn=preview_images,
            inputs=[folder_input],
            outputs=[gallery, log_output],
        )

        preset_dropdown.change(
            fn=lambda preset_name: preset_change_handler(preset_name, presets),
            inputs=[preset_dropdown],
            outputs=[prompt],
        )

        test_btn.click(
            fn=execute_test,
            inputs=[endpoint, model_dropdown, folder_input, prompt, max_tokens, temperature, top_p],
            outputs=[model_response, log_output],
        )

        run_btn.click(
            fn=execute_batch,
            inputs=[endpoint, model_dropdown, folder_input, prompt, max_tokens, temperature, top_p],
            outputs=[model_response, log_output],
        )

    return demo


def load_allowed_paths(path: Path) -> list[str]:
    """allowed_paths.json から allowed_paths を読み込む。

    概要:
        JSONファイルが存在し、形式が正しい場合に allowed_paths 配列を返す。
    引数:
        path: 設定JSONファイルパス。
    戻り値:
        読み込み済み allowed_paths 配列。未存在時は空配列。
    例外:
        ValueError: JSON形式不正または allowed_paths が文字列配列でない場合。
    使用例:
        >>> load_allowed_paths(Path("allowed_paths.json"))
    """

    if not path.exists():
        LOGGER.info("INFO: allowed_paths_json_not_found path=%s", path)
        return []

    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    values = payload.get("allowed_paths")
    if not isinstance(values, list) or not all(isinstance(x, str) for x in values):
        raise ValueError(
            f"allowed_paths.json の形式が不正です。path={path} expected={{\"allowed_paths\": [\"...\"]}}"
        )
    return values


def resolve_allowed_paths(allowed_paths_json: Path, allowed_paths_cli: list[str]) -> list[str]:
    """起動時の allowed_paths を統合する。

    概要:
        JSON由来とCLI由来の allowed_paths を結合し重複を除去して返す。
    引数:
        allowed_paths_json: JSON設定ファイルパス。
        allowed_paths_cli: CLI直接指定パス配列。
    戻り値:
        起動時に適用する allowed_paths 配列。
    例外:
        ValueError: JSON設定が不正な場合。
    使用例:
        >>> resolve_allowed_paths(Path("allowed_paths.json"), ["/tmp"])
    """

    merged: list[str] = []
    for item in [*load_allowed_paths(allowed_paths_json), *allowed_paths_cli]:
        candidate = item.strip()
        if candidate and candidate not in merged:
            merged.append(candidate)
    return merged


def launch(allowed_paths_json: Path = Path("allowed_paths.json"), allowed_paths_cli: list[str] | None = None) -> None:
    """Gradioアプリを起動する。

    概要:
        ローカル環境向けにcaptifyアプリを起動する。
    引数:
        allowed_paths_json: allowed_paths 設定JSONファイルパス。
        allowed_paths_cli: 起動オプションで直接指定された allowed_paths。
    戻り値:
        なし。
    例外:
        ValueError: allowed_paths 設定が不正な場合。
        Exception: 起動失敗時。
    使用例:
        >>> launch()
    """

    setup_logging()
    paths_from_cli = allowed_paths_cli or []
    allowed_paths = resolve_allowed_paths(
        allowed_paths_json=allowed_paths_json,
        allowed_paths_cli=paths_from_cli,
    )
    LOGGER.info("INFO: gradio_allowed_paths count=%s values=%s", len(allowed_paths), allowed_paths)

    app = build_app()
    app.launch(server_name="0.0.0.0", server_port=7860, allowed_paths=allowed_paths)


if __name__ == "__main__":
    launch()
