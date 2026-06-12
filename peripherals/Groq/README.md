# Groq API ライブラリ

シンプルなGroq APIラッパー。AIプロジェクトへの組み込み用。
GPT-OSS 120Bを含む最新のGroqモデルをサポート。

## セットアップ

```bash
# プロジェクトルートで仮想環境を作成・有効化
cd /path/to/VEXIS-CLI-2.2
source venv/bin/activate

# Groq依存関係をインストール
pip install groq

# APIキーを設定
export GROQ_API_KEY='your_api_key'
```

## 初期選択 (Initial Selection)

このプログラムを実行すると、最初にプロバイダーの選択が求められます。

**利用可能なプロバイダー:**
1. **Ollama** - ローカル実行向け
2. **Google** - Google AIモデル 
3. **Groq** - 高速推論API (現在の実装)

> **注:** これは「初期選択」設定です。将来的には選択したプロバイダーに応じて適切なAPIが呼び出される予定です。

## 使用方法

### 基本使用

```python
from groq_hello import ask_groq

# 基本使用 (初期選択プロンプトが表示されます)
response = ask_groq("Hello")
print(response)
```

### モデル指定

```python
# 利用可能なモデル
models = [
    "llama-3.1-8b-instant",      # Meta Llama 3.1 8B
    "llama-3.3-70b-versatile",   # Meta Llama 3.3 70B  
    "openai/gpt-oss-120b",       # OpenAI GPT-OSS 120B (推奨)
    "openai/gpt-oss-20b",        # OpenAI GPT-OSS 20B
    "whisper-large-v3",          # 音声認識
    "whisper-large-v3-turbo"     # 音声認識 (高速)
]

# モデル指定
response = ask_groq("日本語で説明して", model="openai/gpt-oss-120b")
```

### モデル選択

モデルは以下のように整理されています：

#### **Production Models (推奨)**
- **Meta**: Llama 3.1 8B Instant, Llama 3.3 70B Versatile
- **OpenAI**: GPT-OSS 120B, GPT-OSS 20B (ブラウザ検索・コード実行対応)
- **Audio**: Whisper Large V3, Whisper Large V3 Turbo

#### **Preview Models (評価用)**
- **Meta**: Llama 4 Scout 17B, Prompt Guard 2
- **Moonshot AI**: Kimi K2 Instruct  
- **Alibaba**: Qwen 3 32B
- **Canopy Labs**: Orpheus (アラビア語・英語)

## モデル機能

### GPT-OSS 120Bの特徴
- 120Bパラメータ
- 組み込みブラウザ検索
- コード実行機能
- 高度な推論能力
- ツール使用対応

### Llama 3.3 70B Versatileの特徴  
- 70Bパラメータ
- 128Kコンテキスト
- 高い応答速度
- 多目的対応

## 実行

```bash
# プロジェクトルートの仮想環境を使用
cd /path/to/VEXIS-CLI-2.2
source venv/bin/activate && cd Groq
python groq_hello.py
```

## モデル定義

`groq_models.py`には全モデルの詳細な定義が含まれています：

```python
from groq_models import GROQ_MODEL_FAMILIES, get_production_models
from groq_models import get_models_by_capability, get_model_info

# 生産モデル一覧
production = get_production_models()

# ツール対応モデル
tools_models = get_models_by_capability("tools")

# モデル詳細情報
info = get_model_info("openai/gpt-oss-120b")
```

## APIキー取得

1. [Groq Console](https://console.groq.com/) にアクセス
2. アカウント登録・ログイン
3. API Keysセクションでキーを生成
4. 環境変数に設定: `export GROQ_API_KEY='your_key'`

## 注意事項

- **Productionモデル**: 本番環境での使用を推奨
- **Previewモデル**: 評価目的のみ、予告なしに終了される可能性あり
- **初期選択**: 現在はGroq APIのみ実装。将来的には選択プロバイダーに応じたルーティングを予定
