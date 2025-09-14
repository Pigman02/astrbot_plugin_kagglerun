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
        
        # 初始化方法调用
        self.setup_directories()
        self.setup_kaggle_api()
        self.load_notebooks()
        self.start_cleanup_task()

    def setup_directories(self):
        """设置输出目录"""
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"输出目录设置完成: {self.output_dir}")
        except Exception as e:
            logger.error(f"设置输出目录失败: {e}")

    def setup_kaggle_api(self):
        """设置Kaggle API配置"""
        try:
            kaggle_dir = os.path.expanduser('~/.kaggle')
            os.makedirs(kaggle_dir, exist_ok=True)
            
            kaggle_config = {
                "username": self.config.kaggle_username,
                "key": self.config.kaggle_api_key
            }
            
            config_path = os.path.join(kaggle_dir, 'kaggle.json')
            with open(config_path, 'w') as f:
                json.dump(kaggle_config, f)
            os.chmod(config_path, 0o600)
            
            logger.info("Kaggle API配置完成")
        except Exception as e:
            logger.error(f"Kaggle API配置失败: {e}")

    def start_cleanup_task(self):
        """启动清理任务"""
        self.cleanup_task = asyncio.create_task(self.cleanup_old_files())

    async def cleanup_old_files(self):
        """清理旧文件任务"""
        while True:
            try:
                await asyncio.sleep(3600)  # 每小时检查一次
                
                if not self.output_dir.exists():
                    continue
                    
                cutoff_time = datetime.now() - timedelta(days=self.config.retention_days)
                
                for file_path in self.output_dir.glob('*.zip'):
                    if file_path.is_file():
                        file_time = datetime.fromtimestamp(file_path.stat().st_mtime)
                        if file_time < cutoff_time:
                            file_path.unlink()
                            logger.info(f"已删除旧文件: {file_path.name}")
                            
            except asyncio.CancelledError:
                logger.info("清理任务已取消")
                break
            except Exception as e:
                logger.error(f"清理文件失败: {e}")
                await asyncio.sleep(300)  # 错误后等待5分钟

    def load_notebooks(self):
        """加载notebook列表"""
        try:
            if self.notebooks_file.exists():
                with open(self.notebooks_file, 'r', encoding='utf-8') as f:
                    self.notebooks = json.load(f)
                logger.info(f"已加载 {len(self.notebooks)} 个notebook")
            else:
                self.notebooks = {}
                self.save_notebooks()
        except Exception as e:
            logger.error(f"加载notebook列表失败: {e}")
            self.notebooks = {}

    def save_notebooks(self):
        """保存notebook列表"""
        try:
            self.notebooks_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.notebooks_file, 'w', encoding='utf-8') as f:
                json.dump(self.notebooks, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存notebook列表失败: {e}")

    async def stop_kaggle_notebook(self, notebook_path: str) -> bool:
        """强制停止运行的notebook"""
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
            api = KaggleApi()
            api.authenticate()
            
            if '/' not in notebook_path:
                return False
            
            username, slug = notebook_path.split('/', 1)
            
            # 获取运行中的kernels并停止匹配的
            kernels = api.kernels_list()
            for kernel in kernels:
                if kernel['ref'] == f"{username}/{slug}":
                    api.kernels_stop(kernel['id'])
                    return True
            
            return False
        except Exception as e:
            logger.error(f"停止notebook失败: {e}")
            return False

    async def download_and_package_output(self, notebook_path: str, notebook_name: str) -> Optional[Path]:
        """下载并打包输出文件"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_name = f"{timestamp}_{notebook_name}"
            
            from kaggle.api.kaggle_api_extended import KaggleApi
            api = KaggleApi()
            api.authenticate()
            
            if '/' not in notebook_path:
                return None
            
            username, slug = notebook_path.split('/', 1)
            
            # 创建临时目录并下载
            temp_dir = self.output_dir / "temp" / output_name
            temp_dir.mkdir(parents=True, exist_ok=True)
            api.kernels_output(f"{username}/{slug}", path=str(temp_dir))
            
            # 创建ZIP文件
            zip_filename = f"{output_name}.zip"
            zip_path = self.output_dir / zip_filename
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file in temp_dir.rglob('*'):
                    if file.is_file():
                        arcname = file.relative_to(temp_dir)
                        zipf.write(file, arcname)
            
            shutil.rmtree(temp_dir, ignore_errors=True)
            return zip_path
            
        except Exception as e:
            logger.error(f"打包输出文件失败: {e}")
            return None

    async def run_notebook(self, notebook_path: str, notebook_name: str, event: AstrMessageEvent = None) -> Optional[Path]:
        """运行notebook并返回输出文件路径"""
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
            api = KaggleApi()
            api.authenticate()
            
            # 记录运行中的notebook
            if event:
                session_id = event.get_session_id()
                self.running_notebooks[session_id] = notebook_name
            
            # 运行notebook
            result = api.kernels_push(notebook_path)
            
            if result.get('status') == 'ok':
                # 下载并打包输出
                zip_path = await self.download_and_package_output(notebook_path, notebook_name)
                
                # 清理运行记录
                if event:
                    session_id = event.get_session_id()
                    self.running_notebooks.pop(session_id, None)
                
                return zip_path
            else:
                return None
                
        except Exception as e:
            logger.error(f"运行Notebook失败: {e}")
            if event:
                session_id = event.get_session_id()
                self.running_notebooks.pop(session_id, None)
            return None

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

    def get_notebook_by_index_or_name(self, identifier: str) -> Optional[tuple]:
        """通过序号或名称获取notebook名称和路径"""
        if identifier.isdigit():
            index = int(identifier) - 1
            notebooks_list = list(self.notebooks.items())
            if 0 <= index < len(notebooks_list):
                return notebooks_list[index]
        
        if identifier in self.notebooks:
            return (identifier, self.notebooks[identifier])
        
        for name, path in self.notebooks.items():
            if identifier.lower() in name.lower():
                return (name, path)
        
        return None

    def is_admin_user(self, user_id: str) -> bool:
        """检查用户是否是管理员"""
        return user_id in self.config.admin_users

    def should_keep_running(self, message: str) -> bool:
        """检查消息中是否包含关键词"""
        message_lower = message.lower()
        return any(keyword.lower() in message_lower for keyword in self.config.keywords)

    async def auto_start_notebook(self, event: AstrMessageEvent):
        """自动启动默认notebook"""
        if not self.config.enable_auto_start or not self.config.default_notebook:
            return
        
        notebook_info = self.get_notebook_by_index_or_name(self.config.default_notebook)
        if not notebook_info:
            return
        
        notebook_name, notebook_path = notebook_info
        
        await event.send(event.plain_result("🔍 检测到关键词，启动中..."))
        
        zip_path = await self.run_notebook(notebook_path, notebook_name, event)
        
        if zip_path and self.config.send_to_group:
            try:
                from astrbot.api.message_components import File
                await event.send(event.chain_result([
                    File.fromFileSystem(str(zip_path), zip_path.name)
                ]))
            except Exception as e:
                logger.error(f"发送文件失败: {e}")

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """监听群消息，处理自动启动"""
        # 检查关键词自动启动
        if (self.config.enable_auto_start and 
            self.config.default_notebook and
            any(keyword in event.message_str for keyword in self.config.auto_start_keywords)):
            await self.auto_start_notebook(event)
        
        # 更新会话活动时间
        session_id = event.get_session_id()
        if session_id in self.active_sessions and self.should_keep_running(event.message_str):
            self.active_sessions[session_id] = datetime.now()

    async def terminate(self):
        """插件卸载时清理"""
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
        
        self.active_sessions.clear()
        self.running_notebooks.clear()
        logger.info("Kaggle插件已卸载")

@register("kaggle_runner", "AstrBot", "Kaggle Notebook执行插件", "1.0.0")
class KaggleRunner(KagglePlugin):
    pass
