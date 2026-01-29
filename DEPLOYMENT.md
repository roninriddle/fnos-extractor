# FNOS Extractor - 部署和运维指南

**维护者**: Ronin

## 🚀 部署指南

### 最小化部署

```bash
# 一键启动（需要 Docker 和 Docker Compose）
bash start.sh

# 访问 Web UI
# http://localhost:5000
```

### 完整部署（含自定义配置）

```bash
# 1. 构建镜像
docker build -t fnos-extractor:v1.0 .

# 2. 编辑 docker-compose.yml
# 修改 volumes 和 ports 根据需要

# 3. 启动服务
docker-compose up -d

# 4. 验证
docker ps | grep fnos-extractor
curl http://localhost:5000
```

## 🔧 配置指南

### 1. 自定义密码词典

编辑 `passwords.txt`：

```
# 每行一个密码
123456
password
admin123
...
```

**性能提示**：
- 密码数量越多，尝试时间越长
- 建议按常见度排序
- 特殊密码放在前面

### 2. 自定义挂载目录

编辑 `docker-compose.yml`：

```yaml
volumes:
  - /your/custom/path:/volume1/downloads
```

### 3. 自定义端口

编辑 `docker-compose.yml`：

```yaml
ports:
  - "8080:5000"  # 外部:内部
```

### 4. 资源限制

编辑 `docker-compose.yml`：

```yaml
deploy:
  resources:
    limits:
      cpus: '2'
      memory: 4G
    reservations:
      cpus: '1'
      memory: 2G
```

## 📊 运维命令

### 查看容器日志

```bash
# 实时日志
docker-compose logs -f

# 最后 100 行
docker logs fnos-extractor | tail -100

# 搜索特定错误
docker logs fnos-extractor | grep ERROR
```

### 容器管理

```bash
# 启动
docker-compose up -d

# 停止
docker-compose down

# 重启
docker-compose restart

# 清理（删除容器和网络）
docker-compose down -v

# 查看容器状态
docker ps -a | grep fnos

# 进入容器调试
docker exec -it fnos-extractor bash
```

### 镜像管理

```bash
# 查看镜像
docker images | grep fnos

# 删除镜像
docker rmi fnos-extractor:latest

# 保存镜像（用于离线传输）
docker save fnos-extractor:latest | gzip > fnos-extractor.tar.gz

# 加载镜像
docker load < fnos-extractor.tar.gz
```

## 🔍 故障排查

### 常见问题

#### Q: 容器无法启动

A: 检查日志和端口占用

```bash
# 查看启动错误
docker-compose logs

# 检查 5000 端口
lsof -i :5000

# 更改端口或关闭占用程序
```

#### Q: Web UI 无法访问

A: 验证容器和网络

```bash
# 检查容器是否运行
docker ps | grep fnos-extractor

# 检查防火墙
sudo ufw allow 5000

# 测试连接
curl http://localhost:5000
```

#### Q: 解压失败

A: 检查系统工具和权限

```bash
# 进入容器测试
docker exec -it fnos-extractor bash

# 测试 7z
7z --version

# 测试 unrar
unrar --version

# 测试文件权限
ls -la /volume1/downloads
```

#### Q: 密码无法识别

A: 检查密码词典

```bash
# 进入容器
docker exec -it fnos-extractor bash

# 查看已加载密码数量
wc -l /app/passwords.txt

# 手动测试
7z x -ppassword123 file.7z
```

## 📈 性能监控

### 实时监控

```bash
# CPU 和内存使用
docker stats fnos-extractor

# 详细统计
docker stats --no-stream
```

### 日志分析

```bash
# 统计解压成功率
docker logs fnos-extractor | grep -c "成功"

# 统计失败次数
docker logs fnos-extractor | grep -c "失败"

# 查看平均处理时间
docker logs fnos-extractor | grep "成功" | wc -l
```

## 🔐 安全加固

### 1. 使用反向代理

```bash
# 使用 Nginx
docker run -d \
  --name nginx-proxy \
  -p 80:80 \
  -p 443:443 \
  -v /etc/nginx/conf.d:/etc/nginx/conf.d \
  nginx:latest
```

### 2. 添加认证

编辑 `app.py`：

```python
from functools import wraps

@app.before_request
def check_auth():
    auth = request.authorization
    if not auth or auth.password != 'your_password':
        return jsonify({'error': 'Unauthorized'}), 401
```

### 3. 限制访问 IP

使用 Nginx 配置：

```nginx
allow 192.168.1.0/24;
deny all;
```

## 📦 备份和恢复

### 备份配置

```bash
# 备份整个项目
tar -czf fnos-backup.tar.gz fnos-extractor/

# 备份仅配置和密码词典
tar -czf fnos-config-backup.tar.gz \
  fnos-extractor/passwords.txt \
  fnos-extractor/docker-compose.yml
```

### 恢复配置

```bash
# 恢复整个项目
tar -xzf fnos-backup.tar.gz

# 启动服务
cd fnos-extractor
docker-compose up -d
```

## 🚀 高级功能

### 自定义解压器

编辑 `app.py` 的 `extract_archive()` 函数添加支持的格式。

### 扩展密码词典

```bash
# 从在线源获取
curl https://example.com/passwords.txt >> passwords.txt

# 去重
sort passwords.txt | uniq > passwords.txt.new
mv passwords.txt.new passwords.txt
```

### 多实例部署

```bash
# 启动多个实例
docker-compose -f docker-compose-1.yml up -d
docker-compose -f docker-compose-2.yml up -d

# 使用负载均衡器分发请求
```

## 📞 技术支持

遇到问题？

1. 查看 [README.md](README.md)
2. 检查 [故障排查](#-故障排查) 部分
3. 查看容器日志：`docker-compose logs -f`
4. 提交 Issue：https://github.com/roninriddle/fnos-extractor/issues

---

**文档更新于**: 2024年
