#!/bin/bash

# 快速镜像构建和测试脚本

echo "🔨 FNOS Extractor 构建脚本"
echo "=========================="

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 步骤 1: 验证环境
echo -e "${BLUE}[1/4] 检查环境...${NC}"
if ! command -v docker &> /dev/null; then
    echo "❌ 需要安装 Docker"
    exit 1
fi
echo -e "${GREEN}✓ Docker 就绪${NC}"

# 步骤 2: 构建镜像
echo ""
echo -e "${BLUE}[2/4] 构建镜像...${NC}"
docker build -t fnos-extractor:latest -t fnos-extractor:dev .

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ 镜像构建成功${NC}"
else
    echo "❌ 镜像构建失败"
    exit 1
fi

# 步骤 3: 清理旧容器
echo ""
echo -e "${BLUE}[3/4] 清理旧容器...${NC}"
docker-compose down 2>/dev/null || true

# 步骤 4: 启动新容器
echo ""
echo -e "${BLUE}[4/4] 启动容器...${NC}"
docker-compose up -d

# 等待服务就绪
echo ""
echo "⏳ 等待服务就绪..."
for i in {1..30}; do
    if curl -s http://localhost:5000/ > /dev/null 2>&1; then
        echo -e "${GREEN}✓ 服务就绪${NC}"
        break
    fi
    echo -n "."
    sleep 1
done

echo ""
echo -e "${GREEN}✅ 构建完成！${NC}"
echo ""
echo "📊 镜像信息:"
docker image inspect fnos-extractor:latest | grep -E '"RepoTags"|"Size"'
echo ""
echo "🌐 访问地址: http://localhost:5000"
echo ""
echo "查看日志: docker-compose logs -f"
