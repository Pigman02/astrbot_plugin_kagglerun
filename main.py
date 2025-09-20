import os
import json
import asyncio
import zipfile
import shutil
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

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
        
        # 初始化
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
                await asyncio.sleep(300)

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

    def get_notebook_by_identifier(self, identifier: str) -> Optional[Tuple[str, str]]:
        """通过序号或名称获取notebook"""
        try:
            # 尝试按序号查找
            if identifier.isdigit():
                index = int(identifier) - 1
                notebooks_list = list(self.notebooks.items())
                if 0 <= index < len(notebooks_list):
                    return notebooks_list[index]
            
            # 尝试按名称查找
            if identifier in self.notebooks:
                return (identifier, self.notebooks[identifier])
            
            # 尝试模糊匹配
            for name, path in self.notebooks.items():
                if identifier.lower() in name.lower():
                    return (name, path)
            
            return None
        except Exception as e:
            logger.error(f"获取notebook失败: {e}")
            return None

    # ... (其他方法保持不变，如 stop_kaggle_notebook, download_and_package_output, validate_notebook_path, run_notebook 等)

    def is_admin_user(self, user_id: str) -> bool:
        """检查用户是否是管理员"""
        return user_id in self.config.admin_users

    def should_keep_running(self, message: str) -> bool:
        """检查消息中是否包含关键词"""
        message_lower = message.lower()
        return any(keyword.lower() in message_lower for keyword in self.config.keywords)

    # 修复命令注册部分
    @filter.command("kaggle")
    async def kaggle_main(self, event: AstrMessageEvent):
        """Kaggle主命令 - 显示帮助信息"""
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
            "/kaggle config - 查看配置\n"
            "/kaggle test - 测试API连接\n"
            "/kaggle check <路径> - 检查notebook状态"
        )

    @filter.command("kaggle test")
    async def kaggle_test(self, event: AstrMessageEvent):
        """测试Kaggle API连接"""
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
            api = KaggleApi()
            api.authenticate()
            
            # 测试列出notebooks
            kernels = api.kernels_list(page_size=5)
            if kernels:
                yield event.plain_result("✅ Kaggle API连接正常")
            else:
                yield event.plain_result("⚠️ API连接正常但未找到notebooks")
                
        except Exception as e:
            yield event.plain_result(f"❌ API连接失败: {str(e)}")

    @filter.command("kaggle check")
    async def kaggle_check(self, event: AstrMessageEvent, path: str):
        """检查notebook状态"""
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
            api = KaggleApi()
            api.authenticate()
            
            yield event.plain_result(f"🔍 检查notebook: {path}")
            
            # 首先检查路径格式
            if '/' not in path:
                yield event.plain_result("❌ Notebook路径格式错误，应为: username/slug")
                return
            
            # 检查notebook状态
            status = api.kernels_status(path)
            yield event.plain_result(f"📊 状态: {getattr(status, 'status', 'unknown')}")
            yield event.plain_result(f"📈 运行次数: {getattr(status, 'totalRunCount', 0)}")
            yield event.plain_result(f"⭐ 投票数: {getattr(status, 'totalVotes', 0)}")
            yield event.plain_result(f"🔗 链接: https://www.kaggle.com/{path}")
            
        except Exception as e:
            if "Not Found" in str(e) or "404" in str(e):
                yield event.plain_result(f"❌ Notebook不存在: {path}")
            elif "403" in str(e) or "Forbidden" in str(e):
                yield event.plain_result(f"❌ 访问被拒绝: {path}")
                yield event.plain_result("💡 可能的原因: 1.notebook不是公开的 2.API密钥权限不足 3.账号未验证邮箱")
            elif "Invalid folder" in str(e):
                yield event.plain_result(f"❌ Notebook路径无效: {path}")
                yield event.plain_result("💡 请确认用户名和slug是否正确")
            else:
                yield event.plain_result(f"❌ 检查失败: {str(e)}")

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

    @filter.command("kaggle add")
    async def kaggle_add(self, event: AstrMessageEvent, name: str, path: str):
        """添加notebook"""
        sender_id = getattr(event, 'sender_id', 'unknown')
        if not self.is_admin_user(sender_id):
            yield event.plain_result("❌ 需要管理员权限")
            return
        
        if name in self.notebooks:
            yield event.plain_result(f"❌ 名称 '{name}' 已存在")
            return
        
        # 验证notebook路径格式
        if '/' not in path:
            yield event.plain_result("❌ Notebook路径格式错误，应为: username/slug")
            return
        
        # 验证notebook路径是否有效
        yield event.plain_result("🔍 验证notebook路径...")
        
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
            api = KaggleApi()
            api.authenticate()
            
            # 尝试获取notebook信息来验证
            status = api.kernels_status(path)
            
            if status:
                self.notebooks[name] = path
                self.save_notebooks()
                yield event.plain_result(f"✅ 已添加: {name} -> {path}")
                yield event.plain_result(f"🔗 链接: https://www.kaggle.com/{path}")
            else:
                yield event.plain_result(f"❌ Notebook验证失败: {path}")
                
        except Exception as e:
            if "Not Found" in str(e) or "404" in str(e):
                yield event.plain_result(f"❌ Notebook不存在: {path}")
            elif "Invalid folder" in str(e):
                yield event.plain_result(f"❌ Notebook路径无效: {path}")
                yield event.plain_result("💡 请确认用户名和slug是否正确")
            else:
                yield event.plain_result(f"❌ 验证失败: {str(e)}")

    @filter.command("kaggle remove")
    async def kaggle_remove(self, event: AstrMessageEvent, name: str):
        """删除notebook"""
        sender_id = getattr(event, 'sender_id', 'unknown')
        if not self.is_admin_user(sender_id):
            yield event.plain_result("❌ 需要管理员权限")
            return
        
        # 尝试按名称删除
        if name in self.notebooks:
            del self.notebooks[name]
            self.save_notebooks()
            yield event.plain_result(f"✅ 已删除: {name}")
            return
        
        # 尝试按序号删除
        notebook_info = self.get_notebook_by_identifier(name)
        if notebook_info:
            notebook_name, _ = notebook_info
            del self.notebooks[notebook_name]
            self.save_notebooks()
            yield event.plain_result(f"✅ 已删除: {notebook_name}")
            return
        
        yield event.plain_result("❌ 未找到指定的notebook")

    @filter.command("kaggle run")
    async def kaggle_run(self, event: AstrMessageEvent, name: str = None):
        """运行notebook"""
        # 使用默认notebook如果未指定
        if not name and self.config.default_notebook:
            name = self.config.default_notebook
        
        if not name:
            yield event.plain_result("❌ 请指定notebook名称或设置默认notebook")
            return
        
        notebook_info = self.get_notebook_by_identifier(name)
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

    # 添加其他命令
    @filter.command("kaggle outputs")
    async def kaggle_outputs(self, event: AstrMessageEvent):
        """查看输出文件"""
        if not self.output_dir.exists():
            yield event.plain_result("📁 输出目录不存在")
            return
        
        files = list(self.output_dir.glob('*.zip'))
        if not files:
            yield event.plain_result("📁 暂无输出文件")
            return
        
        message = "📦 输出文件列表:\n"
        for i, file in enumerate(sorted(files, key=lambda x: x.stat().st_mtime, reverse=True)[:10], 1):
            file_time = datetime.fromtimestamp(file.stat().st_mtime)
            message += f"{i}. {file.name} ({file_time.strftime('%Y-%m-%d %H:%M')})\n"
        
        if len(files) > 10:
            message += f"\n... 还有 {len(files) - 10} 个文件"
        
        yield event.plain_result(message)

    @filter.command("kaggle status")
    async def kaggle_status(self, event: AstrMessageEvent):
        """查看状态"""
        status_msg = "📊 Kaggle插件状态:\n"
        status_msg += f"📝 已注册notebook: {len(self.notebooks)} 个\n"
        status_msg += f"📁 输出目录: {self.output_dir}\n"
        status_msg += f"🔄 运行中notebook: {len(self.running_notebooks)} 个\n"
        
        if self.running_notebooks:
            status_msg += "\n正在运行:\n"
            for session_id, notebook_name in self.running_notebooks.items():
                status_msg += f"  - {notebook_name}\n"
        
        yield event.plain_result(status_msg)

@register("kaggle_runner", "AstrBot", "Kaggle Notebook执行插件", "1.0.0")
class KaggleRunner(KagglePlugin):
    pass
