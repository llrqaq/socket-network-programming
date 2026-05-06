import socket
import struct
import os
import threading
import zipfile
import shutil
import tempfile
import time
import json
import hashlib

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
BACKUP_FOLDER = "server_backup"
FOLDER_UPLOAD_PREFIX = "__FOLDER__:"
PROGRESS_FILE_NAME = ".upload_progress.json"
UPLOAD_PROGRESS = {}
PROGRESS_LOCK = threading.Lock()


# 计算报文头和数据体的 CRC16 校验值。
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


# 校验并解析固定 9 字节协议头。
def verify_header(header):
    if len(header) < 9:
        return False, None, None, None, None
    magic, msg_type, length, checksum = struct.unpack('>HBIH', header[:9])
    if magic != MAGIC:
        return False, None, None, None, None
    return True, msg_type, length, checksum, header


# 向客户端发送指定块号的 ACK 响应。
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


# 向客户端返回上传结果和可选提示信息。
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


# 校验备份名称是否合法，避免非法路径访问。
def is_valid_backup_name(name):
    if not name:
        return False
    if name.startswith('/') or name.startswith('\\') or '..' in name:
        return False
    if '/' in name or '\\' in name:
        return False
    return True


# 生成服务端不重名的备份保存名称。
def get_unique_backup_name(name):
    base, ext = os.path.splitext(name)
    candidate = name
    count = 1
    while os.path.exists(os.path.join(BACKUP_FOLDER, candidate)):
        candidate = f"{base}({count}){ext}"
        count += 1
    return candidate


# 读取备份对应的时间戳和客户端 IP 元数据。
def get_backup_metadata(filename):
    """Get backup metadata (timestamp and client IP)"""
    meta_file = os.path.join(BACKUP_FOLDER, f".{filename}.meta")
    timestamp = int(time.time())
    client_ip = "unknown"
    
    if os.path.exists(meta_file):
        try:
            with open(meta_file, 'r') as f:
                lines = f.read().strip().split('\n')
                if len(lines) >= 2:
                    timestamp = int(lines[0])
                    client_ip = lines[1]
        except (OSError, ValueError):
            pass
    return timestamp, client_ip


# 保存备份对应的时间戳和客户端 IP 元数据。
def save_backup_metadata(filename, client_addr):
    """Save backup metadata (timestamp and client IP)"""
    meta_file = os.path.join(BACKUP_FOLDER, f".{filename}.meta")
    try:
        with open(meta_file, 'w') as f:
            timestamp = int(time.time())
            client_ip = client_addr[0]
            f.write(f"{timestamp}\n{client_ip}")
    except OSError:
        pass


# 组装并发送当前服务器备份列表。
def send_backup_list(sock):
    try:
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

        list_data = b''
        for filename, size, is_dir in items:
            timestamp, client_ip = get_backup_metadata(filename)
            item_type = 2 if is_dir else 1
            list_data += struct.pack('>B', item_type)
            list_data += filename.encode('utf-8') + b'\x00' + struct.pack('>Q', size)
            list_data += struct.pack('>Q', timestamp)
            list_data += client_ip.encode('utf-8') + b'\x00'

        header = struct.pack('>HBIH', MAGIC, MSG_BACKUP_LIST, len(list_data), 0)
        checksum = calc_crc16(header + list_data)
        header = struct.pack('>HBIH', MAGIC, MSG_BACKUP_LIST, len(list_data), checksum)
        sock.sendall(header + list_data)
        return True
    except socket.error:
        return False


# 发送下载前的存在性、类型和大小信息。
def send_download_info(sock, success, file_type=0, size=0):
    if not success:
        info_data = struct.pack('>B', 0)
    else:
        info_data = struct.pack('>BBQ', 1, file_type, size)

    header = struct.pack('>HBIH', MAGIC, MSG_DOWNLOAD_INFO, len(info_data), 0)
    checksum = calc_crc16(header + info_data)
    header = struct.pack('>HBIH', MAGIC, MSG_DOWNLOAD_INFO, len(info_data), checksum)
    try:
        sock.sendall(header + info_data)
        return True
    except socket.error:
        return False


# 按协议发送单个下载数据块。
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


# 发送删除操作结果给客户端。
def send_delete_result(sock, success):
    result_data = struct.pack('>B', 1 if success else 0)
    header = struct.pack('>HBIH', MAGIC, MSG_DELETE_RESULT, len(result_data), 0)
    checksum = calc_crc16(header + result_data)
    header = struct.pack('>HBIH', MAGIC, MSG_DELETE_RESULT, len(result_data), checksum)
    try:
        sock.sendall(header + result_data)
        return True
    except socket.error:
        return False


# 按指定长度完整接收数据，不足则返回 None。
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


# 删除上传或下载过程中产生的临时文件。
def cleanup_temp(temp_path):
    try:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    except OSError:
        pass


# 获取断点进度持久化文件的路径。
def get_progress_file_path():
    return os.path.join(BACKUP_FOLDER, PROGRESS_FILE_NAME)


# 生成断点记录使用的“客户端IP+文件名”键。
def get_upload_key(client_ip, filename):
    return f"{client_ip}|{filename}"


# 根据上传键生成稳定且可复用的临时文件路径。
def get_temp_path_for_key(upload_key):
    digest = hashlib.md5(upload_key.encode('utf-8')).hexdigest()[:16]
    return os.path.join(BACKUP_FOLDER, f".temp_{digest}.part")


# 从磁盘加载并清洗断点续传进度记录。
def load_upload_progress():
    path = get_progress_file_path()
    if not os.path.exists(path):
        return {}

    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(raw, dict):
        return {}

    cleaned = {}
    for key, entry in raw.items():
        if not isinstance(entry, dict):
            continue

        temp_path = entry.get('temp_path')
        expected_size = entry.get('expected_size')
        received_size = entry.get('received_size')
        last_block = entry.get('last_block')

        if not isinstance(temp_path, str) or not os.path.exists(temp_path):
            continue
        if not isinstance(expected_size, int) or not isinstance(received_size, int) or not isinstance(last_block, int):
            continue
        if expected_size < 0 or received_size < 0 or received_size > expected_size or last_block < 0:
            continue

        cleaned[key] = {
            'expected_size': expected_size,
            'received_size': received_size,
            'last_block': last_block,
            'temp_path': temp_path,
        }

    return cleaned


# 将当前断点续传进度原子化写入磁盘。
def save_upload_progress():
    path = get_progress_file_path()
    tmp_path = path + '.tmp'
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(UPLOAD_PROGRESS, f, ensure_ascii=False)
        os.replace(tmp_path, path)
    except OSError:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


# 向客户端发送可续传的下一块号和已接收字节数。
def send_resume_info(sock, next_block, received_size):
    data = struct.pack('>IQ', next_block, received_size)
    header = struct.pack('>HBIH', MAGIC, MSG_RESUME_INFO, len(data), 0)
    checksum = calc_crc16(header + data)
    header = struct.pack('>HBIH', MAGIC, MSG_RESUME_INFO, len(data), checksum)
    try:
        sock.sendall(header + data)
        return True
    except socket.error:
        return False


# 将目录压缩为 ZIP 文件并返回压缩包大小。
def zip_directory(source_dir, zip_path):
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(source_dir):
            rel_root = os.path.relpath(root, source_dir)
            if rel_root == '.':
                rel_root = ''
            for file in files:
                full_path = os.path.join(root, file)
                arcname = os.path.join(rel_root, file) if rel_root else file
                zf.write(full_path, arcname)
    return os.path.getsize(zip_path)


# 处理单个客户端连接的上传、下载、列表和删除请求。
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
                print(f"[{addr}] 请求下载文件/文件夹: {filename}")

                if not is_valid_backup_name(filename):
                    send_download_info(sock, False)
                    continue

                filepath = os.path.join(BACKUP_FOLDER, filename)
                if not os.path.exists(filepath):
                    send_download_info(sock, False)
                    continue

                if os.path.isfile(filepath):
                    file_size = os.path.getsize(filepath)
                    if not send_download_info(sock, True, 1, file_size):
                        return

                    try:
                        with open(filepath, 'rb') as f:
                            block_num = 0
                            while True:
                                chunk = f.read(BLOCK_SIZE)
                                if not chunk:
                                    break

                                block_num += 1
                                if not send_download_block(sock, filepath, block_num, chunk):
                                    print(f"[{addr}] 发送下载块 {block_num} 失败")
                                    return

                                print(f"[{addr}] 发送下载块 {block_num} 成功")

                        print(f"[{addr}] 文件下载完成: {filename}")
                        continue
                    except IOError as e:
                        print(f"[{addr}] 读取文件失败: {e}")
                        return

                if os.path.isdir(filepath):
                    try:
                        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as temp_zip:
                            temp_path = temp_zip.name
                        zip_size = zip_directory(filepath, temp_path)
                        if not send_download_info(sock, True, 2, zip_size):
                            cleanup_temp(temp_path)
                            return

                        with open(temp_path, 'rb') as f:
                            block_num = 0
                            while True:
                                chunk = f.read(BLOCK_SIZE)
                                if not chunk:
                                    break

                                block_num += 1
                                if not send_download_block(sock, temp_path, block_num, chunk):
                                    print(f"[{addr}] 发送下载块 {block_num} 失败")
                                    cleanup_temp(temp_path)
                                    return

                                print(f"[{addr}] 发送下载块 {block_num} 成功")

                        print(f"[{addr}] 文件夹下载完成: {filename}")
                        cleanup_temp(temp_path)
                        continue
                    except IOError as e:
                        print(f"[{addr}] 读取目录压缩失败: {e}")
                        cleanup_temp(temp_path)
                        return

            elif msg_type == MSG_DELETE_REQUEST:
                data = receive_full(sock, length)
                if not data or len(data) < length:
                    print(f"[{addr}] 接收删除请求数据失败")
                    return

                if calc_crc16(hdr[:7] + b'\x00\x00' + data) != checksum:
                    print(f"[{addr}] 删除请求校验失败")
                    return

                filename = data.decode('utf-8')
                print(f"[{addr}] 请求删除文件/文件夹: {filename}")

                if not is_valid_backup_name(filename):
                    send_delete_result(sock, False)
                    continue

                filepath = os.path.join(BACKUP_FOLDER, filename)
                if os.path.isfile(filepath):
                    try:
                        os.remove(filepath)
                        print(f"[{addr}] 文件删除成功: {filename}")
                        send_delete_result(sock, True)
                    except OSError as e:
                        print(f"[{addr}] 删除文件失败: {e}")
                        send_delete_result(sock, False)
                elif os.path.isdir(filepath):
                    try:
                        shutil.rmtree(filepath)
                        print(f"[{addr}] 文件夹删除成功: {filename}")
                        send_delete_result(sock, True)
                    except OSError as e:
                        print(f"[{addr}] 删除文件夹失败: {e}")
                        send_delete_result(sock, False)
                else:
                    print(f"[{addr}] 文件或文件夹不存在: {filename}")
                    send_delete_result(sock, False)
                continue

            elif msg_type == MSG_FILE_INFO:
                pass
            else:
                print(f"[{addr}] 未知消息类型: {msg_type}")
                return

            data = receive_full(sock, length)
            if not data or len(data) < length:
                print(f"[{addr}] 接收文件信息数据失败")
                return

            if calc_crc16(hdr[:7] + b'\x00\x00' + data) != checksum:
                print(f"[{addr}] 文件信息校验失败")
                return

            null_pos = data.find(b'\x00')
            if null_pos == -1 or len(data) < null_pos + 9:
                print(f"[{addr}] 文件信息格式错误")
                return

            filename = data[:null_pos].decode('utf-8')
            expected_size = struct.unpack('>Q', data[null_pos + 1:null_pos + 9])[0]
            expected_blocks = (expected_size + BLOCK_SIZE - 1) // BLOCK_SIZE

            folder_upload = filename.startswith(FOLDER_UPLOAD_PREFIX)
            if folder_upload:
                folder_name = filename[len(FOLDER_UPLOAD_PREFIX):]
                if not is_valid_backup_name(folder_name):
                    print(f"[{addr}] 非法文件夹名称: {folder_name}")
                    send_result(sock, False, "非法文件夹名称")
                    return
                print(f"[{addr}] 收到文件夹备份请求: {folder_name}, 大小: {expected_size} 字节")
            else:
                folder_name = None
                print(f"[{addr}] 收到文件信息: {filename}, 大小: {expected_size} 字节")

            if not os.path.exists(BACKUP_FOLDER):
                try:
                    os.makedirs(BACKUP_FOLDER)
                except OSError as e:
                    print(f"[{addr}] 创建备份文件夹失败: {e}")
                    send_result(sock, False, "服务器无法创建备份文件夹")
                    return

            resume_header = receive_full(sock, 9)
            if not resume_header:
                print(f"[{addr}] 未收到断点查询请求")
                return

            resume_valid, resume_type, resume_len, resume_checksum, resume_hdr = verify_header(resume_header)
            if not resume_valid or resume_type != MSG_RESUME_QUERY:
                print(f"[{addr}] 断点查询消息类型错误")
                return

            resume_data = receive_full(sock, resume_len)
            if not resume_data or len(resume_data) < resume_len:
                print(f"[{addr}] 断点查询数据不完整")
                return

            if calc_crc16(resume_hdr[:7] + b'\x00\x00' + resume_data) != resume_checksum:
                print(f"[{addr}] 断点查询校验失败")
                return

            resume_null_pos = resume_data.find(b'\x00')
            if resume_null_pos == -1 or len(resume_data) < resume_null_pos + 9:
                print(f"[{addr}] 断点查询格式错误")
                return

            resume_filename = resume_data[:resume_null_pos].decode('utf-8')
            resume_size = struct.unpack('>Q', resume_data[resume_null_pos + 1:resume_null_pos + 9])[0]
            if resume_filename != filename or resume_size != expected_size:
                print(f"[{addr}] 断点查询与文件信息不一致")
                send_result(sock, False, "断点查询参数不一致")
                return

            upload_key = get_upload_key(addr[0], filename)
            stable_temp_path = get_temp_path_for_key(upload_key)
            corrupted_progress = False

            with PROGRESS_LOCK:
                progress = UPLOAD_PROGRESS.get(upload_key)
                if progress and progress.get('expected_size') == expected_size and os.path.exists(progress.get('temp_path', '')):
                    temp_path = progress['temp_path']
                    received_size = progress.get('received_size', 0)
                    received_blocks = progress.get('last_block', 0)
                else:
                    temp_path = stable_temp_path
                    cleanup_temp(temp_path)
                    received_size = 0
                    received_blocks = 0
                    UPLOAD_PROGRESS[upload_key] = {
                        'expected_size': expected_size,
                        'received_size': 0,
                        'last_block': 0,
                        'temp_path': temp_path,
                    }
                    save_upload_progress()

            if os.path.exists(temp_path):
                actual_size = os.path.getsize(temp_path)
                if actual_size != received_size:
                    received_size = actual_size
                    received_blocks = received_size // BLOCK_SIZE
                    corrupted_progress = True

            if received_size > expected_size:
                received_size = 0
                received_blocks = 0
                corrupted_progress = True
                cleanup_temp(temp_path)

            with PROGRESS_LOCK:
                UPLOAD_PROGRESS[upload_key] = {
                    'expected_size': expected_size,
                    'received_size': received_size,
                    'last_block': received_blocks,
                    'temp_path': temp_path,
                }
                save_upload_progress()

            next_block = received_blocks + 1
            if not send_resume_info(sock, next_block, received_size):
                print(f"[{addr}] 发送断点信息失败")
                return

            if corrupted_progress:
                print(f"[{addr}] 已重建断点进度: 块 {received_blocks}/{expected_blocks}")
            else:
                print(f"[{addr}] 断点信息: 从块 {next_block}/{expected_blocks} 开始")

            open_mode = 'r+b' if os.path.exists(temp_path) else 'wb'
            try:
                temp_file = open(temp_path, open_mode)
                temp_file.seek(received_size)
            except IOError as e:
                print(f"[{addr}] 无法打开临时文件: {e}")
                send_result(sock, False, "服务器无法打开临时文件")
                return

            while received_size < expected_size:
                header = receive_full(sock, 9)
                if not header:
                    print(f"[{addr}] 接收数据块头失败，保留断点")
                    if temp_file:
                        temp_file.close()
                        temp_file = None
                    return

                valid, msg_type, length, checksum, hdr = verify_header(header)
                if not valid or msg_type != MSG_FILE_BLOCK:
                    print(f"[{addr}] 协议错误或消息类型错误")
                    if temp_file:
                        temp_file.close()
                        temp_file = None
                    cleanup_temp(temp_path)
                    with PROGRESS_LOCK:
                        UPLOAD_PROGRESS.pop(upload_key, None)
                        save_upload_progress()
                    return

                data = receive_full(sock, length)
                if not data or len(data) < length:
                    print(f"[{addr}] 接收数据块数据失败，保留断点")
                    if temp_file:
                        temp_file.close()
                        temp_file = None
                    return

                if calc_crc16(hdr[:7] + b'\x00\x00' + data) != checksum:
                    print(f"[{addr}] 数据块校验失败")
                    if temp_file:
                        temp_file.close()
                        temp_file = None
                    cleanup_temp(temp_path)
                    with PROGRESS_LOCK:
                        UPLOAD_PROGRESS.pop(upload_key, None)
                        save_upload_progress()
                    return

                if len(data) < 4:
                    print(f"[{addr}] 数据块格式错误")
                    if temp_file:
                        temp_file.close()
                        temp_file = None
                    cleanup_temp(temp_path)
                    with PROGRESS_LOCK:
                        UPLOAD_PROGRESS.pop(upload_key, None)
                        save_upload_progress()
                    return

                block_num = struct.unpack('>I', data[:4])[0]
                block_data = data[4:]
                expected_block = received_blocks + 1
                if block_num != expected_block:
                    print(f"[{addr}] 块序号错误: 期望 {expected_block}, 收到 {block_num}")
                    if temp_file:
                        temp_file.close()
                        temp_file = None
                    cleanup_temp(temp_path)
                    with PROGRESS_LOCK:
                        UPLOAD_PROGRESS.pop(upload_key, None)
                        save_upload_progress()
                    return

                if received_size + len(block_data) > expected_size:
                    print(f"[{addr}] 数据块超出期望大小")
                    if temp_file:
                        temp_file.close()
                        temp_file = None
                    cleanup_temp(temp_path)
                    with PROGRESS_LOCK:
                        UPLOAD_PROGRESS.pop(upload_key, None)
                        save_upload_progress()
                    return

                try:
                    temp_file.write(block_data)
                    temp_file.flush()
                except IOError as e:
                    print(f"[{addr}] 写入临时文件失败: {e}")
                    if temp_file:
                        temp_file.close()
                        temp_file = None
                    send_result(sock, False, "服务器写入文件失败")
                    return

                received_size += len(block_data)
                received_blocks = block_num

                with PROGRESS_LOCK:
                    UPLOAD_PROGRESS[upload_key] = {
                        'expected_size': expected_size,
                        'received_size': received_size,
                        'last_block': received_blocks,
                        'temp_path': temp_path,
                    }
                    save_upload_progress()

                if not send_ack(sock, block_num):
                    print(f"[{addr}] 发送ACK失败")
                    if temp_file:
                        temp_file.close()
                        temp_file = None
                    return

                print(f"[{addr}] 接收块 {received_blocks}/{expected_blocks} 成功")

            if temp_file:
                temp_file.close()
                temp_file = None

            if received_size != expected_size:
                print(f"[{addr}] 文件大小不匹配: 期望 {expected_size}, 收到 {received_size}")
                cleanup_temp(temp_path)
                with PROGRESS_LOCK:
                    UPLOAD_PROGRESS.pop(upload_key, None)
                    save_upload_progress()
                send_result(sock, False, "文件大小不匹配")
                return

            if folder_upload:
                final_folder_name = get_unique_backup_name(folder_name)
                final_dir = os.path.join(BACKUP_FOLDER, final_folder_name)
                try:
                    os.makedirs(final_dir, exist_ok=True)
                    with zipfile.ZipFile(temp_path, 'r') as zf:
                        zf.extractall(final_dir)
                    cleanup_temp(temp_path)
                    temp_path = None
                except zipfile.BadZipFile as e:
                    print(f"[{addr}] 解压文件夹失败: {e}")
                    cleanup_temp(temp_path)
                    with PROGRESS_LOCK:
                        UPLOAD_PROGRESS.pop(upload_key, None)
                        save_upload_progress()
                    send_result(sock, False, "文件夹解压失败")
                    return
                except OSError as e:
                    print(f"[{addr}] 保存文件夹失败: {e}")
                    cleanup_temp(temp_path)
                    with PROGRESS_LOCK:
                        UPLOAD_PROGRESS.pop(upload_key, None)
                        save_upload_progress()
                    send_result(sock, False, "服务器保存文件夹失败")
                    return

                print(f"[{addr}] 文件夹保存成功: {final_dir}")
                save_backup_metadata(final_folder_name, addr)
            else:
                final_name = get_unique_backup_name(filename)
                final_path = os.path.join(BACKUP_FOLDER, final_name)
                try:
                    os.rename(temp_path, final_path)
                    temp_path = None
                except OSError as e:
                    print(f"[{addr}] 保存文件失败: {e}")
                    cleanup_temp(temp_path)
                    with PROGRESS_LOCK:
                        UPLOAD_PROGRESS.pop(upload_key, None)
                        save_upload_progress()
                    send_result(sock, False, "服务器保存文件失败")
                    return

                print(f"[{addr}] 文件保存成功: {final_path}")
                save_backup_metadata(final_name, addr)

            with PROGRESS_LOCK:
                UPLOAD_PROGRESS.pop(upload_key, None)
                save_upload_progress()

            send_result(sock, True, "传输成功")
            print(f"[{addr}] 传输完成")
            continue

        except ConnectionResetError:
            print(f"[{addr}] 客户端断开连接，保留断点进度")
            if temp_file:
                temp_file.close()
            return
        except socket.timeout:
            print(f"[{addr}] 接收超时，保留断点进度")
            if temp_file:
                temp_file.close()
            return
        except socket.error as e:
            print(f"[{addr}] 套接字错误: {e}，保留断点进度")
            if temp_file:
                temp_file.close()
            return
        except Exception as e:
            print(f"[{addr}] 处理客户端时发生错误: {e}")
            if temp_file:
                temp_file.close()
            if temp_path:
                cleanup_temp(temp_path)
            return


# 启动服务器监听并为每个客户端连接创建处理线程。
def main():
    global BACKUP_FOLDER, UPLOAD_PROGRESS

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

    with PROGRESS_LOCK:
        UPLOAD_PROGRESS = load_upload_progress()
    if UPLOAD_PROGRESS:
        print(f"已加载断点记录: {len(UPLOAD_PROGRESS)} 条")

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