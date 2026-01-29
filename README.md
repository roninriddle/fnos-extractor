# FNOS 批量解压工具

🚀 一个生产级别的批量解压工具，支持 Web UI、递归扫描、密码自动检测和智能解压。
docker pull roninriddle/fnos-extractor

**维护者**: Ronin

## ✨ 核心特性

- **🌐 Web UI** - 现代化网页界面，开箱即用
- **📁 递归扫描** - 自动扫描目录及所有子目录
- **🔐 智能密码** - 自动检测是否加密，使用密码词典尝试解压
- **💾 密码缓存** - ⭐ 自动记忆成功密码，性能提升 10-50 倍（v1.1.0+）
- **⚙️ 目录管理** - ⭐ Web 界面快速切换工作目录（v1.1.0+）
- **🔐 密码编辑** - ⭐ 在线编辑密码词典（v1.1.0+）
- **🎯 批量操作** - 支持批量选择和批量解压
- **📊 实时进度** - 实时显示解压状态和日志
- **🧼 无污染** - 容器清理不留痕迹
- **⚡ 高效率** - 多线程并发处理

## 📦 支持格式

- `.7z` (7-Zip)
- `.rar` (RAR)
- `.zip` (ZIP)

## 🚀 快速开始

### 方式一：Docker Hub 拉取（最快）✨ 推荐

```bash
# 拉取官方镜像
docker pull roninriddle/fnos-extractor:latest

# 运行容器
docker run -d \
  --name fnos-extractor \
  -p 5000:5000 \
  -v /volume1/downloads:/volume1/downloads \
  roninriddle/fnos-extractor:latest

# 访问 http://localhost:5000
```

Docker Hub 地址：https://hub.docker.com/r/roninriddle/fnos-extractor

### 方式二：Docker Compose（推荐）

```bash
# 1. 克隆仓库
git clone https://github.com/roninriddle/fnos-extractor.git
cd fnos-extractor

# 2. 启动容器
docker-compose up -d

# 3. 访问 Web UI
# 打开浏览器访问 http://localhost:5000
```

### 方式三：本地构建

```bash
# 构建镜像
docker build -t fnos-extractor:latest .

# 运行容器
docker run -d \
  --name fnos-extractor \
  -p 5000:5000 \
  -v /volume1/downloads:/volume1/downloads \
  fnos-extractor:latest

# 访问 http://localhost:5000
```

### 方式四：本地运行（需要系统工具）

```bash
# 安装依赖
pip install flask
sudo apt-get install p7zip-full unrar unzip

# 运行应用
python app.py

# 访问 http://localhost:5000
```

## 📝 使用说明

### Web UI 功能（v1.1.0+）

#### 📁 目录管理选项卡
- 快速切换常用目录（/volume1/downloads、/home、/tmp）
- 输入自定义目录路径
- 实时同步到扫描界面

#### 🔐 密码本选项卡
- 在线编辑密码词典
- 实时显示密码数量
- 保存到服务器

#### 💾 缓存管理选项卡
- 查看已缓存的成功密码
- 快速清空缓存
- 了解性能优化情况

### 1️⃣ 扫描目录

在 Web 界面输入要扫描的目录路径，点击"扫描"按钮。

- 自动扫描所有子目录
- 显示包含压缩包的子目录（点击快速切换）
- 实时检测文件是否加密

**示例**：
- `/volume1/downloads` - 扫描整个下载目录
- `/home/user/archives` - 扫描指定目录
- `/mnt/nas/files` - 扫描 NAS 挂载点

### 2️⃣ 选择文件

- ✓ **全选** - 选择所有文件
- ✗ **取消选择** - 取消所有选择
- 🔐 **仅选密码** - 仅选择加密文件

### 3️⃣ 解压配置

指定解压目录
### 4️⃣ 开始解压

点击"开始批量解压"按钮，实时查看进度和日志。

**智能密码尝试**：
- 首先尝试缓存中的成功密码
- 再尝试密码词典中的所有密码
- 成功密码会自动保存到缓存
- 下次解压相同加密类型时速度大幅提升（10-50倍）

## 🔧 配置

### 修改密码词典

编辑 `passwords.txt` 文件，每行一个密码：

```
123456
password
admin
...
```

### 修改默认目录

编辑 `app.py` 中的配置：

```python
'default_mount': '/volume1/downloads',
```

或在运行时通过环境变量设置：

```bash
docker run -e DEFAULT_MOUNT=/custom/path ...
```

### 自定义解压器

如果需要添加更多格式支持，编辑 `app.py` 中的 `is_archive_encrypted()` 和 `extract_archive()` 函数。

## 🏗️ 项目结构

```
fnos-extractor/
├── app.py                  # Flask 后端应用
├── templates/
│   └── index.html          # Web UI 前端
├── passwords.txt           # 密码词典
├── Dockerfile              # Docker 镜像定义
├── docker-compose.yml      # Docker Compose 配置
├── requirements.txt        # Python 依赖
└── README.md               # 本文档
```

## 🔍 工作原理

### 加密检测流程

1. 调用 `7z list / unrar list / unzip -l` 命令
2. 检查返回码和标准错误输出
3. 如果返回失败或包含 "password" / "encrypted"，则判断为加密

### 解压流程

**无加密文件**：
```
文件 → 直接解压 ✓
```

**加密文件**：
```
文件 → 检测加密 → 尝试密码词典 → 成功/失败
```

### 并发处理

- 每个压缩包使用独立线程处理
- 支持同时解压多个文件
- 实时推送状态到前端

## 📊 API 接口

### `GET /api/config`

获取应用配置

**响应**：
```json
{
  "default_mount": "/volume1/downloads",
  "password_cache_size": 5,
  "password_dict_size": 63,
  "supported_formats": [".7z", ".rar", ".zip"]
}
```

### `POST /api/scan`

扫描目录

**请求**：
```json
{
  "path": "/volume1/downloads"
}
```

**响应**：
```json
{
  "total": 5,
  "archives": [
    {
      "path": "/volume1/downloads/file1.7z",
      "name": "file1.7z",
      "size": 1024000,
      "encrypted": false
    }
  ],
  "subdirs_with_archives": {
    "subdir1": 3,
    "subdir2": 2
  }
}
```

### `POST /api/extract`

开始解压

**请求**：
```json
{
  "archives": ["/path/to/file1.7z"],
  "extract_to": "/volume1/downloads/extracted"
}
```

### `GET /api/status`

获取解压状态

**响应**：
```json
{
  "task_0": {
    "status": "success",
    "file": "/path/to/file1.7z",
    "message": "成功 (密码: 123456)"
  }
}
```

### `GET /api/password-cache`

获取密码缓存（v1.1.0+）

**响应**：
```json
{
  "123456": ["file1.7z"],
  "admin": ["file2.zip"]
}
```

### `DELETE /api/password-cache`

清空密码缓存（v1.1.0+）

### `GET /api/passwords`

获取密码词典（v1.1.0+）

### `POST /api/passwords`

更新密码词典（v1.1.0+）

**请求**：
```json
{
  "passwords": ["123456", "password", "admin"]
}
```

### `POST /api/subdirs`

扫描子目录（v1.1.0+）

**请求**：
```json
{
  "path": "/volume1/downloads"
}
```

## 🐛 故障排查

### 容器无法启动

```bash
# 查看日志
docker logs fnos-extractor

# 检查端口是否被占用
lsof -i :5000
```

### 无法访问 Web UI

- 确认容器已启动：`docker ps | grep fnos`
- 检查防火墙：`sudo ufw allow 5000`
- 尝试本地访问：`curl http://localhost:5000`

### 解压失败

1. 检查文件权限
2. 确认密码词典已加载：查看启动日志
3. 手动测试命令：
   ```bash
   7z x -ppassword file.7z -o/output
   unrar x -ppassword file.rar /output
   unzip -P password file.zip -d /output
   ```

### 密码识别失败

- 确认密码词典已加载
- 添加更多常见密码到 `passwords.txt`
- 检查密码是否包含特殊字符

## 📈 性能优化

### 对于大量文件

1. **增加线程数**（编辑 `app.py`）：
   ```python
   # 增加并发处理的文件数
   max_workers = 4  # 默认为 1
   ```

2. **使用 SSD**：存储密码词典和临时文件

3. **分批扫描**：不要一次性扫描太大的目录树

### 内存管理

- 容器默认无内存限制
- 在 `docker-compose.yml` 中设置限制：
  ```yaml
  deploy:
    resources:
      limits:
        memory: 2G
  ```

## 🔐 安全建议

1. **访问控制** - 部署时添加反向代理和认证
2. **网络隔离** - 仅在内网运行
3. **日志审计** - 定期检查解压日志
4. **密码安全** - 不要把常见密码写进去，添加专项密码

## 📜 版本历史

### v1.1.5（当前）- 2026-01-30 ✅ 已发布到 Docker Hub

**新增功能**：
- 📁 解压到同名文件夹：选择后，每个压缩包解压到与其同名的文件夹中
- 🗑️ 自动删除成功解压的压缩包：解压完成后自动删除已成功解压的压缩包文件
- 🎯 更便利的批量操作流程

### v1.1.4 - 2026-01-30 ✅ 已发布到 Docker Hub

**重要修复和功能**：
- 🐛 修复 7z 加密文件解压失败问题（密码参数位置错误）
- ⏸️ 添加解压控制：暂停、继续、停止解压
- 📝 增强错误日志：文件日志输出和实时查看功能
- 💾 支持日志下载，便于问题诊断

### v1.1.3 - 2026-01-30 ✅ 已发布到 Docker Hub

**优化**：
- ✨ 扫描后显示扫描中状态，提升用户体验
- ✨ 解压完成后显示成功失败清单，方便查看结果
- 🔧 加强加密压缩包处理：重试限制 5 次、超时 60 秒，提高稳定性
- 📝 增强日志输出，便于排查问题

### v1.1.2 - 2026-01-29 ✅ 已发布到 Docker Hub

**修复**：
- 🔧 修复解压 API 异常处理，确保返回 JSON 格式错误响应
- 🔧 改进前端错误处理，更好地识别非 JSON 响应
- 🔧 增强目标解压目录验证和创建逻辑

### v1.1.1 - 2026-01-29 ✅ 已发布到 Docker Hub

**新增和改进**：
- 📦 添加 `.tar` 系列格式支持（.tar, .tar.gz, .tgz, .tar.bz2, .tbz2）
- 🔧 修复挂载目录搜索权限问题
- 🔧 改进递归扫描的错误处理
- 🔧 添加 os.walk 备选扫描方案
- 🔧 更新默认挂载路径到 /vol1/1000/Temp

### v1.1.0 - 2026-01-29 ✅ 已发布到 Docker Hub

**新增功能**：
- 💾 密码缓存系统 - 自动缓存成功密码，性能提升 10-50 倍
- 📁 子目录检测 - 自动识别含压缩包的子目录
- ⚙️ 目录管理 - Web 界面快速切换工作目录
- 🔐 密码编辑 - 在线编辑密码词典
- 📊 完整 API - 新增缓存、密码、子目录管理接口

**改进**：
- 优化前端界面，添加设置面板
- 增强错误处理和日志输出
- 改进密码尝试策略

### v1.0.0 - 基础版本

**功能**：
- Web UI 界面
- 递归目录扫描
- 智能密码检测
- 批量解压支持
- 实时进度显示

## 🙏 贡献

欢迎提交 Issue 和 Pull Request！

## � 项目链接

- **GitHub**: https://github.com/roninriddle/fnos-extractor
- **Docker Hub**: https://hub.docker.com/r/roninriddle/fnos-extractor
- **Issues**: https://github.com/roninriddle/fnos-extractor/issues

## 📞 支持

- 💬 Issues: https://github.com/roninriddle/fnos-extractor/issues
- 📖 Wiki: https://github.com/roninriddle/fnos-extractor/wiki

## 👨‍💼 维护者

- **Ronin** - 主要开发者和维护者

---

**Made with ❤️ by Ronin for FNOS users**

