#!/usr/bin/env bash
# 交互式设置 Gemini 密钥并重启服务。密钥只存在于本机进程环境中，
# 不写入任何文件、不进 git、不经过对话。
set -euo pipefail
cd "$(dirname "$0")"

echo "在 https://aistudio.google.com → API Keys 获取密钥"
echo "（输入时不显示字符，粘贴后直接回车）"
printf "Gemini API Key: "
read -rs GEMINI_API_KEY
echo
[ -z "$GEMINI_API_KEY" ] && { echo "未输入，已取消。"; exit 1; }
export GEMINI_API_KEY

: "${QUANT_API_KEY:=chainquant2026}"
export QUANT_API_KEY

pkill -f "uvicorn backend.api_server" 2>/dev/null || true
sleep 1
nohup uvicorn backend.api_server:app --host 127.0.0.1 --port 8000 > /tmp/cq_api.log 2>&1 &
sleep 4

echo
echo "=== 实测结果（Google 原始响应）==="
curl -s -H "X-API-Key: $QUANT_API_KEY" -X POST http://127.0.0.1:8000/api/gemini/probe \
 | python3 -c "
import json,sys
d=json.load(sys.stdin)
if not d.get('configured'):
    print('未检测到密钥'); raise SystemExit
for r in d['results']:
    print(f\"密钥 {r['alias']}  状态={r['status']}  延迟={r['latency_ms']}ms\")
    if r.get('tokens'): print(f\"  token 消耗: {r['tokens']}\")
    if r.get('detail'): print(f\"  Google 原文: {r['detail'][:600]}\")
print()
print(d['tier_hint'])
"
echo
echo "后端已在运行，面板: http://localhost:5173"
