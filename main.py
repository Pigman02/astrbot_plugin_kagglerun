import os
import json
import asyncio
import aiofiles
import aiohttp
import zipfile
import shutil
from typing import Dict, List, Set, Optional
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.utils.session_waiter import session_waiter, SessionController

class KagglePlugin(Star):
    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config = config
        self.active_sessions: Dict[str, datetime] = {}
        self.running_notebooks: Dict[str, str] = {}
        self.notebooks_file = Path("data/kaggle_notebooks.json")
        self.notebooks: Dict[str, str] = {}
        self.output_dir = Path(self.config.output_dir)
        self.cleanup_task = None
        self.setup_directories()
        self.setup_kaggle_api()
        self.load_notebooks()
        self.start_cleanup_task()

    # ... 其他方法保持不变 ...

    @filter.command("kaggle")
    async def kaggle_command(self, event: AstrMessageEvent, subcommand: str = ""):
        """Kaggle主命令 - 修复参数问题"""
        if not subcommand:
            yield event.plain_result("📋 /kaggle [list|add|remove|run|outputs|off|status]")
            return
        
        subcommand = subcommand.lower()
        
        # 获取完整的消息内容来解析参数
        full_message = event.message_str.strip()
        parts = full_message.split()
        
        if subcommand == "list":
            await self.list_notebooks(event)
        elif subcommand == "add" and len(parts) >= 3:
            await self.add_notebook(event, parts[2], ' '.join(parts[3:]))
        elif subcommand == "remove" and len(parts) >= 2:
            await self.remove_notebook(event, parts[2])
        elif subcommand == "run":
            notebook_name = parts[2] if len(parts) >= 3 else None
            await self.run_specific_notebook(event, notebook_name)
        elif subcommand == "outputs":
            await self.list_outputs(event)
        elif subcommand == "off":
            await self.stop_notebook(event)
        elif subcommand == "status":
            await self.show_status(event)
        else:
            yield event.plain_result("❌ 命令错误")

    async def add_notebook(self, event: AstrMessageEvent, name: str, path: str):
        """添加notebook"""
        if not self.is_admin_user(event.get_sender_id()):
            yield event.plain_result("❌ 需要管理员权限")
            return
        
        if not name or not path:
            yield event.plain_result("❌ 请提供名称和路径")
            return
        
        if name in self.notebooks:
            yield event.plain_result(f"❌ 名称 '{name}' 已存在")
            return
        
        self.notebooks[name] = path
        self.save_notebooks()
        yield event.plain_result(f"✅ 已添加: {name} -> {path}")

    async def remove_notebook(self, event: AstrMessageEvent, identifier: str):
        """删除notebook"""
        if not self.is_admin_user(event.get_sender_id()):
            yield event.plain_result("❌ 需要管理员权限")
            return
        
        if not identifier:
            yield event.plain_result("❌ 请提供notebook名称或序号")
            return
        
        notebook_info = self.get_notebook_by_index_or_name(identifier)
        if not notebook_info:
            yield event.plain_result("❌ 未找到指定的notebook")
            return
        
        name, _ = notebook_info
        del self.notebooks[name]
        self.save_notebooks()
        yield event.plain_result(f"✅ 已删除: {name}")

    # ... 其他方法保持不变 ...

@register("kaggle_runner", "AstrBot", "Kaggle Notebook执行插件", "1.0.0")
class KaggleRunner(KagglePlugin):
    pass
