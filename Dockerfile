FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量支持中文
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PYTHONIOENCODING=utf-8

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    p7zip-full \
    unrar-free \
    unzip \
    zip \
    curl \
    xz-utils \
    zstd \
    locales \
    && rm -rf /var/lib/apt/lists/* \
    && localedef -i en_US -c -f UTF-8 -A /usr/share/locale/locale.alias en_US.UTF-8

# 复制应用文件
COPY app.py .
COPY templates/ templates/
COPY passwords.txt .
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/api/health || exit 1

# 暴露端口
EXPOSE 5000

# 启动应用
CMD ["python", "app.py"]
