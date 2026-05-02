import socket
import struct
import os
import threading

MAGIC = 0x424B
BLOCK_SIZE = 4096
MSG_FILE_INFO = 0x01
MSG_FILE_BLOCK = 0x02
MSG_ACK = 0x03
MSG_VERIFY_RESULT = 0x04
MSG_LIST_BACKUPS = 0x05
MSG_BACKUP_LIST = 0x06
MSG_DOWNLOAD_REQUEST = 0x07
MSG_DOWNLOAD_INFO = 0x08
MSG_DOWNLOAD_BLOCK = 0x09
BACKUP_FOLDER = "server_backup"


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


def verify_header(header):
    if len(header) < 9:
        return False, None, None, None, None
    magic, msg_type, length, checksum = struct.unpack('>HBIH', header[:9])
    if magic != MAGIC:
        return False, None, None, None, None
    return True, msg_type, length, checksum, header


def send_ack(sock, block_num):
    data = struct.pack('>I', block_num)
    header = struct.pack('>HBIH', MAGIC, MSG_ACK, len(data), 0)
    checksum = calc_crc16(header + data)
    header = struct.pack('>HBIH', MAGIC, MSG_ACK, len(data), checksum)
    try:
        sock.sendall(header + data)
        return True
    except socket.error:
        return False


def send_result(sock, success, message=""):
    result_data = struct.pack('>B', 0 if success else 1) + message.encode('utf-8')
    header = struct.pack('>HBIH', MAGIC, MSG_VERIFY_RESULT, len(result_data), 0)
    checksum = calc_crc16(header + result_data)
    header = struct.pack('>HBIH', MAGIC, MSG_VERIFY_RESULT, len(result_data), checksum)
    try:
        sock.sendall(header + result_data)
        return True
    except socket.error:
        return False


def send_backup_list(sock):
    try:
        files = []
        if os.path.exists(BACKUP_FOLDER):
            for filename in os.listdir(BACKUP_FOLDER):
                filepath = os.path.join(BACKUP_FOLDER, filename)
                if os.path.isfile(filepath):
                    size = os.path.getsize(filepath)
                    files.append((filename, size))

        list_data = b''
        for filename, size in files:
            list_data += filename.encode('utf-8') + b'\x00' + struct.pack('>Q', size)

        header = struct.pack('>HBIH', MAGIC, MSG_BACKUP_LIST, len(list_data), 0)
        checksum = calc_crc16(header + list_data)
        header = struct.pack('>HBIH', MAGIC, MSG_BACKUP_LIST, len(list_data), checksum)
        sock.sendall(header + list_data)
        return True
    except socket.error:
        return False


def send_download_info(sock, filename):
    filepath = os.path.join(BACKUP_FOLDER, filename)
    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        info_data = struct.pack('>B', 0)
    else:
        size = os.path.getsize(filepath)
        info_data = struct.pack('>BQ', 1, size)

    header = struct.pack('>HBIH', MAGIC, MSG_DOWNLOAD_INFO, len(info_data), 0)
    checksum = calc_crc16(header + info_data)
    header = struct.pack('>HBIH', MAGIC, MSG_DOWNLOAD_INFO, len(info_data), checksum)
    try:
        sock.sendall(header + info_data)
        return True
    except socket.error:
        return False


def send_download_block(sock, filepath, block_num, block_data):
    data = struct.pack('>I', block_num) + block_data
    header = struct.pack('>HBIH', MAGIC, MSG_DOWNLOAD_BLOCK, len(data), 0)
    checksum = calc_crc16(header + data)
    header = struct.pack('>HBIH', MAGIC, MSG_DOWNLOAD_BLOCK, len(data), checksum)
    try:
        sock.sendall(header + data)
        return True
    except socket.error:
        return False


def receive_full(sock, size, timeout=None):
    if timeout is not None:
        sock.settimeout(timeout)
    else:
        sock.settimeout(None)
    data = b''
    try:
        while len(data) < size:
            chunk = sock.recv(size - len(data))
            if not chunk:
                return None
            data += chunk
        return data
    except socket.timeout:
        return None
    except socket.error:
        return None


def cleanup_temp(temp_path):
    try:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    except OSError:
        pass


def handle_client(sock, addr):
    print(f"客户端连接: {addr}")

    while True:
        temp_file = None
        temp_path = None
        expected_size = 0
        received_size = 0
        expected_blocks = 0
        received_blocks = 0
        filename = None

        try:
            header = receive_full(sock, 9, timeout=None)
            if not header:
                print(f"[{addr}] 客户端断开连接或超时")
                return

            valid, msg_type, length, checksum, hdr = verify_header(header)
            if not valid:
                print(f"[{addr}] 协议错误")
                return

            if msg_type == MSG_LIST_BACKUPS:
                print(f"[{addr}] 请求备份列表")
                send_backup_list(sock)
                continue

            elif msg_type == MSG_DOWNLOAD_REQUEST:
                data = receive_full(sock, length)
                if not data or len(data) < length:
                    print(f"[{addr}] 接收下载请求数据失败")
                    return

                if calc_crc16(hdr[:7] + b'\x00\x00' + data) != checksum:
                    print(f"[{addr}] 下载请求校验失败")
                    return

                filename = data.decode('utf-8')
                print(f"[{addr}] 请求下载文件: {filename}")

                filepath = os.path.join(BACKUP_FOLDER, filename)
                if not os.path.exists(filepath) or not os.path.isfile(filepath):
                    send_download_info(sock, filename)
                    continue

                file_size = os.path.getsize(filepath)
                send_download_info(sock, filename)

                try:
                    with open(filepath, 'rb') as f:
                        block_num = 0
                        total_blocks = (file_size + BLOCK_SIZE - 1) // BLOCK_SIZE

                        while True:
                            data = f.read(BLOCK_SIZE)
                            if not data:
                                break

                            block_num += 1
                            if not send_download_block(sock, filepath, block_num, data):
                                print(f"[{addr}] 发送下载块 {block_num} 失败")
                                return

                            print(f"[{addr}] 发送下载块 {block_num}/{total_blocks} 成功")

                    print(f"[{addr}] 文件下载完成: {filename}")
                    continue

                except IOError as e:
                    print(f"[{addr}] 读取文件失败: {e}")
                    return

            elif msg_type == MSG_FILE_INFO:
                # 处理上传逻辑
                pass
            else:
                print(f"[{addr}] 未知消息类型: {msg_type}")
                return

            # 上传逻辑
            data = receive_full(sock, length)
            if not data or len(data) < length:
                print(f"[{addr}] 接收文件信息数据失败")
                return

            if calc_crc16(hdr[:7] + b'\x00\x00' + data) != checksum:
                print(f"[{addr}] 文件信息校验失败")
                return

            null_pos = data.find(b'\x00')
            if null_pos == -1:
                print(f"[{addr}] 文件信息格式错误")
                return

            filename = data[:null_pos].decode('utf-8')
            expected_size = struct.unpack('>Q', data[null_pos + 1:null_pos + 9])[0]
            expected_blocks = (expected_size + BLOCK_SIZE - 1) // BLOCK_SIZE

            print(f"[{addr}] 收到文件信息: {filename}, 大小: {expected_size} 字节")

            if not os.path.exists(BACKUP_FOLDER):
                try:
                    os.makedirs(BACKUP_FOLDER)
                except OSError as e:
                    print(f"[{addr}] 创建备份文件夹失败: {e}")
                    send_result(sock, False, "服务器无法创建备份文件夹")
                    return

            temp_path = os.path.join(BACKUP_FOLDER, f".temp_{addr[0]}_{addr[1]}")
            try:
                temp_file = open(temp_path, 'wb')
            except IOError as e:
                print(f"[{addr}] 无法创建临时文件: {e}")
                send_result(sock, False, "服务器无法创建临时文件")
                return

            received_size = 0
            received_blocks = 0

            while received_size < expected_size:
                header = receive_full(sock, 9)
                if not header:
                    print(f"[{addr}] 接收数据块头失败")
                    if temp_file:
                        temp_file.close()
                    cleanup_temp(temp_path)
                    return

                valid, msg_type, length, checksum, hdr = verify_header(header)
                if not valid or msg_type != MSG_FILE_BLOCK:
                    print(f"[{addr}] 协议错误或消息类型错误")
                    if temp_file:
                        temp_file.close()
                    cleanup_temp(temp_path)
                    return

                data = receive_full(sock, length)
                if not data or len(data) < length:
                    print(f"[{addr}] 接收数据块数据失败")
                    if temp_file:
                        temp_file.close()
                    cleanup_temp(temp_path)
                    return

                if calc_crc16(hdr[:7] + b'\x00\x00' + data) != checksum:
                    print(f"[{addr}] 数据块校验失败")
                    if temp_file:
                        temp_file.close()
                    cleanup_temp(temp_path)
                    return

                block_num = struct.unpack('>I', data[:4])[0]
                block_data = data[4:]
                received_size += len(block_data)
                received_blocks += 1

                if block_num != received_blocks:
                    print(f"[{addr}] 块序号错误: 期望 {received_blocks}, 收到 {block_num}")
                    if temp_file:
                        temp_file.close()
                    cleanup_temp(temp_path)
                    return

                try:
                    temp_file.write(block_data)
                    temp_file.flush()
                except IOError as e:
                    print(f"[{addr}] 写入临时文件失败: {e}")
                    if temp_file:
                        temp_file.close()
                    cleanup_temp(temp_path)
                    send_result(sock, False, "服务器写入文件失败")
                    return

                if not send_ack(sock, block_num):
                    print(f"[{addr}] 发送ACK失败")
                    if temp_file:
                        temp_file.close()
                    cleanup_temp(temp_path)
                    return

                print(f"[{addr}] 接收块 {received_blocks}/{expected_blocks} 成功")

            if temp_file:
                temp_file.close()
                temp_file = None

            if received_size != expected_size:
                print(f"[{addr}] 文件大小不匹配: 期望 {expected_size}, 收到 {received_size}")
                cleanup_temp(temp_path)
                send_result(sock, False, "文件大小不匹配")
                return

            final_path = os.path.join(BACKUP_FOLDER, filename)
            try:
                if os.path.exists(final_path):
                    os.remove(final_path)
                os.rename(temp_path, final_path)
                temp_path = None
            except OSError as e:
                print(f"[{addr}] 保存文件失败: {e}")
                cleanup_temp(temp_path)
                send_result(sock, False, "服务器保存文件失败")
                return

            print(f"[{addr}] 文件保存成功: {final_path}")
            send_result(sock, True, "传输成功")
            print(f"[{addr}] 传输完成")
            continue

        except ConnectionResetError:
            print(f"[{addr}] 客户端断开连接")
            if temp_file:
                temp_file.close()
            cleanup_temp(temp_path)
            return
        except socket.timeout:
            print(f"[{addr}] 接收超时")
            if temp_file:
                temp_file.close()
            cleanup_temp(temp_path)
            return
        except socket.error as e:
            print(f"[{addr}] 套接字错误: {e}")
            if temp_file:
                temp_file.close()
            cleanup_temp(temp_path)
            return
        except Exception as e:
            print(f"[{addr}] 处理客户端时发生错误: {e}")
            if temp_file:
                temp_file.close()
            cleanup_temp(temp_path)
            return


def main():
    global BACKUP_FOLDER

    print("=" * 40)
    print("TCP 网络文件备份系统 - 服务器")
    print("=" * 40)

    if not os.path.exists(BACKUP_FOLDER):
        try:
            os.makedirs(BACKUP_FOLDER)
            print(f"已创建备份文件夹: {BACKUP_FOLDER}")
        except OSError as e:
            print(f"无法创建备份文件夹: {e}")
            return

    port_str = input("请输入监听端口: ").strip()
    try:
        port = int(port_str)
        if port < 1 or port > 65535:
            raise ValueError
    except ValueError:
        print("端口无效，请输入1-65535之间的数字")
        return

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server.bind(('0.0.0.0', port))
        server.listen(5)
        print(f"服务器监听端口: {port}")
        print(f"备份保存目录: {os.path.abspath(BACKUP_FOLDER)}")
        print("-" * 40)
        print("等待客户端连接...")
    except socket.error as e:
        print(f"绑定端口失败: {e}")
        return

    try:
        while True:
            try:
                client_sock, client_addr = server.accept()
                thread = threading.Thread(target=handle_client, args=(client_sock, client_addr))
                thread.daemon = True
                thread.start()
            except KeyboardInterrupt:
                print("\n服务器正在关闭...")
                break
            except socket.error as e:
                print(f"接受连接时发生错误: {e}")
                continue
    finally:
        server.close()
        print("服务器已关闭")


if __name__ == "__main__":
    main()