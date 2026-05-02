import socket
import struct
import hashlib
import os
import sys

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
MAX_RETRIES = 3
TIMEOUT = 10


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


def make_header(msg_type, data):
    length = len(data)
    header = struct.pack('>HBIH', MAGIC, msg_type, length, 0)
    checksum = calc_crc16(header + data)
    header = struct.pack('>HBIH', MAGIC, msg_type, length, checksum)[:9]
    return struct.pack('>HBIH', MAGIC, msg_type, length, checksum)


def send_with_retry(sock, data, addr=None):
    for attempt in range(MAX_RETRIES):
        try:
            if addr:
                sock.sendto(data, addr)
            else:
                sock.sendall(data)
            return True
        except socket.timeout:
            print(f"发送超时，正在重试 ({attempt + 1}/{MAX_RETRIES})")
        except socket.error as e:
            print(f"发送失败，正在重试 ({attempt + 1}/{MAX_RETRIES}): {e}")
    return False


def receive_with_timeout(sock, size, timeout=TIMEOUT):
    sock.settimeout(timeout)
    try:
        data = b''
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


def connect_to_server(ip, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
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


def get_file_info(file_path):
    if not os.path.exists(file_path):
        return None, "文件不存在"
    if not os.path.isfile(file_path):
        return None, "路径不是文件"
    try:
        size = os.path.getsize(file_path)
        filename = os.path.basename(file_path)
        return {"name": filename, "size": size}, None
    except OSError as e:
        return None, f"无法读取文件: {e}"


def send_file(sock, file_path):
    file_info, err = get_file_info(file_path)
    if err:
        print(err)
        return False

    filename = file_info["name"]
    file_size = file_info["size"]

    print(f"准备发送文件: {filename} ({file_size} 字节)")

    info_data = filename.encode('utf-8') + b'\x00' + struct.pack('>Q', file_size)
    header = make_header(MSG_FILE_INFO, info_data)
    full_message = header + info_data

    if not send_with_retry(sock, full_message):
        print("发送文件信息失败")
        return False

    try:
        with open(file_path, 'rb') as f:
            block_num = 0
            total_blocks = (file_size + BLOCK_SIZE - 1) // BLOCK_SIZE

            while True:
                data = f.read(BLOCK_SIZE)
                if not data:
                    break

                block_num += 1
                block_data = struct.pack('>I', block_num) + data
                header = make_header(MSG_FILE_BLOCK, block_data)

                for attempt in range(MAX_RETRIES):
                    full_message = header + block_data
                    if not send_with_retry(sock, full_message):
                        print(f"发送块 {block_num} 失败，正在重试 ({attempt + 1}/{MAX_RETRIES})")
                        continue

                    ack_data = receive_with_timeout(sock, 13, TIMEOUT)
                    if ack_data and len(ack_data) == 13:
                        ack_magic, ack_type, ack_len, ack_checksum = struct.unpack('>HBIH', ack_data[:9])
                        if ack_type == MSG_ACK and ack_magic == MAGIC:
                            print(f"块 {block_num}/{total_blocks} 发送成功")
                            break
                        else:
                            print(f"收到无效ACK，正在重试 ({attempt + 1}/{MAX_RETRIES})")
                    else:
                        print(f"ACK超时或错误，正在重试 ({attempt + 1}/{MAX_RETRIES})")

                    if attempt == MAX_RETRIES - 1:
                        print("传输失败: 重试次数超出限制")
                        return False
                else:
                    print("传输失败")
                    return False

    except IOError as e:
        print(f"读取文件失败: {e}")
        return False

    print("等待服务器校验结果...")
    result_header = receive_with_timeout(sock, 9, TIMEOUT * 2)
    if not result_header or len(result_header) < 9:
        print("未收到校验结果")
        return False

    magic, msg_type, length, checksum = struct.unpack('>HBIH', result_header[:9])
    if magic != MAGIC or msg_type != MSG_VERIFY_RESULT:
        print("收到无效的校验结果报文")
        return False

    result_data = receive_with_timeout(sock, length, TIMEOUT)
    if not result_data or len(result_data) < length:
        print("校验结果数据不完整")
        return False

    if len(result_data) >= 1:
        result = result_data[0]
        if result == 0:
            print("传输成功: 文件校验通过")
            return True
        else:
            print("传输失败: 文件校验失败")
            return False

    print("收到无效的校验结果报文")
    return False


def list_backups(sock):
    header = make_header(MSG_LIST_BACKUPS, b'')
    if not send_with_retry(sock, header):
        print("发送备份列表请求失败")
        return False

    result_header = receive_with_timeout(sock, 9, TIMEOUT)
    if not result_header or len(result_header) < 9:
        print("未收到备份列表响应")
        return False

    magic, msg_type, length, checksum = struct.unpack('>HBIH', result_header[:9])
    if magic != MAGIC or msg_type != MSG_BACKUP_LIST:
        print("收到无效的备份列表响应")
        return False

    list_data = receive_with_timeout(sock, length, TIMEOUT)
    if not list_data or len(list_data) < length:
        print("备份列表数据不完整")
        return False

    if len(list_data) == 0:
        print("服务器上没有备份文件")
        return True

    print("\n服务器备份文件列表:")
    print("-" * 50)
    pos = 0
    while pos < len(list_data):
        null_pos = list_data.find(b'\x00', pos)
        if null_pos == -1:
            break
        filename = list_data[pos:null_pos].decode('utf-8')
        pos = null_pos + 1
        if pos + 8 > len(list_data):
            break
        file_size = struct.unpack('>Q', list_data[pos:pos+8])[0]
        pos += 8
        print(f"{filename} ({file_size} 字节)")
    print("-" * 50)
    return True


def download_file(sock, filename):
    request_data = filename.encode('utf-8')
    header = make_header(MSG_DOWNLOAD_REQUEST, request_data)
    if not send_with_retry(sock, header + request_data):
        print("发送下载请求失败")
        return False

    info_header = receive_with_timeout(sock, 9, TIMEOUT)
    if not info_header or len(info_header) < 9:
        print("未收到下载信息响应")
        return False

    magic, msg_type, length, checksum = struct.unpack('>HBIH', info_header[:9])
    if magic != MAGIC or msg_type != MSG_DOWNLOAD_INFO:
        print("收到无效的下载信息响应")
        return False

    info_data = receive_with_timeout(sock, length, TIMEOUT)
    if not info_data or len(info_data) < length:
        print("下载信息数据不完整")
        return False

    if len(info_data) < 9:
        print("下载信息格式错误")
        return False

    success = info_data[0]
    if success == 0:
        print("文件不存在")
        return False

    file_size = struct.unpack('>Q', info_data[1:9])[0]
    print(f"开始下载文件: {filename} ({file_size} 字节)")

    download_path = os.path.join("downloaded_backups", filename)
    os.makedirs("downloaded_backups", exist_ok=True)

    try:
        with open(download_path, 'wb') as f:
            received_size = 0
            block_num = 0
            total_blocks = (file_size + BLOCK_SIZE - 1) // BLOCK_SIZE

            while received_size < file_size:
                block_header = receive_with_timeout(sock, 9, TIMEOUT)
                if not block_header or len(block_header) < 9:
                    print("接收下载块头失败")
                    return False

                magic, msg_type, length, checksum = struct.unpack('>HBIH', block_header[:9])
                if magic != MAGIC or msg_type != MSG_DOWNLOAD_BLOCK:
                    print("收到无效的下载块")
                    return False

                block_data = receive_with_timeout(sock, length, TIMEOUT)
                if not block_data or len(block_data) < length:
                    print("下载块数据不完整")
                    return False

                if len(block_data) < 4:
                    print("下载块格式错误")
                    return False

                recv_block_num = struct.unpack('>I', block_data[:4])[0]
                data = block_data[4:]
                received_size += len(data)
                block_num += 1

                if recv_block_num != block_num:
                    print(f"块序号错误: 期望 {block_num}, 收到 {recv_block_num}")
                    return False

                f.write(data)
                print(f"下载块 {block_num}/{total_blocks} 成功")

        print(f"下载完成: {download_path}")
        return True

    except IOError as e:
        print(f"写入文件失败: {e}")
        return False


def ask_continue():
    while True:
        choice = input("\n是否继续备份文件？(y/n): ").strip().lower()
        if choice in ['y', 'n']:
            return choice == 'y'
        print("请输入 y 或 n")


def main():
    print("=" * 40)
    print("TCP 网络文件备份系统 - 客户端")
    print("=" * 40)

    # 先输入服务器信息并建立连接
    while True:
        ip = input("\n请输入服务器IP: ").strip()
        if not ip:
            print("IP不能为空")
            continue

        port_str = input("请输入服务器端口: ").strip()
        try:
            port = int(port_str)
            if port < 1 or port > 65535:
                raise ValueError
        except ValueError:
            print("端口无效，请输入1-65535之间的数字")
            continue

        print("-" * 40)
        sock, err = connect_to_server(ip, port)
        if err:
            print(f"连接失败: {err}")
            choice = input("是否重试连接？(y/n): ").strip().lower()
            if choice != 'y':
                print("\n感谢使用，再见！")
                return
            continue

        print("连接成功！")
        break

    # 进入操作菜单
    while True:
        print("\n请选择操作:")
        print("1. 上传文件")
        print("2. 查看服务器备份列表")
        print("3. 下载备份文件")
        print("4. 退出")

        choice = input("请输入选择 (1-4): ").strip()
        if choice == '4':
            print("\n感谢使用，再见！")
            break

        if choice not in ['1', '2', '3']:
            print("无效选择，请重新输入")
            continue

        success = False
        if choice == '1':
            file_path = input("请输入要备份的文件路径: ").strip()
            if not file_path:
                print("文件路径不能为空")
                continue

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

            success = send_file(sock, file_path)

        elif choice == '2':
            success = list_backups(sock)

        elif choice == '3':
            filename = input("请输入要下载的文件名: ").strip()
            if not filename:
                print("文件名不能为空")
                continue
            success = download_file(sock, filename)

        print("-" * 40)
        if success:
            print("操作完成")
        else:
            print("操作失败")

        cont = input("\n是否继续操作？(y/n): ").strip().lower()
        if cont != 'y':
            print("\n感谢使用，再见！")
            break

    sock.close()


if __name__ == "__main__":
    main()