#!/bin/bash
# 将 stable 强推到 GitHub main（仅稳定版发布）。
# 日常提交：git push gitea main
# 发稳定版：
#   git branch -f stable main && git push gitea stable && bash scripts/publish-stable.sh
set -euo pipefail
REPO="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$REPO"
URL=$(git remote get-url github 2>/dev/null || git remote get-url origin 2>/dev/null || true)
CREDS=$(python3 -c 'import re,sys; m=re.search(r"://([^:/@]+):([^@]+)@", sys.argv[1] or ""); print(("%s:%s"%(m.group(1),m.group(2))) if m else "")' "$URL")
if [ -z "$CREDS" ]; then echo "no creds in remote"; exit 1; fi
git remote remove github 2>/dev/null || true
git remote add github "https://${CREDS}@ghproxy.net/https://github.com/BcubBo/lark-hls-v2.git"
git -c http.sslVerify=false push --force github stable:main
echo "published: https://github.com/BcubBo/lark-hls-v2"
