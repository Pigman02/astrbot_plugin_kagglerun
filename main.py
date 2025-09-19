from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.config import AstrBotConfig
import asyncio
import aiohttp
import json
import os
import base64
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

@dataclass
class KaggleNotebook:
    name: str
    path: str
    last_run: Optional[str] = None
    status: str = "idle"

@register("kaggle_manager", "AstrBot", "Kaggle Notebook管理插件", "1.0.0")
class KaggleManagerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.kaggle_data_path = Path("data/kaggle_data.json")
        self.notebooks: Dict[str, KaggleNotebook] = {}
        self.ensure_data_directory()
        self.load_notebooks()

    def ensure_data_directory(self):
        """确保数据目录存在"""
        os.makedirs("data", exist_ok=True)

    def load_notebooks(self):
        """加载Notebook数据"""
        if self.kaggle_data_path.exists():
            try:
                with open(self.kaggle_data_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for nb_data in data.get('notebooks', []):
                        nb = KaggleNotebook(**nb_data)
                        self.notebooks[nb.name] = nb
            except Exception as e:
                logger.error(f"加载Kaggle数据失败: {e}")

    def save_notebooks(self):
        """保存Notebook数据"""
        try:
            data = {
                'notebooks': [
                    {
                        'name': nb.name,
                        'path': nb.path,
                        'last_run': nb.last_run,
                        'status': nb.status
                    }
                    for nb in self.notebooks.values()
                ]
            }
            with open(self.kaggle_data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存Kaggle数据失败: {e}")

    def get_kaggle_auth(self) -> Optional[aiohttp.BasicAuth]:
        """获取Kaggle认证信息"""
        username = self.config.get('kaggle_username', '')
        key = self.config.get('kaggle_key', '')
        if username and key:
            return aiohttp.BasicAuth(username, key)
        return None

    @filter.command("kaggle run")
    async def kaggle_run(self, event: AstrMessageEvent, notebook_name: str):
        """运行Kaggle Notebook"""
        if not await self.check_admin(event):
            yield event.plain_result("❌ 需要管理员权限才能执行此操作")
            return

        if notebook_name not in self.notebooks:
            yield event.plain_result(f"❌ 未找到Notebook: {notebook_name}")
            return

        notebook = self.notebooks[notebook_name]
        auth = self.get_kaggle_auth()
        
        if not auth:
            yield event.plain_result("❌ 请先在Web UI中配置Kaggle API信息")
            return

        yield event.plain_result(f"🚀 开始执行Notebook: {notebook_name}")

        try:
            result = await self.execute_kaggle_notebook(notebook, auth)
            notebook.last_run = result.get('run_id', 'unknown')
            notebook.status = result.get('status', 'completed')
            self.save_notebooks()
            
            yield event.plain_result(f"✅ 执行完成！\n运行ID: {result.get('run_id')}\n状态: {result.get('status')}")
        except Exception as e:
            logger.error(f"执行Notebook失败: {e}")
            notebook.status = "failed"
            self.save_notebooks()
            yield event.plain_result(f"❌ 执行失败: {str(e)}")

    @filter.command("kaggle add")
    async def kaggle_add(self, event: AstrMessageEvent, path: str):
        """添加Kaggle Notebook路径"""
        if not await self.check_admin(event):
            yield event.plain_result("❌ 需要管理员权限才能执行此操作")
            return

        # 验证路径格式
        if path.count('/') != 1:
            yield event.plain_result("❌ 路径格式错误，请使用: username/notebookname")
            return

        username, notebookname = path.split('/')
        notebook_key = f"{username}_{notebookname}"

        if notebook_key in self.notebooks:
            yield event.plain_result(f"❌ Notebook已存在: {notebook_key}")
            return

        # 验证Notebook是否存在
        auth = self.get_kaggle_auth()
        if auth:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f'https://www.kaggle.com/api/v1/kernels/{path}',
                        auth=auth
                    ) as resp:
                        if resp.status != 200:
                            yield event.plain_result(f"❌ Notebook不存在或无法访问: {path}")
                            return
            except Exception as e:
                yield event.plain_result(f"❌ 验证Notebook失败: {str(e)}")
                return

        # 添加Notebook
        self.notebooks[notebook_key] = KaggleNotebook(
            name=notebook_key,
            path=path
        )
        self.save_notebooks()

        yield event.plain_result(f"✅ 已添加Notebook: {notebook_key} -> {path}")

    @filter.command("kaggle list")
    async def kaggle_list(self, event: AstrMessageEvent):
        """列出所有已添加的Notebook"""
        if not self.notebooks:
            yield event.plain_result("📝 暂无已添加的Notebook")
            return

        result = "📋 已添加的Notebooks:\n"
        for name, notebook in self.notebooks.items():
            status_emoji = "🟢" if notebook.status == "completed" else "🔴" if notebook.status == "failed" else "🟡"
            result += f"{status_emoji} {name} -> {notebook.path}"
            if notebook.last_run:
                result += f" (最后运行: {notebook.last_run})"
            result += "\n"

        yield event.plain_result(result)

    @filter.command("kaggle remove")
    async def kaggle_remove(self, event: AstrMessageEvent, notebook_name: str):
        """移除Notebook"""
        if not await self.check_admin(event):
            yield event.plain_result("❌ 需要管理员权限才能执行此操作")
            return

        if notebook_name not in self.notebooks:
            yield event.plain_result(f"❌ 未找到Notebook: {notebook_name}")
            return

        removed_notebook = self.notebooks.pop(notebook_name)
        self.save_notebooks()

        yield event.plain_result(f"✅ 已移除Notebook: {notebook_name} -> {removed_notebook.path}")

    @filter.command("kaggle status")
    async def kaggle_status(self, event: AstrMessageEvent, notebook_name: str):
        """查看Notebook状态"""
        if notebook_name not in self.notebooks:
            yield event.plain_result(f"❌ 未找到Notebook: {notebook_name}")
            return

        notebook = self.notebooks[notebook_name]
        auth = self.get_kaggle_auth()
        
        if not auth:
            yield event.plain_result("❌ 请先在Web UI中配置Kaggle API信息")
            return

        try:
            status = await self.get_notebook_status(notebook, auth)
            yield event.plain_result(f"📊 Notebook状态: {notebook_name}\n状态: {status.get('status', 'unknown')}\n最后运行: {status.get('last_run', '从未运行')}")
        except Exception as e:
            yield event.plain_result(f"❌ 获取状态失败: {str(e)}")

    async def execute_kaggle_notebook(self, notebook: KaggleNotebook, auth: aiohttp.BasicAuth) -> Dict[str, Any]:
        """执行Kaggle Notebook"""
        timeout = self.config.get('default_timeout', 300)
        
        async with aiohttp.ClientSession() as session:
            # 启动Notebook运行
            async with session.post(
                f'https://www.kaggle.com/api/v1/kernels/{notebook.path}/run',
                auth=auth,
                json={},
                timeout=timeout
            ) as resp:
                if resp.status != 200:
                    raise Exception(f"API请求失败: {resp.status}")
                
                result = await resp.json()
                return result

    async def get_notebook_status(self, notebook: KaggleNotebook, auth: aiohttp.BasicAuth) -> Dict[str, Any]:
        """获取Notebook状态"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f'https://www.kaggle.com/api/v1/kernels/{notebook.path}/status',
                auth=auth
            ) as resp:
                if resp.status != 200:
                    raise Exception(f"状态查询失败: {resp.status}")
                
                return await resp.json()

    async def check_admin(self, event: AstrMessageEvent) -> bool:
        """检查是否为管理员"""
        return event.is_admin()

    async def terminate(self):
        """插件卸载时的清理工作"""
        logger.info("KaggleManagerPlugin 正在卸载")

# 创建requirements.txt文件
"""
aiohttp>=3.8.0
"""
