# AGENTS.md

## 実行環境（必須）
- 仮想環境は **venv** を使用する
- グローバル環境への直接インストールは禁止

推奨手順例：
```
python3.13 -m venv venv
source venv/bin/activate  # Windowsは venv\Scripts\activate
pip install -r requirements.txt
```

---

## 参照ルール（最重要）
- 要件定義・仕様はすべて `docs/spec.md` を唯一の正（Single Source of Truth）とする。
- 実装判断に迷いが出た場合は、勝手に補完せず `docs/spec.md` に従う。
- `docs/spec.md` に不足がある場合は、推測で実装を進めず、仕様の追記を提案する。

---

## 作業規約（Codexの進め方）
- 変更は意味のある単位でコミットしやすい構成にする。
- 例外やエラーは握りつぶさず、UIで分かる形に出す。
- 依存関係は最小化し、`requirements.txt` を明確にする。

---

## コーディング規約（必須）

### 1) 日本語 docstring を必ず付与
- クラス・関数は必ず docstring を持つ。
- 概要 / 引数 / 戻り値 / 例外 / 使用例 を含める。

### 2) 型ヒント必須

### 3) 例外処理必須
- パス、モデル名、ファイルI/O、画像破損、推論失敗、VRAM不足を防御する。
- 失敗時はUIにエラー表示する。

### 4) ログ
- INFO / WARNING / ERROR を適切に出力する。

---

## 実装時の禁止事項
- `docs/spec.md` にない要件を追加しない。

