import os
import json
from pathlib import Path
from typing import Optional, Tuple, Any, Dict
from datetime import datetime, timedelta

def ensure_string(value: Any) -> str:
    """确保输入值为字符串"""
    if value is None:
        return ""
    return str(value)

def safe_isdigit(value: Any) -> bool:
    """安全检查是否为数字字符串"""
    if value is None:
        return False
    return str(value).isdigit()

def parse_identifier(identifier: Any, items: Dict[str, Any]) -> Optional[Tuple[str, Any]]:
    """解析标识符（序号或名称）"""
    identifier_str = ensure_string(identifier)
    
    if safe_isdigit(identifier_str):
        index = int(identifier_str) - 1
        items_list = list(items.items())
        if 0 <= index < len(items_list):
            return items_list[index]
    
    if identifier_str in items:
        return (identifier_str, items[identifier_str])
    
    # 模糊匹配
    for name, value in items.items():
        if identifier_str.lower() in name.lower():
            return (name, value)
    
    return None

def format_file_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f}GB"
    elif size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f}KB"
    else:
        return f"{size_bytes}B"