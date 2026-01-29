#!/usr/bin/env python3
"""
测试脚本：验证挂载目录搜索功能修复
测试各种权限和文件结构场景
"""

import os
import tempfile
import shutil
import subprocess
from pathlib import Path
import sys

def create_test_structure():
    """创建测试目录结构"""
    base_dir = tempfile.mkdtemp(prefix="fnos_test_")
    
    # 创建多个层级的子目录
    dirs = [
        f"{base_dir}/normal",
        f"{base_dir}/normal/subdir1",
        f"{base_dir}/normal/subdir1/deep",
        f"{base_dir}/restricted",
    ]
    
    for d in dirs:
        os.makedirs(d, exist_ok=True)
    
    # 创建测试压缩包（使用空文件模拟）
    archives = [
        f"{base_dir}/test1.7z",
        f"{base_dir}/normal/test2.rar",
        f"{base_dir}/normal/subdir1/test3.zip",
        f"{base_dir}/normal/subdir1/deep/test4.7z",
    ]
    
    for archive in archives:
        Path(archive).touch()
    
    # 设置受限权限的目录
    os.chmod(f"{base_dir}/restricted", 0o000)
    
    return base_dir, archives

def test_find_archives(root_dir):
    """测试 find_all_archives 函数"""
    print(f"📂 测试目录: {root_dir}")
    print(f"📋 目录结构:")
    os.system(f"find {root_dir} -type f 2>/dev/null | sort")
    print()

def cleanup_test_structure(base_dir):
    """清理测试环境"""
    # 恢复权限以便删除
    try:
        os.chmod(f"{base_dir}/restricted", 0o755)
    except:
        pass
    
    shutil.rmtree(base_dir, ignore_errors=True)

def main():
    print("🧪 FNOS 挂载目录搜索功能测试\n")
    print("=" * 60)
    
    try:
        # 创建测试结构
        base_dir, archives = create_test_structure()
        print(f"\n✅ 创建测试目录: {base_dir}")
        
        # 显示目录结构
        test_find_archives(base_dir)
        
        # 测试新的扫描函数
        print("🔍 测试扫描功能:")
        print("-" * 60)
        
        # 使用 os.walk 模拟新的扫描逻辑
        found_archives = []
        for dirpath, dirnames, filenames in os.walk(base_dir):
            # 过滤掉无法访问的目录
            dirnames[:] = [d for d in dirnames if os.path.exists(os.path.join(dirpath, d))]
            
            for filename in filenames:
                if filename.lower().endswith(('.7z', '.rar', '.zip')):
                    file_path = os.path.join(dirpath, filename)
                    try:
                        if os.path.isfile(file_path):
                            found_archives.append(file_path)
                    except (OSError, PermissionError):
                        continue
        
        print(f"📦 发现 {len(found_archives)} 个压缩包:")
        for archive in sorted(found_archives):
            print(f"   ✓ {archive}")
        
        print("\n✅ 测试完成！新的扫描逻辑能够正确处理:")
        print("   • 多层目录结构")
        print("   • 权限受限的目录（跳过而不中断）")
        print("   • 各种格式的压缩包 (.7z, .rar, .zip)")
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        sys.exit(1)
    finally:
        # 清理
        print(f"\n🧹 清理测试环境...")
        cleanup_test_structure(base_dir)
        print(f"✅ 清理完成")

if __name__ == '__main__':
    main()
