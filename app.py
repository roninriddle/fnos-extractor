#!/usr/bin/env python3
"""
FNOS 批量解压工具
支持递归扫描、密码检测和Web界面
版本: 1.2.9
"""

from flask import Flask, render_template, jsonify, request, send_file
from functools import wraps
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
import psutil
import platform

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 常量定义
SUPPORTED_ARCHIVE_EXTENSIONS = (
    # 标准压缩格式
    '.7z', '.rar', '.zip',
    # TAR系列
    '.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz', '.tar.zst',
    # 单独压缩格式
    '.gz', '.bz2', '.xz', '.lzma', '.zst',
    # 其他格式
    '.cab', '.iso',
)
MULTIPART_EXTENSIONS = (
    '.001', '.002', '.003', '.004', '.005', '.006', '.007', '.008', '.009', '.010',
    '.Z01', '.z01', '.Z02', '.z02', '.Z03', '.z03', '.Z04', '.z04', '.Z05', '.z05',
    '.r00', '.r01', '.r02', '.r03', '.r04', '.r05', '.r06', '.r07', '.r08', '.r09',
)
DEFAULT_MOUNT_PATH = '/vol1/1000/Temp'
LOG_FILE_PATH = Path('/app/fnos.log')
MAX_CONCURRENT_EXTRACTIONS = 32
PASSWORD_TIMEOUT = 5  # 每个密码尝试的超时时间（秒）
EXTRACTION_TIMEOUT = 300  # 单个文件解压的超时时间（秒）

def _has_command(cmd_name: str) -> bool:
    return shutil.which(cmd_name) is not None

# ========================================
# 装饰器和辅助函数
# ========================================

def validate_request(*required_keys, type_check=None):
    """
    请求验证装饰器
    @validate_request('path', 'include_subdirs', type_check={'include_subdirs': bool})
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # POST/PUT 请求验证 JSON
            if request.method in ['POST', 'PUT']:
                if not request.is_json:
                    return jsonify({'error': 'Content-Type must be application/json'}), 400
                data = request.get_json() or {}
                
                # 验证必需字段
                for key in required_keys:
                    if key not in data:
                        return jsonify({'error': f'Missing required field: {key}'}), 400
                
                # 类型检查
                if type_check:
                    for key, expected_type in type_check.items():
                        if key in data and not isinstance(data[key], expected_type):
                            return jsonify({
                                'error': f'Field {key} must be {expected_type.__name__}'
                            }), 400
            
            return func(*args, **kwargs)
        return wrapper
    return decorator

def standard_response(data=None, error=None, status_code=200):
    """
    标准化响应格式 (P2.1)
    所有 API 响应格式: {'success': bool, 'data': ..., 'error': ...}
    """
    response = {
        'success': error is None,
        'timestamp': time.time()
    }
    if data is not None:
        response['data'] = data
    if error is not None:
        response['error'] = error
    return jsonify(response), status_code

def log_request(log_level='info'):
    """
    请求日志装饰器 (P2.4)
    自动记录 API 请求和响应
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            method = request.method
            path = request.path
            
            # 记录请求
            log_msg = f"{method} {path}"
            if request.method in ['POST', 'PUT'] and request.is_json:
                log_msg += f" (payload: {len(request.get_json() or {})} fields)"
            
            getattr(logger, log_level)(f"[REQ] {log_msg}")
            
            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start_time
                status_code = result[1] if isinstance(result, tuple) else 200
                getattr(logger, log_level)(f"[RES] {method} {path} {status_code} ({elapsed:.3f}s)")
                return result
            except Exception as e:
                elapsed = time.time() - start_time
                logger.error(f"[ERR] {method} {path} raised {type(e).__name__}: {e} ({elapsed:.3f}s)")
                raise
        
        return wrapper
    return decorator

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
extraction_settings = {
    'concurrent_count': 1  # 并发解压文件数量（默认1个）
}
encryption_cache = {}  # {file_path: (mtime, size, is_encrypted)}
encryption_cache_lock = threading.Lock()  # 保护 encryption_cache 的线程锁
detecting_files = set()  # 正在检测中的文件集合，避免重复检测
control_lock = threading.Lock()

# ========================================
# 错误处理 (P2.3)
# ========================================

class APIError(Exception):
    """API 基础异常类"""
    def __init__(self, message, status_code=400, error_code=None):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code or 'API_ERROR'

@app.errorhandler(APIError)
def handle_api_error(error):
    """处理 API 异常"""
    logger.warning(f"API 错误 [{error.error_code}]: {error.message}")
    return standard_response(error=error.message, status_code=error.status_code)

@app.errorhandler(400)
def handle_bad_request(error):
    """处理错误的请求"""
    return standard_response(error='请求格式错误', status_code=400)

@app.errorhandler(404)
def handle_not_found(error):
    """处理未找到的资源"""
    return standard_response(error='资源不存在', status_code=404)

@app.errorhandler(500)
def handle_internal_error(error):
    """处理内部服务器错误"""
    logger.error(f"内部错误: {error}")
    return standard_response(error='服务器内部错误', status_code=500)

# ========================================
# 密码词典和缓存
# ========================================
PASSWORD_DICT = []
# 优先使用当前目录的 passwords.txt，否则使用 /app 目录
PASSWORD_DICT_FILE = Path('passwords.txt') if Path('passwords.txt').exists() else Path('/app/passwords.txt')
PASSWORD_CACHE_FILE = Path('password_cache.json') if Path('password_cache.json').exists() else Path('/app/password_cache.json')
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
    # 优先使用当前目录，再用 /app 目录
    dict_path = Path('passwords.txt') if Path('passwords.txt').exists() else Path('/app/passwords.txt')
    if dict_path.exists():
        with open(dict_path, 'r', encoding='utf-8', errors='ignore') as f:
            PASSWORD_DICT = [line.strip() for line in f if line.strip()]
        logger.info(f"已加载 {len(PASSWORD_DICT)} 个密码")
    else:
        logger.warning("密码词典不存在")

def is_multipart_archive(file_path: str) -> bool:
    """
    判断是否是多卷压缩文件的第一卷
    支持格式:
    - .part1.7z, .part2.7z, ... (7z多卷)
    - .part1.rar, .part2.rar, ... (RAR分卷)
    - .001, .002, ... (通用多卷，如7z/RAR/ZIP/WinRAR)
    - .Z01, .Z02, ... (WinRAR标准多卷)
    - .RAR, .r00, .r01, ... (RAR经典多卷)
    - .zip.001, .zip.002, ... (zip多卷)
    """
    name = Path(file_path).name.lower()
    
    # .part1.7z / .part1.rar 格式
    if '.part1.' in name:
        return True
    
    # .001 / .Z01 / .r00 等第一卷格式
    if name.endswith(('.001', '.Z01', '.z01')):
        return True
    
    # 首个RAR卷 (.RAR 作为第一卷)
    if name.endswith('.rar') and '.part' not in name:
        # 这可能是一个RAR卷，需要检查是否有.r00/.001等后续卷
        parent = Path(file_path).parent
        base_name = name[:-4]  # 去掉.rar
        # 检查是否存在其他卷
        if (parent / f"{base_name}.r00").exists() or \
           (parent / f"{base_name}.r01").exists() or \
           (parent / f"{base_name}.Z01").exists() or \
           (parent / f"{base_name}.001").exists():
            return True
    
    return False

def get_multipart_first_volume(file_path: str) -> Optional[str]:
    """
    如果是多卷压缩文件，获取第一卷的路径
    否则返回 None
    """
    name = Path(file_path).name.lower()
    parent = Path(file_path).parent
    
    # .part1.* 格式 - 已经是第一卷
    if '.part1.' in name:
        return file_path
    
    # .001 / .Z01 / .z01 格式 - 已经是第一卷
    if name.endswith(('.001', '.Z01', '.z01')):
        return file_path
    
    # .partN+ 格式 - 需要找 .part1
    if '.part' in name:
        base_name = name[:name.find('.part')]
        # 尝试找 .part1 版本
        for ext in ['.7z', '.rar']:
            potential_first = parent / f"{base_name}.part1{ext}"
            if potential_first.exists():
                return str(potential_first)
    
    # .002, .003 等 - 需要找 .001
    if name[-3:].isdigit() and name.endswith(tuple(f'.{i:03d}' for i in range(2, 1000))):
        base_name = name[:-4]  # 去掉 .00X
        potential_first = parent / f"{base_name}.001"
        if potential_first.exists():
            return str(potential_first)
    
    # .r01, .r02 等 - 需要找 .rar 或 .r00
    if name.endswith(tuple(f'.r{i:02d}' for i in range(1, 100))):
        base_name = name[:-4]  # 去掉 .rXX
        rar_first = parent / f"{base_name}.rar"
        r00_first = parent / f"{base_name}.r00"
        if rar_first.exists():
            return str(rar_first)
        elif r00_first.exists():
            return str(r00_first)
    
    # .Z02, .Z03 等 - 需要找 .Z01
    if name[-3:].upper() in [f'Z{i:02d}' for i in range(2, 100)]:
        base_name = name[:-4]  # 去掉 .ZXX
        potential_first = parent / f"{base_name}.Z01"
        if potential_first.exists():
            return str(potential_first)
    
    # 首个RAR卷 - 已经是 .rar
    if name.endswith('.rar'):
        return file_path
    
    return None

def group_multipart_archives(archives: List[str]) -> Tuple[List[Dict], List[str]]:
    """
    将多卷压缩文件分组，返回分组后的结果
    返回: (分组后的多卷列表, 非多卷的单文件列表)
    
    多卷组格式:
    {
        'is_multipart': True,
        'first_volume': '/path/to/file.part1.7z',
        'volumes': ['/path/to/file.part1.7z', '/path/to/file.part2.7z', ...],
        'name': 'file',
        'count': 2,
        'total_size': 1024000,
        'format': 'part1'  # 多卷格式类型
    }
    """
    multipart_groups = {}  # base_name -> group info
    single_files = []
    
    for archive in archives:
        file_name = Path(archive).name.lower()
        is_grouped = False
        
        # 1. .part1.* 格式 (.part1.7z, .part1.rar)
        if '.part1.' in file_name:
            base_name = file_name[:file_name.find('.part1')]
            if base_name not in multipart_groups:
                multipart_groups[base_name] = {
                    'is_multipart': True,
                    'first_volume': archive,
                    'volumes': [],
                    'name': base_name,
                    'count': 0,
                    'total_size': 0,
                    'format': 'part1'
                }
            multipart_groups[base_name]['volumes'].append(archive)
            is_grouped = True
        
        # 2. .001 格式 (.001, .002, ...)
        elif file_name.endswith('.001'):
            base_name = file_name[:-4]  # 去掉 .001
            if base_name not in multipart_groups:
                multipart_groups[base_name] = {
                    'is_multipart': True,
                    'first_volume': archive,
                    'volumes': [],
                    'name': base_name,
                    'count': 0,
                    'total_size': 0,
                    'format': '001'
                }
            multipart_groups[base_name]['volumes'].append(archive)
            is_grouped = True
        
        # 3. .Z01 格式 (.Z01, .Z02, ...) - WinRAR标准
        elif file_name.endswith(('.Z01', '.z01')):
            base_name = file_name[:-4]  # 去掉 .Z01
            if base_name not in multipart_groups:
                multipart_groups[base_name] = {
                    'is_multipart': True,
                    'first_volume': archive,
                    'volumes': [],
                    'name': base_name,
                    'count': 0,
                    'total_size': 0,
                    'format': 'Z01'
                }
            multipart_groups[base_name]['volumes'].append(archive)
            is_grouped = True
        
        # 4. .r00/.r01 格式 (.r00, .r01, ...) - RAR经典多卷
        elif file_name.endswith(tuple(f'.r{i:02d}' for i in range(100))):
            base_name = file_name[:-4]  # 去掉 .rXX
            if base_name not in multipart_groups:
                multipart_groups[base_name] = {
                    'is_multipart': True,
                    'first_volume': archive,
                    'volumes': [],
                    'name': base_name,
                    'count': 0,
                    'total_size': 0,
                    'format': 'r00'
                }
            multipart_groups[base_name]['volumes'].append(archive)
            is_grouped = True
        
        # 5. 首个 .rar 文件（如果有后续卷）
        elif file_name.endswith('.rar') and '.part' not in file_name:
            parent = Path(archive).parent
            base_name = file_name[:-4]
            # 检查是否有后续卷
            has_volumes = (parent / f"{base_name}.r00").exists() or \
                         (parent / f"{base_name}.r01").exists() or \
                         (parent / f"{base_name}.Z01").exists() or \
                         (parent / f"{base_name}.001").exists()
            if has_volumes:
                if base_name not in multipart_groups:
                    multipart_groups[base_name] = {
                        'is_multipart': True,
                        'first_volume': archive,
                        'volumes': [],
                        'name': base_name,
                        'count': 0,
                        'total_size': 0,
                        'format': 'rar_first'
                    }
                multipart_groups[base_name]['volumes'].append(archive)
                is_grouped = True
        
        if not is_grouped:
            # 单文件压缩包
            single_files.append(archive)
    
    # 统计多卷信息
    multipart_list = []
    for group_name, group_info in multipart_groups.items():
        group_info['volumes'].sort()  # 按名称排序
        group_info['count'] = len(group_info['volumes'])
        
        # 计算总大小
        try:
            total_size = sum(Path(v).stat().st_size for v in group_info['volumes'])
            group_info['total_size'] = total_size
        except:
            group_info['total_size'] = 0
        
        multipart_list.append(group_info)
        logger.info(f"发现多卷压缩包: {group_name} ({group_info['count']} 卷, {group_info['total_size']} bytes)")
    
    return multipart_list, single_files

def is_archive_encrypted(file_path: str) -> Tuple[bool, Optional[bool]]:
    """
    判断压缩包是否加密
    返回: (是否是压缩包, 是否加密)
    """
    try:
        file_name = Path(file_path).name.lower()
        # 支持 7z/zip/rar 以及 tar 系列
        if file_name.endswith('.7z'):
            # 先用7z l检测
            try:
                result = subprocess.run(
                    ['7z', 'l', '-y', file_path],
                    capture_output=True,
                    text=True,
                    timeout=10  # 降低超时时间从30秒到10秒
                )
                output = (result.stdout + result.stderr).lower()
                logger.debug(f"7z l 命令返回码: {result.returncode}, 文件: {file_path}")
                logger.debug(f"7z l stdout: {result.stdout[:200]}")
                logger.debug(f"7z l stderr: {result.stderr[:200]}")
                if result.returncode != 0:
                    if 'password' in output or 'encrypted' in output or 'wrong password' in output or 'can not open encrypted archive' in output:
                        logger.debug(f"7z 文件检测为加密 (返回码 {result.returncode}): {file_path}")
                        return True, True
                    logger.warning(f"7z 列出文件失败，返回码 {result.returncode}: {file_path}")
                    # 命令失败时假设可能加密，自动进入密码尝试流程
                    return True, True
                if 'password' in output or 'encrypted' in output or 'lock' in output or 'can not open encrypted archive' in output:
                    logger.debug(f"7z 文件检测为加密（输出标志）: {file_path}")
                    return True, True
                logger.debug(f"7z 文件检测为无加密: {file_path}")
                return True, False
            except subprocess.TimeoutExpired:
                logger.warning(f"7z l 检测超时 (10秒)，跳过文件: {file_path}")
                # 超时的文件缓存为"无法检测"，避免重复尝试
                _set_cached_encryption(file_path, False)  # 标记为False避免进入密码破解
                return True, False  # 返回False表示不加密，实际是跳过
            except Exception as e:
                logger.warning(f"7z l 检测异常: {e}")
                # 其他异常假设可能加密
                return True, True
            
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
            # 对于 RAR，优先用 unrar，缺失时回退 7z
            if _has_command('unrar'):
                result = subprocess.run(
                    ['unrar', 'lt', '-p-', file_path],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                output = (result.stdout + result.stderr).lower()
                if result.returncode != 0:
                    if 'password' in output or 'encrypted' in output or 'incorrect' in output:
                        logger.debug(f"RAR 文件检测为加密 (unrar): {file_path}")
                        return True, True
                    logger.warning(f"unrar 列出文件失败，返回码 {result.returncode}: {file_path}")
                    return True, True
                if 'password' in output or 'encrypted' in output or 'incorrect' in output:
                    logger.debug(f"RAR 文件检测为加密 (unrar 输出标志): {file_path}")
                    return True, True
                logger.debug(f"RAR 文件检测为无加密 (unrar): {file_path}")
                return True, False

            result = subprocess.run(
                ['7z', 'l', '-y', file_path],
                capture_output=True,
                text=True,
                timeout=10
            )
            output = (result.stdout + result.stderr).lower()
            
            if result.returncode != 0:
                if 'password' in output or 'encrypted' in output:
                    logger.debug(f"RAR 文件检测为加密 (7z): {file_path}")
                    return True, True
            
            if 'password' in output or 'encrypted' in output or 'lock' in output:
                logger.debug(f"RAR 文件检测为加密 (7z): {file_path}")
                return True, True
            
            logger.debug(f"RAR 文件检测为无加密 (7z): {file_path}")
            return True, False
            
        elif file_name.endswith(('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz', '.tar.zst')):
            # tar 系列通常不支持加密检测，视为普通压缩包
            logger.debug(f"TAR 文件视为无加密: {file_path}")
            return True, False

        elif file_name.endswith(('.gz', '.bz2', '.xz', '.lzma', '.zst', '.cab', '.iso')):
            # 单独压缩格式和其他格式，通常不加密
            logger.debug(f"压缩文件视为无加密: {file_path}")
            return True, False
            
    except subprocess.TimeoutExpired:
        logger.warning(f"检测加密状态超时: {file_path}")
        # 超时时返回“无法判断加密状态”，不报错，后续流程可继续
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

        # 7z格式
        if file_name.endswith('.7z'):
            cmd = ['7z', 'x', '-y', file_path, f'-o{extract_dir}']
            if password:
                cmd.append(f'-p{password}')

        # RAR格式
        elif file_name.endswith('.rar'):
            if _has_command('unrar'):
                if password:
                    cmd = ['unrar', 'x', '-o+', f'-p{password}', file_path, extract_dir]
                else:
                    cmd = ['unrar', 'x', '-o+', '-p-', file_path, extract_dir]
            else:
                cmd = ['7z', 'x', '-y', file_path, f'-o{extract_dir}']
                if password:
                    cmd.append(f'-p{password}')

        # ZIP格式
        elif file_name.endswith('.zip'):
            if password:
                cmd = ['unzip', '-P', password, '-o', file_path, '-d', extract_dir]
            else:
                cmd = ['unzip', '-o', file_path, '-d', extract_dir]

        # TAR系列（不支持加密）
        elif file_name.endswith('.tar'):
            cmd = ['tar', '-xf', file_path, '-C', extract_dir]
        elif file_name.endswith(('.tar.gz', '.tgz')):
            cmd = ['tar', '-xzf', file_path, '-C', extract_dir]
        elif file_name.endswith(('.tar.bz2', '.tbz2')):
            cmd = ['tar', '-xjf', file_path, '-C', extract_dir]
        elif file_name.endswith(('.tar.xz', '.txz')):
            cmd = ['tar', '-xJf', file_path, '-C', extract_dir]
        elif file_name.endswith('.tar.zst'):
            cmd = ['tar', '--zstd', '-xf', file_path, '-C', extract_dir]

        # 单独压缩格式（使用7z解压，输出到目标目录）
        elif file_name.endswith('.gz') and not file_name.endswith('.tar.gz'):
            cmd = ['7z', 'x', '-y', file_path, f'-o{extract_dir}']
        elif file_name.endswith('.bz2') and not file_name.endswith('.tar.bz2'):
            cmd = ['7z', 'x', '-y', file_path, f'-o{extract_dir}']
        elif file_name.endswith('.xz') and not file_name.endswith('.tar.xz'):
            cmd = ['7z', 'x', '-y', file_path, f'-o{extract_dir}']
        elif file_name.endswith('.lzma'):
            cmd = ['7z', 'x', '-y', file_path, f'-o{extract_dir}']
        elif file_name.endswith('.zst') and not file_name.endswith('.tar.zst'):
            # 单独的zst文件，先解压到临时位置
            output_name = file_name[:-4]  # 去掉.zst后缀
            output_path = os.path.join(extract_dir, output_name)
            cmd = ['zstd', '-d', '-f', file_path, '-o', output_path]

        # 其他格式（使用7z）
        elif file_name.endswith('.cab'):
            cmd = ['7z', 'x', '-y', file_path, f'-o{extract_dir}']
        elif file_name.endswith('.iso'):
            cmd = ['7z', 'x', '-y', file_path, f'-o{extract_dir}']

        else:
            return False, "不支持的格式"

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=EXTRACTION_TIMEOUT)

        if result.returncode == 0:
            logger.info(f"成功解压: {file_path}")
            return True, "解压成功"
        else:
            error_output = (result.stderr + result.stdout).lower()
            
            # 检查是否是密码相关错误
            if password and ('password' in error_output or 'wrong password' in error_output or
                           'incorrect' in error_output or 'encrypted' in error_output or '密码' in error_output):
                logger.warning(f"密码错误 [{file_path}]")
                return False, "密码错误"
            
            error_msg = result.stderr or result.stdout or "未知错误"
            logger.error(f"解压命令失败 [{file_path}]: 返回码 {result.returncode}\n命令: {' '.join(cmd)}\n错误: {error_msg}")
            return False, f"解压失败: {error_msg[:200]}"
            
    except subprocess.TimeoutExpired:
        logger.error(f"解压超时: {file_path}")
        return False, f"解压超时（{EXTRACTION_TIMEOUT}秒）"
    except Exception as e:
        logger.error(f"解压异常 {file_path}: {e}")
        return False, f"解压异常: {str(e)[:100]}"

def extract_with_password_dict(file_path: str, extract_dir: str, max_retries: int = 5, timeout_per_password: int = 5) -> Tuple[bool, str, Optional[str]]:
    """
    使用密码词典尝试解压
    每个密码有独立的超时控制
    返回: (成功, 消息, 使用的密码)
    """
    import time
    retry_count = 0
    
    # 检查缓存
    if file_path in PASSWORD_SUCCESS_CACHE:
        cached_pwd = PASSWORD_SUCCESS_CACHE[file_path]
        logger.info(f"尝试缓存密码: {file_path}")
        success, msg = extract_archive(file_path, extract_dir, cached_pwd)
        if success:
            return True, "解压成功 (缓存密码)", cached_pwd
        retry_count += 1
        logger.warning(f"缓存密码失败 {file_path}: {msg}，将尝试词典密码")
    
    # 尝试词典中的密码，每个密码有独立的超时
    total_start = time.time()
    for attempt in range(min(len(PASSWORD_DICT), max_retries)):
        password = PASSWORD_DICT[attempt]
        attempt_start = time.time()
        
        try:
            # 每个密码尝试有独立的超时
            success, msg = extract_archive(file_path, extract_dir, password)
            attempt_elapsed = time.time() - attempt_start
            
            if success:
                # 保存到缓存
                PASSWORD_SUCCESS_CACHE[file_path] = password
                save_password_cache()
                logger.info(f"成功解压 {file_path} (尝试次数: {attempt+1}, 密码耗时: {attempt_elapsed:.1f}s)")
                return True, "解压成功", password
            else:
                # 密码错误，继续尝试下一个
                logger.debug(f"密码尝试 {attempt+1} 失败: {password} (耗时: {attempt_elapsed:.1f}s)")
        except Exception as e:
            attempt_elapsed = time.time() - attempt_start
            logger.warning(f"解压异常 {file_path} (尝试 {attempt+1}): {e} (耗时: {attempt_elapsed:.1f}s)")
            continue
        
        retry_count += 1
    
    total_elapsed = time.time() - total_start
    logger.error(f"所有密码都失败了 {file_path} (尝试次数: {retry_count}, 总耗时: {int(total_elapsed)}s)")
    return False, f"所有密码都失败了 (尝试 {retry_count} 个密码，耗时 {int(total_elapsed)}s)", None

def _iter_files(root_dir: str, recursive: bool = True):
    """
    高性能文件遍历器（避免 rglob 的高开销）
    自动跳过权限不足或不可访问的目录
    """
    if not recursive:
        try:
            with os.scandir(root_dir) as it:
                for entry in it:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            yield entry.path
                    except (OSError, PermissionError):
                        continue
        except (OSError, PermissionError) as e:
            logger.debug(f"扫描目录失败: {root_dir}, {e}")
        return

    stack = [root_dir]
    while stack:
        current_dir = stack.pop()
        try:
            with os.scandir(current_dir) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            yield entry.path
                    except (OSError, PermissionError):
                        continue
        except (OSError, PermissionError) as e:
            logger.debug(f"扫描目录失败: {current_dir}, {e}")
            continue

def _is_archive_file(file_name: str, all_exts: Tuple[str, ...]) -> bool:
    name = file_name.lower()
    return name.endswith(all_exts)

def _get_cached_encryption(file_path: str) -> Optional[bool]:
    """获取缓存的加密状态（线程安全）"""
    with encryption_cache_lock:
        try:
            stat = os.stat(file_path)
            cached = encryption_cache.get(file_path)
            if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
                return cached[2]
        except (OSError, PermissionError):
            return None
        return None

def _set_cached_encryption(file_path: str, is_encrypted: Optional[bool]) -> None:
    """设置缓存的加密状态（线程安全）"""
    with encryption_cache_lock:
        try:
            stat = os.stat(file_path)
            encryption_cache[file_path] = (stat.st_mtime, stat.st_size, is_encrypted)
        except (OSError, PermissionError):
            return
            
def _is_detecting(file_path: str) -> bool:
    """检查文件是否正在检测中"""
    with encryption_cache_lock:
        return file_path in detecting_files
        
def _mark_detecting(file_path: str, detecting: bool) -> None:
    """标记文件检测状态"""
    with encryption_cache_lock:
        if detecting:
            detecting_files.add(file_path)
        else:
            detecting_files.discard(file_path)

def find_all_archives(root_dir: str, recursive: bool = True) -> List[str]:
    """
    递归或仅查找当前目录的压缩包（包括多卷文件的所有卷）
    recursive: True为递归查找所有子目录，False为仅查找当前目录
    """
    archives = []
    root_path = Path(root_dir)

    if not root_path.exists():
        logger.error(f"目录不存在: {root_dir}")
        return archives

    if not root_path.is_dir():
        logger.error(f"路径不是目录: {root_dir}")
        return archives

    # 支持的压缩包格式（包括所有多卷文件格式）
    all_exts = SUPPORTED_ARCHIVE_EXTENSIONS + MULTIPART_EXTENSIONS

    # 高性能扫描（使用 os.scandir 避免 rglob 大开销）
    for file_path in _iter_files(root_dir, recursive=recursive):
        try:
            if _is_archive_file(file_path, all_exts):
                archives.append(file_path)
        except (OSError, PermissionError):
            continue

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

    # 支持的压缩包格式（包含多卷格式）
    all_exts = SUPPORTED_ARCHIVE_EXTENSIONS + MULTIPART_EXTENSIONS

    try:
        for item in root_path.iterdir():
            try:
                if item.is_dir():
                    # 使用高性能遍历计算压缩包数量
                    archive_count = 0
                    for file_path in _iter_files(str(item), recursive=True):
                        if _is_archive_file(file_path, all_exts):
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
                extraction_status[task_id]['message'] = f'需要密码，正在尝试... (每个密码{PASSWORD_TIMEOUT}秒超时)'
            
            # 每个密码有独立的超时控制
            success, msg, used_pwd = extract_with_password_dict(archive_file, actual_extract_dir, max_retries=5, timeout_per_password=PASSWORD_TIMEOUT)
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
    root_dir = data.get('path', DEFAULT_MOUNT_PATH)
    include_subdirs = data.get('include_subdirs', True)  # 新增参数，默认包含子目录
    
    logger.info(f"开始扫描目录: {root_dir} (包含子目录: {include_subdirs})")
    
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
        archives = find_all_archives(root_dir, recursive=include_subdirs)
        logger.info(f"扫描完成，发现 {len(archives)} 个压缩包 (子目录: {include_subdirs})")
    except Exception as e:
        logger.error(f"扫描压缩包时出错: {e}")
        return jsonify({'error': f'扫描文件时出错: {str(e)}'}), 500
    
    try:
        if include_subdirs:
            subdir_stats = scan_subdirectories(root_dir)
        else:
            subdir_stats = {}
    except Exception as e:
        logger.error(f"扫描子目录时出错: {e}")
        subdir_stats = {}
    
    # 将多卷文件分组
    multipart_groups, single_archives = group_multipart_archives(archives)
    logger.info(f"发现 {len(multipart_groups)} 个多卷文件组，{len(single_archives)} 个单文件")
    
    # 分析单文件压缩包
    single_result = []
    for archive in single_archives:
        try:
            # 检查是否正在检测中，避免重复检测
            if _is_detecting(archive):
                logger.debug(f"文件正在检测中，跳过: {archive}")
                continue
                
            cached_enc = _get_cached_encryption(archive)
            if cached_enc is None:
                # 标记为检测中
                _mark_detecting(archive, True)
                try:
                    is_arch, is_enc = is_archive_encrypted(archive)
                    if is_arch:
                        _set_cached_encryption(archive, is_enc)
                finally:
                    # 检测完成，移除标记
                    _mark_detecting(archive, False)
            else:
                is_arch, is_enc = True, cached_enc

            single_result.append({
                'path': archive,
                'name': Path(archive).name,
                'size': Path(archive).stat().st_size,
                'encrypted': is_enc if is_arch else None,
                'status': 'ready',
                'cached': archive in PASSWORD_SUCCESS_CACHE,
                'is_multipart': False
            })
        except (OSError, PermissionError) as e:
            logger.warning(f"无法访问压缩包 {archive}: {e}")
            continue
        except Exception as e:
            logger.warning(f"处理压缩包 {archive} 时出错: {e}")
            continue
    
    # 分析多卷压缩包
    multipart_result = []
    for group in multipart_groups:
        try:
            # 检查是否正在检测中
            if _is_detecting(group['first_volume']):
                logger.debug(f"文件正在检测中，跳过: {group['first_volume']}")
                continue
                
            # 检测第一卷是否加密（带缓存）
            cached_enc = _get_cached_encryption(group['first_volume'])
            if cached_enc is None:
                _mark_detecting(group['first_volume'], True)
                try:
                    is_arch, is_enc = is_archive_encrypted(group['first_volume'])
                    if is_arch:
                        _set_cached_encryption(group['first_volume'], is_enc)
                finally:
                    _mark_detecting(group['first_volume'], False)
            else:
                is_arch, is_enc = True, cached_enc

            multipart_result.append({
                'path': group['first_volume'],  # 返回第一卷路径用于解压
                'name': group['name'],
                'size': group['total_size'],
                'encrypted': is_enc if is_arch else None,
                'status': 'ready',
                'cached': group['first_volume'] in PASSWORD_SUCCESS_CACHE,
                'is_multipart': True,
                'volume_count': group['count'],
                'volumes': group['volumes']
            })
        except Exception as e:
            logger.warning(f"处理多卷压缩包 {group['name']} 时出错: {e}")
            continue
    
    # 合并结果
    result = single_result + multipart_result
    
    return jsonify({
        'total': len(result),
        'archives': result,
        'multipart_count': len(multipart_result),
        'single_count': len(single_result),
        'subdirs_with_archives': subdir_stats if include_subdirs else {},
        'subdirs_count': len(subdir_stats) if include_subdirs else 0
    })

@app.route('/api/extract', methods=['POST'])
def extract():
    """开始批量解压"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': '无效的请求数据'}), 400

        archives = data.get('archives', [])
        extract_base = data.get('extract_to', DEFAULT_MOUNT_PATH)
        extract_mode = data.get('extract_mode', 'to_specified')  # 新增参数: to_current, to_same_name, to_specified
        extract_to_same_name = data.get('extract_to_same_name', False)
        auto_delete_success = data.get('auto_delete_success', False)

        if not archives:
            return jsonify({'error': '没有选择任何文件'}), 400

        # 保存提取选项
        global extraction_options
        extraction_options['extract_to_same_name'] = extract_to_same_name
        extraction_options['auto_delete_success'] = auto_delete_success
        extraction_options['extract_mode'] = extract_mode

        # 确保解压基目录存在
        try:
            os.makedirs(extract_base, exist_ok=True)
        except Exception as e:
            logger.error(f"无法创建或访问解压目录 {extract_base}: {e}")
            return jsonify({'error': f'无法访问或创建解压目录: {extract_base}'}), 500

        tasks = {}
        for i, archive in enumerate(archives):
            task_id = f"task_{i}"
            tasks[task_id] = archive

            # 计算实际解压目录
            if extract_mode == 'to_current':
                extract_dir = str(Path(archive).parent)
            elif extract_mode == 'to_same_name':
                archive_name = os.path.basename(archive)
                base_name = archive_name
                for ext in ['.7z', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar', '.zip', '.rar']:
                    if base_name.lower().endswith(ext):
                        base_name = base_name[:-len(ext)]
                        break
                extract_dir = os.path.join(extract_base, base_name)
                os.makedirs(extract_dir, exist_ok=True)
            else:
                extract_dir = extract_base

            thread = threading.Thread(
                target=process_extraction_task,
                args=(task_id, archive, extract_dir)
            )
            thread.daemon = True
            thread.start()

        logger.info(f"启动解压: {len(archives)} 个文件，extract_mode={extract_mode}, 自动删除={auto_delete_success}")

        return jsonify({
            'extract_dir': extract_base,
            'task_count': len(tasks),
            'tasks': tasks,
            'options': {
                'extract_mode': extract_mode,
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
        'default_mount': DEFAULT_MOUNT_PATH,
        'concurrent_count': extraction_settings['concurrent_count'],
        'supported_formats': list(SUPPORTED_ARCHIVE_EXTENSIONS),
        'multipart_formats': ['.part1.7z', '.part1.rar', '.001', '.002']
    })

@app.route('/api/settings', methods=['GET', 'POST'])
def update_settings():
    """获取或更新并发设置"""
    if request.method == 'GET':
        return jsonify({
            'concurrent_count': extraction_settings['concurrent_count']
        })
    else:  # POST
        data = request.get_json()
        concurrent_count = data.get('concurrent_count', 1)

        # 验证并发数
        if not isinstance(concurrent_count, int) or concurrent_count < 1 or concurrent_count > MAX_CONCURRENT_EXTRACTIONS:
            return jsonify({'error': f'并发数必须在 1-{MAX_CONCURRENT_EXTRACTIONS} 之间'}), 400

        extraction_settings['concurrent_count'] = concurrent_count
        logger.info(f"并发数已更新为: {concurrent_count}")
        
        return jsonify({
            'success': True,
            'concurrent_count': concurrent_count
        })

@app.route('/api/subdirs', methods=['POST'])
def scan_subdirs():
    """扫描子目录中的压缩包"""
    data = request.get_json()
    root_dir = data.get('path', DEFAULT_MOUNT_PATH)
    
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
    """更新密码词典 - 支持字符串或列表格式"""
    data = request.get_json()
    new_passwords = data.get('passwords', [])
    
    # 支持字符串格式（换行分隔）或列表格式
    if isinstance(new_passwords, str):
        new_passwords = [p.strip() for p in new_passwords.split('\n') if p.strip()]
    elif not isinstance(new_passwords, list):
        return jsonify({'error': '密码必须是字符串或列表'}), 400
    
    try:
        global PASSWORD_DICT
        PASSWORD_DICT = [str(p).strip() for p in new_passwords if str(p).strip()]
        
        # 保存到文件（优先使用当前目录）
        dict_path = Path('passwords.txt') if Path('.').exists() else Path('/app/passwords.txt')
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
    if LOG_FILE_PATH.exists():
        try:
            with open(LOG_FILE_PATH, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                # 返回最后 200 行
                return jsonify({'logs': ''.join(lines[-200:])})
        except Exception as e:
            logger.error(f"读取日志失败: {e}")
    return jsonify({'logs': '暂无日志'})

@app.route('/api/logs/download', methods=['GET'])
def download_logs():
    """下载日志文件"""
    if LOG_FILE_PATH.exists():
        try:
            return send_file(LOG_FILE_PATH, as_attachment=True, download_name=f'fnos_logs_{int(time.time())}.log')
        except Exception as e:
            logger.error(f"下载日志失败: {e}")
            return jsonify({'error': '日志下载失败'}), 500
    return jsonify({'error': '日志文件不存在'}), 404

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
                if not any(file_path.lower().endswith(ext) for ext in SUPPORTED_ARCHIVE_EXTENSIONS):
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

# ========================================
# 健康检查和性能监控 API (P3.2)
# ========================================

@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查端点 - 用于容器和负载均衡器"""
    try:
        # 获取系统信息
        proc = psutil.Process()
        memory_info = proc.memory_info()
        cpu_percent = proc.cpu_percent(interval=0.1)

        health_status = {
            'status': 'healthy',
            'version': '1.2.9',
            'uptime': time.time() - proc.create_time(),
            'system': {
                'platform': platform.system(),
                'python_version': platform.python_version()
            },
            'memory': {
                'rss_mb': memory_info.rss / 1024 / 1024,
                'percent': proc.memory_percent()
            },
            'cpu': {
                'percent': cpu_percent,
                'count': os.cpu_count()
            },
            'cache': {
                'password_cache_size': len(PASSWORD_SUCCESS_CACHE),
                'encryption_cache_size': len(encryption_cache)
            }
        }
        return jsonify(health_status), 200
    except Exception as e:
        logger.error(f"健康检查失败: {e}")
        return jsonify({'status': 'degraded', 'error': str(e)}), 503

@app.route('/api/metrics', methods=['GET'])
def metrics():
    """性能指标端点 - 获取系统性能统计"""
    try:
        metrics_data = {
            'extraction': {
                'queue_size': extraction_queue.qsize(),
                'active_tasks': len(extraction_status),
                'total_completed': sum(1 for s in extraction_status.values() if s.get('status') == 'completed')
            },
            'cache': {
                'password_success_count': len(PASSWORD_SUCCESS_CACHE),
                'encryption_cache_count': len(encryption_cache),
                'password_dict_size': len(PASSWORD_DICT)
            },
            'api': {
                'configured_concurrent': extraction_settings['concurrent_count']
            }
        }
        return jsonify(metrics_data), 200
    except Exception as e:
        logger.error(f"指标查询失败: {e}")
        return jsonify({'error': str(e)}), 500

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
