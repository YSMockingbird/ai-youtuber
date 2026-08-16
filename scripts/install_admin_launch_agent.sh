#!/bin/zsh
set -euo pipefail

project_dir="/Users/ysaeki/Library/Mobile Documents/com~apple~CloudDocs/rockstar/ai-youtuber"
source_plist="$project_dir/launchd/com.ysaeki.ai-youtuber-admin.plist"
target_plist="$HOME/Library/LaunchAgents/com.ysaeki.ai-youtuber-admin.plist"
log_dir="$HOME/Library/Logs/AiYoutuber"
service_target="gui/$(id -u)/com.ysaeki.ai-youtuber-admin"

if [[ ! -x "$project_dir/.venv/bin/python" ]]; then
  echo "エラー: 仮想環境のPythonが見つかりません: $project_dir/.venv/bin/python" >&2
  exit 1
fi

/usr/bin/plutil -lint "$source_plist"
/bin/mkdir -p "$HOME/Library/LaunchAgents" "$log_dir"

if /bin/launchctl print "$service_target" >/dev/null 2>&1; then
  /bin/launchctl bootout "gui/$(id -u)" "$target_plist"
fi

/bin/cp "$source_plist" "$target_plist"
/bin/launchctl bootstrap "gui/$(id -u)" "$target_plist"
/bin/launchctl enable "$service_target"
/bin/launchctl kickstart -k "$service_target"

echo "常駐配信管理を登録しました。"
echo "管理画面: http://127.0.0.1:8765/admin"
echo "ログ: $log_dir/admin-service.log"
echo "エラーログ: $log_dir/admin-service-error.log"
