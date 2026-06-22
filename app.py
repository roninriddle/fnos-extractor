#!/usr/bin/env python3
"""
FNOS 批量文件处理工具
支持递归扫描、密码检测和 Web 界面
版本: 1.3.28
"""

from flask import Flask, render_template, jsonify, request, send_file
from functools import wraps
from pathlib import Path
import os
import subprocess
import json
import threading
import re
from queue import Queue
from typing import Dict, List, Tuple, Optional, Set
import logging
import tempfile
import shutil
import time
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import psutil
import platform

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

APP_VERSION = '1.3.28'
APP_RELEASE_TAG = ''
APP_DISPLAY_VERSION = f"{APP_VERSION}-{APP_RELEASE_TAG}" if APP_RELEASE_TAG else APP_VERSION

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

def _resolve_default_mount_path() -> str:
    configured = os.environ.get('FNOS_MOUNT_PATH') or os.environ.get('DEFAULT_MOUNT_PATH')
    if configured:
        return configured

    for candidate in ('/temp', '/vol1/1000/Temp'):
        if Path(candidate).exists():
            return candidate

    return '/vol1/1000/Temp'

DEFAULT_MOUNT_PATH = _resolve_default_mount_path()
LOG_FILE_PATH = Path(os.environ.get('FNOS_LOG_FILE', '/app/fnos.log'))
MAX_CONCURRENT_EXTRACTIONS = 32

class AdjustableConcurrencyLimiter:
    """线程安全的可调并发限制器，允许排队任务实时感知新并发数。"""
    def __init__(self, limit: int):
        self._limit = limit
        self._active = 0
        self._condition = threading.Condition()

    def acquire(self) -> None:
        with self._condition:
            while self._active >= self._limit:
                self._condition.wait()
            self._active += 1

    def release(self) -> None:
        with self._condition:
            if self._active > 0:
                self._active -= 1
            self._condition.notify_all()

    def set_limit(self, limit: int) -> None:
        with self._condition:
            self._limit = limit
            self._condition.notify_all()

    def state(self) -> Dict[str, int]:
        with self._condition:
            return {
                'limit': self._limit,
                'active': self._active
            }

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.release()
        return False

def _has_command(cmd_name: str) -> bool:
    return shutil.which(cmd_name) is not None

def _contains_non_ascii(value: Optional[str]) -> bool:
    """判断字符串是否包含非 ASCII 字符，用于兼容中文密码。"""
    return bool(value) and any(ord(ch) > 127 for ch in value)

def _get_app_timezone_name() -> str:
    """获取应用日志时区，优先使用环境变量。"""
    return os.environ.get('APP_TIMEZONE') or os.environ.get('TZ') or 'Asia/Shanghai'

def _get_app_timezone() -> ZoneInfo:
    """解析应用日志时区，失败时回退到上海时区。"""
    timezone_name = _get_app_timezone_name()
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning(f"无效时区配置 {timezone_name}，回退到 Asia/Shanghai")
        return ZoneInfo('Asia/Shanghai')

class TimezoneFormatter(logging.Formatter):
    """使用指定时区输出日志时间。"""
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, _get_app_timezone())
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec='seconds')

def _configure_logging_timezone() -> None:
    """统一修正现有日志处理器的时间格式。"""
    formatter = TimezoneFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
    else:
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)

_configure_logging_timezone()

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
extraction_settings_lock = threading.Lock()
extraction_concurrency_limiter = AdjustableConcurrencyLimiter(extraction_settings['concurrent_count'])
timeout_settings = {
    'password_timeout': 15,        # 每个密码尝试的超时时间（秒）
    'extraction_timeout': 300,     # 单个文件解压的超时时间（秒）
    'detection_7z_timeout': 3,     # 7z加密检测超时（秒）
    'detection_zip_timeout': 10,   # ZIP加密检测超时（秒）
    'detection_rar_timeout': 10    # RAR加密检测超时（秒）
}
encryption_cache = {}  # {file_path: (mtime, size, is_encrypted)}
encryption_cache_lock = threading.Lock()  # 保护 encryption_cache 的线程锁
detecting_files = set()  # 正在检测中的文件集合，避免重复检测
control_lock = threading.Lock()
scan_status = {
    'scanning': False,
    'found_count': 0,
    'current_path': '',
    'message': ''
}  # 扫描状态
scan_status_lock = threading.Lock()  # 保护扫描状态的锁
task_counter = 0
task_counter_lock = threading.Lock()

MULTIPART_PATTERNS = [
    ('part', re.compile(r'^(?P<base>.+)\.part(?P<index>\d+)\.(?P<ext>7z|rar)$', re.IGNORECASE)),
    ('001', re.compile(r'^(?P<base>.+)\.(?P<index>\d{3})$', re.IGNORECASE)),
    ('z', re.compile(r'^(?P<base>.+)\.(?P<index>z\d{2})$', re.IGNORECASE)),
    ('r', re.compile(r'^(?P<base>.+)\.(?P<index>r\d{2})$', re.IGNORECASE)),
]

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
            with open(PASSWORD_CACHE_FILE, 'r', encoding='utf-8') as f:
                PASSWORD_SUCCESS_CACHE = json.load(f)
                logger.info(f"已加载 {len(PASSWORD_SUCCESS_CACHE)} 个缓存密码")
        except Exception as e:
            logger.warning(f"密码缓存加载失败: {e}")

def save_password_cache():
    """保存密码缓存"""
    try:
        PASSWORD_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PASSWORD_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(PASSWORD_SUCCESS_CACHE, f, ensure_ascii=False, indent=2)
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
    return _classify_multipart_file(file_path) is not None

def _multipart_sort_key(file_path: str) -> Tuple[int, str]:
    classification = _classify_multipart_file(file_path)
    if classification:
        return classification['index'], Path(file_path).name.lower()
    return (10**9, Path(file_path).name.lower())

def _classify_multipart_file(file_path: str) -> Optional[Dict[str, object]]:
    """识别多卷压缩文件并返回分组信息。"""
    path = Path(file_path)
    file_name = path.name
    lower_name = file_name.lower()

    for pattern_name, pattern in MULTIPART_PATTERNS:
        match = pattern.match(file_name)
        if not match:
            continue

        base_name = match.group('base')
        raw_index = match.group('index')
        if pattern_name == 'part':
            index = int(raw_index)
            group_key = f"part::{base_name.lower()}::{match.group('ext').lower()}"
            format_name = 'part1'
        elif pattern_name == '001':
            index = int(raw_index)
            group_key = f"001::{base_name.lower()}"
            format_name = '001'
        elif pattern_name == 'z':
            index = int(raw_index[1:])
            group_key = f"zip::{base_name.lower()}"
            format_name = 'Z01'
        else:
            index = int(raw_index[1:])
            group_key = f"rar::{base_name.lower()}"
            format_name = 'r00'

        return {
            'group_key': group_key,
            'name': base_name,
            'index': index,
            'format': format_name,
            'is_first': index in (0, 1)
        }

    if lower_name.endswith('.rar') and '.part' not in lower_name:
        base_name = file_name[:-4]
        parent = path.parent
        has_volumes = any(
            candidate.exists()
            for candidate in (
                parent / f"{base_name}.r00",
                parent / f"{base_name}.r01",
                parent / f"{base_name}.Z01",
                parent / f"{base_name}.z01",
                parent / f"{base_name}.001",
            )
        )
        if has_volumes:
            return {
                'group_key': f"rar::{base_name.lower()}",
                'name': base_name,
                'index': 0,
                'format': 'rar_first',
                'is_first': True
            }

    if lower_name.endswith('.zip'):
        base_name = file_name[:-4]
        parent = path.parent
        has_volumes = any(
            candidate.exists()
            for candidate in (
                parent / f"{base_name}.Z01",
                parent / f"{base_name}.z01",
            )
        )
        if has_volumes:
            return {
                'group_key': f"zip::{base_name.lower()}",
                'name': base_name,
                'index': 999999,
                'format': 'zip_last',
                'is_first': False
            }

    return None

def get_multipart_first_volume(file_path: str) -> Optional[str]:
    """
    如果是多卷压缩文件，获取第一卷的路径
    否则返回 None
    """
    classification = _classify_multipart_file(file_path)
    if not classification:
        return None

    path = Path(file_path)
    parent = path.parent
    group_name = classification['name']
    group_format = classification['format']

    if group_format == 'part1':
        for ext in ['7z', 'rar']:
            candidate = parent / f"{group_name}.part1.{ext}"
            if candidate.exists():
                return str(candidate)
    elif group_format == '001':
        candidate = parent / f"{group_name}.001"
        if candidate.exists():
            return str(candidate)
    elif group_format == 'Z01':
        for suffix in ['Z01', 'z01']:
            candidate = parent / f"{group_name}.{suffix}"
            if candidate.exists():
                return str(candidate)
    elif group_format == 'zip_last':
        for suffix in ['Z01', 'z01']:
            candidate = parent / f"{group_name}.{suffix}"
            if candidate.exists():
                return str(candidate)
    elif group_format == 'r00':
        for suffix in ['rar', 'r00']:
            candidate = parent / f"{group_name}.{suffix}"
            if candidate.exists():
                return str(candidate)
    elif group_format == 'rar_first':
        return file_path

    return file_path if classification['is_first'] else None

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
    multipart_groups = {}
    single_files = []

    for archive in archives:
        classification = _classify_multipart_file(archive)
        if not classification:
            single_files.append(archive)
            continue

        group_key = classification['group_key']
        if group_key not in multipart_groups:
            multipart_groups[group_key] = {
                'is_multipart': True,
                'first_volume': archive if classification['is_first'] else None,
                'volumes': [],
                'name': classification['name'],
                'count': 0,
                'total_size': 0,
                'format': classification['format']
            }

        multipart_groups[group_key]['volumes'].append(archive)
        if classification['is_first']:
            multipart_groups[group_key]['first_volume'] = archive
    
    multipart_list = []
    for group_name, group_info in multipart_groups.items():
        group_info['volumes'].sort(key=_multipart_sort_key)
        group_info['count'] = len(group_info['volumes'])
        if not group_info['first_volume'] and group_info['volumes']:
            group_info['first_volume'] = group_info['volumes'][0]

        try:
            total_size = sum(Path(v).stat().st_size for v in group_info['volumes'])
            group_info['total_size'] = total_size
        except Exception:
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
            # 使用 7z t 命令测试文件，而不是 l 命令
            # -p- 表示无密码，如果文件加密则会失败
            try:
                result = subprocess.run(
                    ['7z', 't', '-p-', file_path],
                    capture_output=True,
                    text=True,
                    timeout=timeout_settings.get('detection_7z_timeout', 3)
                )
                output = (result.stdout + result.stderr).lower()
                stderr_output = result.stderr.lower()
                
                logger.debug(f"7z t 命令返回码: {result.returncode}, 文件: {file_path}")
                
                # 检查是否有明确的加密标志
                if ('password' in output or 'encrypted' in output or 
                    'wrong password' in output or 'can not open encrypted' in output or
                    'cannot open encrypted' in output or 'enter password' in output):
                    logger.info(f"7z 文件检测为加密: {file_path}")
                    return True, True
                
                # 返回码为0表示测试成功，文件无加密
                if result.returncode == 0:
                    logger.debug(f"7z 文件检测为无加密: {file_path}")
                    return True, False
                
                # 返回码不为0，检查是否是加密相关错误
                if result.returncode == 2:  # 7z 的加密错误代码
                    logger.info(f"7z 文件检测为加密 (返回码 2): {file_path}")
                    return True, True
                
                # 其他错误，假设可能加密（谨慎处理）
                logger.warning(f"7z 测试失败，假设可能加密，返回码 {result.returncode}: {file_path}")
                return True, True
                
            except subprocess.TimeoutExpired:
                logger.warning(f"7z t 检测超时 (3秒)，假设可能加密: {file_path}")
                # 超时的文件标记为可能加密，进入密码尝试
                return True, True
            except Exception as e:
                logger.warning(f"7z t 检测异常，假设可能加密: {e}")
                # 其他异常假设可能加密
                return True, True
            
        elif file_name.endswith('.zip'):
            result = subprocess.run(
                ['unzip', '-t', file_path],
                capture_output=True,
                text=True,
                timeout=timeout_settings.get('detection_zip_timeout', 10)
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
                    timeout=timeout_settings.get('detection_rar_timeout', 10)
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
                timeout=timeout_settings.get('detection_rar_timeout', 10)
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

def extract_archive(
    file_path: str,
    extract_dir: str,
    password: Optional[str] = None,
    timeout: Optional[int] = None,
    task_id: Optional[str] = None,
    force_7z_zip: bool = False
) -> Tuple[bool, str]:
    """
    解压文件
    返回: (成功, 消息)
    timeout: 自定义超时时间，如果为None则使用默认的extraction_timeout设置
    """
    try:
        actual_timeout = timeout if timeout is not None else timeout_settings.get('extraction_timeout', 300)
        dir_ok, dir_error = _ensure_read_write_directory(extract_dir)
        if not dir_ok:
            return False, dir_error

        file_name = Path(file_path).name.lower()
        cmd = []
        prefer_7z_for_password = _contains_non_ascii(password)

        # 7z格式
        if file_name.endswith('.7z'):
            cmd = ['7z', 'x', '-y', file_path, f'-o{extract_dir}']
            if password:
                cmd.append(f'-p{password}')
            else:
                cmd.append('-p-')  # 明确指定无密码

        # RAR格式
        elif file_name.endswith('.rar'):
            if _has_command('unrar') and not prefer_7z_for_password:
                if password:
                    cmd = ['unrar', 'x', '-o+', f'-p{password}', file_path, extract_dir]
                else:
                    cmd = ['unrar', 'x', '-o+', '-p-', file_path, extract_dir]
            else:
                cmd = ['7z', 'x', '-y', file_path, f'-o{extract_dir}']
                if password:
                    cmd.append(f'-p{password}')
                else:
                    cmd.append('-p-')

        # ZIP格式
        elif file_name.endswith('.zip'):
            if force_7z_zip or (password and prefer_7z_for_password):
                cmd = ['7z', 'x', '-y', file_path, f'-o{extract_dir}']
                if password:
                    cmd.append(f'-p{password}')
                else:
                    cmd.append('-p-')
            elif password:
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

        uses_7z_progress = bool(cmd) and cmd[0] == '7z'
        if uses_7z_progress and '-bsp1' not in cmd:
            cmd.insert(1, '-bsp1')

        # 直接把子进程输出写入临时文件，避免大体积输出填满 PIPE 后卡死，
        # 导致明明已经解压成功却被误判成“超时/密码失败”。
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode='w+t', encoding='utf-8', errors='ignore') as stdout_buffer, \
             tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode='w+t', encoding='utf-8', errors='ignore') as stderr_buffer:
            process = subprocess.Popen(cmd, stdout=stdout_buffer, stderr=stderr_buffer, text=True)
            start_time = time.time()
            last_reported_progress = None

            while True:
                if process.poll() is not None:
                    break

                if uses_7z_progress and task_id:
                    parsed_progress = _extract_progress_percent(
                        _read_buffer_tail(stdout_buffer),
                        _read_buffer_tail(stderr_buffer)
                    )
                    if parsed_progress is not None and parsed_progress != last_reported_progress:
                        last_reported_progress = parsed_progress
                        _update_task_progress(
                            task_id,
                            file_path,
                            parsed_progress,
                            f'正在解压... ({parsed_progress}%)'
                        )

                if task_id:
                    with control_lock:
                        should_stop = extraction_control['stop']
                    if should_stop:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        logger.warning(f"解压任务收到停止信号: {file_path}")
                        return False, "已停止"

                if time.time() - start_time > actual_timeout:
                    # 给刚好到达超时边界的进程一次最终完成机会，
                    # 避免已经完成的解压被误判成超时。
                    if process.poll() is not None:
                        break
                    try:
                        process.wait(timeout=0.3)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise subprocess.TimeoutExpired(cmd, actual_timeout)

                time.sleep(0.2)

            stdout_buffer.seek(0)
            stderr_buffer.seek(0)
            stdout = stdout_buffer.read()
            stderr = stderr_buffer.read()
            result = subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)

        if result.returncode == 0:
            logger.info(f"成功解压: {file_path}")
            return True, "解压成功"
        else:
            error_output = (result.stderr + result.stdout).lower()

            if file_name.endswith('.zip') and cmd[0] == 'unzip' and _is_zip_overlap_warning(result.stderr + result.stdout):
                logger.warning(
                    f"unzip 判定 ZIP 存在 overlapped components，改用 7z 重试: {file_path}"
                )
                _update_task_message(
                    task_id,
                    file_path,
                    'unzip 触发 zip-bomb 保护，正在改用 7z 重试...'
                )
                return extract_archive(
                    file_path,
                    extract_dir,
                    password=password,
                    timeout=timeout,
                    task_id=task_id,
                    force_7z_zip=True
                )
            
            # 检查是否是密码相关错误
            if ('password' in error_output or 'wrong password' in error_output or
                'incorrect' in error_output or 'encrypted' in error_output or 
                '密码' in error_output or 'cannot open encrypted' in error_output or
                'can not open encrypted' in error_output):
                if password:
                    logger.warning(f"密码错误 [{file_path}]")
                    return False, "密码错误"
                else:
                    logger.warning(f"需要密码 [{file_path}]")
                    return False, "需要密码"
            
            error_msg = result.stderr or result.stdout or "未知错误"
            friendly_error = _build_friendly_extraction_error(error_msg, extract_dir)
            logger.error(f"解压命令失败 [{file_path}]: 返回码 {result.returncode}\n命令: {' '.join(cmd)}\n错误: {error_msg}")
            return False, friendly_error
            
    except subprocess.TimeoutExpired:
        full_extraction_timeout = timeout_settings.get('extraction_timeout', 300)
        if timeout is not None and actual_timeout < full_extraction_timeout:
            logger.info(f"密码短超时未完成: {file_path} ({actual_timeout}秒)，等待调用方完整重试")
        else:
            logger.error(f"解压超时: {file_path} ({actual_timeout}秒)")
        return False, f"解压超时（{actual_timeout}秒）"
    except Exception as e:
        logger.error(f"解压异常 {file_path}: {e}")
        return False, f"解压异常: {str(e)[:100]}"

def _is_password_failure_message(message: Optional[str]) -> bool:
    if not message:
        return False
    lowered = str(message).lower()
    return '密码错误' in message or '需要密码' in message or 'wrong password' in lowered

def _is_timeout_message(message: Optional[str]) -> bool:
    if not message:
        return False
    lowered = str(message).lower()
    return '超时' in message or 'timeout' in lowered

def _is_zip_overlap_warning(output: str) -> bool:
    """识别 unzip 对重叠 ZIP 组件的 zip-bomb 保护性拒绝。"""
    lowered = (output or '').lower()
    return 'overlapped components' in lowered and 'possible zip bomb' in lowered

def _read_buffer_tail(buffer, max_chars: int = 4096) -> str:
    """读取输出缓冲区尾部，用于解析 7z 实时进度。"""
    try:
        buffer.seek(0, os.SEEK_END)
        current_pos = buffer.tell()
        seek_pos = max(0, current_pos - max_chars)
        buffer.seek(seek_pos)
        tail = buffer.read()
        buffer.seek(current_pos)
        return tail or ''
    except Exception:
        return ''

def _extract_progress_percent(*chunks: str) -> Optional[int]:
    """从命令输出中提取最新的百分比进度。"""
    matches = []
    for chunk in chunks:
        if not chunk:
            continue
        matches.extend(re.findall(r'(?<!\d)(100|[1-9]?\d)%', chunk))

    if not matches:
        return None

    try:
        return max(0, min(100, int(matches[-1])))
    except (TypeError, ValueError):
        return None

def _ensure_read_write_directory(directory: str, check_existing_tree: bool = False) -> Tuple[bool, str]:
    """确认目录可创建，且当前进程可以实际读取、进入和写入。"""
    try:
        os.makedirs(directory, exist_ok=True)
        if not os.access(directory, os.R_OK | os.W_OK | os.X_OK):
            return False, (
                f"解压目录读写权限不足: {directory}。"
                f"请确认容器用户可以读取、进入并写入该目录，或改用有权限的目录后重试。"
            )
        try:
            with os.scandir(directory):
                pass
        except Exception as e:
            return False, (
                f"解压目录不可读取: {directory}。"
                f"请检查 Docker 挂载目录权限，或改用可读写目录后重试。原始错误: {e}"
            )

        probe_path = None
        with tempfile.NamedTemporaryFile(prefix='.fnos-write-test-', dir=directory, delete=False) as probe:
            probe.write(b'ok')
            probe_path = probe.name
        if probe_path and os.path.exists(probe_path):
            os.remove(probe_path)

        if check_existing_tree:
            for root, dirs, files in os.walk(directory):
                if not os.access(root, os.W_OK | os.X_OK):
                    return False, (
                        f"同名输出目录中已有不可写目录: {root}。"
                        f"请删除该同名文件夹、修正权限，或更换解压目录后重试。"
                    )
                for name in dirs:
                    dir_path = os.path.join(root, name)
                    if not os.access(dir_path, os.W_OK | os.X_OK):
                        return False, (
                            f"同名输出目录中已有不可写目录: {dir_path}。"
                            f"请删除该同名文件夹、修正权限，或更换解压目录后重试。"
                        )
                for name in files:
                    file_path = os.path.join(root, name)
                    if not os.access(file_path, os.W_OK):
                        return False, (
                            f"同名输出目录中已有不可覆盖文件: {file_path}。"
                            f"请删除该同名文件夹、修正权限，或更换解压目录后重试。"
                        )
        return True, ''
    except Exception as e:
        logger.warning(f"解压目录读写权限不足 {directory}: {e}")
        return False, (
            f"解压目录读写权限不足: {directory}。请检查 Docker 挂载目录权限，"
            f"或改用可读写目录后重试。原始错误: {e}"
        )

def _extract_failed_output_path(error_output: str) -> Optional[str]:
    """从 7z/unrar 输出中提取无法写入的目标文件路径。"""
    for line in error_output.splitlines():
        if 'cannot open output file' not in line.lower():
            continue
        parts = [part.strip() for part in line.split(' : ') if part.strip()]
        if parts:
            return parts[-1]
    return None

def _build_friendly_extraction_error(error_output: str, extract_dir: str) -> str:
    """把常见命令行错误转成更可操作的前端提示。"""
    lowered = error_output.lower()
    if (
        'cannot open output file' in lowered
        and ('operation not permitted' in lowered or 'permission denied' in lowered or 'errno=1' in lowered or 'errno=13' in lowered)
    ):
        failed_path = _extract_failed_output_path(error_output) or extract_dir
        return (
            f"目标目录读写权限不足或已有文件无法覆盖: {failed_path}。"
            f"如果使用“解压到同名文件夹”，通常是旧的同名目录里已有不可覆盖文件；"
            f"请删除该同名文件夹、检查 Docker 挂载权限，或改用可写目录后重试。"
        )
    if 'no space left on device' in lowered:
        return f"目标磁盘空间不足: {extract_dir}。请清理空间后重试。"
    return f"解压失败: {error_output[:200]}"

def extract_with_password_dict(
    file_path: str,
    extract_dir: str,
    max_retries: int = 5,
    timeout_per_password: int = 15,
    task_id: Optional[str] = None
) -> Tuple[bool, str, Optional[str]]:
    """
    使用密码词典尝试解压
    每个密码有独立的超时控制
    返回: (成功, 消息, 使用的密码)
    """
    import time
    retry_count = 0
    full_extraction_timeout = timeout_settings.get('extraction_timeout', 300)
    
    # 检查缓存
    if file_path in PASSWORD_SUCCESS_CACHE:
        cached_pwd = PASSWORD_SUCCESS_CACHE[file_path]
        logger.info(f"尝试缓存密码: {file_path}")
        _update_task_message(
            task_id,
            file_path,
            f'需要密码，正在尝试缓存密码... (完整解压超时{full_extraction_timeout}秒)'
        )
        success, msg = extract_archive(
            file_path,
            extract_dir,
            cached_pwd,
            timeout=full_extraction_timeout,
            task_id=task_id
        )
        if success:
            return True, "解压成功 (缓存密码)", cached_pwd

        retry_count += 1
        if _is_password_failure_message(msg):
            logger.warning(f"缓存密码失效 {file_path}: {msg}，将尝试词典密码")
            PASSWORD_SUCCESS_CACHE.pop(file_path, None)
            save_password_cache()
            _update_task_message(
                task_id,
                file_path,
                _build_password_attempt_message(file_path, max_retries=max_retries)
            )
        else:
            return False, msg, cached_pwd
    
    # 记录密码词典大小
    dict_size = len(PASSWORD_DICT)
    if dict_size == 0:
        logger.warning(f"密码词典为空，无法尝试解压")
        return False, "密码词典为空", None
    
    logger.info(f"正在使用密码词典尝试 ({dict_size} 个密码, 每个超时 {timeout_per_password}秒)")
    use_full_timeout_directly = min(dict_size, max_retries) == 1
    if use_full_timeout_directly:
        logger.info(
            f"仅有 1 个密码候选，直接使用完整解压超时: {file_path} "
            f"({full_extraction_timeout}秒)"
        )
    
    # 尝试词典中的密码，每个密码有独立的超时
    total_start = time.time()
    for attempt in range(min(dict_size, max_retries)):
        if task_id and _wait_if_paused_or_stopped(task_id, file_path, '密码尝试已暂停，等待继续...'):
            return False, "已停止", None

        password = PASSWORD_DICT[attempt]
        attempt_start = time.time()
        
        try:
            # 每个密码尝试有独立的超时
            logger.debug(f"尝试密码 {attempt+1}/{min(dict_size, max_retries)}: {password}")
            attempt_timeout = full_extraction_timeout if use_full_timeout_directly else timeout_per_password
            success, msg = extract_archive(
                file_path,
                extract_dir,
                password,
                timeout=attempt_timeout,
                task_id=task_id
            )
            attempt_elapsed = time.time() - attempt_start
            
            if success:
                # 保存到缓存
                PASSWORD_SUCCESS_CACHE[file_path] = password
                save_password_cache()
                logger.info(f"✅ 成功解压 {file_path} (尝试 {attempt+1}, 耗时 {attempt_elapsed:.1f}s)")
                return True, "解压成功", password
            else:
                if (not use_full_timeout_directly and _is_timeout_message(msg)
                    and full_extraction_timeout > timeout_per_password):
                    logger.warning(
                        f"密码 {attempt+1} 短超时未完成 ({attempt_elapsed:.1f}s)，"
                        f"改用完整解压超时重试"
                    )
                    _update_task_message(
                        task_id,
                        file_path,
                        f'密码 {attempt+1} 短超时未完成，正在用完整解压超时重试... ({full_extraction_timeout}秒)'
                    )
                    success, retry_msg = extract_archive(
                        file_path,
                        extract_dir,
                        password,
                        timeout=full_extraction_timeout,
                        task_id=task_id
                    )
                    if success:
                        PASSWORD_SUCCESS_CACHE[file_path] = password
                        save_password_cache()
                        logger.info(f"✅ 成功解压 {file_path} (尝试 {attempt+1}, 长超时重试成功)")
                        return True, "解压成功", password
                    if not _is_password_failure_message(retry_msg):
                        return False, retry_msg, password
                    msg = retry_msg

                if _is_timeout_message(msg):
                    logger.warning(f"密码 {attempt+1} 尝试超时 ({attempt_elapsed:.1f}s)，继续尝试下一个")
                else:
                    logger.debug(f"密码 {attempt+1} 失败: {msg} ({attempt_elapsed:.1f}s)")
        except Exception as e:
            attempt_elapsed = time.time() - attempt_start
            logger.warning(f"密码 {attempt+1} 异常: {e} ({attempt_elapsed:.1f}s)")
            continue
        
        retry_count += 1
    
    total_elapsed = time.time() - total_start
    logger.error(f"❌ 所有密码都失败 {file_path} (尝试 {retry_count} 个，总耗时 {int(total_elapsed)}s)")
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

def _directory_has_child_directories(path: str) -> bool:
    """判断目录下是否还有可见子目录，用于前端目录树懒加载。"""
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        return True
                except (OSError, PermissionError):
                    continue
    except (OSError, PermissionError):
        return False
    return False

def _list_child_directories(root_dir: str, limit: int = 500) -> Tuple[List[Dict[str, object]], bool]:
    """列出直接子目录。返回 (目录列表, 是否被数量限制截断)。"""
    directories = []
    truncated = False
    try:
        with os.scandir(root_dir) as it:
            for entry in it:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    readable = os.access(entry.path, os.R_OK | os.X_OK)
                    directories.append({
                        'name': entry.name,
                        'path': entry.path,
                        'readable': readable,
                        'has_children': readable and _directory_has_child_directories(entry.path)
                    })
                    if len(directories) >= limit:
                        truncated = True
                        break
                except (OSError, PermissionError):
                    continue
    except (OSError, PermissionError) as e:
        logger.debug(f"列出子目录失败: {root_dir}, {e}")

    directories.sort(key=lambda item: str(item['name']).lower())
    return directories, truncated

def _normalize_scan_roots(raw_paths, fallback_path: str, recursive: bool) -> List[str]:
    """清洗扫描根目录；递归扫描时去掉被父目录覆盖的子目录。"""
    if isinstance(raw_paths, list):
        candidate_paths = raw_paths
    elif raw_paths:
        candidate_paths = [raw_paths]
    else:
        candidate_paths = [fallback_path]

    normalized = []
    seen = set()
    for item in candidate_paths:
        if not isinstance(item, str):
            continue
        path = item.strip()
        if not path:
            continue
        abs_path = os.path.abspath(path)
        if abs_path in seen:
            continue
        seen.add(abs_path)
        normalized.append((path, abs_path))

    if not normalized:
        return [fallback_path]

    if not recursive:
        return [path for path, _ in normalized]

    selected = []
    selected_abs = []
    for path, abs_path in sorted(normalized, key=lambda item: len(item[1])):
        covered = False
        for parent_abs in selected_abs:
            try:
                if os.path.commonpath([abs_path, parent_abs]) == parent_abs:
                    covered = True
                    break
            except ValueError:
                continue
        if not covered:
            selected.append(path)
            selected_abs.append(abs_path)

    return selected

def _validate_scan_root(root_dir: str) -> Optional[Tuple[str, int]]:
    """校验扫描目录。返回 (错误消息, HTTP 状态码) 或 None。"""
    root_path = Path(root_dir)
    if not root_path.exists():
        logger.error(f"目录不存在: {root_dir}")
        return f'目录不存在: {root_dir}', 400

    if not root_path.is_dir():
        logger.error(f"路径不是目录: {root_dir}")
        return f'路径不是目录: {root_dir}', 400

    if not os.access(root_dir, os.R_OK):
        logger.error(f"没有目录读取权限: {root_dir}")
        return f'没有目录读取权限: {root_dir}', 403

    return None

def _merge_subdirectory_stats(scan_roots: List[str], scan_mode: str) -> Dict[str, object]:
    """合并一个或多个扫描根目录的命中子目录统计。"""
    merged = {}
    multi_root = len(scan_roots) > 1

    for root_dir in scan_roots:
        for name, count in scan_subdirectories(root_dir, scan_mode=scan_mode).items():
            path = str(Path(root_dir) / name)
            key = path if multi_root else name
            merged[key] = {
                'name': name,
                'path': path,
                'count': count
            }

    return merged

def _is_archive_file(file_name: str, all_exts: Tuple[str, ...]) -> bool:
    name = file_name.lower()
    return name.endswith(all_exts)

def _split_filename_parts(file_path: str) -> Tuple[str, str]:
    """按多后缀拆分文件名和扩展名。"""
    name = Path(file_path).name
    suffixes = Path(file_path).suffixes
    extension = ''.join(suffixes)
    if extension:
        return name[:-len(extension)], extension
    return name, ''

def _archive_output_name(file_path: str) -> str:
    """生成同名解压目录名，兼容 tar.* 和多卷等多后缀格式。"""
    base_name, _ = _split_filename_parts(file_path)
    return base_name or Path(file_path).stem or Path(file_path).name

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

    # 更新扫描状态
    with scan_status_lock:
        scan_status['scanning'] = True
        scan_status['found_count'] = 0
        scan_status['current_path'] = root_dir
        scan_status['message'] = '开始扫描...'

    # 高性能扫描（使用 os.scandir 避免 rglob 大开销）
    for file_path in _iter_files(root_dir, recursive=recursive):
        try:
            if _is_archive_file(file_path, all_exts):
                archives.append(file_path)
                # 更新扫描状态
                with scan_status_lock:
                    scan_status['found_count'] = len(archives)
                    scan_status['current_path'] = file_path
                    scan_status['message'] = f'已发现 {len(archives)} 个压缩包'
        except (OSError, PermissionError):
            continue

    # 扫描完成
    with scan_status_lock:
        scan_status['scanning'] = False
        scan_status['message'] = f'扫描完成，共发现 {len(archives)} 个压缩包'

    return sorted(archives)

def find_all_files(root_dir: str, recursive: bool = True) -> List[str]:
    """扫描目录下所有普通文件。"""
    files = []
    root_path = Path(root_dir)

    if not root_path.exists():
        logger.error(f"目录不存在: {root_dir}")
        return files

    if not root_path.is_dir():
        logger.error(f"路径不是目录: {root_dir}")
        return files

    with scan_status_lock:
        scan_status['scanning'] = True
        scan_status['found_count'] = 0
        scan_status['current_path'] = root_dir
        scan_status['message'] = '开始扫描所有文件...'

    for file_path in _iter_files(root_dir, recursive=recursive):
        files.append(file_path)
        with scan_status_lock:
            scan_status['found_count'] = len(files)
            scan_status['current_path'] = file_path
            scan_status['message'] = f'已发现 {len(files)} 个文件'

    with scan_status_lock:
        scan_status['scanning'] = False
        scan_status['message'] = f'扫描完成，共发现 {len(files)} 个文件'

    return sorted(files)

def scan_subdirectories(root_dir: str, scan_mode: str = 'archives') -> Dict[str, int]:
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
                    item_count = 0
                    for file_path in _iter_files(str(item), recursive=True):
                        if scan_mode == 'all':
                            item_count += 1
                        else:
                            all_exts = SUPPORTED_ARCHIVE_EXTENSIONS + MULTIPART_EXTENSIONS
                            if _is_archive_file(file_path, all_exts):
                                item_count += 1

                    if item_count > 0:
                        subdir_stats[item.name] = item_count
            except (OSError, PermissionError) as e:
                logger.debug(f"无法访问子目录 {item}: {e}")
                continue
    except (OSError, PermissionError) as e:
        logger.warning(f"扫描子目录失败: {e}")

    return subdir_stats

def _next_task_id() -> str:
    """生成唯一任务 ID，避免多批次覆盖。"""
    global task_counter
    with task_counter_lock:
        task_counter += 1
        return f"task_{int(time.time() * 1000)}_{task_counter}"

def _wait_if_paused_or_stopped(task_id: str, archive_file: str, message: str) -> bool:
    """
    处理暂停/停止控制。
    返回 True 表示应当停止当前任务。
    """
    while True:
        with control_lock:
            should_stop = extraction_control['stop']
            is_paused = extraction_control['pause']

        if should_stop:
            with extraction_lock:
                current = extraction_status.get(task_id, {})
                extraction_status[task_id] = {
                    'status': 'stopped',
                    'file': archive_file,
                    'message': '已停止',
                    'extract_dir': current.get('extract_dir')
                }
            return True

        if not is_paused:
            return False

        with extraction_lock:
            current = extraction_status.get(task_id, {})
            extraction_status[task_id] = {
                'status': 'paused',
                'file': archive_file,
                'message': message,
                'extract_dir': current.get('extract_dir')
            }
        time.sleep(0.3)

def _get_archive_delete_targets(archive_file: str) -> List[str]:
    """获取压缩包删除目标，单文件返回自身，多卷返回整组文件。"""
    files_to_delete = [archive_file]
    first_volume = get_multipart_first_volume(archive_file)
    if first_volume:
        parent = Path(first_volume).parent
        try:
            archives_in_dir = [
                entry.path for entry in os.scandir(parent)
                if entry.is_file(follow_symlinks=False) and _is_archive_file(entry.path, SUPPORTED_ARCHIVE_EXTENSIONS + MULTIPART_EXTENSIONS)
            ]
        except (OSError, PermissionError) as e:
            logger.warning(f"读取多卷目录失败 {parent}: {e}")
            return files_to_delete
        multipart_groups, _ = group_multipart_archives(archives_in_dir)
        for group in multipart_groups:
            if os.path.normpath(group['first_volume']) == os.path.normpath(first_volume):
                files_to_delete = group['volumes']
                break

    return list(dict.fromkeys(files_to_delete))

def _delete_archive_files(archive_file: str) -> Tuple[List[str], List[Dict[str, str]]]:
    """按配置删除已成功解压的压缩包，支持多卷。"""
    deleted_files = []
    failed_files = []
    all_exts = SUPPORTED_ARCHIVE_EXTENSIONS + MULTIPART_EXTENSIONS
    files_to_delete = _get_archive_delete_targets(archive_file)

    for file_path in files_to_delete:
        try:
            if not os.path.exists(file_path):
                failed_files.append({'file': file_path, 'error': '文件不存在'})
                continue
            if not _is_archive_file(file_path, all_exts):
                failed_files.append({'file': file_path, 'error': '不是有效的压缩包文件'})
                continue
            os.remove(file_path)
            deleted_files.append(file_path)
            logger.info(f"自动删除压缩包: {file_path}")
        except Exception as e:
            failed_files.append({'file': file_path, 'error': str(e)})
            logger.warning(f"自动删除压缩包失败 {file_path}: {e}")

    return deleted_files, failed_files

def _update_task_message(task_id: Optional[str], archive_file: str, message: str) -> None:
    """更新进行中任务的提示文案。"""
    if not task_id:
        return
    with extraction_lock:
        current = extraction_status.get(task_id, {})
        extraction_status[task_id] = {
            'status': 'processing',
            'file': archive_file,
            'progress': current.get('progress', 0),
            'message': message,
            'extract_dir': current.get('extract_dir')
        }

def _update_task_progress(task_id: Optional[str], archive_file: str, progress: int, message: Optional[str] = None) -> None:
    """更新当前任务的解压百分比。"""
    if not task_id:
        return
    bounded_progress = max(0, min(100, int(progress)))
    with extraction_lock:
        current = extraction_status.get(task_id, {})
        extraction_status[task_id] = {
            'status': 'processing',
            'file': archive_file,
            'progress': bounded_progress,
            'message': message or current.get('message', '正在解压...'),
            'extract_dir': current.get('extract_dir')
        }

def _build_password_attempt_message(file_path: str, max_retries: int = 5) -> str:
    """根据当前密码尝试策略生成准确的前端提示。"""
    password_timeout = timeout_settings.get('password_timeout', 15)
    full_extraction_timeout = timeout_settings.get('extraction_timeout', 300)

    if file_path in PASSWORD_SUCCESS_CACHE:
        return f'需要密码，正在尝试缓存密码... (完整解压超时{full_extraction_timeout}秒)'

    candidate_count = min(len(PASSWORD_DICT), max_retries)
    if candidate_count == 0:
        return '需要密码，但密码词典为空'
    if candidate_count <= 1:
        return f'需要密码，正在尝试唯一密码... (完整解压超时{full_extraction_timeout}秒)'

    return (
        f'需要密码，正在尝试词典密码... '
        f'(每个密码{password_timeout}秒短超时，必要时自动改用完整解压)'
    )

def process_extraction_task(task_id: str, archive_file: str, extract_dir: str, extraction_limiter: AdjustableConcurrencyLimiter):
    """处理单个解压任务，支持暂停/继续/停止"""
    try:
        with extraction_lock:
            extraction_status[task_id] = {
                'status': 'queued',
                'file': archive_file,
                'progress': 0,
                'message': '排队中，等待并发空位...'
            }

        with extraction_limiter:
            if _wait_if_paused_or_stopped(task_id, archive_file, '任务已暂停，等待继续...'):
                return

            actual_extract_dir = extract_dir
            if extraction_options.get('extract_to_same_name', False) and extraction_options.get('extract_mode') != 'to_same_name':
                base_name = _archive_output_name(archive_file)
                actual_extract_dir = os.path.join(extract_dir, base_name)

            with extraction_lock:
                extraction_status[task_id] = {
                    'status': 'processing',
                    'file': archive_file,
                    'progress': 0,
                    'message': '准备解压目录...',
                    'extract_dir': actual_extract_dir
                }

            check_existing_tree = extraction_options.get('extract_mode') == 'to_same_name'
            dir_ok, dir_error = _ensure_read_write_directory(
                actual_extract_dir,
                check_existing_tree=check_existing_tree
            )
            if not dir_ok:
                with extraction_lock:
                    extraction_status[task_id] = {
                        'status': 'failed',
                        'file': archive_file,
                        'progress': 0,
                        'message': dir_error,
                        'extract_dir': actual_extract_dir
                    }
                logger.error(f"解压目录读写权限不足 {archive_file}: {dir_error}")
                return

            with extraction_lock:
                extraction_status[task_id] = {
                    'status': 'processing',
                    'file': archive_file,
                    'progress': 0,
                    'message': '检测加密状态...',
                    'extract_dir': actual_extract_dir
                }

            is_archive, is_encrypted = is_archive_encrypted(archive_file)

            if not is_archive:
                with extraction_lock:
                    extraction_status[task_id] = {
                        'status': 'failed',
                        'file': archive_file,
                        'message': '不是有效的压缩包',
                        'extract_dir': actual_extract_dir
                    }
                return

            if _wait_if_paused_or_stopped(task_id, archive_file, '检测完成，等待继续解压...'):
                return

            if is_encrypted:
                password_timeout = timeout_settings.get('password_timeout', 15)
                _update_task_message(
                    task_id,
                    archive_file,
                    _build_password_attempt_message(archive_file, max_retries=5)
                )

                success, msg, used_pwd = extract_with_password_dict(
                    archive_file,
                    actual_extract_dir,
                    max_retries=5,
                    timeout_per_password=password_timeout,
                    task_id=task_id
                )
                if success:
                    with extraction_lock:
                        extraction_status[task_id] = {
                            'status': 'success',
                            'file': archive_file,
                            'progress': 100,
                            'message': f"成功 (密码: {used_pwd})",
                            'password': used_pwd,
                            'extract_dir': actual_extract_dir
                        }
                    if extraction_options.get('auto_delete_success', False):
                        _delete_archive_files(archive_file)
                else:
                    final_status = 'stopped' if '已停止' in msg else 'failed'
                    current_progress = extraction_status.get(task_id, {}).get('progress', 0)
                    with extraction_lock:
                        extraction_status[task_id] = {
                            'status': final_status,
                            'file': archive_file,
                            'progress': current_progress,
                            'message': msg,
                            'extract_dir': actual_extract_dir
                        }
                    log_prefix = "密码解压失败" if (
                        _is_password_failure_message(msg) or '所有密码都失败' in msg
                    ) else "解压失败"
                    logger.error(f"{log_prefix} {archive_file}: {msg}")
            else:
                with extraction_lock:
                    extraction_status[task_id] = {
                        'status': 'processing',
                        'file': archive_file,
                        'progress': 0,
                        'message': '无加密，正在解压...',
                        'extract_dir': actual_extract_dir
                    }

                success, msg = extract_archive(archive_file, actual_extract_dir, task_id=task_id)
                if success:
                    with extraction_lock:
                        extraction_status[task_id] = {
                            'status': 'success',
                            'file': archive_file,
                            'progress': 100,
                            'message': '成功',
                            'extract_dir': actual_extract_dir
                        }
                    if extraction_options.get('auto_delete_success', False):
                        _delete_archive_files(archive_file)
                else:
                    final_status = 'stopped' if '已停止' in msg else 'failed'
                    current_progress = extraction_status.get(task_id, {}).get('progress', 0)
                    with extraction_lock:
                        extraction_status[task_id] = {
                            'status': final_status,
                            'file': archive_file,
                            'progress': current_progress,
                            'message': msg,
                            'extract_dir': actual_extract_dir
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
    return render_template(
        'index.html',
        app_version=APP_DISPLAY_VERSION,
        default_mount_path=DEFAULT_MOUNT_PATH,
        default_extract_path=os.path.join(DEFAULT_MOUNT_PATH, 'extracted')
    )

@app.route('/api/directories', methods=['POST'])
def list_directories():
    """列出目录树的一层子目录。"""
    data = request.get_json() or {}
    root_dir = data.get('path', DEFAULT_MOUNT_PATH)
    try:
        limit = int(data.get('limit', 500))
    except (TypeError, ValueError):
        limit = 500
    limit = max(1, min(limit, 1000))

    validation_error = _validate_scan_root(root_dir)
    if validation_error:
        message, status_code = validation_error
        return jsonify({'error': message}), status_code

    directories, truncated = _list_child_directories(root_dir, limit=limit)
    return jsonify({
        'path': root_dir,
        'name': Path(root_dir).name or root_dir,
        'parent': str(Path(root_dir).parent) if Path(root_dir).parent != Path(root_dir) else None,
        'directories': directories,
        'total': len(directories),
        'truncated': truncated
    })

@app.route('/api/scan', methods=['POST'])
def scan_directory():
    """扫描目录"""
    data = request.get_json() or {}
    root_dir = data.get('path', DEFAULT_MOUNT_PATH)
    selected_paths = data.get('paths')
    include_subdirs = data.get('include_subdirs', True)
    scan_mode = data.get('scan_mode', 'archives')

    if scan_mode not in {'archives', 'all'}:
        return jsonify({'error': 'scan_mode 仅支持 archives 或 all'}), 400

    scan_roots = _normalize_scan_roots(selected_paths, root_dir, include_subdirs)
    logger.info(
        f"开始扫描目录: {scan_roots} (包含子目录: {include_subdirs}, 模式: {scan_mode})"
    )

    for scan_root in scan_roots:
        validation_error = _validate_scan_root(scan_root)
        if validation_error:
            message, status_code = validation_error
            return jsonify({'error': message}), status_code
    
    try:
        scanned_files = []
        seen_files = set()
        scanner = find_all_files if scan_mode == 'all' else find_all_archives
        scan_label = '文件' if scan_mode == 'all' else '压缩包'
        for scan_root in scan_roots:
            for file_path in scanner(scan_root, recursive=include_subdirs):
                if file_path in seen_files:
                    continue
                seen_files.add(file_path)
                scanned_files.append(file_path)

        scanned_files = sorted(scanned_files)
        if scan_mode == 'all':
            logger.info(f"扫描完成，发现 {len(scanned_files)} 个文件 (子目录: {include_subdirs})")
        else:
            logger.info(f"扫描完成，发现 {len(scanned_files)} 个压缩包 (子目录: {include_subdirs})")

        with scan_status_lock:
            scan_status['scanning'] = False
            scan_status['found_count'] = len(scanned_files)
            scan_status['current_path'] = ', '.join(scan_roots[:3])
            scan_status['message'] = f'扫描完成，共发现 {len(scanned_files)} 个{scan_label}'
    except Exception as e:
        logger.error(f"扫描压缩包时出错: {e}")
        return jsonify({'error': f'扫描文件时出错: {str(e)}'}), 500
    
    try:
        if include_subdirs:
            subdir_stats = _merge_subdirectory_stats(scan_roots, scan_mode=scan_mode)
        else:
            subdir_stats = {}
    except Exception as e:
        logger.error(f"扫描子目录时出错: {e}")
        subdir_stats = {}

    if scan_mode == 'all':
        all_exts = SUPPORTED_ARCHIVE_EXTENSIONS + MULTIPART_EXTENSIONS
        result = []
        archive_count = 0
        for file_path in scanned_files:
            try:
                is_archive = _is_archive_file(file_path, all_exts)
                if is_archive:
                    archive_count += 1

                result.append({
                    'path': file_path,
                    'name': Path(file_path).name,
                    'size': Path(file_path).stat().st_size,
                    'encrypted': None,
                    'status': 'ready',
                    'cached': file_path in PASSWORD_SUCCESS_CACHE,
                    'is_multipart': False,
                    'is_archive': is_archive,
                    'item_type': 'archive' if is_archive else 'file'
                })
            except (OSError, PermissionError) as e:
                logger.warning(f"无法访问文件 {file_path}: {e}")
                continue

        return jsonify({
            'total': len(result),
            'archives': result,
            'multipart_count': 0,
            'single_count': len(result),
            'archive_count': archive_count,
            'scan_mode': scan_mode,
            'scan_roots': scan_roots,
            'subdirs_with_archives': subdir_stats if include_subdirs else {},
            'subdirs_count': len(subdir_stats) if include_subdirs else 0
        })

    multipart_groups, single_archives = group_multipart_archives(scanned_files)
    logger.info(f"发现 {len(multipart_groups)} 个多卷文件组，{len(single_archives)} 个单文件")

    single_result = []
    for archive in single_archives:
        try:
            if _is_detecting(archive):
                logger.debug(f"文件正在检测中，跳过: {archive}")
                continue

            cached_enc = _get_cached_encryption(archive)
            if cached_enc is None:
                _mark_detecting(archive, True)
                try:
                    is_arch, is_enc = is_archive_encrypted(archive)
                    if is_arch:
                        _set_cached_encryption(archive, is_enc)
                finally:
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
                'is_multipart': False,
                'is_archive': True,
                'item_type': 'archive'
            })
        except (OSError, PermissionError) as e:
            logger.warning(f"无法访问压缩包 {archive}: {e}")
            continue
        except Exception as e:
            logger.warning(f"处理压缩包 {archive} 时出错: {e}")
            continue

    multipart_result = []
    for group in multipart_groups:
        try:
            if _is_detecting(group['first_volume']):
                logger.debug(f"文件正在检测中，跳过: {group['first_volume']}")
                continue

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
                'path': group['first_volume'],
                'name': group['name'],
                'size': group['total_size'],
                'encrypted': is_enc if is_arch else None,
                'status': 'ready',
                'cached': group['first_volume'] in PASSWORD_SUCCESS_CACHE,
                'is_multipart': True,
                'volume_count': group['count'],
                'volumes': group['volumes'],
                'is_archive': True,
                'item_type': 'archive'
            })
        except Exception as e:
            logger.warning(f"处理多卷压缩包 {group['name']} 时出错: {e}")
            continue

    result = single_result + multipart_result

    return jsonify({
        'total': len(result),
        'archives': result,
        'multipart_count': len(multipart_result),
        'single_count': len(single_result),
        'archive_count': len(result),
        'scan_mode': scan_mode,
        'scan_roots': scan_roots,
        'subdirs_with_archives': subdir_stats if include_subdirs else {},
        'subdirs_count': len(subdir_stats) if include_subdirs else 0
    })

@app.route('/api/scan/status', methods=['GET'])
def get_scan_status():
    """获取扫描状态"""
    with scan_status_lock:
        return jsonify(scan_status)

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

        # 仅在确实需要指定目录时检查基目录，避免其他模式误触碰隐藏的 extracted 路径
        if extract_mode == 'to_specified':
            dir_ok, dir_error = _ensure_read_write_directory(extract_base)
            if not dir_ok:
                logger.error(f"解压目录读写权限不足 {extract_base}: {dir_error}")
                return jsonify({'error': dir_error}), 400

        with control_lock:
            extraction_control['stop'] = False

        tasks = {}
        task_extract_dirs = {}
        for i, archive in enumerate(archives):
            task_id = _next_task_id()
            tasks[task_id] = archive

            # 计算实际解压目录
            if extract_mode == 'to_current':
                extract_dir = str(Path(archive).parent)
            elif extract_mode == 'to_same_name':
                extract_dir = os.path.join(str(Path(archive).parent), _archive_output_name(archive))
            else:
                extract_dir = extract_base
            task_extract_dirs[task_id] = extract_dir

            thread = threading.Thread(
                target=process_extraction_task,
                args=(task_id, archive, extract_dir, extraction_concurrency_limiter)
            )
            thread.daemon = True
            thread.start()

        logger.info(f"启动解压: {len(archives)} 个文件，extract_mode={extract_mode}, 自动删除={auto_delete_success}")

        return jsonify({
            'extract_dir': extract_base,
            'task_count': len(tasks),
            'tasks': tasks,
            'extract_dirs': task_extract_dirs,
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
    limiter_state = extraction_concurrency_limiter.state()
    return jsonify({
        'password_dict_size': len(PASSWORD_DICT),
        'password_cache_size': len(PASSWORD_SUCCESS_CACHE),
        'default_mount': DEFAULT_MOUNT_PATH,
        'version': APP_DISPLAY_VERSION,
        'concurrent_count': limiter_state['limit'],
        'active_extractions': limiter_state['active'],
        'supported_formats': list(SUPPORTED_ARCHIVE_EXTENSIONS),
        'multipart_formats': ['.part1.7z', '.part1.rar', '.001', '.002']
    })

@app.route('/api/settings', methods=['GET', 'POST'])
def update_settings():
    """获取或更新并发设置"""
    if request.method == 'GET':
        limiter_state = extraction_concurrency_limiter.state()
        return jsonify({
            'concurrent_count': limiter_state['limit'],
            'active_extractions': limiter_state['active']
        })
    else:  # POST
        data = request.get_json(silent=True) or {}
        try:
            concurrent_count = int(data.get('concurrent_count', 1))
        except (TypeError, ValueError):
            return jsonify({'error': f'并发数必须在 1-{MAX_CONCURRENT_EXTRACTIONS} 之间'}), 400

        # 验证并发数
        if concurrent_count < 1 or concurrent_count > MAX_CONCURRENT_EXTRACTIONS:
            return jsonify({'error': f'并发数必须在 1-{MAX_CONCURRENT_EXTRACTIONS} 之间'}), 400

        with extraction_settings_lock:
            extraction_settings['concurrent_count'] = concurrent_count
            extraction_concurrency_limiter.set_limit(concurrent_count)
            limiter_state = extraction_concurrency_limiter.state()
        logger.info(f"并发数已实时更新为: {concurrent_count}")
        
        return jsonify({
            'success': True,
            'concurrent_count': limiter_state['limit'],
            'active_extractions': limiter_state['active']
        })

@app.route('/api/subdirs', methods=['POST'])
def scan_subdirs():
    """扫描子目录中的压缩包"""
    data = request.get_json() or {}
    root_dir = data.get('path', DEFAULT_MOUNT_PATH)
    scan_mode = data.get('scan_mode', 'archives')
    
    if not Path(root_dir).exists():
        return jsonify({'error': f'目录不存在: {root_dir}'}), 400
    
    subdir_stats = scan_subdirectories(root_dir, scan_mode=scan_mode)
    
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
        dict_path = PASSWORD_DICT_FILE
        dict_path.parent.mkdir(parents=True, exist_ok=True)
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

def _build_renamed_filename(path: str, mode: str, options: Dict, index: int, total: int) -> str:
    base_name, extension = _split_filename_parts(path)

    if mode == 'add':
        add_text = str(options.get('add_text', ''))
        if not add_text:
            raise APIError('追加内容不能为空')
        position = options.get('position', 'suffix')
        if position == 'prefix':
            new_base_name = f"{add_text}{base_name}"
        elif position == 'index':
            raw_index = options.get('insert_index', 1)
            try:
                insert_index = int(raw_index)
            except (TypeError, ValueError):
                raise APIError('插入位置必须是整数')
            insert_index = max(1, min(insert_index, len(base_name) + 1))
            insert_at = insert_index - 1
            new_base_name = f"{base_name[:insert_at]}{add_text}{base_name[insert_at:]}"
        else:
            new_base_name = f"{base_name}{add_text}"
        return f"{new_base_name}{extension}"

    if mode == 'replace':
        find_text = str(options.get('find_text', ''))
        replace_text = str(options.get('replace_text', ''))
        replace_scope = options.get('replace_scope', 'base_name')
        if not find_text:
            raise APIError('替换目标不能为空')

        if replace_scope == 'extension':
            target = extension
            replaced = target.replace(find_text, replace_text)
            return f"{base_name}{replaced}"

        if replace_scope == 'full_name':
            return Path(path).name.replace(find_text, replace_text)

        new_base_name = base_name.replace(find_text, replace_text)
        return f"{new_base_name}{extension}"

    raise APIError('不支持的重命名模式')

@app.route('/api/rename', methods=['POST'])
def rename_files():
    """批量重命名文件"""
    try:
        data = request.get_json() or {}
        files = data.get('files', [])
        mode = data.get('mode', '')
        options = data.get('options', {})

        if not isinstance(files, list) or not files:
            return jsonify({'error': '请至少选择一个文件'}), 400

        if mode not in {'add', 'replace'}:
            return jsonify({'error': '不支持的重命名模式'}), 400

        rename_plan = []
        seen_targets = set()

        for index, file_path in enumerate(files):
            path = Path(file_path)
            if not path.exists() or not path.is_file():
                return jsonify({'error': f'文件不存在或不可用: {file_path}'}), 400

            new_name = _build_renamed_filename(file_path, mode, options, index, len(files))
            if new_name == path.name:
                continue

            target_path = str(path.with_name(new_name))
            if target_path in seen_targets:
                return jsonify({'error': f'批量重命名结果冲突: {new_name}'}), 400

            if Path(target_path).exists() and Path(target_path) != path:
                return jsonify({'error': f'目标文件已存在: {target_path}'}), 400

            seen_targets.add(target_path)
            rename_plan.append((file_path, target_path))

        if not rename_plan:
            return jsonify({'success': True, 'message': '没有需要重命名的文件', 'renamed': [], 'count': 0})

        renamed = []
        for source, target in rename_plan:
            os.rename(source, target)
            renamed.append({
                'old_path': source,
                'new_path': target,
                'old_name': Path(source).name,
                'new_name': Path(target).name
            })
            logger.info(f"批量重命名: {source} -> {target}")

        return jsonify({
            'success': True,
            'message': f'已重命名 {len(renamed)} 个文件',
            'renamed': renamed,
            'count': len(renamed)
        })
    except APIError as e:
        return jsonify({'error': e.message}), e.status_code
    except Exception as e:
        logger.exception(f"批量重命名失败: {e}")
        return jsonify({'error': f'批量重命名失败: {str(e)}'}), 500

@app.route('/api/settings/timeouts', methods=['GET'])
def get_timeout_settings():
    """获取超时设置"""
    return jsonify(timeout_settings)

@app.route('/api/settings/timeouts', methods=['POST'])
def update_timeout_settings():
    """更新超时设置"""
    global timeout_settings
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': '无效的请求数据'}), 400
        
        # 验证并更新每个设置
        valid_keys = ['password_timeout', 'extraction_timeout', 'detection_7z_timeout', 
                     'detection_zip_timeout', 'detection_rar_timeout']
        
        for key in valid_keys:
            if key in data:
                value = data[key]
                # 验证是否为正整数
                if not isinstance(value, (int, float)) or value <= 0:
                    return jsonify({'error': f'{key} 必须是正数'}), 400
                timeout_settings[key] = int(value)
        
        return jsonify({'success': True, 'message': '超时设置已更新', 'settings': timeout_settings})
    
    except Exception as e:
        logger.error(f"更新超时设置失败: {e}")
        return jsonify({'error': str(e)}), 500

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
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': '无效的请求数据'}), 400

        files = data.get('files', [])
        if not files:
            return jsonify({'error': '没有指定要删除的文件'}), 400

        deleted_files = []
        failed_files = []
        seen_targets = set()
        all_exts = SUPPORTED_ARCHIVE_EXTENSIONS + MULTIPART_EXTENSIONS

        for file_path in files:
            try:
                if not isinstance(file_path, str) or not file_path.strip():
                    failed_files.append({'file': str(file_path), 'error': '无效的文件路径'})
                    continue

                file_path = file_path.strip()
                # 安全检查：确保文件确实存在且是我们期望的类型
                if not os.path.exists(file_path):
                    failed_files.append({'file': file_path, 'error': '文件不存在'})
                    continue

                # 检查是否是压缩包
                if not _is_archive_file(file_path, all_exts):
                    failed_files.append({'file': file_path, 'error': '不是有效的压缩包文件'})
                    continue

                for target_path in _get_archive_delete_targets(file_path):
                    normalized_target = os.path.normpath(target_path)
                    if normalized_target in seen_targets:
                        continue
                    seen_targets.add(normalized_target)

                    if not os.path.exists(target_path):
                        failed_files.append({'file': target_path, 'error': '文件不存在'})
                        continue
                    if not _is_archive_file(target_path, all_exts):
                        failed_files.append({'file': target_path, 'error': '不是有效的压缩包文件'})
                        continue

                    os.remove(target_path)
                    deleted_files.append(target_path)
                    logger.info(f"已删除成功解压的压缩包: {target_path}")
            except Exception as e:
                failed_files.append({'file': file_path, 'error': str(e)})
                logger.error(f"删除文件失败 {file_path}: {e}")

        deleted_count = len(deleted_files)
        failed_count = len(failed_files)
        if deleted_count and failed_count:
            message = f'已删除 {deleted_count} 个文件，{failed_count} 个失败'
        elif deleted_count:
            message = f'已删除 {deleted_count} 个压缩包文件'
        else:
            message = f'未删除任何文件，{failed_count} 个失败'

        return jsonify({
            'success': failed_count == 0 and deleted_count > 0,
            'message': message,
            'deleted': deleted_files,
            'failed': failed_files,
            'deleted_count': deleted_count,
            'failed_count': failed_count
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
            'version': APP_DISPLAY_VERSION,
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
                'active_tasks': sum(1 for s in extraction_status.values() if s.get('status') in {'queued', 'processing', 'paused'}),
                'total_completed': sum(1 for s in extraction_status.values() if s.get('status') == 'success')
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
    if 'TZ' in os.environ and hasattr(time, 'tzset'):
        time.tzset()

    log_path = LOG_FILE_PATH
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        log_path = Path.cwd() / 'fnos.log'

    file_handler = logging.FileHandler(log_path)
    file_handler.setLevel(logging.DEBUG)
    formatter = TimezoneFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    load_password_dict()
    load_password_cache()
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
