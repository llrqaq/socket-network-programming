# TCP 网络文件备份系统说明

本项目包含三个 Python 程序：

- `server.py`：服务器端程序，负责监听 TCP 端口、接收客户端连接、保存客户端上传的备份文件、返回服务器已有备份列表、向客户端发送备份文件、删除备份，并维护断点续传进度。
- `client.py`：客户端程序，负责连接服务器，并通过菜单完成上传文件、上传文件夹、查看服务器备份列表、下载服务器备份文件/文件夹、删除服务器备份等操作。
- `gen_testfile.py`：用于生成测试文件。

这两个程序之间使用 **TCP** 作为传输层协议。在 TCP 连接建立之后，程序又自己定义了一套简单的 **应用层报文格式**。也就是说，TCP 只负责保证字节流能够可靠地从一端传到另一端，而“这些字节具体代表什么含义”，是由 `client.py` 和 `server.py` 中的应用层协议共同约定的。

下面会详细说明：

1. 应用层报文的整体结构是什么；
2. 报文头有哪些字段；
3. 数据部分在哪里，不同类型报文的数据部分分别是什么；
4. 每个字段和代码中的变量、函数之间是怎样对应的；
5. 客户端与服务器端的连接过程是什么；
6. 代码中是怎样判断连接成功的；
7. 客户端和服务器端分别具备哪些功能；
8. 新增的断点续传、删除、文件夹备份等功能在协议层是怎样体现的。

## 目录

- [一、整体运行方式](#overview)
- [二、应用层报文总结构](#protocol-structure)
- [三、固定 9 字节报文头结构](#header-structure)
- [四、数据区 Data 在哪里？](#data-section)
- [五、各种应用层报文的详细结构](#message-types)
  - [1. 上传文件信息报文：MSG_FILE_INFO = 0x01](#msg-file-info)
  - [2. 上传文件块报文：MSG_FILE_BLOCK = 0x02](#msg-file-block)
  - [3. 上传块确认报文：MSG_ACK = 0x03](#msg-ack)
  - [4. 上传最终结果报文：MSG_VERIFY_RESULT = 0x04](#msg-verify-result)
  - [5. 查看备份列表请求报文：MSG_LIST_BACKUPS = 0x05](#msg-list-backups)
  - [6. 备份列表响应报文：MSG_BACKUP_LIST = 0x06](#msg-backup-list)
  - [7. 下载请求报文：MSG_DOWNLOAD_REQUEST = 0x07](#msg-download-request)
  - [8. 下载信息响应报文：MSG_DOWNLOAD_INFO = 0x08](#msg-download-info)
  - [9. 下载文件块报文：MSG_DOWNLOAD_BLOCK = 0x09](#msg-download-block)
  - [10. 删除请求报文：MSG_DELETE_REQUEST = 0x0A](#msg-delete-request)
  - [11. 删除结果报文：MSG_DELETE_RESULT = 0x0B](#msg-delete-result)
  - [12. 断点查询报文：MSG_RESUME_QUERY = 0x0C](#msg-resume-query)
  - [13. 断点信息响应报文：MSG_RESUME_INFO = 0x0D](#msg-resume-info)
- [六、上传文件的完整流程](#upload-flow)
- [七、查看服务器备份列表的完整流程](#list-flow)
- [八、下载备份文件的完整流程](#download-flow)
- [九、客户端与服务器端的连接过程](#connection-flow)
- [十、客户端功能详细说明](#client-details)
- [十一、服务器端功能详细说明](#server-details)
- [十二、协议字段与代码映射总表](#mapping-table)
- [十三、重要实现细节总结](#implementation-notes)
- [十四、一句话总结](#summary)

---

<a id="overview"></a>
## 一、整体运行方式

这个系统可以理解成一个“通过网络备份文件”的小工具。

日常语言来说，使用过程大概是：

1. 先运行服务器 `server.py`；
2. 服务器创建一个用于保存备份文件的目录 `server_backup`；
3. 服务器让用户输入监听端口，例如 `9000`；
4. 服务器开始监听这个端口，等待客户端连接；
5. 再运行客户端 `client.py`；
6. 客户端输入服务器 IP 和端口；
7. 如果 TCP 连接建立成功，客户端进入菜单；
8. 用户可以选择：
   - 上传文件到服务器；
   - 上传文件夹到服务器；
   - 查看服务器上的备份文件列表；
   - 从服务器下载某个备份文件或备份文件夹；
   - 删除服务器上的某个备份文件或备份文件夹；
   - 退出程序。

---

<a id="protocol-structure"></a>
## 二、应用层报文总结构

客户端和服务器之间传输的每一条应用层消息，都由两部分组成：

```text
+----------------------+----------------------+
|  固定长度报文头 Header |  可变长度数据区 Data |
+----------------------+----------------------+
|       9 字节           |     length 字节       |
+----------------------+----------------------+
```

也就是说：

- 前 **9 字节** 永远是报文头；
- 报文头后面才是数据部分；
- 数据部分的长度不是固定的，而是由报文头里的 `length` 字段决定；
- 如果 `length = 0`，说明这条报文没有数据部分，只有报文头。

在代码中，报文头使用下面的格式进行打包和解包：

```python
struct.pack('>HBIH', MAGIC, msg_type, length, checksum)
struct.unpack('>HBIH', header[:9])
```

这里的 `'>HBIH'` 非常关键，它定义了报文头的二进制结构。

含义如下：

| 格式字符 | 字节数 | 含义                             |
| -------- | -----: | -------------------------------- |
| `>`      |      0 | 使用大端字节序，也就是网络字节序 |
| `H`      |      2 | unsigned short，无符号 16 位整数 |
| `B`      |      1 | unsigned char，无符号 8 位整数   |
| `I`      |      4 | unsigned int，无符号 32 位整数   |
| `H`      |      2 | unsigned short，无符号 16 位整数 |

所以整个报文头长度是：

```text
2 + 1 + 4 + 2 = 9 字节
```

---

<a id="header-structure"></a>
## 三、固定 9 字节报文头结构

每条应用层报文的头部结构如下：

```text
+-------------+------------+---------------+----------------+
| magic       | msg_type   | length        | checksum       |
+-------------+------------+---------------+----------------+
| 2 字节       | 1 字节      | 4 字节         | 2 字节          |
+-------------+------------+---------------+----------------+
| 偏移 0-1     | 偏移 2      | 偏移 3-6       | 偏移 7-8        |
+-------------+------------+---------------+----------------+
```

报文头之后紧跟数据区：

```text
+-------------+------------+---------------+----------------+-------------------+
| magic       | msg_type   | length        | checksum       | data              |
+-------------+------------+---------------+----------------+-------------------+
| 2 字节       | 1 字节      | 4 字节         | 2 字节          | length 字节        |
+-------------+------------+---------------+----------------+-------------------+
| 0-1         | 2          | 3-6           | 7-8            | 从第 9 字节开始     |
+-------------+------------+---------------+----------------+-------------------+
```

### 1. `magic`：魔数 / 协议标识

代码中的定义：

```python
MAGIC = 0x424B
```

客户端和服务器端都定义了同样的值：

- `client.py` 第 7 行：`MAGIC = 0x424B`
- `server.py` 第 6 行：`MAGIC = 0x424B`

它占用 **2 字节**。

这个字段的作用是：告诉接收方“这确实是本程序规定的应用层报文”。

服务器端在 `verify_header(header)` 中检查这个字段：

```python
magic, msg_type, length, checksum = struct.unpack('>HBIH', header[:9])
if magic != MAGIC:
    return False, None, None, None, None
```

客户端在接收服务器响应时也会检查它，例如接收校验结果时：

```python
magic, msg_type, length, checksum = struct.unpack('>HBIH', result_header[:9])
if magic != MAGIC or msg_type != MSG_VERIFY_RESULT:
    print("收到无效的校验结果报文")
    return False
```

日常理解：

> `magic` 就像一封信开头写的暗号。双方事先约定暗号是 `0x424B`，如果收到的消息开头不是这个暗号，就说明这条消息不是本协议的正常消息，程序就不继续处理。

---

### 2. `msg_type`：消息类型

`msg_type` 占用 **1 字节**，用于说明这条报文到底要表达什么事情。

两端共同定义了以下消息类型：

| 常量名                 |     值 | 方向            | 含义                                     |
| ---------------------- | -----: | --------------- | ---------------------------------------- |
| `MSG_FILE_INFO`        | `0x01` | 客户端 → 服务器 | 上传文件前，先告诉服务器文件名和文件大小 |
| `MSG_FILE_BLOCK`       | `0x02` | 客户端 → 服务器 | 上传文件过程中的某一个文件数据块         |
| `MSG_ACK`              | `0x03` | 服务器 → 客户端 | 服务器确认某个上传数据块已收到           |
| `MSG_VERIFY_RESULT`    | `0x04` | 服务器 → 客户端 | 上传完成后，服务器返回最终校验/保存结果  |
| `MSG_LIST_BACKUPS`     | `0x05` | 客户端 → 服务器 | 客户端请求查看服务器备份文件列表         |
| `MSG_BACKUP_LIST`      | `0x06` | 服务器 → 客户端 | 服务器返回备份文件列表                   |
| `MSG_DOWNLOAD_REQUEST` | `0x07` | 客户端 → 服务器 | 客户端请求下载某个备份文件或文件夹       |
| `MSG_DOWNLOAD_INFO`    | `0x08` | 服务器 → 客户端 | 服务器返回下载目标是否存在、类型和大小   |
| `MSG_DOWNLOAD_BLOCK`   | `0x09` | 服务器 → 客户端 | 服务器发送下载文件的某一个数据块         |
| `MSG_DELETE_REQUEST`   | `0x0A` | 客户端 → 服务器 | 客户端请求删除某个备份文件或文件夹       |
| `MSG_DELETE_RESULT`    | `0x0B` | 服务器 → 客户端 | 服务器返回删除操作的结果                 |
| `MSG_RESUME_QUERY`     | `0x0C` | 客户端 → 服务器 | 客户端上传前查询该文件已接收到哪个断点   |
| `MSG_RESUME_INFO`      | `0x0D` | 服务器 → 客户端 | 服务器返回断点信息：下一块号和已接收字节 |

代码映射位置：

- 客户端：`client.py` 中从 `MSG_FILE_INFO` 到 `MSG_RESUME_INFO` 的常量定义；
- 服务器端：`server.py` 中从 `MSG_FILE_INFO` 到 `MSG_RESUME_INFO` 的常量定义。

日常理解：

> `msg_type` 就像快递单上的“业务类型”。有的包裹是“我要上传文件”，有的是“这是文件内容的一块”，有的是“我收到了”，有的是“我要下载文件”。接收方只有先看清楚业务类型，才知道后面的数据应该按什么格式解释。

---

### 3. `length`：数据区长度

`length` 占用 **4 字节**，表示报文头后面的数据区有多少字节。

在客户端构造报文头时，对应代码是：

```python
def make_header(msg_type, data):
    length = len(data)
    ...
```

也就是说：

```text
length = len(data)
```

如果数据区是空的，例如客户端请求备份列表：

```python
header = make_header(MSG_LIST_BACKUPS, b'')
```

那么：

```text
length = 0
```

接收方收到报文头以后，会根据 `length` 再继续从 TCP 字节流中读取对应长度的数据。例如服务器接收上传文件信息：

```python
data = receive_full(sock, length)
```

客户端下载备份列表时也一样：

```python
list_data = receive_with_timeout(sock, length, TIMEOUT)
```

日常理解：

> TCP 是字节流，没有天然的“消息边界”。所以程序必须自己告诉对方：“我后面这段数据有多长”。`length` 就是用来解决这个问题的。接收方先读固定 9 字节头，再根据 `length` 精确读取后面的数据区。

---

### 4. `checksum`：CRC16 校验值

`checksum` 占用 **2 字节**，用于检查报文在传输或处理过程中是否发生错误。

两端都实现了同样的 CRC16 计算函数：

```python
def calc_crc16(data):
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc
```

代码映射位置：

- 客户端：`client.py` 第 22 行到第 32 行；
- 服务器端：`server.py` 第 20 行到第 30 行。

#### 校验值是怎么计算出来的？

发送方构造报文时，会先把 `checksum` 临时填成 `0`，然后把“临时头部 + 数据区”一起送进 `calc_crc16()` 计算。

客户端通用构造函数在 `client.py` 中：

```python
def make_header(msg_type, data):
    length = len(data)
    header = struct.pack('>HBIH', MAGIC, msg_type, length, 0)
    checksum = calc_crc16(header + data)
    return struct.pack('>HBIH', MAGIC, msg_type, length, checksum)
```

服务器端发送 ACK、校验结果、备份列表、下载信息、下载块时，也采用同样思路：

```python
header = struct.pack('>HBIH', MAGIC, 某个消息类型, len(data), 0)
checksum = calc_crc16(header + data)
header = struct.pack('>HBIH', MAGIC, 某个消息类型, len(data), checksum)
sock.sendall(header + data)
```

#### 接收方是怎么校验的？

服务器端在处理上传文件信息、上传文件块、下载请求时，会把收到的头部中的 checksum 字段位置重新替换成 `0`，再加上数据区重新计算 CRC16，然后与收到的 `checksum` 比较。

例如服务器验证上传文件信息：

```python
if calc_crc16(hdr[:7] + b'\x00\x00' + data) != checksum:
    print(f"[{addr}] 文件信息校验失败")
    return
```

这里的含义是：

- `hdr[:7]`：取报文头前 7 字节，也就是 `magic + msg_type + length`；
- `b'\x00\x00'`：把 checksum 字段按发送方计算时的方式重新置为 0；
- `data`：拼接数据区；
- 再计算 CRC16；
- 如果结果不等于报文头里的 `checksum`，说明校验失败。

需要注意的是，代码不是在每一种接收场景都完整校验 CRC。例如：

- 服务器端会对 `MSG_FILE_INFO`、`MSG_RESUME_QUERY`、`MSG_FILE_BLOCK`、`MSG_DOWNLOAD_REQUEST`、`MSG_DELETE_REQUEST` 做 CRC 校验；
- 客户端在接收 `MSG_RESUME_INFO` 时会重新计算 CRC；
- 客户端在接收部分其他服务器响应时主要检查 `magic` 和 `msg_type`，并没有对每一个服务器响应都重新计算 CRC。

这不是协议结构不存在校验字段，而是当前代码实现中并没有在所有接收路径上使用这个字段。

---

<a id="data-section"></a>
## 四、数据区 Data 在哪里？

数据区的位置非常明确：

```text
报文头固定 9 字节，所以 data 从第 9 字节之后开始。
```

换成偏移量来说：

```text
byte 0-8   ：Header
byte 9-end ：Data
```

在代码中，发送时通常是这样拼接完整报文的：

```python
full_message = header + data
sock.sendall(full_message)
```

例如客户端上传文件信息：

```python
info_data = filename.encode('utf-8') + b'\x00' + struct.pack('>Q', file_size)
header = make_header(MSG_FILE_INFO, info_data)
full_message = header + info_data
send_with_retry(sock, full_message)
```

接收时通常分两步：

```python
header = receive_full(sock, 9)
magic, msg_type, length, checksum = struct.unpack('>HBIH', header[:9])
data = receive_full(sock, length)
```

也就是说，接收方永远先读 9 字节报文头，再根据 `length` 读数据区。

---

<a id="message-types"></a>
## 五、各种应用层报文的详细结构

下面按照 `msg_type` 对每一种报文进行详细说明。

---

<a id="msg-file-info"></a>
## 1. 上传文件信息报文：`MSG_FILE_INFO = 0x01`

方向：

```text
客户端 -> 服务器
```

作用：

客户端在真正发送文件内容之前，先告诉服务器：

- 我要上传的文件叫什么名字；
- 这个文件总共有多少字节。

这样服务器就能提前知道后面应该接收多少数据，也能判断什么时候整个文件接收完毕。

### 报文结构

```text
+----------------------+----------------------------------------------+
| Header               | Data                                         |
+----------------------+----------------------------------------------+
| 9 字节                | 文件名 UTF-8 + '\0' + 文件大小               |
+----------------------+----------------------------------------------+
```

数据区结构：

```text
+------------------------+--------------+-------------------------+
| filename               | 分隔符        | file_size               |
+------------------------+--------------+-------------------------+
| 不固定长度，UTF-8 编码   | 1 字节 0x00   | 8 字节，无符号整数，大端   |
+------------------------+--------------+-------------------------+
```

### 客户端代码映射

在 `client.py` 的 `send_file(sock, file_path)` 中：

```python
filename = file_info["name"]
file_size = file_info["size"]
info_data = filename.encode('utf-8') + b'\x00' + struct.pack('>Q', file_size)
header = make_header(MSG_FILE_INFO, info_data)
full_message = header + info_data
send_with_retry(sock, full_message)
```

字段对应关系：

| 协议字段    | 代码来源                                            |
| ----------- | --------------------------------------------------- |
| `magic`     | `MAGIC = 0x424B`                                    |
| `msg_type`  | `MSG_FILE_INFO = 0x01`                              |
| `length`    | `len(info_data)`                                    |
| `checksum`  | `calc_crc16(header_with_zero_checksum + info_data)` |
| `filename`  | `os.path.basename(file_path)`                       |
| `file_size` | `os.path.getsize(file_path)`                        |

### 服务器端代码映射

服务器在 `handle_client(sock, addr)` 中收到 `MSG_FILE_INFO` 后，会进入上传逻辑：

```python
data = receive_full(sock, length)
```

然后验证 CRC：

```python
if calc_crc16(hdr[:7] + b'\x00\x00' + data) != checksum:
    print(f"[{addr}] 文件信息校验失败")
    return
```

然后解析文件名和文件大小：

```python
null_pos = data.find(b'\x00')
filename = data[:null_pos].decode('utf-8')
expected_size = struct.unpack('>Q', data[null_pos + 1:null_pos + 9])[0]
expected_blocks = (expected_size + BLOCK_SIZE - 1) // BLOCK_SIZE
```

这里的 `expected_size` 就是服务器认为接下来应该收到的文件总字节数。

---

<a id="msg-file-block"></a>
## 2. 上传文件块报文：`MSG_FILE_BLOCK = 0x02`

方向：

```text
客户端 -> 服务器
```

作用：

真正传输文件内容。大文件不会一次性全部塞进一个报文，而是按块发送。

代码中定义每块最大数据大小：

```python
BLOCK_SIZE = 4096
```

也就是说，文件内容每次最多读取 4096 字节发送。

### 报文结构

```text
+----------------------+----------------------------------+
| Header               | Data                             |
+----------------------+----------------------------------+
| 9 字节                | block_num + block_data           |
+----------------------+----------------------------------+
```

数据区结构：

```text
+-------------------------+-------------------------------+
| block_num               | block_data                    |
+-------------------------+-------------------------------+
| 4 字节，无符号整数，大端   | 文件内容，最多 4096 字节        |
+-------------------------+-------------------------------+
```

`block_num` 从 1 开始递增。

比如第一个文件块：

```text
block_num = 1
```

第二个文件块：

```text
block_num = 2
```

以此类推。

### 客户端代码映射

在 `client.py` 的 `send_file()` 中：

```python
data = f.read(BLOCK_SIZE)
block_num += 1
block_data = struct.pack('>I', block_num) + data
header = make_header(MSG_FILE_BLOCK, block_data)
full_message = header + block_data
send_with_retry(sock, full_message)
```

字段对应关系：

| 协议字段     | 代码来源                                           |
| ------------ | -------------------------------------------------- |
| `msg_type`   | `MSG_FILE_BLOCK = 0x02`                            |
| `length`     | `len(block_data)`，也就是 `4 + 当前块文件内容长度` |
| `block_num`  | 客户端循环中的 `block_num`                         |
| `block_data` | `f.read(BLOCK_SIZE)` 读出的文件字节                |

### 服务器端代码映射

服务器端在上传循环中不断接收文件块：

```python
while received_size < expected_size:
    header = receive_full(sock, 9)
    valid, msg_type, length, checksum, hdr = verify_header(header)
```

服务器要求收到的消息类型必须是 `MSG_FILE_BLOCK`：

```python
if not valid or msg_type != MSG_FILE_BLOCK:
    print(f"[{addr}] 协议错误或消息类型错误")
    return
```

然后读取数据区并做 CRC 校验：

```python
data = receive_full(sock, length)
if calc_crc16(hdr[:7] + b'\x00\x00' + data) != checksum:
    print(f"[{addr}] 数据块校验失败")
    return
```

解析块序号和真正的文件内容：

```python
block_num = struct.unpack('>I', data[:4])[0]
block_data = data[4:]
```

服务器还会检查块序号是否符合预期：

```python
if block_num != received_blocks:
    print(f"[{addr}] 块序号错误: 期望 {received_blocks}, 收到 {block_num}")
    return
```

如果一切正常，服务器把 `block_data` 写入临时文件，然后给客户端发送 ACK。

---

<a id="msg-ack"></a>
## 3. 上传块确认报文：`MSG_ACK = 0x03`

方向：

```text
服务器 -> 客户端
```

作用：

服务器每成功接收并写入一个上传文件块，就回复一个 ACK，告诉客户端：

> 这个块我已经收到了，你可以继续发下一个块。

### 报文结构

```text
+----------------------+---------------------------+
| Header               | Data                      |
+----------------------+---------------------------+
| 9 字节                | block_num                 |
+----------------------+---------------------------+
```

数据区结构：

```text
+-------------------------+
| block_num               |
+-------------------------+
| 4 字节，无符号整数，大端   |
+-------------------------+
```

所以 ACK 完整报文长度固定为：

```text
9 字节 Header + 4 字节 Data = 13 字节
```

### 服务器端代码映射

服务器在 `send_ack(sock, block_num)` 中发送 ACK：

```python
data = struct.pack('>I', block_num)
header = struct.pack('>HBIH', MAGIC, MSG_ACK, len(data), 0)
checksum = calc_crc16(header + data)
header = struct.pack('>HBIH', MAGIC, MSG_ACK, len(data), checksum)
sock.sendall(header + data)
```

字段对应关系：

| 协议字段    | 代码来源                 |
| ----------- | ------------------------ |
| `msg_type`  | `MSG_ACK = 0x03`         |
| `length`    | `len(data)`，固定为 4    |
| `block_num` | 服务器刚刚成功接收的块号 |

### 客户端代码映射

客户端发送每个上传文件块后，会等待 13 字节 ACK：

```python
ack_data = receive_with_timeout(sock, 13, TIMEOUT)
```

然后检查 ACK 头部中的魔数和消息类型：

```python
ack_magic, ack_type, ack_len, ack_checksum = struct.unpack('>HBIH', ack_data[:9])
if ack_type == MSG_ACK and ack_magic == MAGIC:
    print(f"块 {block_num}/{total_blocks} 发送成功")
    break
```

需要说明的是，服务器的 ACK 数据区里确实带了 `block_num`，但是当前客户端代码只检查 ACK 的 `magic` 和 `msg_type`，没有进一步解析 ACK 数据区里的块号，也没有重新计算 ACK 的 CRC。

---

<a id="msg-verify-result"></a>
## 4. 上传最终结果报文：`MSG_VERIFY_RESULT = 0x04`

方向：

```text
服务器 -> 客户端
```

作用：

当所有上传文件块都传完之后，服务器会做最终处理：

1. 判断实际收到的文件大小是否等于文件信息报文中声明的大小；
2. 如果大小正确，把临时文件改名为最终文件；
3. 保存成功后给客户端返回最终结果。

### 报文结构

```text
+----------------------+--------------------------------+
| Header               | Data                           |
+----------------------+--------------------------------+
| 9 字节                | result_code + message          |
+----------------------+--------------------------------+
```

数据区结构：

```text
+----------------------+-------------------------------+
| result_code          | message                       |
+----------------------+-------------------------------+
| 1 字节                | UTF-8 字符串，可为空           |
+----------------------+-------------------------------+
```

其中 `result_code` 的含义是：

| result_code | 含义 |
| ----------: | ---- |
|         `0` | 成功 |
|         `1` | 失败 |

### 服务器端代码映射

服务器通过 `send_result(sock, success, message="")` 发送最终结果：

```python
result_data = struct.pack('>B', 0 if success else 1) + message.encode('utf-8')
header = struct.pack('>HBIH', MAGIC, MSG_VERIFY_RESULT, len(result_data), 0)
checksum = calc_crc16(header + result_data)
header = struct.pack('>HBIH', MAGIC, MSG_VERIFY_RESULT, len(result_data), checksum)
sock.sendall(header + result_data)
```

上传成功时，服务器调用：

```python
send_result(sock, True, "传输成功")
```

上传失败时，服务器可能调用：

```python
send_result(sock, False, "文件大小不匹配")
send_result(sock, False, "服务器写入文件失败")
send_result(sock, False, "服务器保存文件失败")
```

### 客户端代码映射

客户端在上传所有块之后等待服务器最终校验结果：

```python
result_header = receive_with_timeout(sock, 9, TIMEOUT * 2)
magic, msg_type, length, checksum = struct.unpack('>HBIH', result_header[:9])
```

客户端要求：

```python
magic == MAGIC
msg_type == MSG_VERIFY_RESULT
```

然后读取数据区：

```python
result_data = receive_with_timeout(sock, length, TIMEOUT)
result = result_data[0]
```

客户端只根据第一个字节判断成功还是失败：

```python
if result == 0:
    print("传输成功: 文件校验通过")
    return True
else:
    print("传输失败: 文件校验失败")
    return False
```

需要注意的是，服务器发送的数据区中除了第一个结果字节，还可能带有文本消息；但是当前客户端没有把后面的 `message` 解码打印出来，只使用了第一个字节。

---

<a id="msg-list-backups"></a>
## 5. 查看备份列表请求报文：`MSG_LIST_BACKUPS = 0x05`

方向：

```text
客户端 -> 服务器
```

作用：

客户端向服务器询问：

> 你那里现在有哪些备份文件？

### 报文结构

```text
+----------------------+
| Header               |
+----------------------+
| 9 字节                |
+----------------------+
```

这条报文没有数据区。

所以：

```text
length = 0
data = b''
```

### 客户端代码映射

在 `client.py` 的 `list_backups(sock)` 中：

```python
header = make_header(MSG_LIST_BACKUPS, b'')
send_with_retry(sock, header)
```

因为数据区为空，所以发送时只发送 9 字节头部。

### 服务器端代码映射

服务器在 `handle_client()` 中识别该消息：

```python
if msg_type == MSG_LIST_BACKUPS:
    print(f"[{addr}] 请求备份列表")
    send_backup_list(sock)
    continue
```

---

<a id="msg-backup-list"></a>
## 6. 备份列表响应报文：`MSG_BACKUP_LIST = 0x06`

方向：

```text
服务器 -> 客户端
```

作用：

服务器把 `server_backup` 目录下已有的普通文件和文件夹列表返回给客户端。

和最基础版本相比，现在返回的内容更完整，不再只是“文件名 + 文件大小”，而是包含：

- 是普通文件还是文件夹；
- 备份名称；
- 大小；
- 备份时间；
- 对应客户端 IP。

### 报文结构

```text
+----------------------+----------------------------------------------------------------+
| Header               | Data                                                           |
+----------------------+----------------------------------------------------------------+
| 9 字节                | 多个 item_type + filename + size + timestamp + client_ip 条目 |
+----------------------+----------------------------------------------------------------+
```

数据区不是 JSON，也不是文本表格，而是连续拼接的二进制结构。

每一个条目的结构是：

```text
+-------------+-------------+-----------+-------------+-------------+
| item_type   | filename    | size      | timestamp   | client_ip   |
+-------------+-------------+-----------+-------------+-------------+
| 1 字节       | UTF-8 + \0  | 8 字节     | 8 字节       | UTF-8 + \0  |
+-------------+-------------+-----------+-------------+-------------+
```

其中：

- `item_type = 1` 表示普通文件；
- `item_type = 2` 表示文件夹；
- `size` 对普通文件表示真实大小；当前目录项一般记为 `0`；
- `timestamp` 是 Unix 时间戳；
- `client_ip` 表示这个备份对应的客户端来源地址。

如果服务器上没有备份文件，数据区为空：

```text
length = 0
data = b''
```

不过要特别注意当前客户端代码中的一个实现细节：协议层面上“空列表”确实是 `length = 0`、`data = b''`，但是 `client.py` 在 `list_backups(sock)` 里读取完数据后先判断了 `if not list_data or len(list_data) < length:`。在 Python 中，空字节串 `b''` 会被当成 `False`，所以当服务器真的返回空列表时，客户端当前实现更可能先打印“备份列表数据不完整”并返回失败，而不是打印后面代码里的“服务器上没有备份文件”。README 这里按照源代码真实行为说明，不修改源代码。

### 服务器端代码映射

服务器在 `send_backup_list(sock)` 中扫描备份目录时，会同时检查普通文件和目录：

```python
items = []
if os.path.exists(BACKUP_FOLDER):
    for filename in sorted(os.listdir(BACKUP_FOLDER)):
        if filename.startswith('.'):
            continue
        filepath = os.path.join(BACKUP_FOLDER, filename)
        if os.path.isfile(filepath):
            size = os.path.getsize(filepath)
            items.append((filename, size, False))
        elif os.path.isdir(filepath):
            items.append((filename, 0, True))
```

然后还会读取每个备份对应的元数据：

```python
timestamp, client_ip = get_backup_metadata(filename)
item_type = 2 if is_dir else 1
```

最后按“类型 + 名称 + 大小 + 时间 + IP”连续编码到 `list_data` 中。

### 客户端代码映射

客户端在 `list_backups(sock)` 中接收响应后，会按下面顺序解析：

1. 先读 1 字节 `item_type`；
2. 找 `\x00` 解析 `filename`；
3. 读 8 字节 `file_size`；
4. 读 8 字节 `timestamp`；
5. 再找 `\x00` 解析 `client_ip`。

典型解析片段：

```python
item_type = list_data[pos]
pos += 1
null_pos = list_data.find(b'\x00', pos)
filename = list_data[pos:null_pos].decode('utf-8')
pos = null_pos + 1
file_size = struct.unpack('>Q', list_data[pos:pos+8])[0]
pos += 8
timestamp = struct.unpack('>Q', list_data[pos:pos+8])[0]
pos += 8
```

如果 `item_type == 2`，客户端会在展示时加上 `[DIR]` 标记。

---

<a id="msg-download-request"></a>
## 7. 下载请求报文：`MSG_DOWNLOAD_REQUEST = 0x07`

方向：

```text
客户端 -> 服务器
```

作用：

客户端告诉服务器：

> 我想下载某个备份目标，请把这个文件或文件夹发给我。

### 报文结构

```text
+----------------------+--------------------------+
| Header               | Data                     |
+----------------------+--------------------------+
| 9 字节                | filename                 |
+----------------------+--------------------------+
```

数据区结构：

```text
+----------------------+
| filename             |
+----------------------+
| UTF-8 编码的文件名     |
+----------------------+
```

这里没有 `\0` 分隔符，因为整个数据区都是文件名，长度直接由报文头里的 `length` 字段给出。

### 客户端代码映射

在 `client.py` 的 `download_file(sock, filename)` 中：

```python
request_data = filename.encode('utf-8')
header = make_header(MSG_DOWNLOAD_REQUEST, request_data)
send_with_retry(sock, header + request_data)
```

### 服务器端代码映射

服务器在 `handle_client()` 中识别下载请求：

```python
elif msg_type == MSG_DOWNLOAD_REQUEST:
    data = receive_full(sock, length)
```

先做 CRC 校验：

```python
if calc_crc16(hdr[:7] + b'\x00\x00' + data) != checksum:
    print(f"[{addr}] 下载请求校验失败")
    return
```

再解析文件名：

```python
filename = data.decode('utf-8')
filepath = os.path.join(BACKUP_FOLDER, filename)
```

---

<a id="msg-download-info"></a>
## 8. 下载信息响应报文：`MSG_DOWNLOAD_INFO = 0x08`

方向：

```text
服务器 -> 客户端
```

作用：

服务器收到下载请求后，先不直接发送文件块，而是先告诉客户端：

- 这个目标是否存在；
- 如果存在，它是普通文件还是文件夹；
- 如果存在，它总共有多大。

### 报文结构

```text
+----------------------+--------------------------------------+
| Header               | Data                                 |
+----------------------+--------------------------------------+
| 9 字节                | success + file_type + file_size      |
+----------------------+--------------------------------------+
```

数据区有两种情况。

#### 情况一：目标不存在

```text
+-----------+
| success   |
+-----------+
| 1 字节     |
+-----------+
```

此时：

```text
success = 0
length = 1
```

#### 情况二：目标存在

```text
+-----------+-----------+-------------------------+
| success   | file_type | file_size               |
+-----------+-----------+-------------------------+
| 1 字节     | 1 字节     | 8 字节，无符号整数，大端   |
+-----------+-----------+-------------------------+
```

此时：

```text
success = 1
length = 10
```

其中：

- `file_type = 1` 表示普通文件；
- `file_type = 2` 表示文件夹。

### 服务器端代码映射

服务器在 `send_download_info(sock, success, file_type=0, size=0)` 中构造该报文：

```python
if not success:
    info_data = struct.pack('>B', 0)
else:
    info_data = struct.pack('>BBQ', 1, file_type, size)
```

这表示：

- 如果目标不存在，只发 1 字节状态位；
- 如果目标存在，就发“状态 + 类型 + 大小”。

### 客户端代码映射

客户端先接收 9 字节头部，并检查类型：

```python
info_header = receive_with_timeout(sock, 9, TIMEOUT)
magic, msg_type, length, checksum = struct.unpack('>HBIH', info_header[:9])
if magic != MAGIC or msg_type != MSG_DOWNLOAD_INFO:
    print("收到无效的下载信息响应")
    return False
```

然后读取数据区：

```python
info_data = receive_with_timeout(sock, length, TIMEOUT)
```

当前客户端要求下载信息数据至少有 10 字节：

```python
if len(info_data) < 10:
    print("下载信息格式错误")
    return False
```

之后解析：

```python
success = info_data[0]
file_type = info_data[1]
file_size = struct.unpack('>Q', info_data[2:10])[0]
```

这里需要特别说明一个实现细节：

- 服务器在目标不存在时只发送 1 字节的 `success = 0`；
- 但是客户端当前代码在判断 `success` 之前，先要求 `len(info_data) >= 10`；
- 因此按照当前代码，目标不存在时客户端更可能先打印“下载信息格式错误”，而不是走到后面的“文件或文件夹不存在”。

这属于当前代码实现中的行为特点，README 这里只如实说明，不修改源代码。

---

<a id="msg-download-block"></a>
## 9. 下载文件块报文：`MSG_DOWNLOAD_BLOCK = 0x09`

方向：

```text
服务器 -> 客户端
```

作用：

服务器把客户端要下载的文件按块发送给客户端。

下载时同样使用：

```python
BLOCK_SIZE = 4096
```

也就是每个下载块最多携带 4096 字节文件内容。

### 报文结构

```text
+----------------------+----------------------------------+
| Header               | Data                             |
+----------------------+----------------------------------+
| 9 字节                | block_num + block_data           |
+----------------------+----------------------------------+
```

数据区结构：

```text
+-------------------------+-------------------------------+
| block_num               | block_data                    |
+-------------------------+-------------------------------+
| 4 字节，无符号整数，大端   | 文件内容，最多 4096 字节        |
+-------------------------+-------------------------------+
```

这个结构和上传文件块 `MSG_FILE_BLOCK` 很像，只是方向相反。

### 服务器端代码映射

服务器在 `send_download_block(sock, filepath, block_num, block_data)` 中构造下载块：

```python
data = struct.pack('>I', block_num) + block_data
header = struct.pack('>HBIH', MAGIC, MSG_DOWNLOAD_BLOCK, len(data), 0)
checksum = calc_crc16(header + data)
header = struct.pack('>HBIH', MAGIC, MSG_DOWNLOAD_BLOCK, len(data), checksum)
sock.sendall(header + data)
```

服务器读取文件并不断发送：

```python
with open(filepath, 'rb') as f:
    block_num = 0
    while True:
        data = f.read(BLOCK_SIZE)
        if not data:
            break
        block_num += 1
        send_download_block(sock, filepath, block_num, data)
```

### 客户端代码映射

客户端在 `download_file(sock, filename)` 中循环接收下载块：

```python
while received_size < file_size:
    block_header = receive_with_timeout(sock, 9, TIMEOUT)
    magic, msg_type, length, checksum = struct.unpack('>HBIH', block_header[:9])
```

客户端要求：

```python
magic == MAGIC
msg_type == MSG_DOWNLOAD_BLOCK
```

然后读取数据区：

```python
block_data = receive_with_timeout(sock, length, TIMEOUT)
recv_block_num = struct.unpack('>I', block_data[:4])[0]
data = block_data[4:]
```

客户端会检查下载块序号是否连续：

```python
if recv_block_num != block_num:
    print(f"块序号错误: 期望 {block_num}, 收到 {recv_block_num}")
    return False
```

然后把真正的文件内容写入本地文件：

```python
f.write(data)
```

下载的文件保存到客户端本地目录：

```text
downloaded_backups/文件名
```

对应代码：

```python
download_path = os.path.join("downloaded_backups", filename)
os.makedirs("downloaded_backups", exist_ok=True)
```

需要注意的是，当前下载流程中客户端不会对每个下载块发送 ACK，服务器是连续发送下载块；客户端只按顺序接收并检查块号。

另外，如果下载目标是文件夹，服务器会先把整个目录临时压缩为 ZIP 文件，再按普通文件下载流程发送给客户端；客户端收到后会保存为：

```text
downloaded_backups/文件夹名.zip
```

---

<a id="msg-delete-request"></a>
## 10. 删除请求报文：`MSG_DELETE_REQUEST = 0x0A`

方向：

```text
客户端 -> 服务器
```

作用：

客户端告诉服务器：

> 我想删除某个备份文件或备份文件夹。

### 报文结构

```text
+----------------------+--------------------------+
| Header               | Data                     |
+----------------------+--------------------------+
| 9 字节                | filename                 |
+----------------------+--------------------------+
```

数据区结构：

```text
+----------------------+
| filename             |
+----------------------+
| UTF-8 编码的备份名称   |
+----------------------+
```

服务器会在收到该报文后先做 CRC 校验，再判断该名称对应的是普通文件、目录，还是根本不存在。

---

<a id="msg-delete-result"></a>
## 11. 删除结果报文：`MSG_DELETE_RESULT = 0x0B`

方向：

```text
服务器 -> 客户端
```

作用：

服务器告诉客户端删除是否成功。

### 报文结构

```text
+----------------------+----------------------+
| Header               | Data                 |
+----------------------+----------------------+
| 9 字节                | status               |
+----------------------+----------------------+
```

数据区结构：

```text
+----------------------+
| status               |
+----------------------+
| 1 字节               |
+----------------------+
```

其中：

- `status = 1` 表示删除成功；
- `status = 0` 表示删除失败。

客户端收到后会打印删除成功或删除失败信息。

---

<a id="msg-resume-query"></a>
## 12. 断点查询报文：`MSG_RESUME_QUERY = 0x0C`

方向：

```text
客户端 -> 服务器
```

作用：

客户端在发送 `MSG_FILE_INFO` 之后，不会立刻开始发文件块，而是先主动询问服务器：

- 这个文件之前有没有传过一部分；
- 如果有，服务器目前已经收到多少；
- 客户端应该从第几块继续发送。

也就是说，这条报文是“断点续传协商”的入口。

### 报文结构

```text
+----------------------+----------------------------------------------+
| Header               | Data                                         |
+----------------------+----------------------------------------------+
| 9 字节                | 文件名 UTF-8 + '\0' + 文件大小               |
+----------------------+----------------------------------------------+
```

数据区结构：

```text
+------------------------+--------------+-------------------------+
| filename               | 分隔符        | file_size               |
+------------------------+--------------+-------------------------+
| 不固定长度，UTF-8 编码   | 1 字节 0x00   | 8 字节，无符号整数，大端   |
+------------------------+--------------+-------------------------+
```

可以看到，这个数据区结构和 `MSG_FILE_INFO` 很像，都是“文件名 + 0x00 + 文件大小”。

这样设计的目的是让服务器能够再次确认：

- 客户端现在查询的是哪个文件；
- 这个文件的总大小是否与刚才上传声明一致。

### 客户端代码映射

在 `client.py` 的 `send_file(sock, file_path, remote_name=None)` 中，客户端发送完 `MSG_FILE_INFO` 之后，立刻构造断点查询：

```python
resume_query_data = filename.encode('utf-8') + b'\x00' + struct.pack('>Q', file_size)
resume_query_header = make_header(MSG_RESUME_QUERY, resume_query_data)
send_with_retry(sock, resume_query_header + resume_query_data)
```

字段对应关系：

| 协议字段    | 代码来源                              |
| ----------- | ------------------------------------- |
| `magic`     | `MAGIC = 0x424B`                      |
| `msg_type`  | `MSG_RESUME_QUERY = 0x0C`             |
| `length`    | `len(resume_query_data)`              |
| `checksum`  | `calc_crc16(header_with_zero_checksum + resume_query_data)` |
| `filename`  | `filename.encode('utf-8')`            |
| `file_size` | `struct.pack('>Q', file_size)`        |

### 服务器端代码映射

服务器在 `handle_client(sock, addr)` 中，接收完 `MSG_FILE_INFO` 后会继续读取这条断点查询报文：

```python
resume_header = receive_full(sock, 9)
resume_valid, resume_type, resume_len, resume_checksum, resume_hdr = verify_header(resume_header)
```

之后服务器会：

1. 检查消息类型是不是 `MSG_RESUME_QUERY`；
2. 读取数据区；
3. 校验 CRC；
4. 找到 `\x00` 分隔符；
5. 解析文件名和文件大小；
6. 判断它们是否和刚才 `MSG_FILE_INFO` 中的内容一致。

关键解析代码：

```python
resume_null_pos = resume_data.find(b'\x00')
resume_filename = resume_data[:resume_null_pos].decode('utf-8')
resume_size = struct.unpack('>Q', resume_data[resume_null_pos + 1:resume_null_pos + 9])[0]
```

一致性检查：

```python
if resume_filename != filename or resume_size != expected_size:
    send_result(sock, False, "断点查询参数不一致")
    return
```

也就是说，服务器不会接受一个和上传声明不匹配的断点查询请求。

---

<a id="msg-resume-info"></a>
## 13. 断点信息响应报文：`MSG_RESUME_INFO = 0x0D`

方向：

```text
服务器 -> 客户端
```

作用：

服务器用这条报文告诉客户端当前应该从哪里继续上传，核心信息有两个：

- 下一块号 `next_block`；
- 已接收字节数 `received_size`。

客户端拿到这两个值之后，就可以：

- 把本地文件读指针移动到 `received_size`；
- 把块号设置为 `next_block - 1`；
- 从下一块继续发送，而不是从头重传。

### 报文结构

```text
+----------------------+----------------------------------+
| Header               | Data                             |
+----------------------+----------------------------------+
| 9 字节                | next_block + received_size       |
+----------------------+----------------------------------+
```

数据区结构：

```text
+-------------------------+-------------------------+
| next_block              | received_size           |
+-------------------------+-------------------------+
| 4 字节，无符号整数，大端   | 8 字节，无符号整数，大端   |
+-------------------------+-------------------------+
```

因此：

```text
length = 12
```

如果服务器之前完全没有收到过这个文件，那么通常会返回：

```text
next_block = 1
received_size = 0
```

如果已经传过一部分，就会返回更大的块号和字节数。

### 服务器端代码映射

服务器在 `send_resume_info(sock, next_block, received_size)` 中构造该报文：

```python
data = struct.pack('>IQ', next_block, received_size)
header = struct.pack('>HBIH', MAGIC, MSG_RESUME_INFO, len(data), 0)
checksum = calc_crc16(header + data)
header = struct.pack('>HBIH', MAGIC, MSG_RESUME_INFO, len(data), checksum)
sock.sendall(header + data)
```

也就是说：

- `next_block` 使用 `>I` 打包为 4 字节；
- `received_size` 使用 `>Q` 打包为 8 字节；
- 整个数据区长度固定为 12 字节。

服务器在真正调用发送前，会先根据断点记录计算：

```python
next_block = received_blocks + 1
```

然后发送：

```python
send_resume_info(sock, next_block, received_size)
```

### 客户端代码映射

客户端在 `send_file()` 中先接收 9 字节头部：

```python
resume_header = receive_with_timeout(sock, 9, TIMEOUT)
resume_magic, resume_type, resume_len, resume_checksum = struct.unpack('>HBIH', resume_header[:9])
```

客户端要求：

```python
resume_magic == MAGIC
resume_type == MSG_RESUME_INFO
```

然后再读取数据区：

```python
resume_data = receive_with_timeout(sock, resume_len, TIMEOUT)
```

并重新计算 CRC：

```python
if calc_crc16(resume_header[:7] + b'\x00\x00' + resume_data) != resume_checksum:
    print("断点信息校验失败")
    return False
```

最后解析出：

```python
next_block, sent_size = struct.unpack('>IQ', resume_data[:12])
```

并把它们用于真正的续传起点：

```python
f.seek(sent_size)
block_num = next_block - 1
```

这就是为什么当前客户端能够在重新连接后直接从断点处继续上传，而不是把整个文件重新发送一遍。

---

<a id="upload-flow"></a>
## 六、上传文件的完整流程

上传文件是这个协议里最复杂的一条流程，可以按下面的顺序理解。

和最基础版本相比，当前上传流程已经变成了“支持断点续传”的版本，所以不再是简单的：

```text
MSG_FILE_INFO -> 一直发文件块 -> 收尾
```

而是：

```text
MSG_FILE_INFO -> MSG_RESUME_QUERY -> MSG_RESUME_INFO -> 多个 MSG_FILE_BLOCK/MSG_ACK -> MSG_VERIFY_RESULT
```

### 第 1 步：客户端检查本地文件

客户端在菜单中选择“上传文件”后，会让用户输入文件路径。

客户端会检查：

1. 路径不能为空；
2. 文件是否存在；
3. 路径是否真的是文件，而不是文件夹；
4. 是否有读取权限。

相关代码在 `client.py` 的 `main()` 中：

```python
if not os.path.exists(file_path):
    print("文件不存在")
    continue

if not os.path.isfile(file_path):
    print("路径不是文件")
    continue

try:
    with open(file_path, 'rb'):
        pass
except PermissionError:
    print("没有读取文件的权限")
    continue
```

### 第 2 步：客户端发送 `MSG_FILE_INFO`

客户端先把文件名和文件大小发给服务器。

数据区格式是：

```text
filename + 0x00 + file_size
```

如果上传的是文件夹，那么客户端实际上会先把文件夹压缩成 ZIP 文件，再把远程名称写成：

```text
__FOLDER__:文件夹名
```

服务器通过这个前缀识别“这是文件夹备份”。

### 第 3 步：客户端发送 `MSG_RESUME_QUERY`

这是新增的断点续传步骤。

客户端在发完 `MSG_FILE_INFO` 之后，不会立刻开始发块，而是会再发送一次断点查询报文：

```text
MSG_RESUME_QUERY
```

它的数据区与 `MSG_FILE_INFO` 一样，也是：

```text
filename + 0x00 + file_size
```

作用是询问服务器：

> 这个客户端上传的这个文件，你之前是否已经接收过一部分？

### 第 4 步：服务器解析文件信息并查询断点

服务器收到 `MSG_FILE_INFO` 后：

1. 检查 `magic`；
2. 检查消息类型是不是 `MSG_FILE_INFO`；
3. 读取数据区；
4. 进行 CRC16 校验；
5. 找到 `\x00`，解析文件名；
6. 读取后 8 字节，解析文件大小；
7. 计算应该收到多少个块。

服务器计算总块数：

```python
expected_blocks = (expected_size + BLOCK_SIZE - 1) // BLOCK_SIZE
```

这个公式的作用是向上取整。

然后服务器会接着接收 `MSG_RESUME_QUERY`，并按：

```text
客户端IP + 文件名
```

查找是否已有断点记录。

当前服务器的断点信息会持久化到：

```text
server_backup/.upload_progress.json
```

### 第 5 步：服务器返回 `MSG_RESUME_INFO`

服务器把断点信息返回给客户端。

数据区是：

```text
next_block + received_size
```

也就是：

- 下一块应该从第几块开始发；
- 服务器当前已经收到了多少字节。

如果之前没有任何进度，通常返回：

```text
next_block = 1
received_size = 0
```

如果有断点，则返回真实断点位置。

### 第 6 步：服务器创建备份目录和稳定临时文件

服务器把备份文件保存到：

```text
server_backup
```

如果目录不存在，服务器会创建。

服务器不会直接写最终文件，而是先写临时文件。临时文件路径使用“客户端IP + 文件名”，因为重连后端口可能变化。当前实现会根据上传键生成稳定临时文件路径，以便断点续传时继续复用。

这样做的好处是：

- 传输中途失败，不会留下一个看起来像正常文件但内容不完整的备份文件；
- 连接断开后还可以继续使用原来的临时文件续传。

### 第 7 步：客户端从断点位置开始分块发送 `MSG_FILE_BLOCK`

客户端收到 `MSG_RESUME_INFO` 后，会先执行：

```python
f.seek(sent_size)
block_num = next_block 
```

这意味着：

- 文件读指针直接跳到服务器已收到的位置；
- 块号从服务器要求的下一块开始；
- 已成功上传的那一部分不会再重复发送。

之后客户端每次最多读取 4096 字节：

```python
data = f.read(BLOCK_SIZE)
```

然后加上 4 字节块号：

```python
block_data = struct.pack('>I', block_num) + data
```

再发送完整报文：

```python
full_message = header + block_data
```

### 第 8 步：服务器接收文件块并回复 ACK

服务器每收到一个块，会检查：

1. 报文头是否合法；
2. 消息类型是不是 `MSG_FILE_BLOCK`；
3. 数据区是否完整；
4. CRC 是否正确；
5. 块号是否是当前期望的下一个块；
6. 数据是否超出声明总大小；
7. 文件内容是否能成功写入临时文件。

如果这些都成功，服务器会：

1. 把块内容写入临时文件；
2. 更新 `received_size` 和 `received_blocks`；
3. 更新内存中的断点信息；
4. 把断点同步写入 `.upload_progress.json`；
5. 发送 ACK：

```python
send_ack(sock, block_num)
```

### 第 9 步：客户端等待 ACK，失败会重试

客户端每发送一个块之后，会等待服务器返回 ACK。

等待代码：

```python
ack_data = receive_with_timeout(sock, 13, TIMEOUT)
```

如果没收到 ACK，或者 ACK 类型不对，客户端会重试。

重试次数由下面的常量控制：

```python
MAX_RETRIES = 3
TIMEOUT = 10
```

也就是说，每个块最多尝试发送 3 次，每次等待 ACK 的超时时间是 10 秒。

### 第 10 步：传输中断时服务器保留断点

这是当前版本和最基础版的核心区别之一。

如果上传过程中发生：

- 客户端断开；
- 套接字超时；
- 连接被重置；

服务器会尽量：

- 关闭临时文件句柄；
- 保留临时文件；
- 保留 `.upload_progress.json` 中的断点记录；
- 等待客户端稍后重新连接续传。

因此现在“网络中断”并不一定意味着“从头重传整个文件”。

### 第 11 步：服务器确认总大小并保存最终文件

服务器持续接收，直到：

```python
received_size < expected_size
```

这个条件不再成立，也就是实际收到的字节数已经达到文件信息中声明的文件大小。

之后服务器会再次检查：

```python
if received_size != expected_size:
    send_result(sock, False, "文件大小不匹配")
    return
```

如果大小正确：

- 对普通文件：把临时文件改成最终文件名；
- 对文件夹备份：把临时 ZIP 解压为最终目录。

当前服务器保存时，如果目标名已存在，会自动改名为：

```text
name(1)
name(2)
...
```

也就是说，不是覆盖旧备份，而是生成新的不冲突名称。

### 第 12 步：服务器删除断点记录并发送最终结果 `MSG_VERIFY_RESULT`

最终保存成功后，服务器会：

1. 删除对应临时文件；
2. 删除 `.upload_progress.json` 中该任务的断点记录；
3. 返回最终结果：

```python
send_result(sock, True, "传输成功")
```

客户端收到 `MSG_VERIFY_RESULT` 后，如果数据区第一个字节是 `0`，就认为上传成功：

```python
if result == 0:
    print("传输成功: 文件校验通过")
    return True
```

---

<a id="list-flow"></a>
## 七、查看服务器备份列表的完整流程

查看备份列表的过程比较简单。

### 第 1 步：客户端发送列表请求

客户端发送：

```text
MSG_LIST_BACKUPS
```

这条消息没有数据区。

### 第 2 步：服务器扫描 `server_backup` 目录

服务器现在不只检查普通文件，也会检查目录，并读取对应元数据。

也就是说，当前列表里可能出现两类备份：

- 普通文件备份；
- 文件夹备份。

### 第 3 步：服务器返回备份列表

服务器把每个条目编码成：

```text
item_type + 文件名 UTF-8 + 0x00 + 文件大小 8 字节 + 时间戳 8 字节 + 客户端IP UTF-8 + 0x00
```

多个条目就连续拼接。

因此当前列表响应中包含的信息比基础版更多：

- 类型（文件 / 文件夹）；
- 名称；
- 大小；
- 备份时间；
- 客户端 IP。

### 第 4 步：客户端解析并打印

客户端收到 `MSG_BACKUP_LIST` 后，会按条目逐个解析，并以表格形式打印。

如果 `item_type == 2`，客户端会显示：

```text
[DIR]
```

表示这是一个目录备份。

从协议设计上说，如果数据区为空，就表示服务器没有备份文件。客户端代码里也写了下面这个提示分支：

```text
服务器上没有备份文件
```

但按照当前 `client.py` 的实际判断顺序，空字节串 `b''` 会先被 `if not list_data` 判断为接收失败，所以空列表场景下实际更可能输出：

```text
备份列表数据不完整
```

也就是说，协议本身支持空列表，但当前客户端实现对空列表响应的处理存在这个行为特点。

---

<a id="download-flow"></a>
## 八、下载备份文件的完整流程

下载流程可以理解为“客户端先问有没有这个目标，服务器回答有的话再分块发送”。

### 第 1 步：客户端输入备份名称

用户在客户端菜单中选择“下载备份文件/文件夹”后，输入要下载的备份名称。

客户端除了检查名称不能为空外，还会检查名称是否合法，避免路径穿越等非法输入。

### 第 2 步：客户端发送 `MSG_DOWNLOAD_REQUEST`

数据区就是 UTF-8 编码后的备份名称。

### 第 3 步：服务器检查目标是否存在以及类型

服务器把名称拼接到备份目录下面：

```python
filepath = os.path.join(BACKUP_FOLDER, filename)
```

然后判断：

- 是否存在；
- 是普通文件还是目录。

### 第 4 步：服务器发送 `MSG_DOWNLOAD_INFO`

如果目标存在，服务器发送：

```text
success = 1
file_type = 1 或 2
file_size = 总大小
```

如果目标不存在，服务器发送：

```text
success = 0
```

其中：

- `file_type = 1` 表示普通文件；
- `file_type = 2` 表示文件夹。

### 第 5 步：如果目标是文件夹，服务器先压缩为 ZIP

如果服务器发现目标是目录，不会直接把目录结构逐层发送，而是会先把整个目录打包为临时 ZIP 文件，然后再按普通文件分块发送。

因此在客户端看来，文件夹下载最终会保存成 ZIP。

### 第 6 步：客户端准备本地下载目录

如果目标存在，客户端创建本地目录：

```text
downloaded_backups
```

保存路径规则是：

- 普通文件：

```text
downloaded_backups/文件名
```

- 文件夹备份：

```text
downloaded_backups/文件夹名.zip
```

如果本地已存在同名文件，客户端会自动生成带数字后缀的不冲突文件名。

### 第 7 步：服务器分块发送 `MSG_DOWNLOAD_BLOCK`

服务器每次读取最多 4096 字节，并附带块号发送给客户端。

### 第 8 步：客户端按顺序接收并写入本地文件

客户端循环接收，直到实际接收的文件内容大小达到 `file_size`。

每收到一个下载块，客户端检查块号是否连续：

```python
if recv_block_num != block_num:
    print(f"块序号错误: 期望 {block_num}, 收到 {recv_block_num}")
    return False
```

如果块号正确，就把数据写入文件：

```python
f.write(data)
```

下载完成后打印：

```python
print(f"下载完成: {download_path}")
```

---

<a id="connection-flow"></a>
## 九、客户端与服务器端的连接过程

这一部分很重要：当前程序使用的是 TCP，所以连接是否成功主要由操作系统的 TCP 连接机制判断，不是靠自定义应用层报文判断。

### 1. 服务器端先启动监听

服务器端 `main()` 函数中，先创建 TCP socket：

```python
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
```

这里：

- `socket.AF_INET` 表示使用 IPv4；
- `socket.SOCK_STREAM` 表示使用 TCP。

然后设置端口复用：

```python
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
```

这样做的常见作用是：服务器重启时，端口更容易被重新绑定，减少“端口还被占用”的情况。

之后绑定地址和端口：

```python
server.bind(('0.0.0.0', port))
```

`0.0.0.0` 表示监听本机所有网卡地址。也就是说，如果机器有多个 IP，服务器都可以在这个端口上接受连接。

然后开始监听：

```python
server.listen(5)
```

这里的 `5` 是监听队列大小，表示允许等待处理的连接排队。

服务器打印：

```text
服务器监听端口: port
等待客户端连接...
```

此时服务器已经准备好了，可以接受客户端连接。

---

### 2. 客户端创建 TCP socket 并连接服务器

客户端在 `connect_to_server(ip, port)` 中创建 socket：

```python
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
```

然后调用：

```python
sock.connect((ip, port))
```

这一步会让操作系统发起 TCP 连接，也就是通常所说的 TCP 三次握手。

从代码层面看，程序没有自己实现三次握手，因为三次握手是操作系统 TCP 协议栈完成的。

日常语言理解：

> 客户端打电话给服务器。服务器如果正在监听这个号码，电话就能接通；如果服务器没开、IP 不对、端口不对、防火墙拦截，电话就打不通。

---

### 3. 客户端怎样判断连接成功？

客户端判断连接成功的核心依据是：

```python
sock.connect((ip, port))
```

是否正常返回。

在 `client.py` 中：


```python
try:
    sock.connect((ip, port))
    sock.settimeout(TIMEOUT)
    print(f"成功连接到服务器 {ip}:{port}")
    return sock, None
except socket.timeout:
    sock.close()
    return None, "连接超时"
except socket.error as e:
    sock.close()
    return None, f"连接失败: {e}"
```

也就是说：

- 如果 `connect()` 没有抛出异常，客户端认为 TCP 连接成功；
- 然后设置 socket 超时时间 `TIMEOUT = 10`；
- 返回 `(sock, None)`；
- `main()` 收到 `err is None` 后打印“连接成功！”并进入菜单。

对应主流程：

```python
sock, err = connect_to_server(ip, port)
if err:
    print(f"连接失败: {err}")
    ...

print("连接成功！")
break
```

所以客户端的连接成功判断非常直接：

```text
connect() 成功返回 = 连接成功
connect() 抛出异常 = 连接失败
```

---

### 4. 服务器端怎样知道有客户端连接成功？

服务器端在监听后进入循环：

```python
client_sock, client_addr = server.accept()
```

`accept()` 是一个阻塞调用。意思是：

> 如果没有客户端连接，它就一直等；如果有客户端完成 TCP 连接，它就返回一个新的客户端 socket 和客户端地址。

返回值含义：

- `client_sock`：专门用于和这个客户端通信的新 socket；
- `client_addr`：客户端地址，一般是 `(客户端IP, 客户端端口)`。

服务器拿到连接后，为这个客户端创建一个线程：

```python
thread = threading.Thread(target=handle_client, args=(client_sock, client_addr))
thread.daemon = True
thread.start()
```

在线程函数 `handle_client(sock, addr)` 一开始，服务器打印：

```python
print(f"客户端连接: {addr}")
```

所以服务器端判断连接成功的依据是：

```text
server.accept() 成功返回 = 有客户端连接成功
```

---

### 5. 这个程序有没有应用层握手？

从当前代码来看，没有单独设计“应用层握手报文”。

也就是说，连接成功不是通过下面这种方式判断的：

```text
客户端发送 HELLO
服务器回复 OK
```

当前程序不是这样做的。

当前程序是：

```text
TCP connect 成功后，就认为连接建立成功。
```

连接成功之后，客户端才会根据用户选择发送第一条真正的应用层业务报文，例如：

- 上传文件时发送 `MSG_FILE_INFO`；
- 查看列表时发送 `MSG_LIST_BACKUPS`；
- 下载文件时发送 `MSG_DOWNLOAD_REQUEST`。

所以可以总结为：

```text
本项目的连接成功判断依赖 TCP 层，而不是依赖自定义应用层握手。
```

---

### 6. 连接建立后的断开和异常判断

虽然连接建立依赖 `connect()` 和 `accept()`，但连接建立后仍然可能断开。

服务器端接收报文头时：

```python
header = receive_full(sock, 9, timeout=None)
if not header:
    print(f"[{addr}] 客户端断开连接或超时")
    return
```

`receive_full()` 内部如果 `recv()` 返回空数据：

```python
chunk = sock.recv(size - len(data))
if not chunk:
    return None
```

这通常表示对方已经关闭连接。

服务器还捕获了：

```python
except ConnectionResetError:
    print(f"[{addr}] 客户端断开连接")
```

客户端接收数据时也使用：

```python
receive_with_timeout(sock, size, timeout=TIMEOUT)
```

如果超时、socket 错误、连接关闭，可能返回 `None`，随后客户端会打印例如：

- “未收到校验结果”；
- “ACK超时或错误”；
- “未收到备份列表响应”；
- “接收下载块头失败”。

---

<a id="client-details"></a>
## 十、客户端功能详细说明

客户端文件是 `client.py`。

客户端主要负责“主动发起操作”。它不是一直被动等待，而是由用户通过菜单选择要做什么。

---

### 1. 输入服务器 IP 和端口

客户端启动后显示：

```text
TCP 网络文件备份系统 - 客户端
```

然后让用户输入服务器 IP 和端口。

客户端会检查：

- IP 不能为空；
- 端口必须是数字；
- 端口范围必须在 `1-65535`。

如果连接失败，客户端会询问是否重试。

---

### 2. 建立 TCP 连接

客户端通过 `connect_to_server(ip, port)` 连接服务器。

连接成功后会设置超时时间：

```python
sock.settimeout(TIMEOUT)
```

其中：

```python
TIMEOUT = 10
```

也就是后续很多接收操作默认最多等待 10 秒。

---

### 3. 上传文件功能

菜单选项：

```text
1. 上传文件
```

功能说明：

客户端把本地指定文件上传到服务器的 `server_backup` 目录。

主要步骤：

1. 检查本地文件是否存在、是否可读；
2. 获取文件名和文件大小；
3. 发送 `MSG_FILE_INFO`；
4. 发送 `MSG_RESUME_QUERY` 自动查询断点；
5. 接收 `MSG_RESUME_INFO`；
6. 从断点位置继续分块读取文件；
7. 每块前面加 4 字节块号并发送 `MSG_FILE_BLOCK`；
8. 每发送一块就等待服务器 ACK；
9. 如果 ACK 超时或无效，最多重试 3 次；
10. 所有块发送完成后等待服务器 `MSG_VERIFY_RESULT`；
11. 根据结果打印上传成功或失败。

---

### 4. 上传文件夹功能

菜单选项：

```text
2. 上传文件夹
```

功能说明：

客户端会先把本地文件夹压缩成临时 ZIP 文件，再复用上传文件逻辑发送给服务器。

上传时远程名称会带上：

```text
__FOLDER__:
```

这个前缀，服务器据此判断该备份应在接收完成后解压成目录。

---

### 5. 查看服务器备份列表功能

菜单选项：

```text
3. 查看服务器备份列表
```

功能说明：

客户端向服务器发送一个没有数据区的 `MSG_LIST_BACKUPS` 报文。

服务器返回 `MSG_BACKUP_LIST` 后，客户端解析每一个条目，并打印：

- 名称；
- 大小；
- 备份时间；
- 客户端 IP；
- 是否为目录（`[DIR]`）。

从协议含义上说，如果服务器备份目录为空，服务器会返回空的 `MSG_BACKUP_LIST` 数据区。客户端源码中虽然写有下面这个提示：

```text
服务器上没有备份文件
```

但由于 `client.py` 先用 `if not list_data` 判断接收到的数据，空列表对应的 `b''` 会先被当成失败，因此当前实际运行时更可能提示“备份列表数据不完整”。这一点是当前实现的真实行为说明。

---

### 6. 下载备份文件/文件夹功能

菜单选项：

```text
4. 下载备份文件/文件夹
```

功能说明：

客户端输入服务器上的备份名称，然后请求服务器发送该文件或文件夹。

主要步骤：

1. 检查输入名称是否合法；
2. 发送 `MSG_DOWNLOAD_REQUEST`；
3. 接收 `MSG_DOWNLOAD_INFO`；
4. 如果目标存在，创建本地目录 `downloaded_backups`；
5. 如果是文件夹，最终保存为 ZIP；
6. 循环接收 `MSG_DOWNLOAD_BLOCK`；
7. 检查下载块号是否连续；
8. 把文件内容写入本地文件；
9. 收到的总字节数达到服务器声明的文件大小后，下载完成。

---

### 7. 删除备份文件/文件夹功能

菜单选项：

```text
5. 删除备份文件/文件夹
```

功能说明：

客户端向服务器发送 `MSG_DELETE_REQUEST`，请求删除指定备份；服务器返回 `MSG_DELETE_RESULT`，客户端根据结果提示删除成功或失败。

---

### 8. 退出功能

菜单选项：

```text
6. 退出
```

客户端退出前会关闭 socket：

```python
sock.close()
```

---

<a id="server-details"></a>
## 十一、服务器端功能详细说明

服务器端文件是 `server.py`。

服务器主要负责“被动等待并处理客户端请求”，但和最基础版本相比，当前服务器已经不只是“收文件、列文件、发文件”这么简单了，还承担了：

- 断点续传状态管理；
- 文件夹备份的解压和下载压缩；
- 备份元数据记录；
- 删除文件/文件夹；
- 重名备份自动改名；
- 断点进度持久化到磁盘。

---

### 1. 创建备份目录并加载断点进度

服务器启动时，会先确保备份目录存在：

```python
if not os.path.exists(BACKUP_FOLDER):
    os.makedirs(BACKUP_FOLDER)
```

对应常量：

```python
BACKUP_FOLDER = "server_backup"
```

这个目录不只保存最终备份文件或备份目录，还会保存一些隐藏的辅助文件，例如：

- `.upload_progress.json`：断点续传进度记录；
- `.temp_xxx.part`：上传中的稳定临时文件；
- `.<备份名>.meta`：备份时间和客户端 IP 元数据。

服务器启动后还会加载断点记录：

```python
with PROGRESS_LOCK:
    UPLOAD_PROGRESS = load_upload_progress()
```

这意味着：

- 服务器重启后，不会丢失已经落盘的断点信息；
- 只要临时文件还在，客户端稍后重新连接时就可以继续续传；
- 断点状态并不只保存在内存里，而是同时保存到磁盘文件中。

---

### 2. 监听端口并等待客户端连接

服务器会让用户输入监听端口，并检查范围是否在 `1-65535`。

之后执行：

```python
server.bind(('0.0.0.0', port))
server.listen(5)
```

含义是：

- 监听本机所有 IPv4 地址；
- 使用 TCP；
- 最多允许一定数量的连接排队等待处理。

服务器进入主循环后，会不断调用：

```python
client_sock, client_addr = server.accept()
```

只要 `accept()` 正常返回，就表示有一个客户端完成了 TCP 连接。

---

### 3. 为每个客户端创建独立处理线程

服务器不是单线程串行处理所有用户，而是每接受一个客户端连接，就创建一个线程：

```python
thread = threading.Thread(target=handle_client, args=(client_sock, client_addr))
thread.daemon = True
thread.start()
```

这样做的结果是：

- 一个客户端上传大文件时，不会完全阻塞其他客户端查看列表或下载；
- 每个客户端的协议解析、上传状态和异常处理都在自己的线程里完成；
- 断点进度字典 `UPLOAD_PROGRESS` 需要通过 `PROGRESS_LOCK` 保护，避免多线程同时修改时发生竞争。

---

### 4. 处理查看备份列表请求

当服务器收到 `MSG_LIST_BACKUPS` 时，会调用：

```python
send_backup_list(sock)
```

这个函数会扫描 `server_backup` 目录，但会跳过所有以 `.` 开头的隐藏项：

```python
if filename.startswith('.'):
    continue
```

因此下面这些不会出现在客户端看到的列表里：

- `.upload_progress.json`
- `.temp_xxx.part`
- `.<备份名>.meta`

服务器真正返回给客户端的是“可见备份项”，并且每项都包含：

- 类型（普通文件 / 文件夹）；
- 名称；
- 大小；
- 时间戳；
- 客户端 IP。

也就是说，当前列表响应已经不再是基础版那种“只有文件名和大小”的结构。

---

### 5. 处理下载请求

当服务器收到 `MSG_DOWNLOAD_REQUEST` 后，会：

1. 读取数据区并解析备份名称；
2. 校验 CRC；
3. 用 `is_valid_backup_name()` 检查名称是否合法；
4. 判断该目标是普通文件、目录，还是不存在；
5. 先发送 `MSG_DOWNLOAD_INFO`；
6. 如果存在，再发送多个 `MSG_DOWNLOAD_BLOCK`。

如果目标是普通文件：

- 服务器直接读取原文件；
- 每次最多读取 `4096` 字节；
- 按块号 1、2、3……连续发送。

如果目标是目录：

- 服务器先创建一个临时 ZIP；
- 调用 `zip_directory()` 把整个目录压缩进去；
- 再把这个 ZIP 当作普通文件分块发送；
- 发送完成后删除临时 ZIP。

所以从协议角度看，下载目录和下载普通文件共用同一套下载块协议；差别只是在服务器发送前是否先临时压缩。

---

### 6. 处理删除请求

当服务器收到 `MSG_DELETE_REQUEST` 后，会：

1. 读取数据区并解析备份名称；
2. 校验 CRC；
3. 检查名称是否合法；
4. 判断该名称对应的是普通文件、目录，还是不存在；
5. 删除后发送 `MSG_DELETE_RESULT`。

具体删除逻辑是：

- 如果是普通文件，使用 `os.remove(filepath)`；
- 如果是目录，使用 `shutil.rmtree(filepath)`；
- 如果不存在，返回失败结果；
- 如果名称不合法，也直接返回失败结果。

这说明当前服务器支持真正意义上的“删除备份”，而不是只支持上传和下载。

---

### 7. 处理上传文件，并在上传前协商断点

当服务器收到 `MSG_FILE_INFO` 后，并不会像基础版那样立刻开始收块，而是会继续等待客户端发送：

```text
MSG_RESUME_QUERY
```

服务器对这个断点查询也会做完整校验：

- 头部是否合法；
- 消息类型是否正确；
- 数据区是否完整；
- CRC 是否正确；
- 查询里的文件名和文件大小是否与刚才的 `MSG_FILE_INFO` 一致。

如果不一致，服务器会直接返回失败结果：

```python
send_result(sock, False, "断点查询参数不一致")
```

也就是说，现在上传流程已经从“单阶段发送文件块”变成了“先声明文件，再协商从哪一块开始继续发送”。

---

### 8. 用“客户端IP + 文件名”管理断点状态

服务器当前断点续传的核心键是：

```python
upload_key = get_upload_key(addr[0], filename)
```

对应逻辑是：

```text
客户端IP + 文件名
```

不是：

```text
客户端IP + 端口 + 文件名
```

这样设计的意义很重要：

- 客户端断开后重新连接，TCP 源端口通常会变化；
- 如果把端口也算进键里，就会导致“重连后被认为是全新的上传任务”；
- 现在只用 IP 和文件名，所以更符合“同一客户端继续上传同一文件”的断点续传需求。

断点记录中至少会保存：

- `expected_size`：声明的总大小；
- `received_size`：已经写入临时文件的字节数；
- `last_block`：已经成功收到的最后一个块号；
- `temp_path`：对应的稳定临时文件路径。

---

### 9. 使用稳定临时文件路径，而不是依赖客户端端口

当前服务器不会再把上传中的临时文件命名成“IP + 端口”的形式，而是通过：

```python
stable_temp_path = get_temp_path_for_key(upload_key)
```

生成一个稳定路径。

这个路径最终类似：

```text
server_backup/.temp_<hash>.part
```

它是根据 `upload_key` 计算出的固定结果，因此：

- 同一个客户端 IP 上传同一个文件名，会命中同一个临时文件；
- 客户端断线重连后仍然能接着写同一个临时文件；
- 不会因为 TCP 端口变化而产生一堆彼此无关的临时文件。

这正是断点续传能成立的关键条件之一。

---

### 10. 返回断点信息并决定从哪里继续收块

服务器找到或初始化断点记录后，会检查临时文件实际大小是否和记录一致。

如果不一致，服务器会按照真实文件大小重新修正：

- `received_size = actual_size`
- `received_blocks = received_size // BLOCK_SIZE`

然后返回：

```python
send_resume_info(sock, next_block, received_size)
```

其中：

- `next_block = received_blocks + 1`
- `received_size` 表示客户端应该把文件读指针移动到哪里。

这意味着服务器返回给客户端的不是一个模糊的“你继续传吧”，而是明确告诉客户端：

- 下一块号是多少；
- 已经收了多少字节。

客户端随后就能 `seek(received_size)` 并从 `next_block` 继续发。

---

### 11. 接收上传块、更新断点并发送 ACK

进入真正收块阶段后，服务器对每个 `MSG_FILE_BLOCK` 都会检查：

1. 头部是否合法；
2. 消息类型是否仍然是 `MSG_FILE_BLOCK`；
3. 数据区是否完整；
4. CRC 是否正确；
5. 数据区长度是否至少包含 4 字节块号；
6. 块号是否恰好等于当前期望块号；
7. 写入后是否会超出声明总大小；
8. 临时文件是否可成功写入。

只有这些都通过，服务器才会：

1. 把块内容写入临时文件；
2. 更新 `received_size`；
3. 更新 `received_blocks`；
4. 把新进度写回 `UPLOAD_PROGRESS`；
5. 立即调用 `save_upload_progress()` 落盘；
6. 再发送 ACK 给客户端。

因此现在每个成功接收的上传块，都会同时更新：

- 磁盘上的临时文件；
- 内存里的断点进度；
- `.upload_progress.json` 里的持久化记录。

---

### 12. 上传完成后保存最终备份并清理断点

当服务器确认：

```python
received_size == expected_size
```

说明该上传任务已经完整接收。

接下来有两种分支。

#### 普通文件上传

服务器会：

1. 调用 `get_unique_backup_name(filename)` 计算最终名称；
2. 把临时文件 `os.rename()` 成最终备份文件；
3. 记录元数据 `save_backup_metadata(final_name, addr)`。

#### 文件夹上传

如果文件名带有：

```text
__FOLDER__:
```

前缀，服务器就知道这不是普通文件，而是“文件夹的 ZIP 备份上传”。

这时服务器会：

1. 去掉前缀得到真正文件夹名；
2. 调用 `get_unique_backup_name(folder_name)` 避免重名；
3. 创建最终目录；
4. 把临时 ZIP 解压进去；
5. 保存元数据。

无论是文件还是文件夹，只要最终保存成功，服务器都会：

- 删除对应断点记录；
- 再次把 `.upload_progress.json` 落盘；
- 发送 `MSG_VERIFY_RESULT` 表示传输成功。

也就是说，断点记录只在“上传未完成”期间存在；一旦成功完成，服务器就会清掉它。

---

### 13. 当前上传异常处理的真实行为

这一节要特别强调，因为当前服务器对不同类型的失败处理并不完全一样。

#### 会尽量保留断点的场景

下面这些场景，服务器当前实现会倾向于保留断点，以便后续续传：

- 客户端连接重置：`ConnectionResetError`
- 接收超时：`socket.timeout`
- 一般套接字错误：`socket.error`
- 读取块头失败：`receive_full(sock, 9)` 返回空
- 读取块数据不完整：`receive_full(sock, length)` 失败

这些场景下通常会：

- 关闭临时文件句柄；
- 保留临时文件；
- 保留 `UPLOAD_PROGRESS` 里的记录；
- 等待客户端以后重新连接继续传。

#### 会清理临时文件和断点的场景

下面这些场景更像“协议内容不可信”或“数据已经不一致”，服务器当前实现会直接清理：

- 收到的消息类型不是 `MSG_FILE_BLOCK`；
- 上传块 CRC 校验失败；
- 上传块格式错误；
- 块号不连续；
- 写入后会超出声明总大小；
- 最终文件大小不匹配；
- 文件夹解压失败；
- 保存最终文件/目录失败。

这些情况下，服务器会删除临时文件，并把对应断点记录从 `UPLOAD_PROGRESS` 中移除。

因此当前实现的策略可以概括为：

```text
可恢复的网络中断 -> 尽量保留断点
明显的协议/数据错误 -> 清理断点并终止上传
```

---

<a id="mapping-table"></a>
## 十二、协议字段与代码映射总表

### 1. 通用常量映射

| 协议含义       | 客户端代码                    | 服务器端代码                  | 说明                                         |
| -------------- | ----------------------------- | ----------------------------- | -------------------------------------------- |
| 魔数           | `MAGIC = 0x424B`              | `MAGIC = 0x424B`              | 判断是否为本协议报文                         |
| 文件块大小     | `BLOCK_SIZE = 4096`           | `BLOCK_SIZE = 4096`           | 上传和下载都按 4096 字节分块                 |
| 文件信息       | `MSG_FILE_INFO = 0x01`        | `MSG_FILE_INFO = 0x01`        | 上传前声明文件名和大小                       |
| 文件块         | `MSG_FILE_BLOCK = 0x02`       | `MSG_FILE_BLOCK = 0x02`       | 上传文件内容块                               |
| ACK            | `MSG_ACK = 0x03`              | `MSG_ACK = 0x03`              | 服务器确认上传块                             |
| 校验结果       | `MSG_VERIFY_RESULT = 0x04`    | `MSG_VERIFY_RESULT = 0x04`    | 上传完成后的最终结果                         |
| 列表请求       | `MSG_LIST_BACKUPS = 0x05`     | `MSG_LIST_BACKUPS = 0x05`     | 客户端请求备份列表                           |
| 列表响应       | `MSG_BACKUP_LIST = 0x06`      | `MSG_BACKUP_LIST = 0x06`      | 服务器返回备份列表                           |
| 下载请求       | `MSG_DOWNLOAD_REQUEST = 0x07` | `MSG_DOWNLOAD_REQUEST = 0x07` | 客户端请求下载文件或文件夹                   |
| 下载信息       | `MSG_DOWNLOAD_INFO = 0x08`    | `MSG_DOWNLOAD_INFO = 0x08`    | 服务器返回是否存在、类型和大小               |
| 下载块         | `MSG_DOWNLOAD_BLOCK = 0x09`   | `MSG_DOWNLOAD_BLOCK = 0x09`   | 服务器发送下载文件块                         |
| 删除请求       | `MSG_DELETE_REQUEST = 0x0A`   | `MSG_DELETE_REQUEST = 0x0A`   | 客户端请求删除文件或文件夹                   |
| 删除结果       | `MSG_DELETE_RESULT = 0x0B`    | `MSG_DELETE_RESULT = 0x0B`    | 服务器返回删除操作结果                       |
| 断点查询       | `MSG_RESUME_QUERY = 0x0C`     | `MSG_RESUME_QUERY = 0x0C`     | 客户端上传前查询断点                         |
| 断点信息       | `MSG_RESUME_INFO = 0x0D`      | `MSG_RESUME_INFO = 0x0D`      | 服务器返回下一块号和已接收字节               |
| 文件夹上传前缀 | `FOLDER_UPLOAD_PREFIX`        | `FOLDER_UPLOAD_PREFIX`        | 用 `__FOLDER__:` 标记“这是一个目录备份上传” |

### 2. 报文头字段映射

| 字段       | 字节数 | Python struct 格式 | 代码变量名                      | 说明            |
| ---------- | -----: | ------------------ | ------------------------------- | --------------- |
| `magic`    |      2 | `H`                | `MAGIC` / `magic` / `ack_magic` | 固定为 `0x424B` |
| `msg_type` |      1 | `B`                | `msg_type` / `ack_type`         | 消息类型        |
| `length`   |      4 | `I`                | `length` / `ack_len`            | 数据区长度      |
| `checksum` |      2 | `H`                | `checksum` / `ack_checksum`     | CRC16 校验值    |

报文头在客户端和服务器两侧都统一使用：

```python
struct.pack('>HBIH', MAGIC, msg_type, length, checksum)
```

也就是说，整个项目的应用层消息框架始终没有变；变化的是不同 `msg_type` 对应的数据区内容。

### 3. 数据区字段映射

| 报文类型               | 数据区字段                                                                  | 代码变量                         | 说明                               |
| ---------------------- | --------------------------------------------------------------------------- | -------------------------------- | ---------------------------------- |
| `MSG_FILE_INFO`        | `filename + \0 + file_size`                                                 | `info_data` / `data`             | 上传前声明文件信息                 |
| `MSG_FILE_BLOCK`       | `block_num + block_data`                                                    | `block_data` / `data`            | 上传文件块                         |
| `MSG_ACK`              | `block_num`                                                                 | `data` / `ack_data`              | 服务器确认收到哪个上传块           |
| `MSG_VERIFY_RESULT`    | `result_code + message`                                                     | `result_data`                    | 上传最终结果                       |
| `MSG_LIST_BACKUPS`     | 空                                                                          | `b''`                            | 只请求列表，不带数据               |
| `MSG_BACKUP_LIST`      | 多个 `item_type + filename + \0 + size + timestamp + client_ip + \0`      | `list_data`                      | 服务器返回文件/目录列表            |
| `MSG_DOWNLOAD_REQUEST` | `filename`                                                                  | `request_data` / `data`          | 请求下载的备份名称                 |
| `MSG_DOWNLOAD_INFO`    | `success` 或 `success + file_type + file_size`                              | `info_data`                      | 目标是否存在、类型和大小           |
| `MSG_DOWNLOAD_BLOCK`   | `block_num + block_data`                                                    | `data` / `block_data`            | 下载文件块                         |
| `MSG_DELETE_REQUEST`   | `filename`                                                                  | `request_data` / `data`          | 请求删除的备份名称                 |
| `MSG_DELETE_RESULT`    | `status`                                                                    | `result_data` / `delete_data`    | 删除成功或失败                     |
| `MSG_RESUME_QUERY`     | `filename + \0 + file_size`                                                 | `resume_query_data` / `resume_data` | 客户端上传前查询断点            |
| `MSG_RESUME_INFO`      | `next_block + received_size`                                                | `data` / `resume_data`           | 服务器返回续传起点                 |

### 4. 断点状态字段映射

虽然 `.upload_progress.json` 不是网络报文的一部分，但它是当前断点续传功能必不可少的“协议外状态”。

| 状态字段         | 代码字段          | 说明                                 |
| ---------------- | ----------------- | ------------------------------------ |
| 上传任务键       | `upload_key`      | 由 `客户端IP + 文件名` 组成          |
| 总大小           | `expected_size`   | 来自 `MSG_FILE_INFO` / `MSG_RESUME_QUERY` |
| 已接收字节数     | `received_size`   | 用于客户端 `seek()` 到正确位置       |
| 最后成功块号     | `last_block`      | 用于推导 `next_block`                |
| 临时文件路径     | `temp_path`       | 指向稳定的 `.temp_<hash>.part` 文件  |

---

<a id="implementation-notes"></a>
## 十三、重要实现细节总结

### 1. TCP 负责传输，应用层协议负责解释数据

TCP 只保证字节流传输，不知道“文件名”“文件大小”“块号”“删除请求”“断点查询”这些概念。

本项目通过固定 9 字节报文头和可变长度数据区，自己定义了这些字节的含义。

### 2. 所有整数统一使用大端字节序

报文头和数据区里的整数都使用 `>` 开头的 struct 格式，例如：

```python
'>HBIH'
'>Q'
'>I'
'>BBQ'
'>IQ'
```

这表示使用网络字节序，也就是大端。

### 3. 文件名使用 UTF-8，目录备份靠前缀区分

普通文件名、列表中的名称、删除请求名称，都会用：

```python
encode('utf-8')
```

进行编码。

如果上传的是文件夹，客户端不会改协议类型，而是把远程名称改成：

```text
__FOLDER__:原目录名
```

服务器看到此前缀，就知道上传完成后应该按 ZIP 解压为目录，而不是直接当作普通文件保存。

### 4. 上传有 ACK，下载没有 ACK

上传时：

- 客户端每发一个块；
- 服务器成功写入后回 ACK；
- 客户端收到 ACK 才继续下一块；
- 如果 ACK 超时，客户端最多重试 3 次。

下载时：

- 服务器连续发送下载块；
- 客户端按顺序接收；
- 客户端检查块号；
- 客户端不会对每个下载块回复 ACK。

这说明当前上传是“停等式”传输，而下载更接近“单向顺序流式发送”。

### 5. 断点续传依赖“客户端IP + 文件名”以及持久化 JSON

当前续传并不是靠客户端本地猜测，而是依赖服务器端真实记录：

```text
客户端IP + 文件名 -> 已接收块号 / 已接收字节数 / 临时文件路径
```

这些信息会写入：

```text
server_backup/.upload_progress.json
```

因此即使服务器重启，只要这个 JSON 和临时文件仍在，上传就有机会继续。

### 6. 临时文件已经改成稳定命名，不再依赖客户端端口

当前上传中的临时文件类似：

```text
server_backup/.temp_<hash>.part
```

它来自 `upload_key` 的稳定映射，而不是旧思路里的 `客户端IP + 客户端端口`。

这解决了一个关键问题：

- TCP 重连后客户端端口会变；
- 但断点续传要求重新连接后仍然找到原来的临时文件；
- 所以必须使用与端口无关的稳定命名策略。

### 7. 最终保存时不会直接覆盖旧备份

无论普通文件还是目录备份，服务器都会调用：

```python
get_unique_backup_name(...)
```

如果同名备份已存在，最终名称会变成：

```text
name(1)
name(2)
...
```

因此当前服务器的行为是“保留旧备份并生成不冲突新名称”，不是“静默覆盖原文件”。

### 8. 备份列表会隐藏内部辅助文件

列表接口不会把所有目录项原样返回，而是主动跳过所有以 `.` 开头的文件。

这保证了客户端看到的是“真正的备份项”，而不是：

- 进度文件；
- 临时分片文件；
- 元数据文件。

### 9. 当前程序没有应用层登录、认证或单独握手

只要 TCP 能连上，客户端就进入功能菜单。

程序没有用户名密码，也没有单独的 HELLO/OK 应用层握手报文。

连接建立之后，双方才开始发送 `MSG_FILE_INFO`、`MSG_LIST_BACKUPS`、`MSG_DOWNLOAD_REQUEST` 等真正业务消息。

### 10. 完整性检查依赖 CRC、块号和总大小，但客户端/服务器并非所有路径都完全对称

上传时服务器会检查：

- `MSG_FILE_INFO` CRC；
- `MSG_RESUME_QUERY` CRC；
- 每个上传块 CRC；
- 块号是否连续；
- 最终收到的总大小是否等于声明大小。

客户端在上传流程里也会检查 `MSG_RESUME_INFO` 的 CRC。

但在其他一些响应路径上，客户端更偏向先检查：

- `magic`
- `msg_type`
- 数据长度

而不是对每一种服务器响应都重新做 CRC 校验。

这不是协议没有校验字段，而是当前实现并没有在每一条接收路径上都完全对称地使用它。

---

<a id="summary"></a>
## 十四、一句话总结

这个项目当前的协议和实现可以概括为：

```text
每条消息 = 9 字节固定头部 + length 字节数据区

头部 = magic + msg_type + length + checksum

数据区根据 msg_type 不同而不同：
上传文件信息带 文件名和文件大小；
上传块/下载块带 块号和文件内容；
ACK 带确认块号；
列表响应带 类型 + 名称 + 大小 + 时间 + 客户端IP；
下载信息带 是否存在 + 类型 + 大小；
删除结果带 成功/失败状态；
断点查询带 文件名和文件大小；
断点信息带 下一块号和已接收字节数。
```

连接是否建立成功，主要仍然依赖 TCP 层：

```text
客户端 connect() 成功返回，客户端认为连接成功；
服务器 accept() 成功返回，服务器认为有客户端连接成功。
```

而一旦连接建立，双方就用这套自定义应用层协议完成：

```text
上传文件、上传文件夹、断点续传、查看列表、下载文件/文件夹、删除备份
```

这也是为什么当前版本相比最基础版，已经从“简单文件传输”演进成了“带目录备份、删除、列表元数据和断点续传的 TCP 应用层文件备份系统”。