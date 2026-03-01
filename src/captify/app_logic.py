"""captify 主機能1ロジック。"""

from __future__ import annotations

import base64
import io
import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator, Iterable

import httpx
from PIL import Image, UnidentifiedImageError

LOGGER = logging.getLogger("captify")

SUPPORTED_EXTENSIONS: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
DEFAULT_ENDPOINT: str = "http://127.0.0.1:1234"
PRESET_PATH: Path = Path("presets.json")
DEFAULT_MAX_IMAGE_WIDTH: int = 1280
DEFAULT_MAX_IMAGE_PIXELS: int = DEFAULT_MAX_IMAGE_WIDTH * DEFAULT_MAX_IMAGE_WIDTH

DEFAULT_PRESETS: dict[str, str] = {
    "事実ベース（簡潔）": "画像の内容を事実ベースで簡潔に説明してください。",
    "商品説明（EC向け）": "ECサイトの商品説明として、特徴・用途・魅力が伝わる自然な説明文を作成してください。",
    "SNS向け（自然文）": "SNS投稿向けに、自然で読みやすい説明文を作成してください。",
}


@dataclass(frozen=True)
class CaptifyError(Exception):
    """captify用の業務エラーを表す。

    概要:
        UI表示およびログ表示に必要なエラー情報を一元管理する。
    引数:
        error_type: 仕様で定義されたエラー分類名。
        message: UI向けエラーメッセージ。
        status_code: HTTPステータスコード。未取得時はNone。
        model_name: 対象モデル名。未確定時はNone。
        image_path: 対象画像パス。対象なしの場合はNone。
    戻り値:
        なし。
    例外:
        Exception: ベース例外として扱う。
    使用例:
        >>> raise CaptifyError("timeout", "ERROR: タイムアウト", None, "model-a", "a.jpg")
    """

    error_type: str
    message: str
    status_code: int | None = None
    model_name: str | None = None
    image_path: str | None = None


@dataclass(frozen=True)
class RunResult:
    """推論結果を保持するデータ。

    概要:
        推論後のテキストとストリーミング中間表示をまとめて扱う。
    引数:
        final_text: 最終確定テキスト。
        stream_text: ストリーミング中に結合したテキスト。
    戻り値:
        なし。
    例外:
        なし。
    使用例:
        >>> RunResult(final_text="hello", stream_text="hello")
    """

    final_text: str
    stream_text: str


def setup_logging() -> None:
    """ロギングを初期化する。

    概要:
        INFO/WARNING/ERROR を標準出力へ出す設定を行う。
    引数:
        なし。
    戻り値:
        なし。
    例外:
        なし。
    使用例:
        >>> setup_logging()
    """

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def ensure_presets_file() -> dict[str, str]:
    """プリセットJSONを読み込み、未作成時は初期生成する。

    概要:
        ユーザー編集可能なJSONファイルを保証し、内容を返す。
    引数:
        なし。
    戻り値:
        プリセット名と本文の辞書。
    例外:
        CaptifyError: JSON読み込みまたは保存に失敗した場合。
    使用例:
        >>> presets = ensure_presets_file()
    """

    if not PRESET_PATH.exists():
        try:
            PRESET_PATH.write_text(
                json.dumps(DEFAULT_PRESETS, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            LOGGER.info("INFO: created default presets file path=%s", PRESET_PATH)
        except OSError as error:
            raise CaptifyError(
                error_type="file_read_error",
                message=f"ERROR: プリセットファイルを作成できません。path={PRESET_PATH}",
            ) from error

    try:
        raw = PRESET_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise CaptifyError(
            error_type="file_read_error",
            message=f"ERROR: プリセットファイルを読み込めません。path={PRESET_PATH}",
        ) from error

    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise CaptifyError(
            error_type="file_read_error",
            message=f"ERROR: プリセット形式が不正です。path={PRESET_PATH}",
        )
    return data


def save_presets(presets: dict[str, str]) -> None:
    """プリセット辞書をJSONへ保存する。

    概要:
        プリセット情報を `presets.json` へ永続化する。
    引数:
        presets: 保存対象のプリセット辞書。
    戻り値:
        なし。
    例外:
        CaptifyError: 保存失敗時。
    使用例:
        >>> save_presets({"事実ベース（簡潔）": "説明"})
    """

    try:
        PRESET_PATH.write_text(
            json.dumps(presets, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as error:
        raise CaptifyError(
            error_type="file_read_error",
            message=f"ERROR: プリセットファイルを保存できません。path={PRESET_PATH}",
        ) from error


def validate_input_folder(folder: str) -> Path:
    """入力フォルダを検証する。

    概要:
        仕様の入力フォルダ検証ルールに従いエラーを分類する。
    引数:
        folder: 検証対象のフォルダパス。
    戻り値:
        検証済みフォルダパス。
    例外:
        CaptifyError: 入力フォルダ検証に失敗した場合。
    使用例:
        >>> validate_input_folder("./images")
    """

    candidate: str = folder.strip()
    if not candidate:
        raise CaptifyError(
            error_type="folder_not_specified",
            message=f"ERROR: 入力フォルダが未指定です。folder={candidate}",
        )

    path = Path(candidate)
    if not path.exists():
        raise CaptifyError(
            error_type="folder_not_found",
            message=f"ERROR: 入力フォルダが存在しません。folder={candidate}",
        )
    if not path.is_dir():
        raise CaptifyError(
            error_type="not_a_directory",
            message=f"ERROR: 入力パスがディレクトリではありません。folder={candidate}",
        )
    try:
        _ = path.stat()
    except OSError as error:
        raise CaptifyError(
            error_type="permission_denied",
            message=f"ERROR: 入力フォルダにアクセスできません。folder={candidate}",
        ) from error
    return path


def list_target_images(folder: Path) -> list[Path]:
    """対象画像を再帰列挙しフルパス昇順で返す。

    概要:
        指定拡張子の画像のみ抽出し、再現性のある順序で返す。
    引数:
        folder: 入力フォルダ。
    戻り値:
        対象画像パス一覧。
    例外:
        CaptifyError: ディレクトリ探索に失敗した場合。
    使用例:
        >>> list_target_images(Path("./images"))
    """

    try:
        items = [
            p.resolve()
            for p in folder.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
    except OSError as error:
        raise CaptifyError(
            error_type="permission_denied",
            message=f"ERROR: 入力フォルダにアクセスできません。folder={folder}",
        ) from error
    return sorted(items, key=lambda p: str(p))


def _calc_resize_dimensions(width: int, height: int, max_image_width: int, max_image_pixels: int) -> tuple[int, int]:
    """仕様準拠のリサイズ後サイズを算出する。

    概要:
        最大幅と最大画素数の両条件を満たす縮小サイズを返す。
    引数:
        width: 元画像の幅。
        height: 元画像の高さ。
        max_image_width: 許容する最大幅。
        max_image_pixels: 許容する最大画素数。
    戻り値:
        リサイズ後の幅・高さ。
    例外:
        ValueError: 幅または高さが1未満の場合。
    使用例:
        >>> _calc_resize_dimensions(4096, 3072, 1280, 1638400)
    """

    if width < 1 or height < 1:
        raise ValueError("width/height は1以上である必要があります。")
    if max_image_width < 1 or max_image_pixels < 1:
        raise ValueError("max_image_width/max_image_pixels は1以上である必要があります。")

    pixels = width * height
    width_ratio = min(1.0, max_image_width / width)
    pixel_ratio = min(1.0, (max_image_pixels / pixels) ** 0.5)
    scale = min(width_ratio, pixel_ratio)

    if scale >= 1.0:
        return width, height

    resized_width = max(1, int(width * scale))
    resized_height = max(1, int(height * scale))
    return resized_width, resized_height


def to_data_url(image_path: Path, max_image_width: int, max_image_pixels: int) -> str:
    """画像ファイルをbase64 data URLへ変換する。

    概要:
        画像破損を検出し、必要に応じて縮小リサイズしたうえでAPI送信可能なdata URLを返す。
    引数:
        image_path: 変換対象の画像パス。
        max_image_width: 許容する最大幅。
        max_image_pixels: 許容する最大画素数。
    戻り値:
        data URL文字列。
    例外:
        CaptifyError: ファイル読み込み失敗または破損画像の場合。
    使用例:
        >>> to_data_url(Path("a.jpg"), 1280, 1638400)
    """

    suffix = image_path.suffix.lower().lstrip(".")
    mime = "jpeg" if suffix in {"jpg", "jpeg"} else suffix

    try:
        with Image.open(image_path) as img:
            img.verify()

        with Image.open(image_path) as img:
            source_width, source_height = img.size
            resized_width, resized_height = _calc_resize_dimensions(
                source_width,
                source_height,
                max_image_width=max_image_width,
                max_image_pixels=max_image_pixels,
            )

            if (resized_width, resized_height) != (source_width, source_height):
                LOGGER.info(
                    "INFO: image_resized path=%s from=%sx%s to=%sx%s",
                    image_path,
                    source_width,
                    source_height,
                    resized_width,
                    resized_height,
                )
                img = img.resize((resized_width, resized_height), Image.Resampling.LANCZOS)
            else:
                LOGGER.info(
                    "INFO: image_resize_skipped path=%s size=%sx%s",
                    image_path,
                    source_width,
                    source_height,
                )

            if mime == "jpeg" and img.mode in {"RGBA", "LA", "P"}:
                img = img.convert("RGB")

            format_name = {"jpeg": "JPEG", "png": "PNG", "webp": "WEBP", "bmp": "BMP"}.get(mime, "PNG")
            buffer = io.BytesIO()
            img.save(buffer, format=format_name)
            binary = buffer.getvalue()
    except UnidentifiedImageError as error:
        raise CaptifyError(
            error_type="corrupt_image",
            message=f"WARNING: 破損画像のためスキップします。file={image_path}",
            image_path=str(image_path),
        ) from error
    except OSError as error:
        raise CaptifyError(
            error_type="file_read_error",
            message=f"WARNING: 画像読み込みに失敗したためスキップします。file={image_path}",
            image_path=str(image_path),
        ) from error

    encoded = base64.b64encode(binary).decode("ascii")
    return f"data:image/{mime};base64,{encoded}"




def validate_resize_limits(max_image_width: int | float, max_image_pixels: int | float) -> tuple[int, int]:
    """画像リサイズ上限値を検証する。

    概要:
        UI入力された最大幅・最大画素数を整数に正規化し、妥当性を確認する。
    引数:
        max_image_width: 最大幅。
        max_image_pixels: 最大画素数。
    戻り値:
        検証済みの (最大幅, 最大画素数)。
    例外:
        CaptifyError: 数値変換不可または1未満の値が指定された場合。
    使用例:
        >>> validate_resize_limits(1280, 1638400)
    """

    try:
        normalized_width = int(max_image_width)
        normalized_pixels = int(max_image_pixels)
    except (TypeError, ValueError) as error:
        raise CaptifyError(
            error_type="invalid_resize_limit",
            message=(
                "ERROR: 画像リサイズ上限が不正です。"
                f"max_image_width={max_image_width} max_image_pixels={max_image_pixels}"
            ),
        ) from error

    if normalized_width < 1 or normalized_pixels < 1:
        raise CaptifyError(
            error_type="invalid_resize_limit",
            message=(
                "ERROR: 画像リサイズ上限が不正です。"
                f"max_image_width={normalized_width} max_image_pixels={normalized_pixels}"
            ),
        )
    return normalized_width, normalized_pixels
def _format_skip_log(error: CaptifyError) -> str:
    """仕様準拠のSKIPログ文を生成する。

    概要:
        error_type/status/model/file の必須項目を埋め込んだ文を作る。
    引数:
        error: ログ対象のエラー。
    戻り値:
        SKIPログ文字列。
    例外:
        なし。
    使用例:
        >>> _format_skip_log(CaptifyError("empty_text", "", None, None, None))
    """

    status_str = str(error.status_code) if error.status_code is not None else "none"
    model_str = error.model_name if error.model_name else "none"
    file_str = error.image_path if error.image_path else "none"
    return (
        f"SKIP: error_type={error.error_type} status={status_str} "
        f"model={model_str} file={file_str}"
    )


def fetch_models(endpoint: str) -> list[str]:
    """モデル一覧を取得する。

    概要:
        `/v1/models` を再試行付きで呼び出し、モデルID配列を返す。
    引数:
        endpoint: APIエンドポイント。
    戻り値:
        モデル名一覧。
    例外:
        CaptifyError: 再試行上限到達時。
    使用例:
        >>> fetch_models("http://127.0.0.1:1234")
    """

    url = endpoint.rstrip("/") + "/v1/models"
    last_error: CaptifyError | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(url)
            if response.status_code < 200 or response.status_code >= 300:
                raise CaptifyError(
                    error_type="http_error",
                    message=(
                        f"ERROR: モデル一覧の取得に失敗しました。endpoint={endpoint} retries=2 wait=2s"
                    ),
                    status_code=response.status_code,
                )
            payload = response.json()
            data = payload.get("data", [])
            models = [item.get("id", "") for item in data if isinstance(item, dict)]
            models = [m for m in models if m]
            if not models:
                raise CaptifyError(
                    error_type="http_error",
                    message=(
                        f"ERROR: モデル一覧の取得に失敗しました。endpoint={endpoint} retries=2 wait=2s"
                    ),
                    status_code=response.status_code,
                )
            LOGGER.info("INFO: models_fetched endpoint=%s count=%s", endpoint, len(models))
            return models
        except httpx.TimeoutException as error:
            last_error = CaptifyError(
                error_type="timeout",
                message=(
                    f"ERROR: モデル一覧の取得に失敗しました。endpoint={endpoint} retries=2 wait=2s"
                ),
            )
            LOGGER.error("%s detail=%s", _format_skip_log(last_error), error)
        except httpx.ConnectError as error:
            last_error = CaptifyError(
                error_type="connection_error",
                message=(
                    f"ERROR: モデル一覧の取得に失敗しました。endpoint={endpoint} retries=2 wait=2s"
                ),
            )
            LOGGER.error("%s detail=%s", _format_skip_log(last_error), error)
        except CaptifyError as error:
            last_error = error
            LOGGER.error(_format_skip_log(error))
        except ValueError as error:
            last_error = CaptifyError(
                error_type="http_error",
                message=(
                    f"ERROR: モデル一覧の取得に失敗しました。endpoint={endpoint} retries=2 wait=2s"
                ),
            )
            LOGGER.error("%s detail=%s", _format_skip_log(last_error), error)
        except httpx.HTTPError as error:
            last_error = CaptifyError(
                error_type="http_error",
                message=(
                    f"ERROR: モデル一覧の取得に失敗しました。endpoint={endpoint} retries=2 wait=2s"
                ),
            )
            LOGGER.error("%s detail=%s", _format_skip_log(last_error), error)

        if attempt < 2:
            time.sleep(2)

    if last_error is None:
        last_error = CaptifyError(
            error_type="http_error",
            message=f"ERROR: モデル一覧の取得に失敗しました。endpoint={endpoint} retries=2 wait=2s",
        )
    raise last_error


def _build_messages(prompt: str, data_url: str) -> list[dict[str, Any]]:
    """マルチモーダルメッセージを構築する。

    概要:
        OpenAI互換の `messages[].content` 形式を返す。
    引数:
        prompt: ユーザープロンプト。
        data_url: 画像data URL。
    戻り値:
        リクエストメッセージ配列。
    例外:
        なし。
    使用例:
        >>> _build_messages("説明", "data:image/jpeg;base64,AAA")
    """

    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]


def stream_caption(
    endpoint: str,
    model_name: str,
    prompt: str,
    image_path: Path,
    max_tokens: int,
    temperature: float,
    top_p: float,
    stream_enabled: bool,
    max_image_width: int,
    max_image_pixels: int,
) -> Generator[str, None, RunResult]:
    """画像1件のキャプション生成をストリーミング実行する。

    概要:
        `/v1/chat/completions` を呼び、設定に応じてストリーミングまたは通常応答で処理する。
    引数:
        endpoint: APIエンドポイント。
        model_name: 使用モデル名。
        prompt: プロンプト本文。
        image_path: 対象画像パス。
        max_tokens: 最大トークン設定値。
        temperature: 温度パラメータ。
        top_p: top_pパラメータ。
        stream_enabled: ストリーミング表示を有効化するか。
        max_image_width: 画像リサイズ時の最大幅。
        max_image_pixels: 画像リサイズ時の最大画素数。
    戻り値:
        yield: 逐次テキスト断片。
        return: 確定結果。
    例外:
        CaptifyError: 通信失敗・空応答時。
    使用例:
        >>> gen = stream_caption("http://127.0.0.1:1234", "model", "説明", Path("a.jpg"), 256, 0.2, 0.9, True, 1280, 1638400)
    """

    url = endpoint.rstrip("/") + "/v1/chat/completions"
    data_url = to_data_url(image_path, max_image_width=max_image_width, max_image_pixels=max_image_pixels)
    payload = {
        "model": model_name,
        "messages": _build_messages(prompt=prompt, data_url=data_url),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "stream": stream_enabled,
    }

    last_error: CaptifyError | None = None
    for attempt in range(3):
        pieces: list[str] = []
        try:
            with httpx.Client(timeout=60.0) as client:
                if stream_enabled:
                    with client.stream("POST", url, json=payload) as response:
                        if response.status_code < 200 or response.status_code >= 300:
                            raise CaptifyError(
                                error_type="http_error",
                                message="ERROR: 推論に失敗しました。",
                                status_code=response.status_code,
                                model_name=model_name,
                                image_path=str(image_path),
                            )

                        for raw in response.iter_lines():
                            line = raw.strip() if raw else ""
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            if not data:
                                continue
                            try:
                                chunk = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            choices = chunk.get("choices", [])
                            if not choices:
                                continue
                            delta = choices[0].get("delta", {})
                            text = delta.get("content")
                            if isinstance(text, str) and text:
                                pieces.append(text)
                                yield "".join(pieces)
                else:
                    response = client.post(url, json=payload)
                    if response.status_code < 200 or response.status_code >= 300:
                        raise CaptifyError(
                            error_type="http_error",
                            message="ERROR: 推論に失敗しました。",
                            status_code=response.status_code,
                            model_name=model_name,
                            image_path=str(image_path),
                        )
                    body = response.json()
                    choices = body.get("choices", [])
                    if choices:
                        message = choices[0].get("message", {})
                        text = message.get("content")
                        if isinstance(text, str) and text:
                            pieces.append(text)
                            yield "".join(pieces)

            final_text = "".join(pieces).strip()
            if not final_text:
                raise CaptifyError(
                    error_type="empty_text",
                    message="ERROR: モデル応答が空のためスキップしました。",
                    model_name=model_name,
                    image_path=str(image_path),
                )
            return RunResult(final_text=final_text, stream_text="".join(pieces))
        except httpx.TimeoutException as error:
            last_error = CaptifyError(
                error_type="timeout",
                message="ERROR: 推論に失敗しました。",
                model_name=model_name,
                image_path=str(image_path),
            )
            LOGGER.error("%s detail=%s", _format_skip_log(last_error), error)
        except httpx.ConnectError as error:
            last_error = CaptifyError(
                error_type="connection_error",
                message="ERROR: 推論に失敗しました。",
                model_name=model_name,
                image_path=str(image_path),
            )
            LOGGER.error("%s detail=%s", _format_skip_log(last_error), error)
        except CaptifyError as error:
            if error.error_type == "empty_text":
                LOGGER.warning(_format_skip_log(error))
                raise error
            last_error = error
            LOGGER.error(_format_skip_log(error))
        except httpx.HTTPError as error:
            last_error = CaptifyError(
                error_type="http_error",
                message="ERROR: 推論に失敗しました。",
                model_name=model_name,
                image_path=str(image_path),
            )
            LOGGER.error("%s detail=%s", _format_skip_log(last_error), error)

        if attempt < 2:
            time.sleep(2)

    if last_error is None:
        last_error = CaptifyError(
            error_type="http_error",
            message="ERROR: 推論に失敗しました。",
            model_name=model_name,
            image_path=str(image_path),
        )
    raise last_error


def next_backup_path(txt_path: Path) -> Path:
    """バックアップ保存先パスを採番して返す。

    概要:
        `basename.NNN` 形式の最大番号を調べ、次番号を返す。
    引数:
        txt_path: 元txtファイルパス。
    戻り値:
        次バックアップ先パス。
    例外:
        CaptifyError: 採番上限999を超える場合。
    使用例:
        >>> next_backup_path(Path("image01.txt"))
    """

    parent = txt_path.parent
    stem = txt_path.stem
    max_no = 0
    for item in parent.iterdir():
        if not item.is_file() or item.stem != stem:
            continue
        if item.suffix and len(item.suffix) == 4 and item.suffix[1:].isdigit():
            max_no = max(max_no, int(item.suffix[1:]))

    next_no = max_no + 1
    if next_no > 999:
        raise CaptifyError(
            error_type="backup_limit_exceeded",
            message="ERROR: バックアップ上限(999)に到達したため保存できません。",
            image_path=str(txt_path),
        )
    return parent / f"{stem}.{next_no:03d}"


def save_caption(image_path: Path, text: str) -> None:
    """キャプションテキストを保存する。

    概要:
        既存txtがある場合はバックアップ作成後に上書き保存する。
    引数:
        image_path: 対象画像パス。
        text: 保存テキスト。
    戻り値:
        なし。
    例外:
        CaptifyError: ファイル保存またはバックアップに失敗した場合。
    使用例:
        >>> save_caption(Path("a.jpg"), "caption")
    """

    txt_path = image_path.with_suffix(".txt")
    try:
        if txt_path.exists():
            backup_path = next_backup_path(txt_path)
            shutil.copy2(txt_path, backup_path)
            LOGGER.info("INFO: backup_created src=%s dst=%s", txt_path, backup_path)
        txt_path.write_text(text, encoding="utf-8")
        LOGGER.info("INFO: caption_saved path=%s", txt_path)
    except CaptifyError:
        raise
    except OSError as error:
        raise CaptifyError(
            error_type="file_read_error",
            message=f"ERROR: ファイル保存に失敗しました。file={txt_path}",
            image_path=str(image_path),
        ) from error


def _run_single(
    endpoint: str,
    model_name: str,
    prompt: str,
    image_path: Path,
    max_tokens: int,
    temperature: float,
    top_p: float,
    stream_enabled: bool,
    max_image_width: int,
    max_image_pixels: int,
) -> Generator[str, None, RunResult]:
    """単一画像処理を実行する。

    概要:
        ストリーミング実行関数をラップして結果を返す。
    引数:
        endpoint: APIエンドポイント。
        model_name: モデル名。
        prompt: プロンプト。
        image_path: 画像パス。
        max_tokens: max_tokens。
        temperature: temperature。
        top_p: top_p。
        stream_enabled: ストリーミング表示有効フラグ。
        max_image_width: 画像リサイズ時の最大幅。
        max_image_pixels: 画像リサイズ時の最大画素数。
    戻り値:
        yield: モデル応答中間テキスト。
        return: 確定結果。
    例外:
        CaptifyError: 推論失敗時。
    使用例:
        >>> pass
    """

    gen = stream_caption(
        endpoint=endpoint,
        model_name=model_name,
        prompt=prompt,
        image_path=image_path,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        stream_enabled=stream_enabled,
        max_image_width=max_image_width,
        max_image_pixels=max_image_pixels,
    )
    while True:
        try:
            partial = next(gen)
            yield partial
        except StopIteration as stop:
            return stop.value


def _append_log(logs: list[str], line: str, level: str = "INFO") -> str:
    """UIログ一覧へ1行追記する。

    概要:
        画面表示用ログ配列へ追記し、結合文字列を返す。
    引数:
        logs: 既存ログ配列。
        line: 追記行。
        level: ログレベル。
    戻り値:
        結合済みログ文字列。
    例外:
        なし。
    使用例:
        >>> _append_log([], "INFO: start")
    """

    logs.append(line)
    if level == "ERROR":
        LOGGER.error(line)
    elif level == "WARNING":
        LOGGER.warning(line)
    else:
        LOGGER.info(line)
    return "\n".join(logs)


def preview_images(folder: str) -> tuple[list[str], str]:
    """画像プレビュー情報を返す。

    概要:
        入力フォルダを検証し、プレビュー用画像一覧とログを生成する。
    引数:
        folder: 入力フォルダ文字列。
    戻り値:
        (画像パス配列, ログ文字列)。
    例外:
        なし。内部でハンドリングしてUI向け文言を返す。
    使用例:
        >>> preview_images("./images")
    """

    import gradio as gr

    logs: list[str] = []
    try:
        path = validate_input_folder(folder)
        targets = list_target_images(path)
        if not targets:
            line = (
                f"WARNING: no_target_images folder={path} count=0 action=stop_without_save"
            )
            return [], _append_log(logs, line, level="WARNING")
        line = f"INFO: preview_loaded folder={path} count={len(targets)}"
        return [str(p) for p in targets], _append_log(logs, line)
    except CaptifyError as error:
        line = f"ERROR: input_folder_validation error_type={error.error_type} folder={folder}"
        return [], _append_log(logs, line, level="ERROR")


def execute_test(
    endpoint: str,
    model_name: str,
    folder: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    top_p: float,
    stream_enabled: bool,
    max_image_width: int,
    max_image_pixels: int,
) -> Generator[tuple[str, str], None, None]:
    """テストボタン処理を実行する。

    概要:
        先頭1画像のみ推論し、保存なしでモデル応答とログを返す。
    引数:
        endpoint: APIエンドポイント。
        model_name: モデル名。
        folder: 入力フォルダ。
        prompt: プロンプト。
        max_tokens: max_tokens。
        temperature: temperature。
        top_p: top_p。
        stream_enabled: ストリーミング表示有効フラグ。
        max_image_width: 画像リサイズ時の最大幅。
        max_image_pixels: 画像リサイズ時の最大画素数。
    戻り値:
        yield: (モデル応答, ログ文字列)。
    例外:
        なし。内部でハンドリングしUIに表示する。
    使用例:
        >>> pass
    """

    import gradio as gr

    logs: list[str] = []
    if not model_name:
        line = "ERROR: モデルが未選択です。先にモデル取得を実行してください。"
        yield "", _append_log(logs, line, level="ERROR")
        return

    try:
        max_image_width, max_image_pixels = validate_resize_limits(
            max_image_width=max_image_width,
            max_image_pixels=max_image_pixels,
        )
        path = validate_input_folder(folder)
    except CaptifyError as error:
        line = (
            error.message
            if error.error_type == "invalid_resize_limit"
            else f"ERROR: input_folder_validation error_type={error.error_type} folder={folder}"
        )
        yield "", _append_log(logs, line, level="ERROR")
        return

    images = list_target_images(path)
    if not images:
        skip = (
            f"TEST SKIPPED: 対象画像が0件のためテストを実行しません。folder={path} count=0"
        )
        yield "", _append_log(logs, skip, level="WARNING")
        return

    image = images[0]
    try:
        runner = _run_single(
            endpoint=endpoint,
            model_name=model_name,
            prompt=prompt,
            image_path=image,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            stream_enabled=stream_enabled,
            max_image_width=max_image_width,
            max_image_pixels=max_image_pixels,
        )
        while True:
            try:
                partial = next(runner)
                yield partial, "\n".join(logs)
            except StopIteration as stop:
                result = stop.value
                done = f"INFO: TEST SUCCESS file={image}"
                yield result.final_text, _append_log(logs, done)
                return
    except CaptifyError as error:
        status_text = str(error.status_code) if error.status_code is not None else "none"
        failed = f"TEST FAILED: {error.error_type} status={status_text} model={model_name}"
        yield failed, _append_log(logs, failed, level="ERROR")


def execute_batch(
    endpoint: str,
    model_name: str,
    folder: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    top_p: float,
    stream_enabled: bool,
    max_image_width: int,
    max_image_pixels: int,
) -> Generator[tuple[str, str], None, None]:
    """実行ボタン処理を実行する。

    概要:
        対象画像を再帰処理し、推論結果をtxt保存する。
    引数:
        endpoint: APIエンドポイント。
        model_name: モデル名。
        folder: 入力フォルダ。
        prompt: プロンプト。
        max_tokens: max_tokens。
        temperature: temperature。
        top_p: top_p。
        stream_enabled: ストリーミング表示有効フラグ。
        max_image_width: 画像リサイズ時の最大幅。
        max_image_pixels: 画像リサイズ時の最大画素数。
    戻り値:
        yield: (モデル応答, ログ文字列)。
    例外:
        なし。内部でハンドリングしUIに表示する。
    使用例:
        >>> pass
    """

    logs: list[str] = []
    if not model_name:
        line = "ERROR: モデルが未選択です。先にモデル取得を実行してください。"
        yield "", _append_log(logs, line, level="ERROR")
        return

    try:
        max_image_width, max_image_pixels = validate_resize_limits(
            max_image_width=max_image_width,
            max_image_pixels=max_image_pixels,
        )
        path = validate_input_folder(folder)
    except CaptifyError as error:
        line = (
            error.message
            if error.error_type == "invalid_resize_limit"
            else f"ERROR: input_folder_validation error_type={error.error_type} folder={folder}"
        )
        yield "", _append_log(logs, line, level="ERROR")
        return

    images = list_target_images(path)
    if not images:
        ui_message = f"WARNING: 対象画像が見つかりませんでした。folder={path} count=0"
        _append_log(
            logs,
            f"WARNING: no_target_images folder={path} count=0 action=stop_without_save",
            level="WARNING",
        )
        yield ui_message, "\n".join(logs)
        return

    latest_response = ""
    for idx, image in enumerate(images, start=1):
        progress = f"INFO: processing index={idx}/{len(images)} file={image}"
        yield latest_response, _append_log(logs, progress)
        try:
            runner = _run_single(
                endpoint=endpoint,
                model_name=model_name,
                prompt=prompt,
                image_path=image,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stream_enabled=stream_enabled,
                max_image_width=max_image_width,
                max_image_pixels=max_image_pixels,
            )
            while True:
                try:
                    partial = next(runner)
                    latest_response = partial
                    yield latest_response, "\n".join(logs)
                except StopIteration as stop:
                    result = stop.value
                    latest_response = result.final_text
                    break
            save_caption(image, latest_response)
            yield latest_response, _append_log(logs, f"INFO: saved file={image.with_suffix('.txt')}")
        except CaptifyError as error:
            status_text = str(error.status_code) if error.status_code is not None else "none"
            skip_line = (
                f"SKIP: error_type={error.error_type} status={status_text} "
                f"model={model_name} file={image}"
            )
            level = "WARNING" if error.error_type in {"file_read_error", "corrupt_image", "empty_text"} else "ERROR"
            yield latest_response, _append_log(logs, skip_line, level=level)
            continue

    yield latest_response, _append_log(logs, "INFO: batch_completed")


def first_preset(presets: dict[str, str]) -> tuple[str, str]:
    """先頭プリセット名と本文を返す。

    概要:
        UI初期表示に利用する先頭項目を取り出す。
    引数:
        presets: プリセット辞書。
    戻り値:
        (先頭名, 先頭本文)。
    例外:
        CaptifyError: プリセット空の場合。
    使用例:
        >>> first_preset({"a": "b"})
    """

    if not presets:
        raise CaptifyError(
            error_type="file_read_error",
            message="ERROR: プリセットが空です。",
        )
    name = next(iter(presets.keys()))
    return name, presets[name]


def model_fetch_handler(endpoint: str) -> tuple[Any, str, str]:
    """UI向けモデル取得ハンドラー。

    概要:
        モデル一覧を取得し、ドロップダウン更新情報とログを返す。
    引数:
        endpoint: APIエンドポイント。
    戻り値:
        (gradio.Dropdown更新情報, 選択モデル名, ログ文字列)。
    例外:
        なし。内部でハンドリングする。
    使用例:
        >>> pass
    """

    import gradio as gr

    logs: list[str] = []
    try:
        models = fetch_models(endpoint)
        line = f"INFO: models_loaded endpoint={endpoint} count={len(models)}"
        log_text = _append_log(logs, line)
        return gr.update(choices=models, value=models[0], interactive=True), models[0], log_text
    except CaptifyError as error:
        status_text = str(error.status_code) if error.status_code is not None else "none"
        line = (
            "ERROR: モデル一覧の取得に失敗しました。"
            f"endpoint={endpoint} retries=2 wait=2s"
        )
        _append_log(
            logs,
            f"SKIP: error_type={error.error_type} status={status_text} model=none file=none",
            level="ERROR",
        )
        return gr.update(choices=[], value=None, interactive=False), "", _append_log(logs, line, level="ERROR")


def preset_change_handler(preset_name: str) -> str:
    """プリセット選択時に本文を返す。

    概要:
        プリセット名に紐づく本文をプロンプト欄へ反映する。
    引数:
        preset_name: 選択されたプリセット名。
    戻り値:
        プロンプト本文。
    例外:
        なし。
    使用例:
        >>> preset_change_handler("a")
    """

    try:
        presets = ensure_presets_file()
    except CaptifyError:
        return ""
    return presets.get(preset_name, "")


def add_preset_handler(
    preset_name_input: str,
    prompt_text: str,
    selected_preset_name: str,
) -> tuple[dict[str, Any], str, str, str]:
    """現在プロンプトを新規プリセットとして登録する。

    概要:
        入力されたプリセット名で `presets.json` へ新規追加し、UI更新値を返す。
    引数:
        preset_name_input: 追加するプリセット名入力値。
        prompt_text: 現在のプロンプト本文。
        selected_preset_name: 現在選択中のプリセット名。
    戻り値:
        (ドロップダウン更新情報, プロンプト本文, プリセット名入力欄, ログ文字列)。
    例外:
        なし。内部でハンドリングしてERRORを返す。
    使用例:
        >>> add_preset_handler("新規", "本文", "事実ベース（簡潔）")
    """

    import gradio as gr

    logs: list[str] = []
    name = preset_name_input.strip()
    if not name:
        line = "ERROR: プリセット名が未入力です。"
        try:
            presets = ensure_presets_file()
            current_prompt = presets.get(selected_preset_name, prompt_text)
            return gr.update(choices=list(presets.keys()), value=selected_preset_name), current_prompt, preset_name_input, _append_log(logs, line, level="ERROR")
        except CaptifyError as error:
            return gr.update(choices=[], value=None), prompt_text, preset_name_input, _append_log(logs, error.message, level="ERROR")

    try:
        presets = ensure_presets_file()
        if name in presets:
            line = f"ERROR: プリセット名が重複しています。name={name}"
            current_prompt = presets.get(selected_preset_name, prompt_text)
            return gr.update(choices=list(presets.keys()), value=selected_preset_name), current_prompt, preset_name_input, _append_log(logs, line, level="ERROR")

        presets[name] = prompt_text
        save_presets(presets)
        line = f"INFO: preset_added name={name}"
        return gr.update(choices=list(presets.keys()), value=name), prompt_text, "", _append_log(logs, line)
    except CaptifyError as error:
        line = error.message
        return gr.update(choices=[], value=None), prompt_text, preset_name_input, _append_log(logs, line, level="ERROR")


def update_preset_handler(
    selected_preset_name: str,
    prompt_text: str,
) -> tuple[dict[str, Any], str, str]:
    """選択中プリセットを現在プロンプトで更新する。

    概要:
        選択中プリセットの本文を現在のプロンプト本文で上書き保存する。
    引数:
        selected_preset_name: 現在選択中のプリセット名。
        prompt_text: 現在のプロンプト本文。
    戻り値:
        (ドロップダウン更新情報, プロンプト本文, ログ文字列)。
    例外:
        なし。内部でハンドリングしてERRORを返す。
    使用例:
        >>> update_preset_handler("事実ベース（簡潔）", "更新本文")
    """

    import gradio as gr

    logs: list[str] = []
    try:
        presets = ensure_presets_file()
        if not selected_preset_name or selected_preset_name not in presets:
            line = "ERROR: 更新対象のプリセットが選択されていません。"
            return gr.update(choices=list(presets.keys()), value=selected_preset_name), prompt_text, _append_log(logs, line, level="ERROR")

        presets[selected_preset_name] = prompt_text
        save_presets(presets)
        line = f"INFO: preset_updated name={selected_preset_name}"
        return gr.update(choices=list(presets.keys()), value=selected_preset_name), prompt_text, _append_log(logs, line)
    except CaptifyError as error:
        line = error.message
        return gr.update(choices=[], value=None), prompt_text, _append_log(logs, line, level="ERROR")


def delete_preset_handler(selected_preset_name: str) -> tuple[dict[str, Any], str, str]:
    """選択中プリセットを削除する。

    概要:
        選択中プリセットを削除し、残存先頭プリセットを選択状態として返す。
    引数:
        selected_preset_name: 現在選択中のプリセット名。
    戻り値:
        (ドロップダウン更新情報, プロンプト本文, ログ文字列)。
    例外:
        なし。内部でハンドリングしてERRORを返す。
    使用例:
        >>> delete_preset_handler("事実ベース（簡潔）")
    """

    import gradio as gr

    logs: list[str] = []
    try:
        presets = ensure_presets_file()
        if not selected_preset_name or selected_preset_name not in presets:
            line = "ERROR: 削除対象のプリセットが選択されていません。"
            fallback_name, fallback_prompt = first_preset(presets)
            return gr.update(choices=list(presets.keys()), value=fallback_name), fallback_prompt, _append_log(logs, line, level="ERROR")

        if len(presets) <= 1:
            line = "ERROR: プリセットが1件のため削除できません。"
            current_prompt = presets[selected_preset_name]
            return gr.update(choices=list(presets.keys()), value=selected_preset_name), current_prompt, _append_log(logs, line, level="ERROR")

        del presets[selected_preset_name]
        save_presets(presets)
        next_name, next_prompt = first_preset(presets)
        line = f"INFO: preset_deleted name={selected_preset_name}"
        return gr.update(choices=list(presets.keys()), value=next_name), next_prompt, _append_log(logs, line)
    except CaptifyError as error:
        line = error.message
        return gr.update(choices=[], value=None), "", _append_log(logs, line, level="ERROR")


def available_preset_names(presets: dict[str, str]) -> Iterable[str]:
    """プリセット名一覧を返す。

    概要:
        UIドロップダウン表示用にキー一覧を返す。
    引数:
        presets: プリセット辞書。
    戻り値:
        プリセット名の反復可能オブジェクト。
    例外:
        なし。
    使用例:
        >>> list(available_preset_names({"a": "b"}))
    """

    return presets.keys()
