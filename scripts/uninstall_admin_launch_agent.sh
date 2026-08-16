#!/bin/zsh
set -euo pipefail

target_plist="$HOME/Library/LaunchAgents/com.ysaeki.ai-youtuber-admin.plist"
service_target="gui/$(id -u)/com.ysaeki.ai-youtuber-admin"

if /bin/launchctl print "$service_target" >/dev/null 2>&1; then
  /bin/launchctl bootout "gui/$(id -u)" "$target_plist"
fi

if [[ -f "$target_plist" ]]; then
  /bin/rm "$target_plist"
fi

echo "常駐配信管理を解除しました。ログは削除していません。"
