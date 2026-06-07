# FNOS 批量文件处理工具 v1.3.24

🚀 生产级批量文件处理工具 - 支持 Web UI、密码破解、多卷文件、智能解压与批量重命名

**正式镜像**: `docker pull roninriddle/fnos-extractor:latest`

---

## ✨ 核心特性

### 🔍 智能扫描
- **递归扫描** - 支持扫描所有子目录或仅当前目录
- **双模式扫描** - 支持仅扫描压缩包，或扫描目录下所有文件
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
- **密码词典** - 支持自定义密码词典，短超时尝试并在必要时自动切换完整解压超时
- **智能缓存** - 自动记忆成功密码，后续快速解压
- **在线编辑** - Web UI 实时编辑密码词典

### 🎛️ 解压控制
- **三种模式** - 当前文件夹 / 同名文件夹 / 指定目录
- **智能并发** - 支持1-32个文件同时解压，运行中调整实时生效
- **自动删除** - 可选解压成功后自动删除源文件
- **流程控制** - 暂停/继续/停止操作

### ✏️ 批量重命名
- **批量增加** - 支持在文件名前后统一追加内容
- **批量替换** - 支持按文件名、完整文件名或扩展名执行替换

### 🌐 现代化 Web UI
- **响应式设计** - 适配各种屏幕尺寸
- **实时监控** - 显示总进度、当前文件进度、完成数量
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
docker compose up -d
```

仓库自带的 [docker-compose.yml](/Users/ronin/Downloads/fnos-extractor/docker-compose.yml) 默认使用正式版 `latest` 镜像。

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

**当前版本**: v1.3.24

**本次更新**:
- 🗑️ 修复解压完成弹窗中手动删除压缩包失败但前端无明确反馈的问题
- 📦 手动删除支持 `.001`、`.z01`、`.r00` 等多卷压缩包整组删除
- ⚡ 并发数量改为运行中实时应用，排队任务会立即按新上限调度
- 🛡️ 解压前同步检测指定目录真实读写权限，权限不足时直接给出可操作提示
- 📁 同名文件夹已存在时预检旧目录内容权限，避免 7z 覆盖到一半才失败
- 🧭 优化 7z `Operation not permitted` 报错提示，明确建议检查挂载权限或更换目录
- 🧩 密码短超时自动完整重试不再记录为 ERROR，减少误报干扰
- 🧪 补充删除接口和动态并发逻辑的回归检查
- ✅ 本版已整理为正式版 1.3.24

---

## 🤝 贡献

- **GitHub**: https://github.com/roninriddle/fnos-extractor
- **Docker Hub**: https://hub.docker.com/r/roninriddle/fnos-extractor
- **许可**: MIT License

欢迎提交 Issue 和 Pull Request！

---

**Last Updated**: 2026-06-07 | Version: 1.3.24
