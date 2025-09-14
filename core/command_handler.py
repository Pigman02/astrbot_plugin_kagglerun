from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import logger
from typing import Optional

class CommandHandler:
    def __init__(self, kaggle_manager, config_manager, config):
        self.kaggle_manager = kaggle_manager
        self.config_manager = config_manager
        self.config = config

    @filter.command_group("kaggle")
    def kaggle_group(self):
        """Kaggle命令组"""
        pass

    @kaggle_group.command("")
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

    @kaggle_group.command("list")
    async def kaggle_list(self, event: AstrMessageEvent):
        """列出所有notebook"""
        if not self.config_manager.notebooks:
            yield event.plain_result("📝 还没有添加任何notebook")
            return
        
        message = "📋 Notebook列表:\n"
        for i, (name, path) in enumerate(self.config_manager.notebooks.items(), 1):
            message += f"{i}. {name} -> {path}\n"
        
        if self.config.default_notebook:
            message += f"\n默认notebook: {self.config.default_notebook}"
        
        yield event.plain_result(message)

    @kaggle_group.command("add")
    async def kaggle_add(self, event: AstrMessageEvent, name: str, path: str):
        """添加notebook"""
        if not self.is_admin_user(event.get_sender_id()):
            yield event.plain_result("❌ 需要管理员权限")
            return
        
        if self.config_manager.add_notebook(name, path):
            yield event.plain_result(f"✅ 已添加: {name} -> {path}")
        else:
            yield event.plain_result(f"❌ 名称 '{name}' 已存在")

    @kaggle_group.command("remove")
    async def kaggle_remove(self, event: AstrMessageEvent, name: str):
        """删除notebook"""
        if not self.is_admin_user(event.get_sender_id()):
            yield event.plain_result("❌ 需要管理员权限")
            return
        
        if self.config_manager.remove_notebook(name):
            yield event.plain_result(f"✅ 已删除: {name}")
        else:
            yield event.plain_result("❌ 未找到指定的notebook")

    @kaggle_group.command("run")
    async def kaggle_run(self, event: AstrMessageEvent, name: str = None):
        """运行notebook"""
        from utils.helpers import ensure_string
        name_str = ensure_string(name)
        
        if not name_str and self.config.default_notebook:
            name_str = self.config.default_notebook
        
        if not name_str:
            yield event.plain_result("❌ 请指定notebook名称或设置默认notebook")
            return
        
        notebook_info = self.config_manager.get_notebook(name_str)
        if not notebook_info:
            yield event.plain_result("❌ Notebook不存在")
            return
        
        notebook_name, notebook_path = notebook_info
        
        await event.send(event.plain_result("🚀 运行中..."))
        
        zip_path = await self.kaggle_manager.run_notebook(
            notebook_path, notebook_name, event.get_session_id()
        )
        
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

    # ... 其他命令方法保持不变，但使用模块化后的组件 ...

    def is_admin_user(self, user_id: str) -> bool:
        """检查用户是否是管理员"""
        return user_id in self.config.admin_users