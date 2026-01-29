# Docker Hub 推送指南 - v1.1.3

## 📋 概述

本文档说明如何将 FNOS 批量解压工具 v1.1.3 构建并推送到 Docker Hub。

## 🔧 前提条件

1. **Docker 已安装**
   ```bash
   docker --version  # 验证 Docker 版本
   ```

2. **Docker Hub 账户**
   - 创建账户: https://hub.docker.com/signup
   - 用户名: `roninriddle`

3. **本地登陆 Docker Hub**
   ```bash
   docker login
   ```

## 🚀 快速推送 (使用脚本)

### 方法 1: 使用推送脚本 (推荐)

```bash
# 进入项目目录
cd /Users/ronin/fnos-extractor

# 添加执行权限
chmod +x push_to_docker_hub.sh

# 运行推送脚本
./push_to_docker_hub.sh
```

脚本将自动执行以下步骤:
- ✅ 验证 Docker 安装
- ✅ 验证 Docker Hub 认证
- ✅ 构建镜像 (版本 1.1.2)
- ✅ 创建 latest 标签
- ✅ 推送版本标签
- ✅ 推送 latest 标签

## 🔨 手动推送步骤

### 步骤 1: 登陆 Docker Hub

```bash
docker login
# 输入用户名: roninriddle
# 输入密码: ****
```

### 步骤 2: 构建镜像

```bash
cd /Users/ronin/fnos-extractor

docker build -t roninriddle/fnos-extractor:1.1.2 .
```

构建输出示例:
```
Sending build context to Docker daemon  256 kB
Step 1 : FROM python:3.11-slim
 ---> abc123def456
...
Successfully built abc123def456
```

### 步骤 3: 创建 latest 标签

```bash
docker tag roninriddle/fnos-extractor:1.1.2 roninriddle/fnos-extractor:latest
```

### 步骤 4: 推送到 Docker Hub

```bash
# 推送版本标签
docker push roninriddle/fnos-extractor:1.1.2

# 推送 latest 标签
docker push roninriddle/fnos-extractor:latest
```

推送输出示例:
```
The push refers to a repository [docker.io/roninriddle/fnos-extractor]
abc123def456: Pushed
...
1.1.2: digest: sha256:... size: ...
```

## ✅ 验证推送

### 方法 1: 查看 Docker Hub 网页

访问: https://hub.docker.com/r/roninriddle/fnos-extractor

您应该看到:
- ✅ 标签 `1.1.2` 已列出
- ✅ 标签 `latest` 指向 `1.1.2`
- ✅ 镜像层信息

### 方法 2: 从 Docker Hub 拉取验证

```bash
# 拉取版本 1.1.2
docker pull roninriddle/fnos-extractor:1.1.2

# 拉取 latest
docker pull roninriddle/fnos-extractor:latest

# 运行容器验证
docker run -d \
  --name fnos-test \
  -p 5000:5000 \
  -v /vol1/1000/Temp:/vol1/1000/Temp \
  roninriddle/fnos-extractor:1.1.1

# 检查容器状态
docker ps | grep fnos-test

# 测试健康检查
curl http://localhost:5000

# 清理测试容器
docker stop fnos-test
docker rm fnos-test
```

## 📝 镜像信息

### 镜像标签

| 标签 | 说明 | 大小 |
|------|------|------|
| `1.1.1` | 当前版本 (最新修复) | ~200MB |
| `latest` | 指向 1.1.1 | ~200MB |
| `1.1.0` | 上一版本 (如果存在) | ~200MB |

### 镜像详情

```yaml
镜像名: roninriddle/fnos-extractor
标签: 1.1.1
基础镜像: python:3.11-slim
大小: ~200MB (压缩后 ~80MB)
端口: 5000
健康检查: 每 30 秒检查一次
重启策略: unless-stopped
```

### Dockerfile 内容

```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y p7zip-full unzip zip curl
COPY app.py .
COPY templates/ templates/
COPY passwords.txt .
RUN pip install --no-cache-dir flask
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/ || exit 1
EXPOSE 5000
CMD ["python", "app.py"]
```

## 🐛 故障排除

### 问题 1: Docker 命令未找到

```bash
# 解决: 安装 Docker
# macOS: brew install docker
# Ubuntu: sudo apt-get install docker.io
```

### 问题 2: Docker Hub 认证失败

```bash
# 重新登陆
docker logout
docker login

# 输入正确的凭据
# 用户名: roninriddle
# 密码: ****
```

### 问题 3: 构建失败

```bash
# 确保在项目根目录
cd /Users/ronin/fnos-extractor

# 清理旧镜像并重试
docker rmi roninriddle/fnos-extractor:1.1.1
docker build -t roninriddle/fnos-extractor:1.1.1 .
```

### 问题 4: 推送超时

```bash
# 重试推送
docker push roninriddle/fnos-extractor:1.1.1

# 或检查网络连接
ping docker.io
```

## 📊 推送历史

| 版本 | 推送日期 | 标签 | 状态 |
|------|---------|------|------|
| 1.1.1 | 2026-01-29 | ✅ latest, 1.1.1 | ✅ 已推送 |
| 1.1.0 | 2026-01-29 | ✅ 1.1.0 | ✅ 已推送 |
| 1.0.0 | 2026-01-28 | ✅ 1.0.0 | ✅ 已推送 |

## 🔗 相关链接

- **Docker Hub 仓库**: https://hub.docker.com/r/roninriddle/fnos-extractor
- **GitHub 项目**: https://github.com/roninriddle/fnos-extractor
- **Docker 文档**: https://docs.docker.com/
- **Docker Hub 文档**: https://docs.docker.com/docker-hub/

## 💡 最佳实践

1. **始终标记版本**: 每个发布都应有版本标签 (如 1.1.1)
2. **维护 latest 标签**: latest 应指向最新稳定版本
3. **测试镜像**: 推送前在本地测试镜像
4. **保持最小化**: 使用 slim 基础镜像减少大小
5. **添加健康检查**: 确保容器健康监控
6. **文档完善**: 在 Docker Hub 上添加详细说明

## 📞 支持

遇到问题? 请在 GitHub Issues 中报告:
https://github.com/roninriddle/fnos-extractor/issues
