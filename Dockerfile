FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    p7zip-full \
    unzip \
    zip \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制应用文件
COPY app.py .
COPY templates/ templates/
COPY passwords.txt .

# 安装Python依赖
RUN pip install --no-cache-dir flask

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/ || exit 1

# 暴露端口
EXPOSE 5000

# 启动应用
CMD ["python", "app.py"]
