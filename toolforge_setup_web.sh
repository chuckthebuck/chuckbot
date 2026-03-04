#!/usr/bin/env bash
set -euo pipefail

# Bootstrap Toolforge web endpoint files in ~/www/python/src.
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_DIR="${HOME}/www/python/src"

mkdir -p "$TARGET_DIR"
cp "$ROOT_DIR/app.py" "$TARGET_DIR/app.py"
cp "$ROOT_DIR/toolforge_queue_api.py" "$TARGET_DIR/toolforge_queue_api.py"

echo "Installed Toolforge web files into $TARGET_DIR"
echo "Next steps:"
echo "  webservice --backend=kubernetes python3.11 start"
echo "  curl -sS https://<toolname>.toolforge.org/healthz"
