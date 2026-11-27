# 青柚家电客服记忆实施交接

## 1. 准备

准备一份本项目的文件夹，以及 Python 3.10 或更高版本。打开 Bash 或兼容终端，输入 `cd `（留一个空格），把项目文件夹拖入终端并按回车。这个文件夹里应有 `README.md`、`supportmemory` 和 `data`。运行下面的命令检查版本。

~~~bash
python3 --version
~~~

看到 `Python 3.10.x` 或更高版本后，继续下一步。

## 2. 在空目录装好程序和样例订单

在项目根目录逐行执行下面的命令。`source_dir` 保存项目所在的位置，`workdir` 是这次演示的空目录。最后一行把两个虚构账户的订单导入工作目录，客服会直接从该 JSON 文件读取订单。

~~~bash
source_dir=$(pwd)
workdir=$(mktemp -d)
cd "$workdir"
cp -R "$source_dir/supportmemory" .
mkdir data
cp "$source_dir/data/orders.json" data/orders.json
~~~

后面的步骤使用这个终端和工作目录。第一次运行客服时会在这里生成 `memory.sqlite3`。想演示换会话或重新打开终端时，继续用同一工作目录。

## 3. 试问三句话

第一句让 A 设置筛选偏好。

~~~bash
python3 -m supportmemory chat --account A --session a1 "以后只看未发货的"
~~~

~~~text
按你的设置，只列未发货订单
A-102（未发货）
~~~

第二句换成 A 的新会话 `a2`。这条命令会启动一个新进程，预期仍只显示 `A-102`。

~~~bash
python3 -m supportmemory chat --account A --session a2 "列出订单"
~~~

~~~text
按你的设置，只列未发货订单
A-102（未发货）
~~~

第三句换成账户 B。预期列出 B 的两笔订单。

~~~bash
python3 -m supportmemory chat --account B --session b1 "列出订单"
~~~

~~~text
按默认设置，列出全部订单
B-201（已发货）
B-202（未发货）
~~~

## 4. 清除一个用户的全部偏好

把 `A` 换成要清除的账户。`forget` 清除跨会话偏好和会话偏好，历史和审计继续保留。先清除，再查看偏好，最后重问订单。下面以账户 A 为例。

~~~bash
python3 -m supportmemory forget --account A
python3 -m supportmemory show --account A
python3 -m supportmemory chat --account A --session a3 "列出订单"
~~~

~~~text
已清除该账户的全部偏好。
{}
按默认设置，列出全部订单
A-101（已发货）
A-102（未发货）
~~~

## 5. 常见故障

### 缺少订单文件

发送消息时会看到“缺少 data/orders.json”。如果第 2 步已经创建 `data`，在同一个终端、同一个工作目录补上文件，然后重试原命令。

~~~bash
cp "$source_dir/data/orders.json" data/orders.json
~~~

如果 `data` 目录也缺失，先创建它。

~~~bash
mkdir -p data
cp "$source_dir/data/orders.json" data/orders.json
~~~

### Python 版本太低

`python3 --version` 显示低于 3.10，运行程序时可能出现语法错误或找不到 Python 功能。切换到已安装的 Python 3.10 或更高版本，再运行版本检查和原命令。如果电脑上没有合适版本，请让负责环境的人提供后再继续。

### 数据库被锁定

命令会提示“记忆数据库被锁定”。在占用 `memory.sqlite3` 的程序里结束当前操作或关闭数据库工具。等它释放文件后，从同一工作目录重试原命令，保留 `memory.sqlite3` 里的现有偏好。
