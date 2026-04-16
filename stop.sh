#!/bin/bash

# FNOS Extractor 停止脚本

echo "🛑 停止 FNOS 批量文件处理工具"
echo "=========================="

# 优先使用 docker compose，兼容旧版 docker-compose
if docker compose version &> /dev/null; then
    COMPOSE_CMD="docker compose"
elif command -v docker-compose &> /dev/null; then
    COMPOSE_CMD="docker-compose"
else
    echo "❌ 未找到 docker compose / docker-compose"
    exit 1
fi

# 停止容器
echo "停止容器..."
$COMPOSE_CMD down

echo "✅ 已停止"
