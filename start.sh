#!/bin/bash

# FNOS Extractor 快速启动脚本

set -e

echo "🚀 FNOS 批量解压工具启动脚本"
echo "================================"

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker 未安装，请先安装 Docker"
    exit 1
fi

echo "✓ Docker 已安装"

# 检查 docker-compose
if ! command -v docker-compose &> /dev/null; then
    echo "⚠️  docker-compose 未安装，尝试使用 docker compose..."
    COMPOSE_CMD="docker compose"
else
    COMPOSE_CMD="docker-compose"
fi

echo "✓ 准备启动容器"

# 启动容器
echo ""
echo "启动中..."
$COMPOSE_CMD up -d

# 等待容器就绪
echo ""
echo "⏳ 等待容器就绪..."
sleep 3

# 检查容器状态
if docker ps | grep -q fnos-extractor; then
    echo ""
    echo "✅ 容器已启动！"
    echo ""
    echo "🌐 Web 界面: http://localhost:5000"
    echo ""
    echo "📝 常用命令:"
    echo "  查看日志: $COMPOSE_CMD logs -f"
    echo "  停止容器: $COMPOSE_CMD down"
    echo "  重启容器: $COMPOSE_CMD restart"
else
    echo "❌ 容器启动失败"
    echo ""
    echo "查看详细错误:"
    $COMPOSE_CMD logs
    exit 1
fi
