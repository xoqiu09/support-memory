# 青柚家电客服记忆

客服换了会话，应该还能记得同一账户想看哪些订单、回答要多长、该怎么称呼。这个演示把三种偏好存在本地 SQLite 里。客服按账户读取偏好和订单，每次回答的第一行会说明用到了什么设置。用户可以查看写入时间，也可以清除其中一项或全部偏好。

青柚家电、账户和订单都是样例。识别规则在写入 SQLite 前遮盖手机号、身份证号和详细地址格式。数据库只保存账户、偏好键值和写入时间。

## 运行

需要 Python 3.10 或更高版本、Bash 和 Python 标准库。从项目根目录运行以下命令。第一行检查版本，第二行在空临时目录里复制程序，演示缺少订单文件时的错误，再复制订单文件验证恢复，最后一行运行七项验收测试。

~~~bash
python3 --version
bash scripts/smoke.sh
python3 -m unittest -v
~~~

下面这组命令也从项目根目录运行。它把程序和样例订单复制到临时目录，退出时清理该目录。

~~~bash
(
    set -eu
    workdir=$(mktemp -d)
    trap 'rm -r "$workdir"' EXIT
    cp -R supportmemory data "$workdir/"
    cd "$workdir"
    python3 -m supportmemory chat --account A --session a1 "以后只看未发货的"
    python3 -m supportmemory chat --account A --session a2 "列出订单"
    python3 -m supportmemory show --account A
    python3 -m supportmemory forget --account A --key order_filter
    python3 -m supportmemory forget --account A
    python3 -m supportmemory export --out demo.json
)
~~~

- **chat** 用 `--account` 选择账户，`--session` 表示本次会话。保存偏好时，账户标识不能含手机号或身份证号。示例中的两次命令分别启动进程，换了会话后仍只列出 `A-102`。
- **show** 输出该账户的偏好、值和 UTC 写入时间。示例可看到 `order_filter` 的值是 `unshipped`。
- **forget** 带 `--key` 时清除指定偏好，可选 `order_filter`、`brevity` 或 `salutation`。省略 `--key` 时清除该账户的全部偏好。
- **export** 把内置演示写到 `--out` 指定的 JSON 文件。每步包含样例输入、回答和当时该账户的偏好。隐私演示的称呼使用内置样例号码，回答和偏好均显示遮盖结果。导出使用独立的内存数据库和固定演示时间，若目标指向记忆数据库或样例订单文件，命令会报错退出。

需要保留数据库或演示 JSON 时，按 [实施交接](docs/HANDOFF.md) 手动搭建并保留工作目录。`memory.sqlite3` 和相对输出路径都以运行命令时所在的目录为准。

## 七项测试分别证明什么

| 测试 | 核对的结果 |
| --- | --- |
| 01 跨会话和进程 | A 换会话、重新启动命令后只看到 `A-102`。设置和更新偏好后，`show` 显示对应的 UTC 写入时间。 |
| 02 账户隔离 | B 看见自己的 `B-201`、`B-202`，B 的偏好为空。 |
| 03 清除偏好 | 一句话可设置三种偏好。单独清除 A 的筛选项或清除 A 的全部偏好后，B 的筛选值和写入时间都保持原样。 |
| 04 写入前遮盖 | 检查每次交给存储层的值、完整 SQLite 转储、数据库文件和回答。称呼后的独立内容会被跳过，个人号码用作账户标识时无法写入偏好。 |
| 05 普通消息 | 普通消息和直接否定偏好规则的消息执行前后，偏好、SQLite 行数和文件字节都相同。 |
| 06 导出 | 核对六步的输入、回答、偏好快照和字段。两次导出逐字节一致，JSON 不含绝对路径、主机名和用户名。试图覆盖数据库或订单文件时，命令报错且原文件字节保持原样。 |
| 07 空目录恢复 | 从空目录执行冒烟脚本，先观察缺订单文件的错误，再补上样例、核对两个账户的回答和清除后的结果。 |

## M1 范围

M1 交付中文命令行、规则客服、SQLite 记忆和供演示页使用的 JSON。真实模型接入、网页、多语言和向量检索均留在 M1 范围之外。

## English overview

An offline Python 3.10+ CLI for a fictional appliance support agent. It persists order filters, reply length, and salutations per account across sessions. It masks common Chinese phone, ID, and street address formats before writing preferences to SQLite. Users can inspect or clear their preferences, and the bundled demo exports repeatable JSON.
