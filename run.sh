#!/bin/bash
# 毎日のスクリーニングを1コマンドで実行するショートカット。
# 使い方:  ./run.sh   （初回だけ chmod +x run.sh が必要）
cd "$(dirname "$0")" || exit 1
source .venv/bin/activate
python screen.py
