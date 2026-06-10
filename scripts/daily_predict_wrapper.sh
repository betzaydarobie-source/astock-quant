#!/usr/bin/env bash
# daily_predict_wrapper.sh — 每日预测 shell 包装器
# 被 launchd plist 或手动调用。成功时发 macOS 通知 + 开浏览器；失败时发错误通知。
# 退出码始终为 0，不阻塞 launchd 的下次调度。

set -euo pipefail

# launchd 启动的 shell 不继承用户 PATH，必须显式加上 ~/.local/bin（uv） + brew prefix
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

ARTIFACTS_DIR="$PROJECT_ROOT/artifacts/daily_reports"
mkdir -p "$ARTIFACTS_DIR"

DATE_STR="$(date +%Y-%m-%d)"
ERROR_LOG="$ARTIFACTS_DIR/wrapper_error_${DATE_STR}.log"

notify() {
    # 用户 2026-06-10 要求关闭每日推送:不再弹 macOS 通知,只写日志。
    # 如需恢复,把下面 osascript 块取消注释即可。
    local title="$1"
    local message="$2"
    local subtitle="${3:-}"
    echo "[notify-silenced] $title | $message | $subtitle"
    # if command -v osascript &>/dev/null; then
    #     osascript -e "display notification \"$message\" with title \"$title\" subtitle \"$subtitle\"" 2>/dev/null || true
    # fi
}

publish_to_pages() {
    cd "$PROJECT_ROOT"

    # 本地不是 git repo 时跳过（首次部署前用户尚未 git init）
    if ! git rev-parse --git-dir &>/dev/null; then
        echo "[publish] 不是 git repo，跳过部署（用户尚未完成首次部署步骤）"
        return 0
    fi

    # 没有配 origin remote 时跳过
    if ! git remote get-url origin &>/dev/null; then
        echo "[publish] 未配 origin remote，跳过部署"
        return 0
    fi

    # 同步远端，rebase 失败时 abort 并跳过本次
    if ! git pull --rebase origin main 2>&1; then
        echo "[publish] git pull --rebase 失败，abort 并跳过本次部署"
        git rebase --abort 2>/dev/null || true
        notify "A股预测 ⚠️" "Pages 部署跳过：rebase 冲突，请手动 pull"
        return 0
    fi

    # 复制最新 HTML 到 docs/reports/
    mkdir -p docs/reports
    cp "$ARTIFACTS_DIR/daily_report_${DATE_STR}.html" docs/reports/

    # 重生成 index.html
    if ! uv run python scripts/build_index.py 2>&1; then
        echo "[publish] build_index.py 失败，跳过部署"
        notify "A股预测 ⚠️" "Pages 部署跳过：build_index 失败"
        return 0
    fi

    # add / commit（nothing-to-commit 时静默跳过）
    git add docs/
    if git diff --cached --quiet; then
        echo "[publish] docs/ 无变化，跳过 commit"
        return 0
    fi

    git commit -m "report: daily ${DATE_STR}" || {
        echo "[publish] commit 失败"
        return 0
    }

    # push，失败时 retry 1 次
    if ! git push origin main 2>&1; then
        echo "[publish] push 失败，等 10s 后 retry"
        sleep 10
        if ! git push origin main 2>&1; then
            echo "[publish] push 二次失败，放弃本次部署"
            notify "A股预测 ⚠️" "Pages push 失败，下次自动重试"
            return 0
        fi
    fi

    notify "A股预测" "今日报告已发布到 GitHub Pages" "$DATE_STR"
    echo "[publish] OK"
}

run_predict() {
    cd "$PROJECT_ROOT"
    # 默认沪深 300（HS300），用户在 Stage 4 选定此股票池
    uv run python -m astock_quant.predict.daily --universe stage4 2>&1
}

# --- main ---
echo "[$(date '+%Y-%m-%d %H:%M:%S')] wrapper 启动"

if output="$(run_predict 2>&1)"; then
    echo "$output"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 预测成功"

    notify "A股预测" "今日预测报告已生成，点击查看" "$DATE_STR"

    # 用户 2026-06-10 要求关闭每日推送:不再自动打开浏览器。
    # 如需恢复,取消下面注释。
    # latest_html="$(ls -t "$ARTIFACTS_DIR"/daily_report_*.html 2>/dev/null | head -1 || true)"
    # if [[ -n "$latest_html" ]]; then
    #     open "$latest_html" 2>/dev/null || true
    # fi

    publish_to_pages
else
    exit_code=$?
    echo "$output" | tee -a "$ERROR_LOG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 预测失败 (exit $exit_code)，错误日志：$ERROR_LOG"

    notify "A股预测 ⚠️" "预测脚本失败，查看日志" "$(basename "$ERROR_LOG")"
fi

# 始终 exit 0，不阻塞下次 launchd 调度
exit 0
