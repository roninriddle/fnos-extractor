# FNOS 批量解压工具 v1.2.96

🚀 生产级批量解压工具 - 支持 Web UI、密码破解、多卷文件、智能解压

**官方镜像**: `docker pull roninriddle/fnos-extractor:latest`

---

## ✨ 核心特性

### 🔍 智能扫描
- **递归扫描** - 支持扫描所有子目录或仅当前目录
- **自动检测** - 智能判断压缩包加密状态（7z/ZIP/RAR）
- **高性能缓存** - 使用文件签名缓存，避免重复检测

### 📦 全格式支持

**多卷压缩包**:
- `.part1.7z` / `.part1.rar` (分卷格式)
- `.001`, `.002`, `.003` (通用多卷)
- `.Z01`, `.Z02` (WinRAR标准)
- `.r00`, `.r01` (RAR经典)

**标准格式**: `.7z`, `.rar`, `.zip`

**TAR系列**: `.tar`, `.tar.gz/.tgz`, `.tar.bz2/.tbz2`, `.tar.xz/.txz`, `.tar.zst`

**单独压缩**: `.gz`, `.bz2`, `.xz`, `.lzma`, `.zst`

**其他格式**: `.cab` (Windows Cabinet), `.iso` (光盘镜像)

### 🔐 密码管理
- **密码词典** - 支持自定义密码词典，每个密码5秒超时
- **智能缓存** - 自动记忆成功密码，后续快速解压
- **在线编辑** - Web UI 实时编辑密码词典

### 🎛️ 解压控制
- **三种模式** - 当前文件夹 / 同名文件夹 / 指定目录
- **智能并发** - 支持1-32个文件同时解压
- **自动删除** - 可选解压成功后自动删除源文件
- **流程控制** - 暂停/继续/停止操作

### 🌐 现代化 Web UI
- **响应式设计** - 适配各种屏幕尺寸
- **实时监控** - 显示进度、当前文件、完成数量
- **调试模式** - 实时日志显示与下载

---

## 🚀 快速开始

### Docker 运行（推荐）

```bash
docker pull roninriddle/fnos-extractor:latest
docker run -d \
  --name fnos-extractor \
  -p 5000:5000 \
  -v /path/to/archives:/vol1/1000/Temp \
  roninriddle/fnos-extractor:latest

# 访问 http://localhost:5000
```

### Docker Compose

```bash
git clone https://github.com/roninriddle/fnos-extractor.git
cd fnos-extractor
docker-compose up -d
```

### 本地开发

```bash
pip install -r requirements.txt
# Ubuntu/Debian
sudo apt-get install p7zip-full unrar-free unzip
# macOS
brew install p7zip unrar unzip

python app.py
```

---

## 📖 使用指南

### 基本流程

1. **扫描目录** - 输入路径，选择是否递归扫描
2. **选择文件** - 单选/全选/反选
3. **配置模式** - 选择解压位置和并发数
4. **开始解压** - 实时监控进度

### 密码管理

- **优先级** - 缓存密码 > 词典密码
- **编辑** - 点击设置 → 密码管理 → 在线编辑
- **格式** - 每行一个密码，UTF-8编码

### API 端点

- `GET /api/health` - 健康检查
- `GET /api/metrics` - 系统指标
- `POST /api/scan` - 扫描目录
- `POST /api/extract` - 开始解压
- `GET /api/status` - 解压状态

---

## 🔧 技术栈

- **后端**: Flask 3.1.2 + Python 3.11
- **前端**: HTML5 + CSS3 + JavaScript
- **容器**: Docker (多架构支持: amd64/arm64)
- **解压工具**: 7z, unrar, unzip, tar, xz, zstd

---

## 🔐 安全说明

- ✅ 密码仅在内存处理，不上传
- ✅ 成功密码缓存到本地JSON
- ⚠️ 不要在词典中存放敏感密码
- ⚠️ 自动删除功能会永久删除文件
- ✅ 建议在测试环境验证后使用

---

## 📦 版本信息

**当前版本**: v1.2.96

**本次更新**:
- 🧩 修复多卷压缩包分组错误，`.part2/.002/.z02/.r01` 现在会正确归组
- ⏯️ 修复暂停、继续、停止和并发数配置不生效的问题
- 🕒 修复容器内调试日志时间不正确的问题，统一按宿主机时区显示
- 🧹 优化成功解压后的自动删除和运行指标统计

---

## 🤝 贡献

**GitHub**: https://github.com/roninriddle/fnos-extractor
**Docker Hub**: https://hub.docker.com/r/roninriddle/fnos-extractor
**许可**: MIT License

欢迎提交 Issue 和 Pull Request！

---

**Last Updated**: 2026-03-22 | Version: 1.2.96
