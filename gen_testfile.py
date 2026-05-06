# 定义文件大小：10MB (10 * 1024 * 1024 字节)
file_size = 10 * 1024 * 1024

# 创建并写入文件
with open("2", "wb") as f:
    f.write(b"\0" * file_size)  # 写入10MB的空字节

print("文件创建完成！")