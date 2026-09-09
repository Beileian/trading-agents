#!/bin/bash
# ima_push_closing.sh — 将A股收盘复盘详情报告写入 IMA「王海明的知识库/投资理财/盘后复盘」
#
# 使用方式:
#   bash ima_push_closing.sh <复盘md文件路径>
#
# 基于 ima_push_decision_daily.sh 流程改造；目标为个人库「王海明的知识库」
# 子目录「投资理财(folder_7324706025772140)/盘后复盘(folder_7503596430040131)」。
# 带回读校验（P0-2）：add 后读回目录列表验证标题落位，验 KB_ID+目录+标题 三要素。

set -o pipefail

REPORT_FILE="$(cd "$(dirname "$1" 2>/dev/null)" 2>/dev/null && pwd 2>/dev/null)/$(basename "$1" 2>/dev/null)"
if [ "$1" = "" ] || [ ! -f "$REPORT_FILE" ]; then
  echo "[IMA_CLOSING] 错误: 文件不存在或未指定: $1" >&2
  exit 0
fi

SKILL_DIR="/root/.openclaw/workspace/skills/ima-skills"
CLIENT_ID_FILE="$HOME/.config/ima/client_id"
API_KEY_FILE="$HOME/.config/ima/api_key"

# 「王海明的知识库」(个人库; 2026-08-25 事故已实证个人库常量=p2U2...)
KB_ID="p2U2Du3TS2OyfEHx0JpUGTKQsZnE-eLmiUVedwnywEI="
KB_NAME="王海明的知识库/投资理财/盘后复盘"
# 目录: 知识库根 → 投资理财 → 盘后复盘 (定位实测 2026-09-10)
FOLDER_ID="folder_7503596430040131"

if [ ! -f "$CLIENT_ID_FILE" ] || [ ! -f "$API_KEY_FILE" ]; then
  echo "[IMA_CLOSING] IMA 凭证未配置，跳过" >&2
  exit 0
fi

CLIENT_ID=$(cat "$CLIENT_ID_FILE")
API_KEY=$(cat "$API_KEY_FILE")
OPTS=$(printf '{"clientId":"%s","apiKey":"%s"}' "$CLIENT_ID" "$API_KEY")

FILE_NAME=$(basename "$REPORT_FILE")

echo "[IMA_CLOSING] 开始写入「${KB_NAME}」: $FILE_NAME" >&2

# Step 1: preflight
PREFLIGHT=$(node "$SKILL_DIR/knowledge-base/scripts/preflight-check.cjs" --file "$REPORT_FILE" 2>/dev/null)
PASS=$(echo "$PREFLIGHT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('pass',False))" 2>/dev/null)
if [ "$PASS" != "True" ]; then
  echo "[IMA_CLOSING] preflight 不通过，跳过: $PREFLIGHT" >&2
  exit 0
fi

FILE_SIZE=$(echo "$PREFLIGHT" | python3 -c "import sys,json; print(json.load(sys.stdin)['file_size'])")
MEDIA_TYPE=$(echo "$PREFLIGHT" | python3 -c "import sys,json; print(json.load(sys.stdin)['media_type'])")
CONTENT_TYPE=$(echo "$PREFLIGHT" | python3 -c "import sys,json; print(json.load(sys.stdin)['content_type'])")
FILE_EXT=$(echo "$PREFLIGHT" | python3 -c "import sys,json; print(json.load(sys.stdin)['file_ext'])")

# Step 2: check_repeated_names（folder 维度，幂等防重）
RESP=$(node "$SKILL_DIR/ima_api.cjs" "openapi/wiki/v1/check_repeated_names" \
  "{\"params\":[{\"name\":\"$FILE_NAME\",\"media_type\":$MEDIA_TYPE}],\"knowledge_base_id\":\"$KB_ID\",\"folder_id\":\"$FOLDER_ID\"}" "$OPTS" 2>/tmp/ima_push_err)
CODE=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['code'])" 2>/dev/null)
if [ "$CODE" != "0" ]; then
  MSG=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['msg'])" 2>/dev/null)
  echo "[IMA_CLOSING] check_repeated_names 失败 (code=$CODE): $MSG" >&2
  exit 0
fi
IS_REPEATED=$(echo "$RESP" | python3 -c "import sys,json; print(str(json.load(sys.stdin)['data']['results'][0]['is_repeated']).lower())" 2>/dev/null)
if [ "$IS_REPEATED" = "true" ]; then
  echo "[IMA_CLOSING] 目录已有同名文件，跳过写入: $FILE_NAME" >&2
  exit 0
fi

# Step 3: create_media
CREATE_RESP=$(node "$SKILL_DIR/ima_api.cjs" "openapi/wiki/v1/create_media" \
  "{\"file_name\":\"$FILE_NAME\",\"file_size\":$FILE_SIZE,\"content_type\":\"$CONTENT_TYPE\",\"knowledge_base_id\":\"$KB_ID\",\"file_ext\":\"$FILE_EXT\"}" "$OPTS" 2>/tmp/ima_push_err)
CODE=$(echo "$CREATE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['code'])" 2>/dev/null)
if [ "$CODE" != "0" ]; then
  MSG=$(echo "$CREATE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['msg'])" 2>/dev/null)
  echo "[IMA_CLOSING] create_media 失败 (code=$CODE): $MSG" >&2
  exit 0
fi

MEDIA_ID=$(echo "$CREATE_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['media_id'])")
SECRET_ID=$(echo "$CREATE_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['cos_credential']['secret_id'])")
SECRET_KEY=$(echo "$CREATE_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['cos_credential']['secret_key'])")
TOKEN=$(echo "$CREATE_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['cos_credential']['token'])")
BUCKET=$(echo "$CREATE_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['cos_credential']['bucket_name'])")
REGION=$(echo "$CREATE_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['cos_credential']['region'])")
COS_KEY=$(echo "$CREATE_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['cos_credential']['cos_key'])")
START_TIME=$(echo "$CREATE_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['cos_credential']['start_time'])")
EXPIRED_TIME=$(echo "$CREATE_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['cos_credential']['expired_time'])")

# Step 4: COS upload
cd "$SKILL_DIR/knowledge-base"
COS_OUT=$(node scripts/cos-upload.cjs \
  --file "$REPORT_FILE" \
  --secret-id "$SECRET_ID" \
  --secret-key "$SECRET_KEY" \
  --token "$TOKEN" \
  --bucket "$BUCKET" \
  --region "$REGION" \
  --cos-key "$COS_KEY" \
  --content-type "$CONTENT_TYPE" \
  --start-time "$START_TIME" \
  --expired-time "$EXPIRED_TIME" \
  --timeout 300000 2>&1)
if [ $? -ne 0 ]; then
  echo "[IMA_CLOSING] COS 上传失败: $COS_OUT" >&2
  cd "$OLDPWD"
  exit 0
fi
cd "$OLDPWD"

# Step 5: add_knowledge（folder_id 落到「盘后复盘」）
ADD_RESP=$(node "$SKILL_DIR/ima_api.cjs" "openapi/wiki/v1/add_knowledge" \
  "{\"media_type\":$MEDIA_TYPE,\"media_id\":\"$MEDIA_ID\",\"title\":\"$FILE_NAME\",\"knowledge_base_id\":\"$KB_ID\",\"folder_id\":\"$FOLDER_ID\",\"file_info\":{\"cos_key\":\"$COS_KEY\",\"file_size\":$FILE_SIZE,\"file_name\":\"$FILE_NAME\"}}" "$OPTS" 2>/tmp/ima_push_err)

CODE=$(echo "$ADD_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['code'])" 2>/dev/null)
if [ "$CODE" = "0" ]; then
  # Step 6: 回读校验（P0-2）— add 后读回目录列表验证标题落位
  echo "[IMA_CLOSING] add 成功，回读校验中..."
  LIST_RESP=$(node "$SKILL_DIR/ima_api.cjs" "openapi/wiki/v1/get_knowledge_list" \
    "{\"knowledge_base_id\":\"$KB_ID\",\"folder_id\":\"$FOLDER_ID\",\"cursor\":\"\",\"limit\":20}" "$OPTS" 2>/tmp/ima_push_err)
  VERIFY=$(echo "$LIST_RESP" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    kl=d.get('data',{}).get('knowledge_list') or []
    print('1' if any((i.get('title') or '')=='$FILE_NAME' for i in kl) else '0')
except Exception:
    print('0')" 2>/dev/null)
  if [ "$VERIFY" = "1" ]; then
    echo "[IMA_CLOSING] ✅ 已写入并回读可见:「${KB_NAME}」/ $FILE_NAME (IMA_OK)" >&2
  else
    echo "[IMA_CLOSING] ⚠️ add 成功但回读未见标题 — 落库位置需人工核验 (IMA_PENDING)" >&2
  fi
else
  MSG=$(echo "$ADD_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['msg'])" 2>/dev/null)
  echo "[IMA_CLOSING] add_knowledge 失败 (code=$CODE): $MSG (IMA_FAIL)" >&2
fi
