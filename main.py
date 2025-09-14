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

    # 主命令 - 只显示帮助信息
    @filter.command("kaggle")
    async def kaggle_main(self, event: AstrMessageEvent):
        """Kaggle主命令"""
        yield event.plain_result(
            "📋 Kaggle Notebook管理器\n\n"
            "可用命令:\n"
            "/kaggle list - 查看可用notebook\n"
            "/kaggle add <名称> <路径> - 添加notebook\n"
            "/kaggle remove <名称> - 删除notebook\n"
            "/kaggle run [名称] - 运行notebook\n"
            "/kaggle outputs - 查看输出文件\n"
            "/kaggle off - 停止运行\n"
            "/kaggle status - 查看状态\n"
            "/kaggle config - 查看配置"
        )

    # 列出notebook
    @filter.command("kaggle list")
    async def kaggle_list(self, event: AstrMessageEvent):
        """列出所有notebook"""
        if not self.notebooks:
            yield event.plain_result("📝 还没有添加任何notebook")
            return
        
        message = "📋 Notebook列表:\n"
        for i, (name, path) in enumerate(self.notebooks.items(), 1):
            message += f"{i}. {name} -> {path}\n"
        
        if self.config.default_notebook:
            message += f"\n默认notebook: {self.config.default_notebook}"
        
        yield event.plain_result(message)

    # 添加notebook
    @filter.command("kaggle add")
    async def kaggle_add(self, event: AstrMessageEvent, name: str, path: str):
        """添加notebook"""
        if not self.is_admin_user(event.get_sender_id()):
            yield event.plain_result("❌ 需要管理员权限")
            return
        
        if name in self.notebooks:
            yield event.plain_result(f"❌ 名称 '{name}' 已存在")
            return
        
        self.notebooks[name] = path
        self.save_notebooks()
        yield event.plain_result(f"✅ 已添加: {name} -> {path}")

    # 删除notebook
    @filter.command("kaggle remove")
    async def kaggle_remove(self, event: AstrMessageEvent, name: str):
        """删除notebook"""
        if not self.is_admin_user(event.get_sender_id()):
            yield event.plain_result("❌ 需要管理员权限")
            return
        
        if name not in self.notebooks:
            # 尝试按序号删除
            if name.isdigit():
                index = int(name) - 1
                notebooks_list = list(self.notebooks.items())
                if 0 <= index < len(notebooks_list):
                    name, path = notebooks_list[index]
                    del self.notebooks[name]
                    self.save_notebooks()
                    yield event.plain_result(f"✅ 已删除: {name}")
                    return
            
            yield event.plain_result("❌ 未找到指定的notebook")
            return
        
        del self.notebooks[name]
        self.save_notebooks()
        yield event.plain_result(f"✅ 已删除: {name}")

    # 运行notebook
    @filter.command("kaggle run")
    async def kaggle_run(self, event: AstrMessageEvent, name: str = None):
        """运行notebook"""
        if not name and self.config.default_notebook:
            name = self.config.default_notebook
        
        if not name:
            yield event.plain_result("❌ 请指定notebook名称或设置默认notebook")
            return
        
        notebook_info = self.get_notebook_by_index_or_name(name)
        if not notebook_info:
            yield event.plain_result("❌ Notebook不存在")
            return
        
        notebook_name, notebook_path = notebook_info
        
        await event.send(event.plain_result("🚀 运行中..."))
        
        zip_path = await self.run_notebook(notebook_path, notebook_name, event)
        
        if zip_path and self.config.send_to_group:
            try:
                from astrbot.api.message_components import File
                await event.send(event.chain_result([
                    File.fromFileSystem(str(zip_path), zip_path.name)
                ]))
            except Exception as e:
                logger.error(f"发送文件失败: {e}")
                yield event.plain_result(f"📦 完成: {zip_path.name}")
        elif zip_path:
            yield event.plain_result(f"📦 完成: {zip_path.name}")
        else:
            yield event.plain_result("❌ 运行失败")

    # 查看输出文件
    @filter.command("kaggle outputs")
    async def kaggle_outputs(self, event: AstrMessageEvent):
        """查看输出文件"""
        files = list(self.output_dir.glob('*.zip'))
        if not files:
            yield event.plain_result("📭 还没有输出文件")
            return
        
        files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        
        message = "📦 输出文件列表:\n\n"
        for i, file_path in enumerate(files[:5], 1):
            file_time = datetime.fromtimestamp(file_path.stat().st_mtime)
            file_size = file_path.stat().st_size / (1024 * 1024)
            days_old = (datetime.now() - file_time).days
            remaining_days = max(0, self.config.retention_days - days_old)
            
            message += f"{i}. {file_path.name}\n"
            message += f"   大小: {file_size:.1f}MB | 创建: {file_time.strftime('%m-%d %H:%M')}\n"
            message += f"   剩余: {remaining_days}天\n\n"
        
        yield event.plain_result(message)

    # 停止运行
    @filter.command("kaggle off")
    async def kaggle_off(self, event: AstrMessageEvent):
        """停止运行"""
        session_id = event.get_session_id()
        notebook_name = self.running_notebooks.get(session_id)
        
        if not notebook_name:
            yield event.plain_result("❌ 没有正在运行的notebook")
            return
        
        notebook_info = self.get_notebook_by_index_or_name(notebook_name)
        if not notebook_info:
            yield event.plain_result("❌ 找不到notebook信息")
            return
        
        _, notebook_path = notebook_info
        
        if await self.stop_kaggle_notebook(notebook_path):
            self.running_notebooks.pop(session_id, None)
            yield event.plain_result("⏹️ 已停止运行")
        else:
            yield event.plain_result("❌ 停止失败")

    # 查看状态
    @filter.command("kaggle status")
    async def kaggle_status(self, event: AstrMessageEvent):
        """查看状态"""
        session_id = event.get_session_id()
        running = self.running_notebooks.get(session_id)
        
        message = "⚡ 当前状态:\n"
        message += f"• 运行中: {'✅' if running else '❌'}"
        
        if running:
            message += f" - {running}"
        
        message += f"\n• 默认notebook: {self.config.default_notebook or '未设置'}"
        message += f"\n• 总notebook数: {len(self.notebooks)}"
        message += f"\n• 输出文件数: {len(list(self.output_dir.glob('*.zip')))}"
        
        yield event.plain_result(message)

    # 查看配置
    @filter.command("kaggle config")
    async def kaggle_config(self, event: AstrMessageEvent):
        """查看配置"""
        config_info = (
            f"⚙️ 当前配置:\n"
            f"• 自动发送文件: {'✅' if self.config.send_to_group else '❌'}\n"
            f"• 文件保留天数: {self.config.retention_days}天\n"
            f"• 自动启动: {'✅' if self.config.enable_auto_start else '❌'}\n"
            f"• 超时时间: {self.config.timeout_minutes}分钟\n"
            f"• 白名单群组: {len(self.config.whitelist_groups)}个\n"
            f"• 管理员用户: {len(self.config.admin_users)}个"
        )
        yield event.plain_result(config_info)

    # ... 其他方法保持不变 ...

@register("kaggle_runner", "AstrBot", "Kaggle Notebook执行插件", "1.0.0")
class KaggleRunner(KagglePlugin):
    pass
