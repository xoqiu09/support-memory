#!/usr/bin/env bash
set -eu

source_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
workdir=$(mktemp -d)
trap 'rm -r "$workdir"' EXIT
cd "$workdir"

cp -R "$source_dir/supportmemory" .
mkdir data

if missing=$(python3 -m supportmemory chat --account A --session a1 "以后只看未发货的" 2>&1); then
    printf '缺少订单时，命令本应失败。\n' >&2
    exit 1
fi
printf '缺数据时（预期失败）\n%s\n' "$missing"
[[ "$missing" == "缺少 data/orders.json。请按 HANDOFF 的缺数据恢复步骤复制样例订单。" ]]

cp "$source_dir/data/orders.json" data/orders.json
printf '\n按 HANDOFF 复制 data/orders.json 后\n'

check_answer() {
    local expected=$1 answer
    shift
    answer=$(python3 -m supportmemory chat "$@")
    printf '%s\n' "$answer"
    if [[ "$answer" != "$expected" ]]; then
        printf '预期回答为\n%s\n' "$expected" >&2
        return 1
    fi
}

filtered=$'按你的设置，只列未发货订单\nA-102（未发货）'
check_answer "$filtered" --account A --session a1 "以后只看未发货的"
check_answer "$filtered" --account A --session a2 "列出订单"
check_answer $'按默认设置，列出全部订单\nB-201（已发货）\nB-202（未发货）' \
    --account B --session b1 "列出订单"
python3 -m supportmemory forget --account A
preferences=$(python3 -m supportmemory show --account A)
printf '%s\n' "$preferences"
[[ "$preferences" == "{}" ]]
check_answer $'按默认设置，列出全部订单\nA-101（已发货）\nA-102（未发货）' \
    --account A --session a3 "列出订单"
printf '冒烟验证通过。\n'
