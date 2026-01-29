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
extraction_control = {
    'pause': False,      # 暂停标志
    'stop': False,       # 停止标志
    'paused_tasks': {}   # 暂停的任务 ID
}
extraction_options = {
    'extract_to_same_name': False,  # 解压到同名文件夹
    'auto_delete_success': False     # 自动删除成功的压缩包
}
control_lock = threading.Lock()

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

        # 支持 7z/zip/rar 以及 tar 系列
        if file_name.endswith('.7z'):
            # 使用 7z 的 -y 参数自动确认，避免交互式提示
            result = subprocess.run(
                ['7z', 'l', '-y', file_path],
                capture_output=True,
                text=True,
                timeout=10
            )
            # 检查命令返回码和输出内容
            output = (result.stdout + result.stderr).lower()
            
            # 如果返回非零且输出中含有密码/加密提示，则为加密
            if result.returncode != 0:
                if 'password' in output or 'encrypted' in output or 'wrong password' in output:
                    logger.debug(f"7z 文件检测为加密 (返回码 {result.returncode}): {file_path}")
                    return True, True
                # 返回非零但没有明确提示，可能是其他错误
                logger.warning(f"7z 列出文件失败，返回码 {result.returncode}: {file_path}")
            
            # 检查输出中的加密标志
            if 'password' in output or 'encrypted' in output or 'lock' in output:
                logger.debug(f"7z 文件检测为加密（输出标志）: {file_path}")
                return True, True
            
            logger.debug(f"7z 文件检测为无加密: {file_path}")
            return True, False
            
        elif file_name.endswith('.zip'):
            result = subprocess.run(
                ['unzip', '-t', file_path],
                capture_output=True,
                text=True,
                timeout=10
            )
            output = (result.stdout + result.stderr).lower()
            
            if result.returncode != 0:
                if 'password' in output or 'encrypted' in output or '[2] cannot find zipfile or read error' in output:
                    logger.debug(f"ZIP 文件检测为加密: {file_path}")
                    return True, True
                logger.debug(f"ZIP 文件可能损坏或无加密: {file_path}")
                return True, False
            
            if 'password' in output or 'encrypted' in output:
                logger.debug(f"ZIP 文件检测为加密: {file_path}")
                return True, True
            
            logger.debug(f"ZIP 文件检测为无加密: {file_path}")
            return True, False
        
        elif file_name.endswith('.rar'):
            # 对于 RAR，尝试用 7z 处理
            result = subprocess.run(
                ['7z', 'l', '-y', file_path],
                capture_output=True,
                text=True,
                timeout=10
            )
            output = (result.stdout + result.stderr).lower()
            
            if result.returncode != 0:
                if 'password' in output or 'encrypted' in output:
                    logger.debug(f"RAR 文件检测为加密: {file_path}")
                    return True, True
            
            if 'password' in output or 'encrypted' in output or 'lock' in output:
                logger.debug(f"RAR 文件检测为加密: {file_path}")
                return True, True
            
            logger.debug(f"RAR 文件检测为无加密: {file_path}")
            return True, False
            
        elif file_name.endswith(('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2')):
            # tar 系列通常不支持加密检测，视为普通压缩包
            logger.debug(f"TAR 文件视为无加密: {file_path}")
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
                # 7z 的密码参数必须紧跟 -p，中间无空格
                cmd.append(f'-p{password}')
        # zip
        elif file_name.endswith('.zip'):
            if password:
                cmd = ['unzip', '-P', password, '-o', file_path, '-d', extract_dir]
            else:
                cmd = ['unzip', '-o', file_path, '-d', extract_dir]
        # tar 系列（tar 不支持加密）
        elif file_name.endswith('.tar'):
            cmd = ['tar', '-xf', file_path, '-C', extract_dir]
        elif file_name.endswith(('.tar.gz', '.tgz')):
            cmd = ['tar', '-xzf', file_path, '-C', extract_dir]
        elif file_name.endswith(('.tar.bz2', '.tbz2')):
            cmd = ['tar', '-xjf', file_path, '-C', extract_dir]
        else:
            return False, f"不支持的格式"
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            logger.info(f"成功解压: {file_path}")
            return True, "解压成功"
        else:
            error_output = (result.stderr + result.stdout).lower()
            
            # 检查是否是密码相关错误
            if password and ('password' in error_output or 'wrong password' in error_output or 
                           'encrypted' in error_output or '密码' in error_output):
                logger.warning(f"密码错误 [{file_path}] 密码: {password}")
                return False, f"密码错误 (已尝试: {password})"
            
            error_msg = result.stderr or result.stdout or "未知错误"
            logger.error(f"解压命令失败 [{file_path}]: 返回码 {result.returncode}\n命令: {' '.join(cmd)}\n错误: {error_msg}")
            return False, f"解压失败: {error_msg[:200]}"
            
    except subprocess.TimeoutExpired:
        logger.error(f"解压超时: {file_path}")
        return False, "解压超时（300秒）"
    except Exception as e:
        logger.error(f"解压异常 {file_path}: {e}")
        return False, f"解压异常: {str(e)[:100]}"

def extract_with_password_dict(file_path: str, extract_dir: str, max_retries: int = 5, timeout_sec: int = 60) -> Tuple[bool, str, Optional[str]]:
    """
    使用密码词典尝试解压
    支持重试次数和超时控制
    返回: (成功, 消息, 使用的密码)
    """
    import time
    start_time = time.time()
    retry_count = 0
    
    # 检查缓存
    if file_path in PASSWORD_SUCCESS_CACHE:
        cached_pwd = PASSWORD_SUCCESS_CACHE[file_path]
        success, msg = extract_archive(file_path, extract_dir, cached_pwd)
        if success:
            return True, "解压成功 (缓存密码)", cached_pwd
        retry_count += 1
        logger.warning(f"缓存密码失败 {file_path}: {msg}，将尝试词典密码")
    
    # 尝试词典中的密码，支持重试和超时
    for attempt in range(min(len(PASSWORD_DICT), max_retries)):
        # 检查超时
        elapsed = time.time() - start_time
        if elapsed > timeout_sec:
            logger.error(f"解压超时 ({timeout_sec}s): {file_path}")
            return False, f"解压超时 (已尝试 {attempt+1} 个密码，耗时 {int(elapsed)}s)", None
        
        password = PASSWORD_DICT[attempt]
        try:
            success, msg = extract_archive(file_path, extract_dir, password)
            if success:
                # 保存到缓存
                PASSWORD_SUCCESS_CACHE[file_path] = password
                save_password_cache()
                logger.info(f"成功解压 {file_path} (尝试次数: {attempt+1})")
                return True, "解压成功", password
        except Exception as e:
            logger.warning(f"解压异常 {file_path} (尝试 {attempt+1}): {e}")
            continue
        
        retry_count += 1
    
    elapsed = time.time() - start_time
    logger.error(f"所有密码都失败了 {file_path} (尝试次数: {retry_count}, 耗时: {int(elapsed)}s)")
    return False, f"所有密码都失败了 (尝试 {retry_count} 个密码，耗时 {int(elapsed)}s)", None

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
    """处理单个解压任务，支持暂停/继续/停止"""
    try:
        with extraction_lock:
            extraction_status[task_id] = {
                'status': 'processing',
                'file': archive_file,
                'progress': 0,
                'message': '检测加密状态...'
            }
        
        # 检查停止标志
        with control_lock:
            if extraction_control['stop']:
                with extraction_lock:
                    extraction_status[task_id] = {
                        'status': 'stopped',
                        'file': archive_file,
                        'message': '已停止'
                    }
                return
        
        # 确定实际的解压目录
        actual_extract_dir = extract_dir
        if extraction_options.get('extract_to_same_name', False):
            # 从文件名获取同名文件夹
            archive_name = os.path.basename(archive_file)
            # 移除扩展名获取文件夹名称
            base_name = archive_name
            for ext in ['.7z', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar', '.zip', '.rar']:
                if base_name.lower().endswith(ext):
                    base_name = base_name[:-len(ext)]
                    break
            actual_extract_dir = os.path.join(extract_dir, base_name)
            os.makedirs(actual_extract_dir, exist_ok=True)
        
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
        
        # 处理加密压缩包
        if is_encrypted:
            with extraction_lock:
                extraction_status[task_id]['message'] = '需要密码，正在尝试... (限制: 5次重试/60秒超时)'
            
            # 有重试次数限制和超时控制
            success, msg, used_pwd = extract_with_password_dict(archive_file, actual_extract_dir, max_retries=5, timeout_sec=60)
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
                logger.error(f"密码解压失败 {archive_file}: {msg}")
        else:
            # 处理非加密压缩包
            with extraction_lock:
                extraction_status[task_id]['message'] = '无加密，正在解压...'
            
            success, msg = extract_archive(archive_file, actual_extract_dir)
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
                logger.error(f"解压失败 {archive_file}: {msg}")
    
    except Exception as e:
        with extraction_lock:
            extraction_status[task_id] = {
                'status': 'error',
                'file': archive_file,
                'message': f"错误: {str(e)}"
            }
        logger.exception(f"处理解压任务异常 {archive_file}: {e}")

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
        extract_to_same_name = data.get('extract_to_same_name', False)
        auto_delete_success = data.get('auto_delete_success', False)

        if not archives:
            return jsonify({'error': '没有选择任何文件'}), 400

        # 保存提取选项
        global extraction_options
        extraction_options['extract_to_same_name'] = extract_to_same_name
        extraction_options['auto_delete_success'] = auto_delete_success

        # 确保解压基目录存在
        try:
            os.makedirs(extract_base, exist_ok=True)
        except Exception as e:
            logger.error(f"无法创建或访问解压目录 {extract_base}: {e}")
            return jsonify({'error': f'无法访问或创建解压目录: {extract_base}'}), 500

        # 如果启用了"解压到同名文件夹"，则不创建临时目录，而是逐个创建同名文件夹
        if extract_to_same_name:
            extract_dir = extract_base
        else:
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

        logger.info(f"启动解压: {len(archives)} 个文件，选项: 同名文件夹={extract_to_same_name}, 自动删除={auto_delete_success}")

        return jsonify({
            'extract_dir': extract_dir,
            'task_count': len(tasks),
            'tasks': tasks,
            'options': {
                'extract_to_same_name': extract_to_same_name,
                'auto_delete_success': auto_delete_success
            }
        })
    except Exception as e:
        logger.exception(f"启动解压失败: {e}")
        return jsonify({'error': f'启动解压失败: {str(e)}'}), 500

@app.route('/api/status', methods=['GET'])
def get_status():
    """获取解压状态"""
    with extraction_lock:
        return jsonify(extraction_status)

@app.route('/api/extraction/pause', methods=['POST'])
def pause_extraction():
    """暂停解压"""
    with control_lock:
        extraction_control['pause'] = True
    logger.info("用户暂停解压")
    return jsonify({'success': True, 'message': '已暂停解压'})

@app.route('/api/extraction/resume', methods=['POST'])
def resume_extraction():
    """继续解压"""
    with control_lock:
        extraction_control['pause'] = False
    logger.info("用户继续解压")
    return jsonify({'success': True, 'message': '已继续解压'})

@app.route('/api/extraction/stop', methods=['POST'])
def stop_extraction():
    """停止解压"""
    with control_lock:
        extraction_control['stop'] = True
    logger.info("用户停止解压")
    return jsonify({'success': True, 'message': '已停止解压'})

@app.route('/api/extraction/reset', methods=['POST'])
def reset_extraction():
    """重置解压控制状态"""
    with control_lock:
        extraction_control['pause'] = False
        extraction_control['stop'] = False
        extraction_control['paused_tasks'] = {}
    with extraction_lock:
        extraction_status.clear()
    logger.info("已重置解压状态")
    return jsonify({'success': True, 'message': '已重置解压状态'})

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

@app.route('/api/logs', methods=['GET'])
def get_logs():
    """获取最近的日志信息"""
    # 返回日志文件内容或内存日志
    log_file = Path('/app/fnos.log')
    if log_file.exists():
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                # 返回最后 200 行
                return jsonify({'logs': ''.join(lines[-200:])})
        except Exception as e:
            logger.error(f"读取日志失败: {e}")
    return jsonify({'logs': '暂无日志'})

@app.route('/api/logs/download', methods=['GET'])
def download_logs():
    """下载日志文件"""
    from flask import send_file
    log_file = Path('/app/fnos.log')
    if log_file.exists():
        try:
            return send_file(log_file, as_attachment=True, download_name=f'fnos_logs_{int(time.time())}.log')
        except Exception as e:
            logger.error(f"下载日志失败: {e}")
            return jsonify({'error': '日志下载失败'}), 500

@app.route('/api/delete-archives', methods=['POST'])
def delete_archives():
    """删除指定的压缩包文件"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': '无效的请求数据'}), 400
        
        files = data.get('files', [])
        if not files:
            return jsonify({'error': '没有指定要删除的文件'}), 400
        
        deleted_files = []
        failed_files = []
        
        for file_path in files:
            try:
                # 安全检查：确保文件确实存在且是我们期望的类型
                if not os.path.exists(file_path):
                    failed_files.append({'file': file_path, 'error': '文件不存在'})
                    continue
                
                # 检查是否是压缩包
                if not any(file_path.lower().endswith(ext) for ext in ['.7z', '.zip', '.rar', '.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2']):
                    failed_files.append({'file': file_path, 'error': '不是有效的压缩包文件'})
                    continue
                
                # 删除文件
                os.remove(file_path)
                deleted_files.append(file_path)
                logger.info(f"已删除成功解压的压缩包: {file_path}")
            except Exception as e:
                failed_files.append({'file': file_path, 'error': str(e)})
                logger.error(f"删除文件失败 {file_path}: {e}")
        
        return jsonify({
            'success': True,
            'deleted': deleted_files,
            'failed': failed_files,
            'deleted_count': len(deleted_files),
            'failed_count': len(failed_files)
        })
    except Exception as e:
        logger.exception(f"删除压缩包API失败: {e}")
        return jsonify({'error': f'删除失败: {str(e)}'}), 500
    return jsonify({'error': '日志文件不存在'}), 404

if __name__ == '__main__':
    # 配置文件日志处理
    file_handler = logging.FileHandler('/app/fnos.log')
    file_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    load_password_dict()
    load_password_cache()
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
