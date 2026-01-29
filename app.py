#!/usr/bin/env python3
"""
FNOS 批量解压工具
支持递归扫描、密码检测和Web界面
"""

from flask import Flask, render_template, jsonify, request
from pathlib import Path
import os
import subprocess
import json
import threading
from queue import Queue
from typing import Dict, List, Tuple, Optional, Set
import logging
import tempfile
import shutil
import time

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 全局状态
extraction_queue = Queue()
extraction_status = {}
extraction_lock = threading.Lock()

# 密码词典和缓存
PASSWORD_DICT = []
PASSWORD_CACHE_FILE = Path('/app/password_cache.json')
PASSWORD_CACHE = {}  # 格式: {file_path: {password: timestamp}}
PASSWORD_SUCCESS_CACHE = {}  # 成功的密码缓存 {file_path: password}

def load_password_cache():
    """加载密码缓存"""
    global PASSWORD_SUCCESS_CACHE
    if PASSWORD_CACHE_FILE.exists():
        try:
            with open(PASSWORD_CACHE_FILE, 'r') as f:
                PASSWORD_SUCCESS_CACHE = json.load(f)
                logger.info(f"已加载 {len(PASSWORD_SUCCESS_CACHE)} 个缓存密码")
        except Exception as e:
            logger.warning(f"密码缓存加载失败: {e}")

def save_password_cache():
    """保存密码缓存"""
    try:
        with open(PASSWORD_CACHE_FILE, 'w') as f:
            json.dump(PASSWORD_SUCCESS_CACHE, f)
    except Exception as e:
        logger.warning(f"密码缓存保存失败: {e}")

def load_password_dict():
    """加载密码词典"""
    global PASSWORD_DICT
    dict_path = Path('/app/passwords.txt')
    if dict_path.exists():
        with open(dict_path, 'r', encoding='utf-8', errors='ignore') as f:
            PASSWORD_DICT = [line.strip() for line in f if line.strip()]
        logger.info(f"已加载 {len(PASSWORD_DICT)} 个密码")
    else:
        logger.warning("密码词典不存在")

def is_archive_encrypted(file_path: str) -> Tuple[bool, Optional[bool]]:
    """
    判断压缩包是否加密
    返回: (是否是压缩包, 是否加密)
    """
    try:
        file_name = Path(file_path).name.lower()

        # 支持 7z/zip/rar 以及 tar 系列 (.tar, .tar.gz, .tgz, .tar.bz2, .tbz2)
        if file_name.endswith('.7z'):
            result = subprocess.run(
                ['7z', 'l', file_path],
                capture_output=True,
                text=True,
                timeout=10
            )
            # 7z列出文件失败通常意味着加密
            if result.returncode != 0:
                return True, True
            # 检查输出中是否有密码提示
            if 'password' in result.stderr.lower() or 'encrypted' in result.stderr.lower():
                return True, True
            return True, False
            
        elif file_name.endswith('.zip'):
            result = subprocess.run(
                ['unzip', '-l', file_path],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                # 对于zip，列表失败可能表示加密
                if 'password' in result.stderr.lower() or 'encrypted' in result.stderr.lower():
                    return True, True
                # 尝试用 unzip -t 检查
                result2 = subprocess.run(
                    ['unzip', '-t', file_path],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if 'password' in result2.stderr.lower() or 'encrypted' in result2.stderr.lower():
                    return True, True
                return True, False
            return True, False
        
        elif file_name.endswith('.rar'):
            # 对于 RAR，也尝试用 7z 处理（7z 可以处理 rar）
            result = subprocess.run(
                ['7z', 'l', file_path],
                capture_output=True,
                text=True,
                timeout=10
            )
        elif file_name.endswith(('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2')):
            # tar 系列通常不支持加密检测，视为普通压缩包
            return True, False
            if result.returncode != 0:
                return True, True
            if 'password' in result.stderr.lower() or 'encrypted' in result.stderr.lower():
                return True, True
            return True, False
            
    except subprocess.TimeoutExpired:
        logger.warning(f"检测加密状态超时: {file_path}")
        return True, None
    except Exception as e:
        logger.error(f"检测加密状态出错 {file_path}: {e}")
        return False, None
    
    return False, None

def extract_archive(file_path: str, extract_dir: str, password: Optional[str] = None) -> Tuple[bool, str]:
    """
    解压文件
    返回: (成功, 消息)
    """
    try:
        file_name = Path(file_path).name.lower()
        cmd = []

        # 7z / rar
        if file_name.endswith(('.7z', '.rar')):
            cmd = ['7z', 'x', '-y', file_path, f'-o{extract_dir}']
            if password:
                cmd.insert(3, f'-p{password}')
        # zip
        elif file_name.endswith('.zip'):
            if password:
                cmd = ['unzip', '-P', password, '-o', file_path, '-d', extract_dir]
            else:
                cmd = ['unzip', '-o', file_path, '-d', extract_dir]
        # tar 系列
        elif file_name.endswith('.tar'):
            cmd = ['tar', '-xf', file_path, '-C', extract_dir]
        elif file_name.endswith(('.tar.gz', '.tgz')):
            cmd = ['tar', '-xzf', file_path, '-C', extract_dir]
        elif file_name.endswith(('.tar.bz2', '.tbz2')):
            cmd = ['tar', '-xjf', file_path, '-C', extract_dir]
        else:
            return False, f"不支持的格式: {file_ext}"
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            return True, "解压成功"
        else:
            error_msg = result.stderr or result.stdout or "未知错误"
            return False, f"解压失败: {error_msg[:200]}"
            
    except subprocess.TimeoutExpired:
        return False, "解压超时"
    except Exception as e:
        return False, f"解压异常: {str(e)}"

def extract_with_password_dict(file_path: str, extract_dir: str) -> Tuple[bool, str, Optional[str]]:
    """
    使用密码词典尝试解压
    返回: (成功, 消息, 使用的密码)
    """
    # 检查缓存
    if file_path in PASSWORD_SUCCESS_CACHE:
        cached_pwd = PASSWORD_SUCCESS_CACHE[file_path]
        success, msg = extract_archive(file_path, extract_dir, cached_pwd)
        if success:
            return True, "解压成功 (缓存密码)", cached_pwd
    
    # 尝试词典中的密码
    for password in PASSWORD_DICT:
        success, msg = extract_archive(file_path, extract_dir, password)
        if success:
            # 保存到缓存
            PASSWORD_SUCCESS_CACHE[file_path] = password
            save_password_cache()
            return True, "解压成功", password
    
    return False, "所有密码都失败了", None

def find_all_archives(root_dir: str) -> List[str]:
    """递归查找所有压缩包"""
    archives = []
    root_path = Path(root_dir)
    
    if not root_path.exists():
        logger.error(f"目录不存在: {root_dir}")
        return archives
    
    if not root_path.is_dir():
        logger.error(f"路径不是目录: {root_dir}")
        return archives
    
    try:
        # 递归遍历所有文件
        supported_exts = ('.7z', '.rar', '.zip', '.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2')
        for archive_file in root_path.rglob('*'):
            try:
                # 检查是否是文件且是支持的格式（使用文件名后缀匹配以适配复合后缀）
                if archive_file.is_file() and archive_file.name.lower().endswith(supported_exts):
                    archives.append(str(archive_file))
            except (OSError, PermissionError) as e:
                # 跳过无法访问的文件，但记录日志
                logger.debug(f"无法访问文件 {archive_file}: {e}")
                continue
    except (OSError, PermissionError) as e:
        logger.error(f"扫描目录时出错 {root_dir}: {e}")
        # 尝试使用 os.walk 作为备选方案
        try:
            for dirpath, dirnames, filenames in os.walk(root_dir):
                # 过滤掉无法访问的目录
                dirnames[:] = [d for d in dirnames if os.path.exists(os.path.join(dirpath, d))]
                
                for filename in filenames:
                    if filename.lower().endswith(('.7z', '.rar', '.zip', '.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2')):
                        file_path = os.path.join(dirpath, filename)
                        try:
                            if os.path.isfile(file_path):
                                archives.append(file_path)
                        except (OSError, PermissionError):
                            continue
        except (OSError, PermissionError) as e2:
            logger.error(f"os.walk 备选方案也失败 {root_dir}: {e2}")
    
    return sorted(archives)

def scan_subdirectories(root_dir: str) -> Dict[str, int]:
    """扫描子目录中是否存在压缩包"""
    root_path = Path(root_dir)
    subdir_stats = {}
    
    if not root_path.exists():
        logger.warning(f"目录不存在: {root_dir}")
        return subdir_stats
    
    if not root_path.is_dir():
        logger.warning(f"路径不是目录: {root_dir}")
        return subdir_stats
    
    try:
        for item in root_path.iterdir():
            try:
                if item.is_dir():
                    # 计算该目录中的压缩包数量
                    archive_count = 0
                    try:
                        archive_count += len(list(item.rglob('*.7z')))
                        archive_count += len(list(item.rglob('*.rar')))
                        archive_count += len(list(item.rglob('*.zip')))
                        archive_count += len(list(item.rglob('*.tar')))
                        archive_count += len(list(item.rglob('*.tar.gz')))
                        archive_count += len(list(item.rglob('*.tgz')))
                        archive_count += len(list(item.rglob('*.tar.bz2')))
                        archive_count += len(list(item.rglob('*.tbz2')))
                    except (OSError, PermissionError):
                        # 如果 rglob 失败，尝试 os.walk
                        for dirpath, dirnames, filenames in os.walk(str(item)):
                            for filename in filenames:
                                    if filename.lower().endswith(('.7z', '.rar', '.zip', '.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2')):
                                        archive_count += 1
                    
                    if archive_count > 0:
                        subdir_stats[item.name] = archive_count
            except (OSError, PermissionError) as e:
                logger.debug(f"无法访问子目录 {item}: {e}")
                continue
    except (OSError, PermissionError) as e:
        logger.warning(f"扫描子目录失败: {e}")
    
    return subdir_stats

def process_extraction_task(task_id: str, archive_file: str, extract_dir: str):
    """处理单个解压任务"""
    try:
        with extraction_lock:
            extraction_status[task_id] = {
                'status': 'processing',
                'file': archive_file,
                'progress': 0,
                'message': '检测加密状态...'
            }
        
        # 检测是否加密
        is_archive, is_encrypted = is_archive_encrypted(archive_file)
        
        if not is_archive:
            with extraction_lock:
                extraction_status[task_id] = {
                    'status': 'failed',
                    'file': archive_file,
                    'message': '不是有效的压缩包'
                }
            return
        
        # 尝试解压
        if is_encrypted:
            with extraction_lock:
                extraction_status[task_id]['message'] = '需要密码，正在尝试...'
            
            success, msg, used_pwd = extract_with_password_dict(archive_file, extract_dir)
            if success:
                with extraction_lock:
                    extraction_status[task_id] = {
                        'status': 'success',
                        'file': archive_file,
                        'message': f"成功 (密码: {used_pwd})",
                        'password': used_pwd
                    }
            else:
                with extraction_lock:
                    extraction_status[task_id] = {
                        'status': 'failed',
                        'file': archive_file,
                        'message': msg
                    }
        else:
            with extraction_lock:
                extraction_status[task_id]['message'] = '无加密，正在解压...'
            
            success, msg = extract_archive(archive_file, extract_dir)
            if success:
                with extraction_lock:
                    extraction_status[task_id] = {
                        'status': 'success',
                        'file': archive_file,
                        'message': '成功'
                    }
            else:
                with extraction_lock:
                    extraction_status[task_id] = {
                        'status': 'failed',
                        'file': archive_file,
                        'message': msg
                    }
    
    except Exception as e:
        with extraction_lock:
            extraction_status[task_id] = {
                'status': 'error',
                'file': archive_file,
                'message': f"错误: {str(e)}"
            }

# Web 路由
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/scan', methods=['POST'])
def scan_directory():
    """扫描目录"""
    data = request.get_json()
    root_dir = data.get('path', '/vol1/1000/Temp')
    
    logger.info(f"开始扫描目录: {root_dir}")
    
    # 验证目录存在
    root_path = Path(root_dir)
    if not root_path.exists():
        logger.error(f"目录不存在: {root_dir}")
        return jsonify({'error': f'目录不存在: {root_dir}'}), 400
    
    if not root_path.is_dir():
        logger.error(f"路径不是目录: {root_dir}")
        return jsonify({'error': f'路径不是目录: {root_dir}'}), 400
    
    # 检查目录的读取权限
    if not os.access(root_dir, os.R_OK):
        logger.error(f"没有目录读取权限: {root_dir}")
        return jsonify({'error': f'没有目录读取权限: {root_dir}'}), 403
    
    try:
        archives = find_all_archives(root_dir)
        logger.info(f"扫描完成，发现 {len(archives)} 个压缩包")
    except Exception as e:
        logger.error(f"扫描压缩包时出错: {e}")
        return jsonify({'error': f'扫描文件时出错: {str(e)}'}), 500
    
    try:
        subdir_stats = scan_subdirectories(root_dir)
    except Exception as e:
        logger.error(f"扫描子目录时出错: {e}")
        subdir_stats = {}
    
    # 分析每个压缩包
    result = []
    for archive in archives:
        try:
            is_arch, is_enc = is_archive_encrypted(archive)
            result.append({
                'path': archive,
                'name': Path(archive).name,
                'size': Path(archive).stat().st_size,
                'encrypted': is_enc if is_arch else None,
                'status': 'ready',
                'cached': archive in PASSWORD_SUCCESS_CACHE
            })
        except (OSError, PermissionError) as e:
            logger.warning(f"无法访问压缩包 {archive}: {e}")
            continue
        except Exception as e:
            logger.warning(f"处理压缩包 {archive} 时出错: {e}")
            continue
    
    return jsonify({
        'total': len(result),
        'archives': result,
        'subdirs_with_archives': subdir_stats,
        'subdirs_count': len(subdir_stats)
    })

@app.route('/api/extract', methods=['POST'])
def extract():
    """开始批量解压"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': '无效的请求数据'}), 400

        archives = data.get('archives', [])
        extract_base = data.get('extract_to', '/vol1/1000/Temp')

        if not archives:
            return jsonify({'error': '没有选择任何文件'}), 400

        # 确保解压基目录存在
        try:
            os.makedirs(extract_base, exist_ok=True)
        except Exception as e:
            logger.error(f"无法创建或访问解压目录 {extract_base}: {e}")
            return jsonify({'error': f'无法访问或创建解压目录: {extract_base}'}), 500

        # 创建临时目录
        extract_dir = tempfile.mkdtemp(dir=extract_base)

        tasks = {}
        for i, archive in enumerate(archives):
            task_id = f"task_{i}"
            tasks[task_id] = archive

            # 启动后台线程
            thread = threading.Thread(
                target=process_extraction_task,
                args=(task_id, archive, extract_dir)
            )
            thread.daemon = True
            thread.start()

        return jsonify({
            'extract_dir': extract_dir,
            'task_count': len(tasks),
            'tasks': tasks
        })
    except Exception as e:
        logger.exception(f"启动解压失败: {e}")
        return jsonify({'error': f'启动解压失败: {str(e)}'}), 500

@app.route('/api/status', methods=['GET'])
def get_status():
    """获取解压状态"""
    with extraction_lock:
        return jsonify(extraction_status)

@app.route('/api/config', methods=['GET'])
def get_config():
    """获取配置信息"""
    return jsonify({
        'password_dict_size': len(PASSWORD_DICT),
        'password_cache_size': len(PASSWORD_SUCCESS_CACHE),
        'default_mount': '/vol1/1000/Temp',
        'supported_formats': ['.7z', '.rar', '.zip', '.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2']
    })

@app.route('/api/subdirs', methods=['POST'])
def scan_subdirs():
    """扫描子目录中的压缩包"""
    data = request.get_json()
    root_dir = data.get('path', '/vol1/1000/Temp')
    
    if not Path(root_dir).exists():
        return jsonify({'error': f'目录不存在: {root_dir}'}), 400
    
    subdir_stats = scan_subdirectories(root_dir)
    
    return jsonify({
        'path': root_dir,
        'subdirs': subdir_stats,
        'total_subdirs_with_archives': len(subdir_stats)
    })

@app.route('/api/passwords', methods=['GET'])
def get_passwords():
    """获取当前密码词典"""
    return jsonify({
        'passwords': PASSWORD_DICT,
        'count': len(PASSWORD_DICT)
    })

@app.route('/api/passwords', methods=['POST'])
def update_passwords():
    """更新密码词典"""
    data = request.get_json()
    new_passwords = data.get('passwords', [])
    
    if not isinstance(new_passwords, list):
        return jsonify({'error': '密码必须是列表'}), 400
    
    try:
        global PASSWORD_DICT
        PASSWORD_DICT = [str(p).strip() for p in new_passwords if str(p).strip()]
        
        # 保存到文件
        dict_path = Path('/app/passwords.txt')
        with open(dict_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(PASSWORD_DICT))
        
        logger.info(f"已更新 {len(PASSWORD_DICT)} 个密码")
        return jsonify({
            'success': True,
            'message': f'已更新 {len(PASSWORD_DICT)} 个密码',
            'count': len(PASSWORD_DICT)
        })
    except Exception as e:
        logger.error(f"更新密码失败: {e}")
        return jsonify({'error': f'更新失败: {str(e)}'}), 500

@app.route('/api/password-cache', methods=['GET'])
def get_password_cache():
    """获取密码缓存"""
    return jsonify({
        'cache': PASSWORD_SUCCESS_CACHE,
        'count': len(PASSWORD_SUCCESS_CACHE)
    })

@app.route('/api/password-cache', methods=['DELETE'])
def clear_password_cache():
    """清空密码缓存"""
    global PASSWORD_SUCCESS_CACHE
    PASSWORD_SUCCESS_CACHE = {}
    try:
        PASSWORD_CACHE_FILE.unlink()
    except:
        pass
    return jsonify({'success': True, 'message': '已清空密码缓存'})

if __name__ == '__main__':
    load_password_dict()
    load_password_cache()
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
