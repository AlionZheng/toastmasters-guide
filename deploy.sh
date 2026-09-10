#!/usr/bin/env bash
set -euo pipefail
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_DIR="/var/www/guide"
install -d -m 755 "$TARGET_DIR"
install -m 644 "$SOURCE_DIR/index.html" "$TARGET_DIR/index.html"
rm -rf "$TARGET_DIR/images"
cp -R "$SOURCE_DIR/images" "$TARGET_DIR/images"
chown -R www-data:www-data "$TARGET_DIR"
find "$TARGET_DIR" -type d -exec chmod 755 {} +
find "$TARGET_DIR" -type f -exec chmod 644 {} +
echo "OK: $(find "$TARGET_DIR/images" -type f | wc -l | tr -d ' ') 张图片"
