#!/usr/bin/env bash
# macOS 上在访达里双击启动迁跃，会打开一个终端窗口。
root="$(cd "$(dirname "$0")" && pwd)"
exec "$root/scripts/start/start.sh" "$@"
