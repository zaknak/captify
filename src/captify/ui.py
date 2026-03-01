"""captify Gradio UI 定義。"""

from __future__ import annotations

from typing import Any

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


def launch() -> None:
    """Gradioアプリを起動する。

    概要:
        ローカル環境向けにcaptifyアプリを起動する。
    引数:
        なし。
    戻り値:
        なし。
    例外:
        Exception: 起動失敗時。
    使用例:
        >>> launch()
    """

    app = build_app()
    app.launch(server_name="0.0.0.0", server_port=7860)


if __name__ == "__main__":
    launch()
