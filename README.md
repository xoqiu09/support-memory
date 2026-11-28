# 青柚家电客服记忆

客服团队接待回访客户时，往往要重新确认客户之前说过的偏好，拖慢处理速度。

- 同一账户换个会话，仍能沿用订单筛选、回答长短和称呼偏好。
- 客户可查看或清除偏好，也可关闭跨会话保存。
- 写入前遮盖常见的手机号、身份证号和详细地址，记录偏好的读取、写入和删除。

## 怎么验证的

本地运行 `python3 -m unittest`，**59 项测试全部通过**。测试核对了跨会话记忆、账户隔离、偏好清除、写入前遮盖、对话历史和摘要的长度上限。测试也确认，关闭跨会话保存后，新偏好只在当前会话生效，审计记录里没有偏好值。

验证测试时，分别故意跳过写入前遮盖、移除按账户筛选，重跑对应测试，两项测试都按预期失败。

## 试用

打开 [在线演示](https://xoqiu.cn/memory-demo/index.html)，或在刚克隆的仓库根目录用 Python 运行下面两条命令。换到新会话后，仍只会看到未发货订单。

~~~bash
python3 -m supportmemory chat --account A --session first "以后只看未发货的"
python3 -m supportmemory chat --account A --session next "列出订单"
~~~

数据均为虚构，没有真实用户，客服按规则回复，未接入真实模型。我负责定需求和验收标准，并指导 AI 编程工具实现。

## English overview

Support teams lose time asking returning customers to repeat their preferences.\
This offline Python 3.10+ CLI remembers preferences across chats for the same account and lets customers view or clear them and turn off cross-chat saving.\
It masks common personal details before storage, keeps a size-limited history and summary of redacted chats, and logs preference reads, writes, and deletions without recording the values.\
It exports reproducible M1 and M2 demo JSON files. All 59 tests passed. Skipping redaction or removing the account filter made the relevant tests fail.\
The demo uses fictional data, with no real users or live model. I set the requirements and acceptance criteria and directed AI coding tools to build it.

## 运行

需要 Python 3.10 或更高版本、Bash 和 Python 标准库。程序离线运行。从项目根目录运行以下命令。第一行检查版本，第二行演示空目录缺少订单文件时的报错和恢复，最后一行运行 M1 和 M2 的验收测试。

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
    python3 -m supportmemory consent --account A off
    python3 -m supportmemory forget --account A --key order_filter
    python3 -m supportmemory chat --account A --session a3 "只看已发货的"
    python3 -m supportmemory chat --account A --session a3 "列出订单"
    python3 -m supportmemory chat --account A --session a4 "列出订单"
    python3 -m supportmemory history --account A --session a3
    python3 -m supportmemory audit --account A
    python3 -m supportmemory forget --account A
    python3 -m supportmemory consent --account A on
    python3 -m supportmemory export --out demo.json
    ls demo.json demo.m2.json
)
~~~

- **chat** 用 `--account` 选择账户，`--session` 选择会话。回答首行会说明本次采用的设置。账户和会话标识都应避开手机号、身份证号和详细地址。打开同意时，示例中的 `a1` 和 `a2` 都只列出 `A-102`。关闭同意并清掉旧偏好后，`a3` 两次都只列出 `A-101`，新会话 `a4` 列出全部订单。
- **show** 输出该账户跨会话保存的偏好、值和 UTC 写入时间。示例可看到 `order_filter` 的值是 `unshipped`。
- **consent** 按账户设为 `on` 或 `off`，默认开启。关闭保留已有偏好，新识别的偏好仅在相同会话生效，重开命令仍可使用。重新开启后，新识别的偏好恢复跨会话保存。
- **forget** 带 `--key` 时清除 `order_filter`、`brevity` 或 `salutation`。省略 `--key` 时清除该账户的全部偏好。清除范围包括会话偏好，历史和审计继续保留。
- **history** 输出指定账户和会话的 `turns` 和 `summary`。最多保留最近 6 轮，每轮含遮盖后的用户消息、回答和设定的偏好。窗口整体按紧凑 JSON 的字符数除以 2 计费，上限 400 token。折入摘要时保留遮盖后的用户消息前 20 个字符及其偏好。预算用完时先淘汰更旧的摘要条目，单条摘要里过长的称呼会缩短。
- **audit** 输出该账户每个偏好键的读取、写入和删除事件。每条只有账户、会话、键、操作和 UTC 时间。`show` 及账户级 `forget` 的会话字段为 `null`，清除会话偏好时记录所属会话。
- **export** 生成兼容原有网站数据格式的 `demo.json`，并生成 M2 演示数据 `demo.m2.json`。前者包含原有六步的样例输入、回答和账户偏好。后者展示关闭同意后的同会话和新会话回答、遮盖后的窗口和摘要，以及审计记录。两份都由独立的内存数据库和固定时间生成。两份输出路径均受保护，指向记忆数据库或样例订单文件时会报错。

需要保留数据库或演示 JSON 时，按 [实施交接](docs/HANDOFF.md) 手动搭建并保留工作目录。`memory.sqlite3` 保存账户偏好和写入时间，`memory-m2.sqlite3` 保存授权状态、会话偏好、对话历史和审计记录。两份 SQLite 数据库和相对输出路径都以运行命令时所在的目录为准。

## M1 核心测试分别证明什么

| 测试 | 核对的结果 |
| --- | --- |
| 01 跨会话和进程 | A 换会话、重新启动命令后只看到 `A-102`。设置和更新偏好后，`show` 显示对应的 UTC 写入时间。 |
| 02 账户隔离 | B 看见自己的 `B-201`、`B-202`，B 的偏好为空。 |
| 03 清除偏好 | 一句话可设置三种偏好。单独清除 A 的筛选项或清除 A 的全部偏好后，B 的筛选值和写入时间都保持原样。 |
| 04 写入前遮盖 | 检查每次交给存储层的值、完整 SQLite 转储、数据库文件和回答。称呼后的独立内容会被跳过，个人号码用作账户标识时无法写入偏好。 |
| 05 普通消息 | 普通消息和直接否定偏好规则的消息执行前后，M1 偏好库 `memory.sqlite3` 的偏好、行数和文件字节都相同。M2 库仍记录对话历史和实际的偏好读取。 |
| 06 导出 | 核对六步的输入、回答、偏好快照和字段。两次导出逐字节一致，JSON 不含绝对路径、主机名和用户名。试图覆盖数据库或订单文件时，命令报错且原文件字节保持原样。 |
| 07 空目录恢复 | 从空目录执行冒烟脚本，先观察缺订单文件的错误，再补上样例、核对两个账户的回答和清除后的结果。 |

## M2 测试分别证明什么

| 测试 | 核对的结果 |
| --- | --- |
| 关闭同意 | 账户偏好表为空，同会话继续筛选订单，新会话采用默认设置。另一账户默认开启，会话偏好的读写也有审计。 |
| 切换同意和清除 | 关闭时保留原偏好并提示。按键 `forget` 后，设置过临时筛选的会话和新会话都恢复默认。重新开启后新偏好可跨会话使用。 |
| 敏感标识 | 含手机号、身份证号或详细地址的账户和会话标识在写入数据库前被拒绝。清除旧会话偏好时，审计会遮盖旧标识里的隐私。 |
| 六轮窗口 | 八轮短对话留下最近六轮，摘要按顺序保留较早两轮的前 20 字和偏好变更。 |
| 三十轮预算 | 逐轮检查窗口和摘要合计始终在 400 token 内，最新一轮可见。 |
| 超长消息 | 长消息折入摘要后，仍装得下的旧摘要和其中的偏好保持可见。 |
| 单条超长称呼 | 超长称呼折入有界摘要，摘要保留设定的偏好键，账户偏好仍是完整值。 |
| 首轮手机号 | 折入摘要前后检查两份 SQLite 的全部行和文件字节，样例手机号均已遮盖。 |
| 历史写入 | SQLite 在每次插入或更新前检查消息、回复、偏好和摘要中的样例隐私。 |
| 读写删审计 | 核对日志字段、键、会话和动作，逐条检查时间是否落在对应命令的执行时段。删除零行时没有删除事件。 |
| 历史和审计隔离 | A 和 B 使用同一会话名仍各见各的记录，第三个账户的同名历史为空。 |
| M2 导出 | 两次导出逐字节一致，演示包含三项 M2 能力，现有数据库保持原样，两条输出路径均保护数据库。 |
| M1 数据库兼容 | 只含 M1 偏好表的数据库能重新打开，旧偏好仍生效，新偏好按默认同意保存。 |

## 范围

本项目交付中文命令行、规则客服、SQLite 记忆和供演示页使用的 JSON。演示网页代码、多语言和向量检索留在本次交付范围外。
