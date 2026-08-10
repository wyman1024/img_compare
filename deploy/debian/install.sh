#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

cd "$APP_DIR"
mkdir -p data/projects data/drafts logs .run
chmod 700 data data/projects data/drafts logs .run
chmod -R u=rwX,go= "$APP_DIR/data"
bash "$APP_DIR/start.sh" --prepare

current_crontab="$(crontab -l 2>/dev/null || true)"
manage_script="$(printf '%q' "$APP_DIR/deploy/debian/manage.sh")"
autostart="@reboot /usr/bin/env bash $manage_script start # img-compare-autostart"
watchdog="* * * * * /usr/bin/env bash $manage_script start >/dev/null 2>&1 # img-compare-watchdog"
{
    printf '%s\n' "$current_crontab" | \
        sed '/# img-compare-autostart$/d; /# img-compare-watchdog$/d'
    printf '%s\n' "$autostart"
    printf '%s\n' "$watchdog"
} | sed '/^[[:space:]]*$/d' | crontab -

bash "$APP_DIR/deploy/debian/manage.sh" restart
echo "已配置当前用户的开机启动和每分钟健康守护。"
