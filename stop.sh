#!/bin/bash

# FNOS Extractor 停止脚本

echo "🛑 停止 FNOS 批量解压工具"
echo "=========================="

# 检查 docker-compose
if ! command -v docker-compose &> /dev/null; then
    COMPOSE_CMD="docker compose"
else
    COMPOSE_CMD="docker-compose"
fi

# 停止容器
echo "停止容器..."
$COMPOSE_CMD down

echo "✅ 已停止"
