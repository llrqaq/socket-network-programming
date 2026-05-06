在这两个文件（`client.py` 和 `server.py`）中，`socket` 模块被广泛用于实现基于 TCP 的网络通信。以下是代码中用到的所有 `socket` 方法和属性的总结与分类：

### 1. 套接字的创建与配置属性
*   **`socket.AF_INET`**: 代表 IPv4 地址家族（Address Family），用于指定网络协议。
*   **`socket.SOCK_STREAM`**: 代表面向连接的流式套接字，专门用于 TCP 协议。
*   **`socket.socket(family, type)`**: 用于创建一个全新的套接字对象。
*   **`socket.SOL_SOCKET`** 和 **`socket.SO_REUSEADDR`**: 这两个常量在服务器端与 `setsockopt` 配合使用，允许服务器在关闭后立即重用本地端口，避免“端口被占用”的错误。
*   **`sock.setsockopt(level, optname, value)`**: 设置套接字选项（如上述的端口重用）。
*   **`sock.settimeout(timeout)`**: 设置套接字操作的超时时间，防止网络阻塞导致程序永久卡死。传入 `None` 可以将其设置为阻塞模式。

### 2. 连接建立与管理（服务器与客户端）
*   **`sock.connect((ip, port))`**: 客户端专用的方法，用于主动向指定的服务器 IP 和端口发起 TCP 连接请求。
*   **`server.bind((host, port))`**: 服务器专用的方法，将套接字绑定到特定的本地网络接口（如 `'0.0.0.0'`）和端口上。
*   **`server.listen(backlog)`**: 服务器专用的方法，开启监听模式，参数指定了允许排队等待连接的最大客户端数量（代码中设为 5）。
*   **`server.accept()`**: 服务器专用的方法，阻塞并等待客户端的连接请求。连接成功后会返回一个新的用于通信的套接字对象以及客户端的地址信息 `(client_sock, client_addr)`。

### 3. 数据发送与接收
*   **`sock.sendall(data)`**: 用于通过 TCP 套接字发送数据。它会持续发送直到所有数据都发送完毕，确保数据完整性。
*   **`sock.sendto(data, addr)`**: 通常用于 UDP 发送数据。在客户端代码的 `send_with_retry` 函数中包含了这个方法以做兼容，但在实际 TCP 流程中主要走 `sendall` 分支。
*   **`sock.recv(bufsize)`**: 用于从套接字接收数据，参数指定了单次接收的最大字节数。代码中常配合 `while` 循环使用，以确保接收到预期长度的完整数据块。

### 4. 资源释放
*   **`sock.close()`**: 关闭套接字连接，释放底层网络资源。

### 5. 异常处理类
*   **`socket.timeout`**: 捕获套接字操作（如发送、接收、连接）超时时抛出的异常。
*   **`socket.error`**: 捕获与底层网络相关的各种基础套接字异常（如网络突然中断、连接被重置等）。