# Docker Hub v1.1.1 推送说明

## 📌 快速开始

如果您的环境中已安装 Docker，可以使用以下命令直接推送到 Docker Hub：

### 自动推送（推荐）

```bash
# 1. 进入项目目录
cd /Users/ronin/fnos-extractor

# 2. 确保已登陆 Docker Hub
docker login

# 3. 运行推送脚本
./push_to_docker_hub.sh
```

### 手动推送

```bash
# 1. 登陆 Docker Hub
docker login
# 输入用户名: roninriddle
# 输入密码: ****

# 2. 构建镜像
docker build -t roninriddle/fnos-extractor:1.1.1 .

# 3. 创建 latest 标签
docker tag roninriddle/fnos-extractor:1.1.1 roninriddle/fnos-extractor:latest

# 4. 推送到 Docker Hub
docker push roninriddle/fnos-extractor:1.1.1
docker push roninriddle/fnos-extractor:latest
```

## ✅ 推送完成后

镜像将在 Docker Hub 上可用：
- 📦 仓库: https://hub.docker.com/r/roninriddle/fnos-extractor
- 🏷️ 版本标签: `1.1.1`
- 🎯 Latest 标签: `latest`

## 📖 详细指南

查看完整的推送指南：[DOCKER_PUSH_GUIDE.md](DOCKER_PUSH_GUIDE.md)

## 🐳 验证推送

```bash
# 从 Docker Hub 拉取并运行
docker run -d \
  --name fnos-extractor \
  -p 5000:5000 \
  -v /vol1/1000/Temp:/vol1/1000/Temp \
  roninriddle/fnos-extractor:1.1.1

# 访问应用
curl http://localhost:5000
```

## 📝 版本更新内容

v1.1.1 包含以下改进：

- 🔧 **修复挂载目录搜索** - 解决权限问题，能够搜索到挂载目录下的所有压缩包
- 🔧 **改进错误处理** - 添加备选方案，单个文件错误不影响整体扫描
- 🔧 **更新默认路径** - 从 `/volume1/downloads` 改为 `/vol1/1000/Temp`
- 🔧 **增强日志** - 详细记录扫描过程和错误信息

## 🚀 下一步

推送完成后：

1. ✅ 在 GitHub 上验证提交: https://github.com/roninriddle/fnos-extractor
2. ✅ 在 Docker Hub 上验证镜像: https://hub.docker.com/r/roninriddle/fnos-extractor
3. ✅ 更新任何相关文档或公告

需要帮助? 请查看 [DOCKER_PUSH_GUIDE.md](DOCKER_PUSH_GUIDE.md) 中的故障排除部分。
