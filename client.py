import socket
import struct
import hashlib
import os
import sys
import zipfile
import tempfile
import time
import datetime

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
MSG_DELETE_REQUEST = 0x0A
MSG_DELETE_RESULT = 0x0B
MSG_RESUME_QUERY = 0x0C
MSG_RESUME_INFO = 0x0D
FOLDER_UPLOAD_PREFIX = "__FOLDER__:"
MAX_RETRIES = 3
TIMEOUT = 10


# 计算CRC16校验和，输入字节型数据，返回16位CRC16校验和
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

#为即将发送的数据构建一个自定义的协议头。
def make_header(msg_type, data):
    length = len(data)
    header = struct.pack('>HBIH', MAGIC, msg_type, length, 0)
    checksum = calc_crc16(header + data)
    header = struct.pack('>HBIH', MAGIC, msg_type, length, checksum)[:9]
    return struct.pack('>HBIH', MAGIC, msg_type, length, checksum)

#带有重试机制的数据发送函数
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

#在指定的超时时间内，持续从 Socket 中读取数据
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

#尝试与指定的服务器建立 TCP 连接
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


# 获取本地文件名称和大小信息。
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


# 执行文件上传（含断点查询、分块发送、ACK确认和结果校验）。
def send_file(sock, file_path, remote_name=None):
    file_info, err = get_file_info(file_path)
    if err:
        print(err)
        return False

    filename = remote_name if remote_name else file_info["name"]
    file_size = file_info["size"]

    print(f"准备发送文件: {filename} ({file_size} 字节)")

    info_data = filename.encode('utf-8') + b'\x00' + struct.pack('>Q', file_size)
    header = make_header(MSG_FILE_INFO, info_data)
    full_message = header + info_data

    if not send_with_retry(sock, full_message):
        print("发送文件信息失败")
        return False

    resume_query_data = filename.encode('utf-8') + b'\x00' + struct.pack('>Q', file_size)
    resume_query_header = make_header(MSG_RESUME_QUERY, resume_query_data)
    if not send_with_retry(sock, resume_query_header + resume_query_data):
        print("发送断点查询失败")
        return False

    resume_header = receive_with_timeout(sock, 9, TIMEOUT)
    if not resume_header or len(resume_header) < 9:
        print("未收到断点信息")
        return False

    resume_magic, resume_type, resume_len, resume_checksum = struct.unpack('>HBIH', resume_header[:9])
    if resume_magic != MAGIC or resume_type != MSG_RESUME_INFO:
        print("收到无效的断点信息报文")
        return False

    resume_data = receive_with_timeout(sock, resume_len, TIMEOUT)
    if not resume_data or len(resume_data) < resume_len or len(resume_data) < 12:
        print("断点信息数据不完整")
        return False

    if calc_crc16(resume_header[:7] + b'\x00\x00' + resume_data) != resume_checksum:
        print("断点信息校验失败")
        return False

    next_block, sent_size = struct.unpack('>IQ', resume_data[:12])
    if next_block < 1 or sent_size > file_size:
        print("服务器返回的断点信息无效")
        return False

    if sent_size > 0:
        print(f"检测到断点，已完成 {sent_size}/{file_size} 字节，从第 {next_block} 块继续传输")

    try:
        with open(file_path, 'rb') as f:
            f.seek(sent_size)
            block_num = next_block - 1
            start_time = time.time()

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
                        continue

                    ack_data = receive_with_timeout(sock, 13, TIMEOUT)
                    if ack_data and len(ack_data) == 13:
                        ack_magic, ack_type, ack_len, ack_checksum = struct.unpack('>HBIH', ack_data[:9])
                        if ack_type == MSG_ACK and ack_magic == MAGIC:
                            sent_size += len(data)
                            elapsed_time = time.time() - start_time
                            progress = (sent_size / file_size) * 100
                            remaining_size = file_size - sent_size
                            speed = sent_size / elapsed_time if elapsed_time > 0 else 0
                            speed_str = f"{speed / 1024:.1f}KB/s" if speed > 0 else "计算中..."
                            remaining_str = f"{remaining_size / 1024:.1f}KB" if remaining_size > 0 else "0KB"
                            print(f"\r已传输：{progress:.1f}%，剩余：{remaining_str}，速度：{speed_str}", end="", flush=True)
                            break
                        else:
                            continue
                    else:
                        continue
                else:
                    print("\n网络中断")
                    return False

            print()  # 换行
    except IOError as e:
        print(f"\n读取文件失败: {e}")
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


# 请求并展示服务器端的备份列表。
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
    print("-" * 80)
    print(f"{'名称':<25} {'大小':<15} {'备份时间':<20} {'客户端IP':<15}")
    print("-" * 80)
    pos = 0
    while pos < len(list_data):
        if pos + 1 > len(list_data):
            break
        item_type = list_data[pos]
        pos += 1
        null_pos = list_data.find(b'\x00', pos)
        if null_pos == -1:
            break
        filename = list_data[pos:null_pos].decode('utf-8')
        pos = null_pos + 1
        if pos + 8 > len(list_data):
            break
        file_size = struct.unpack('>Q', list_data[pos:pos+8])[0]
        pos += 8
        if pos + 8 > len(list_data):
            break
        timestamp = struct.unpack('>Q', list_data[pos:pos+8])[0]
        pos += 8
        ip_null_pos = list_data.find(b'\x00', pos)
        if ip_null_pos == -1:
            break
        client_ip = list_data[pos:ip_null_pos].decode('utf-8')
        pos = ip_null_pos + 1
        
        backup_time = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
        size_str = f"{file_size / 1024 / 1024:.2f}MB" if file_size > 1024 * 1024 else f"{file_size / 1024:.2f}KB"
        
        if item_type == 2:
            print(f"[DIR]  {filename:<20} {'--':<15} {backup_time:<20} {client_ip:<15}")
        else:
            print(f"{filename:<25} {size_str:<15} {backup_time:<20} {client_ip:<15}")
    print("-" * 80)
    return True


# 校验备份名称是否合法，防止路径穿越等非法输入。
def is_valid_backup_name(name):
    if not name:
        return False
    if '..' in name or name.startswith('/') or name.startswith('\\'):
        return False
    if '/' in name or '\\' in name:
        return False
    return True


# 为下载目标路径生成不冲突的唯一文件名。
def get_unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    count = 1
    while True:
        candidate = f"{base}({count}){ext}"
        if not os.path.exists(candidate):
            return candidate
        count += 1


# 将文件夹打包为 ZIP 并按文件上传到服务器。
def backup_folder(sock, folder_path):
    if not os.path.exists(folder_path) or not os.path.isdir(folder_path):
        print("文件夹不存在或不是目录")
        return False

    folder_name = os.path.basename(os.path.normpath(folder_path))
    if not folder_name:
        print("无效的文件夹名称")
        return False

    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp_file:
            temp_zip_path = temp_file.name

        with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(folder_path):
                rel_root = os.path.relpath(root, folder_path)
                if rel_root == '.':
                    rel_root = ''
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.join(rel_root, file) if rel_root else file
                    zf.write(file_path, arcname)

        remote_name = f"{FOLDER_UPLOAD_PREFIX}{folder_name}"
        return send_file(sock, temp_zip_path, remote_name=remote_name)
    finally:
        try:
            os.remove(temp_zip_path)
        except OSError:
            pass


# 从服务器下载指定备份文件或文件夹到本地。
def download_file(sock, filename):
    if not is_valid_backup_name(filename):
        print("无效的文件名或文件夹名")
        return False

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

    if len(info_data) < 10:
        print("下载信息格式错误")
        return False

    success = info_data[0]
    file_type = info_data[1]
    file_size = struct.unpack('>Q', info_data[2:10])[0]
    if success == 0:
        print("文件或文件夹不存在")
        return False

    if file_type == 2:
        print(f"开始下载文件夹: {filename} ({file_size} 字节, 将保存为 zip)")
    else:
        print(f"开始下载文件: {filename} ({file_size} 字节)")

    os.makedirs("downloaded_backups", exist_ok=True)
    if file_type == 2:
        download_path = os.path.join("downloaded_backups", f"{filename}.zip")
    else:
        download_path = os.path.join("downloaded_backups", filename)
    download_path = get_unique_path(download_path)

    try:
        with open(download_path, 'wb') as f:
            received_size = 0
            block_num = 0
            total_blocks = (file_size + BLOCK_SIZE - 1) // BLOCK_SIZE
            start_time = time.time()

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
                elapsed_time = time.time() - start_time
                progress = (received_size / file_size) * 100
                remaining_size = file_size - received_size
                speed = received_size / elapsed_time if elapsed_time > 0 else 0
                speed_str = f"{speed / 1024:.1f}KB/s" if speed > 0 else "计算中..."
                remaining_str = f"{remaining_size / 1024:.1f}KB" if remaining_size > 0 else "0KB"
                print(f"\r已接收：{progress:.1f}%，剩余：{remaining_str}，速度：{speed_str}", end="", flush=True)

        print(f"\n下载完成: {download_path}")
        return True

    except IOError as e:
        print(f"写入文件失败: {e}")
        return False


# 请求服务器删除指定备份文件或文件夹。
def delete_file(sock, filename):
    if not is_valid_backup_name(filename):
        print("无效的文件名或文件夹名")
        return False

    request_data = filename.encode('utf-8')
    header = make_header(MSG_DELETE_REQUEST, request_data)
    if not send_with_retry(sock, header + request_data):
        print("发送删除请求失败")
        return False

    result_header = receive_with_timeout(sock, 9, TIMEOUT)
    if not result_header or len(result_header) < 9:
        print("未收到删除结果响应")
        return False

    magic, msg_type, length, checksum = struct.unpack('>HBIH', result_header[:9])
    if magic != MAGIC or msg_type != MSG_DELETE_RESULT:
        print("收到无效的删除结果响应")
        return False

    result_data = receive_with_timeout(sock, length, TIMEOUT)
    if not result_data or len(result_data) < length:
        print("删除结果数据不完整")
        return False

    if len(result_data) >= 1:
        result = result_data[0]
        if result == 1:
            print(f"删除成功: {filename}")
            return True
        else:
            print(f"删除失败: {filename} (文件不存在或删除失败)")
            return False

    print("收到无效的删除结果响应")
    return False


# 询问用户是否继续进行备份操作。
def ask_continue():
    while True:
        choice = input("\n是否继续备份文件？(y/n): ").strip().lower()
        if choice in ['y', 'n']:
            return choice == 'y'
        print("请输入 y 或 n")


# 启动客户端交互流程并处理菜单操作。
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
        print("2. 上传文件夹")
        print("3. 查看服务器备份列表")
        print("4. 下载备份文件/文件夹")
        print("5. 删除备份文件/文件夹")
        print("6. 退出")

        choice = input("请输入选择 (1-6): ").strip()
        if choice == '6':
            print("\n感谢使用，再见！")
            break

        if choice not in ['1', '2', '3', '4', '5']:
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
            folder_path = input("请输入要备份的文件夹路径: ").strip()
            if not folder_path:
                print("文件夹路径不能为空")
                continue

            if not os.path.isdir(folder_path):
                print("路径不是文件夹")
                continue

            success = backup_folder(sock, folder_path)

        elif choice == '3':
            success = list_backups(sock)

        elif choice == '4':
            filename = input("请输入要下载的文件或文件夹名: ").strip()
            if not filename:
                print("名称不能为空")
                continue
            success = download_file(sock, filename)

        elif choice == '5':
            filename = input("请输入要删除的文件或文件夹名: ").strip()
            if not filename:
                print("名称不能为空")
                continue
            success = delete_file(sock, filename)

        print("-" * 40)
        if success:
            print("操作完成")
        else:
            print("操作失败")

    sock.close()


if __name__ == "__main__":
    main()