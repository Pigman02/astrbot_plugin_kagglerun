import os
import json
import asyncio
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

class KagglePlugin(Star):
    def get_kaggle_api(self):
        """统一获取 KaggleApi 实例并认证"""
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
        except ImportError as e:
            logger.error(f"未安装kaggle库: {e}")
            raise
        api = KaggleApi()
        try:
            api.authenticate()
        except Exception as e:
            logger.error(f"Kaggle API认证失败: {e}")
            raise
        return api

    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config = config
        self.active_sessions: Dict[str, datetime] = {}
        self.running_notebooks: Dict[str, str] = {}
        # 修改存储路径为相对路径
        self.plugin_data_dir = Path("data/plugin_data/astrbot_plugin_kagglerun")
        self.notebooks_file = self.plugin_data_dir / "kaggle_notebooks.json"
        self.notebooks: Dict[str, str] = {}
        self.cleanup_task = None
        
        # 初始化
        self.setup_directories()
        self.setup_kaggle_api()
        self.load_notebooks()
        self.start_cleanup_task()

    def setup_directories(self):
        """设置目录"""
        try:
            self.plugin_data_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"插件目录设置完成: {self.plugin_data_dir}")
        except Exception as e:
            logger.error(f"设置目录失败: {e}")

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
        self.cleanup_task = asyncio.create_task(self.cleanup_old_sessions())

    async def cleanup_old_sessions(self):
        """清理旧会话任务"""
        while True:
            try:
                await asyncio.sleep(3600)  # 每小时检查一次
                
                cutoff_time = datetime.now() - timedelta(hours=24)
                expired_sessions = []
                
                for session_id, last_active in self.active_sessions.items():
                    if last_active < cutoff_time:
                        expired_sessions.append(session_id)
                
                for session_id in expired_sessions:
                    del self.active_sessions[session_id]
                    logger.info(f"已清理过期会话: {session_id}")
                    
            except asyncio.CancelledError:
                logger.info("清理任务已取消")
                break
            except Exception as e:
                logger.error(f"清理会话失败: {e}")
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

    def get_notebook_by_identifier(self, identifier) -> Optional[Tuple[str, str]]:
        """通过序号或名称获取notebook"""
        try:
            # 确保identifier是字符串类型
            identifier = str(identifier)
            
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

    async def stop_kaggle_notebook(self, notebook_path: str) -> bool:
        """强制停止运行的notebook"""
        try:
            api = self.get_kaggle_api()
            if '/' not in notebook_path:
                return False
            username, slug = notebook_path.split('/', 1)
            kernels = api.kernels_list()
            for kernel in kernels:
                kernel_ref = getattr(kernel, 'ref', '')
                if kernel_ref == f"{username}/{slug}":
                    kernel_id = getattr(kernel, 'id', '')
                    if kernel_id:
                        api.kernels_stop(kernel_id)
                        return True
            return False
        except Exception as e:
            logger.error(f"停止notebook失败: {e}")
            return False

    def validate_notebook_path(self, notebook_path: str) -> bool:
        """验证notebook路径是否有效"""
        try:
            api = self.get_kaggle_api()
            if '/' not in notebook_path:
                logger.error(f"Notebook路径格式错误: {notebook_path}")
                return False
            status = api.kernels_status(notebook_path)
            if status:
                logger.info(f"Notebook验证成功: {notebook_path}")
                return True
            else:
                logger.error(f"Notebook验证失败，返回空状态: {notebook_path}")
                return False
        except Exception as e:
            logger.error(f"验证notebook路径失败: {e}")
            return False

    async def run_notebook(self, notebook_path: str, notebook_name: str, event: AstrMessageEvent = None) -> bool:
        """远程启动notebook运行"""
        try:
            api = self.get_kaggle_api()
            
            if '/' not in notebook_path:
                logger.error(f"Invalid notebook path: {notebook_path}")
                if event:
                    await event.send(event.plain_result("❌ Notebook路径格式错误"))
                return False
                
            username, slug = notebook_path.split('/', 1)
            
            if event:
                await event.send(event.plain_result("🚀 正在远程启动notebook..."))
            
            # 使用KernelPushRequest触发notebook运行
            try:
                from kaggle.models.kernel_push_request import KernelPushRequest
                
                # 先拉取notebook的源代码
                if event:
                    await event.send(event.plain_result("📥 正在获取notebook代码..."))
                
                # 拉取notebook源码和metadata
                notebook_dir = f"/tmp/{slug}_notebook"
                os.makedirs(notebook_dir, exist_ok=True)
                api.kernels_pull(f"{username}/{slug}", path=notebook_dir, metadata=True)
                
                # 读取源码内容
                ipynb_path = os.path.join(notebook_dir, f"{slug}.ipynb")
                with open(ipynb_path, "r", encoding="utf-8") as f:
                    notebook_source = f.read()
                
                # 读取metadata，补全依赖数据集
                metadata_path = os.path.join(notebook_dir, "kernel-metadata.json")
                dataset_sources = []
                if os.path.exists(metadata_path):
                    with open(metadata_path, "r", encoding="utf-8") as f:
                        metadata = json.load(f)
                        dataset_sources = metadata.get("dataset_sources", [])
                
                # 创建推送请求，自动补全依赖
                kernel_push_request = KernelPushRequest(
                    slug=notebook_path,
                    text=notebook_source,
                    language="python",
                    kernel_type="notebook",
                    dataset_data_sources=dataset_sources,
                    enable_gpu=True,
                    enable_internet=True
                )
                push_result = api.kernel_push(kernel_push_request)
                logger.info(f"Notebook启动成功: {push_result}")
                
                if event:
                    await event.send(event.plain_result("✅ Notebook已启动运行"))
                    await event.send(event.plain_result("⏳ Kaggle将自动运行该notebook（最多30分钟）"))
                    await event.send(event.plain_result(f"🔗 查看运行状态: https://www.kaggle.com/{notebook_path}"))
                return True
                
            except Exception as e:
                logger.error(f"启动notebook失败: {e}")
                if event:
                    await event.send(event.plain_result(f"❌ 启动失败: {str(e)}"))
                return False
                
        except Exception as e:
            logger.error(f"运行Notebook失败: {e}")
            if event:
                await event.send(event.plain_result(f"❌ 运行失败: {str(e)}"))
            return False

    def should_keep_running(self, message: str) -> bool:
        """检查消息中是否包含关键词"""
        message_lower = message.lower()
        result = any(keyword.lower() in message_lower for keyword in self.config.keywords)
        logger.debug(f"检查消息是否包含关键词: {message} -> {result}")
        return result

    async def terminate(self):
        """插件卸载时调用"""
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass

    # 命令注册
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
            "/kaggle off - 停止运行\n"
            "/kaggle status - 查看状态\n"
            "/kaggle config - 查看配置\n"
            "/kaggle test - 测试API连接\n"
            "/kaggle check <路径> - 检查notebook状态"
        )

    @kaggle_group.command("test")
    async def kaggle_test(self, event: AstrMessageEvent):
        """测试Kaggle API连接"""
        try:
            api = self.get_kaggle_api()
            kernels = api.kernels_list(page_size=5)
            if kernels:
                yield event.plain_result("✅ Kaggle API连接正常")
                logger.info("Kaggle API连接测试成功")
            else:
                yield event.plain_result("⚠️ API连接正常但未找到notebooks")
                logger.warning("Kaggle API连接正常但未找到notebooks")
        except Exception as e:
            yield event.plain_result(f"❌ API连接失败: {str(e)}")
            logger.error(f"Kaggle API连接测试失败: {e}")

    @kaggle_group.command("check")
    async def kaggle_check(self, event: AstrMessageEvent, path: str):
        """检查notebook状态"""
        try:
            api = self.get_kaggle_api()
            yield event.plain_result(f"🔍 检查notebook: {path}")
            logger.info(f"检查notebook状态: {path}")
            if '/' not in path:
                yield event.plain_result("❌ Notebook路径格式错误，应为: username/slug")
                logger.error(f"Notebook路径格式错误: {path}")
                return
            status = api.kernels_status(path)
            status_str = getattr(status, 'status', 'unknown')
            run_count = getattr(status, 'totalRunCount', 0)
            votes = getattr(status, 'totalVotes', 0)
            yield event.plain_result(f"📊 状态: {status_str}")
            yield event.plain_result(f"📈 运行次数: {run_count}")
            yield event.plain_result(f"⭐ 投票数: {votes}")
            yield event.plain_result(f"🔗 链接: https://www.kaggle.com/{path}")
            logger.info(f"Notebook {path} 状态: {status_str}, 运行次数: {run_count}, 投票数: {votes}")
        except Exception as e:
            logger.error(f"检查notebook状态失败: {e}")
            if "Not Found" in str(e) or "404" in str(e):
                yield event.plain_result(f"❌ Notebook不存在: {path}")
                logger.error(f"Notebook不存在: {path}")
            elif "403" in str(e) or "Forbidden" in str(e):
                yield event.plain_result(f"❌ 访问被拒绝: {path}")
                yield event.plain_result("💡 可能的原因: 1.notebook不是公开的 2.API密钥权限不足 3.账号未验证邮箱")
                logger.error(f"访问被拒绝: {path}")
            elif "Invalid folder" in str(e):
                yield event.plain_result(f"❌ Notebook路径无效: {path}")
                yield event.plain_result("💡 请确认用户名和slug是否正确")
                logger.error(f"Notebook路径无效: {path}")
            else:
                yield event.plain_result(f"❌ 检查失败: {str(e)}")
                logger.error(f"检查notebook失败: {e}")

    @kaggle_group.command("list")
    async def kaggle_list(self, event: AstrMessageEvent):
        """列出所有notebook"""
        if not self.notebooks:
            yield event.plain_result("📝 还没有添加任何notebook")
            logger.info("Notebook列表为空")
            return
        
        message = "📋 Notebook列表:\n"
        for i, (name, path) in enumerate(self.notebooks.items(), 1):
            message += f"{i}. {name} -> {path}\n"
        
        if self.config.default_notebook:
            message += f"\n默认notebook: {self.config.default_notebook}"
        
        yield event.plain_result(message)
        logger.info(f"列出notebook列表，共{len(self.notebooks)}个")

    @kaggle_group.command("add")
    async def kaggle_add(self, event: AstrMessageEvent, name: str, path: str):
        """添加notebook"""
        if name in self.notebooks:
            yield event.plain_result(f"❌ 名称 '{name}' 已存在")
            logger.warning(f"尝试添加已存在的notebook名称: {name}")
            return
        
        # 验证notebook路径格式
        if '/' not in path:
            yield event.plain_result("❌ Notebook路径格式错误，应为: username/slug")
            logger.error(f"Notebook路径格式错误: {path}")
            return
        
        # 验证notebook路径是否有效
        yield event.plain_result("🔍 验证notebook路径...")
        logger.info(f"验证notebook路径: {path}")
        
        try:
            api = self.get_kaggle_api()
            status = api.kernels_status(path)
            if status:
                self.notebooks[name] = path
                self.save_notebooks()
                yield event.plain_result(f"✅ 已添加: {name} -> {path}")
                yield event.plain_result(f"🔗 链接: https://www.kaggle.com/{path}")
                logger.info(f"成功添加notebook: {name} -> {path}")
            else:
                yield event.plain_result(f"❌ Notebook验证失败: {path}")
                logger.error(f"Notebook验证失败: {path}")
        except Exception as e:
            logger.error(f"添加notebook失败: {e}")
            if "Not Found" in str(e) or "404" in str(e):
                yield event.plain_result(f"❌ Notebook不存在: {path}")
                logger.error(f"Notebook不存在: {path}")
            elif "Invalid folder" in str(e):
                yield event.plain_result(f"❌ Notebook路径无效: {path}")
                yield event.plain_result("💡 请确认用户名和slug是否正确")
                logger.error(f"Notebook路径无效: {path}")
            else:
                yield event.plain_result(f"❌ 验证失败: {str(e)}")
                logger.error(f"Notebook验证失败: {e}")

    @kaggle_group.command("remove")
    async def kaggle_remove(self, event: AstrMessageEvent, name: str):
        """删除notebook"""
        # 尝试按名称删除
        if name in self.notebooks:
            del self.notebooks[name]
            self.save_notebooks()
            yield event.plain_result(f"✅ 已删除: {name}")
            logger.info(f"成功删除notebook: {name}")
            return
        
        # 尝试按序号删除
        notebook_info = self.get_notebook_by_identifier(name)
        if notebook_info:
            notebook_name, _ = notebook_info
            del self.notebooks[notebook_name]
            self.save_notebooks()
            yield event.plain_result(f"✅ 已删除: {notebook_name}")
            logger.info(f"成功删除notebook: {notebook_name}")
            return
        
        yield event.plain_result("❌ 未找到指定的notebook")
        logger.warning(f"尝试删除不存在的notebook: {name}")

    @kaggle_group.command("run")
    async def kaggle_run(self, event: AstrMessageEvent, name: str = None):
        """运行notebook"""
        # 使用默认notebook如果未指定
        if not name and self.config.default_notebook:
            name = self.config.default_notebook
        
        if not name:
            yield event.plain_result("❌ 请指定notebook名称或设置默认notebook")
            logger.warning("未指定notebook名称且无默认notebook")
            return
        
        notebook_info = self.get_notebook_by_identifier(name)
        if not notebook_info:
            yield event.plain_result("❌ Notebook不存在")
            logger.warning(f"尝试运行不存在的notebook: {name}")
            return
        
        notebook_name, notebook_path = notebook_info
        logger.info(f"开始运行notebook: {notebook_name} ({notebook_path})")
        
        success = await self.run_notebook(notebook_path, notebook_name, event)
        
        if success:
            logger.info(f"Notebook启动成功: {notebook_name}")
        else:
            logger.error(f"Notebook启动失败: {notebook_name}")

@register("kaggle_runner", "AstrBot", "Kaggle Notebook执行插件", "1.0.0")
class KaggleRunner(KagglePlugin):
    pass
