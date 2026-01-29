#!/bin/bash
# Docker Hub 推送脚本
# 将项目推送到 Docker Hub

set -e

# 配置
DOCKER_USERNAME="roninriddle"
IMAGE_NAME="fnos-extractor"
VERSION="1.1.1"
LATEST_TAG="latest"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}================================${NC}"
echo -e "${YELLOW}FNOS 批量解压工具 - Docker推送脚本${NC}"
echo -e "${YELLOW}================================${NC}"
echo ""

# 检查 Docker 是否已安装
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ 错误: Docker 未安装${NC}"
    echo "请先安装 Docker: https://docs.docker.com/get-docker/"
    exit 1
fi

echo -e "${GREEN}✓ Docker 已安装${NC}"
echo ""

# 检查 Docker 登陆状态
echo -e "${YELLOW}📋 步骤 1: 检查 Docker Hub 认证${NC}"
if ! docker info > /dev/null 2>&1; then
    echo -e "${YELLOW}需要登陆到 Docker Hub...${NC}"
    docker login
else
    echo -e "${GREEN}✓ Docker Hub 已认证${NC}"
fi
echo ""

# 构建镜像
echo -e "${YELLOW}📋 步骤 2: 构建 Docker 镜像${NC}"
echo "构建 ${DOCKER_USERNAME}/${IMAGE_NAME}:${VERSION}..."
docker build -t ${DOCKER_USERNAME}/${IMAGE_NAME}:${VERSION} .

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ 镜像构建成功${NC}"
else
    echo -e "${RED}❌ 镜像构建失败${NC}"
    exit 1
fi
echo ""

# 创建 latest 标签
echo -e "${YELLOW}📋 步骤 3: 创建 latest 标签${NC}"
docker tag ${DOCKER_USERNAME}/${IMAGE_NAME}:${VERSION} ${DOCKER_USERNAME}/${IMAGE_NAME}:${LATEST_TAG}
echo -e "${GREEN}✓ 标签创建成功${NC}"
echo ""

# 推送版本标签
echo -e "${YELLOW}📋 步骤 4: 推送版本标签到 Docker Hub${NC}"
echo "推送 ${DOCKER_USERNAME}/${IMAGE_NAME}:${VERSION}..."
docker push ${DOCKER_USERNAME}/${IMAGE_NAME}:${VERSION}

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ 版本标签推送成功${NC}"
else
    echo -e "${RED}❌ 版本标签推送失败${NC}"
    exit 1
fi
echo ""

# 推送 latest 标签
echo -e "${YELLOW}📋 步骤 5: 推送 latest 标签到 Docker Hub${NC}"
echo "推送 ${DOCKER_USERNAME}/${IMAGE_NAME}:${LATEST_TAG}..."
docker push ${DOCKER_USERNAME}/${IMAGE_NAME}:${LATEST_TAG}

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ latest 标签推送成功${NC}"
else
    echo -e "${RED}❌ latest 标签推送失败${NC}"
    exit 1
fi
echo ""

# 显示推送完成信息
echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}✅ Docker 镜像推送完成！${NC}"
echo -e "${GREEN}================================${NC}"
echo ""
echo "镜像信息:"
echo "  • 仓库: https://hub.docker.com/r/${DOCKER_USERNAME}/${IMAGE_NAME}"
echo "  • 版本标签: ${VERSION}"
echo "  • Latest 标签: ${LATEST_TAG}"
echo ""
echo "使用方式:"
echo "  docker pull ${DOCKER_USERNAME}/${IMAGE_NAME}:${VERSION}"
echo "  docker pull ${DOCKER_USERNAME}/${IMAGE_NAME}:${LATEST_TAG}"
echo ""

# 显示镜像信息
echo -e "${YELLOW}本地镜像信息:${NC}"
docker images | grep ${IMAGE_NAME} || true
echo ""

echo -e "${GREEN}✨ 推送完成！可在 Docker Hub 查看: https://hub.docker.com/r/${DOCKER_USERNAME}/${IMAGE_NAME}${NC}"
