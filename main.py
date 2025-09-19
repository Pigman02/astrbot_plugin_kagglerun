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
        
        # 所有数据存储在data目录下
        self.data_dir = Path("data/kaggle_plugin")
        self.notebooks_file = self.data_dir / "kaggle_notebooks.json"
        self.output_dir = self.data_dir / "outputs"
        self.downloads_dir = self.data_dir / "downloads"
        
        self.notebooks: Dict[str, str] = {}
        self.cleanup_task = None
        
        # 初始化
        self.setup_directories()
        self.setup_kaggle_api()
        self.load_notebooks()
        self.start_cleanup_task()

    def setup_directories(self):
        """设置所有必要的目录"""
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.downloads_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"数据目录设置完成: {self.data_dir}")
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

    async def download_and_package_output(self, notebook_path: str, notebook_name: str) -> Optional[Path]:
        """下载并打包输出文件"""
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
            api = KaggleApi()
            api.authenticate()
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_name = f"{timestamp}_{notebook_name.replace(' ', '_')}"
            
            if '/' not in notebook_path:
                logger.error(f"Invalid notebook path: {notebook_path}")
                return None
            
            username, slug = notebook_path.split('/', 1)
            
            # 创建临时目录
            temp_dir = self.downloads_dir / output_name
            temp_dir.mkdir(parents=True, exist_ok=True)
            
            logger.info(f"Downloading output for: {username}/{slug} to {temp_dir}")
            
            # 下载输出文件
            try:
                api.kernels_output(f"{username}/{slug}", path=str(temp_dir))
            except Exception as e:
                logger.warning(f"kernels_output failed: {e}, trying alternative approach...")
                # 尝试其他方法获取输出
                try:
                    api.kernel_output(f"{username}/{slug}", path=str(temp_dir))
                except Exception as e2:
                    logger.error(f"All output download methods failed: {e2}")
                    return None
            
            # 检查是否有文件下载
            files = list(temp_dir.glob('*'))
            logger.info(f"Found {len(files)} output files: {[f.name for f in files]}")
            
            if not files:
                logger.warning(f"没有找到输出文件: {notebook_path}")
                shutil.rmtree(temp_dir, ignore_errors=True)
                return None
            
            # 创建ZIP文件
            zip_filename = f"{output_name}.zip"
            zip_path = self.output_dir / zip_filename
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file in temp_dir.rglob('*'):
                    if file.is_file():
                        arcname = file.relative_to(temp_dir)
                        zipf.write(file, arcname)
            
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info(f"Output packaged: {zip_path}")
            return zip_path
            
        except Exception as e:
            logger.error(f"打包输出文件失败: {e}")
            # 清理临时目录
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except:
                pass
            return None

    def validate_notebook_path(self, notebook_path: str) -> bool:
        """验证notebook路径是否有效"""
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
            api = KaggleApi()
            api.authenticate()
            
            # 检查路径格式
            if '/' not in notebook_path:
                return False
            
            username, slug = notebook_path.split('/', 1)
            
            # 尝试获取notebook状态来验证
            status = api.kernels_status(notebook_path)
            return status is not None
            
        except Exception as e:
            logger.error(f"验证notebook路径失败: {e}")
            return False

    async def run_notebook(self, notebook_path: str, notebook_name: str, event: AstrMessageEvent = None) -> Optional[Path]:
        """运行notebook并返回输出文件路径 - 修复路径问题"""
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
            api = KaggleApi()
            api.authenticate()
            
            if event:
                await event.send(event.plain_result("🔍 验证notebook是否存在..."))
            
            # 验证notebook状态
            try:
                kernel_status = api.kernels_status(notebook_path)
                status = getattr(kernel_status, 'status', 'unknown')
                
                if event:
                    await event.send(event.plain_result(f"📊 Notebook状态: {status}"))
                
                # 检查状态是否有效
                if status in ['CANCEL_ACKNOWLEDGED', 'ERROR', 'FAILED', 'CANCELLED']:
                    if event:
                        await event.send(event.plain_result("❌ Notebook状态无效，可能已被取消或不存在"))
                    return None
                    
            except Exception as e:
                if "Not Found" in str(e) or "404" in str(e):
                    if event:
                        await event.send(event.plain_result(f"❌ Notebook不存在: {notebook_path}"))
                    return None
                else:
                    if event:
                        await event.send(event.plain_result(f"⚠️ 验证时出现错误: {str(e)}"))
            
            # 记录运行中的notebook
            if event:
                session_id = getattr(event, 'session_id', 'default')
                self.running_notebooks[session_id] = notebook_name
            
            if event:
                await event.send(event.plain_result("📥 正在下载notebook..."))
            
            # 创建下载目录
            download_dir = self.downloads_dir / f"temp_{notebook_name.replace(' ', '_')}_{datetime.now().strftime('%H%M%S')}"
            download_dir.mkdir(parents=True, exist_ok=True)
            
            # 1. 首先pull获取notebook
            try:
                # 下载notebook到指定目录
                api.kernels_pull(notebook_path, path=str(download_dir))
                
                if event:
                    await event.send(event.plain_result("✅ Notebook下载完成"))
                    
                # 检查下载的文件
                downloaded_files = list(download_dir.glob('*'))
                if not downloaded_files:
                    if event:
                        await event.send(event.plain_result("❌ 下载的文件为空"))
                    shutil.rmtree(download_dir, ignore_errors=True)
                    return None
                    
                if event:
                    await event.send(event.plain_result(f"📄 下载的文件: {[f.name for f in downloaded_files]}"))
                    
            except Exception as pull_error:
                if event:
                    await event.send(event.plain_result(f"❌ 下载notebook失败: {str(pull_error)}"))
                shutil.rmtree(download_dir, ignore_errors=True)
                return None
            
            if event:
                await event.send(event.plain_result("🚀 开始运行notebook..."))
            
            # 2. 关键修复：使用下载的目录进行push
            try:
                # 获取下载的notebook文件路径
                notebook_file = None
                for file in download_dir.glob('*'):
                    if file.suffix in ['.ipynb', '.py']:
                        notebook_file = file
                        break
                
                if not notebook_file:
                    if event:
                        await event.send(event.plain_result("❌ 未找到notebook文件 (.ipynb 或 .py)"))
                    shutil.rmtree(download_dir, ignore_errors=True)
                    return None
                
                # 关键修复：使用包含notebook文件的目录路径
                result = api.kernels_push(str(download_dir))
                
                if result and hasattr(result, 'status') and getattr(result, 'status') == 'ok':
                    if event:
                        await event.send(event.plain_result("✅ 运行完成，等待输出文件生成..."))
                    
                    # 等待更长时间让notebook完成运行
                    await asyncio.sleep(20)
                    
                    # 3. 下载输出文件
                    zip_path = await self.download_and_package_output(notebook_path, notebook_name)
                    
                    # 清理下载目录
                    try:
                        shutil.rmtree(download_dir, ignore_errors=True)
                    except:
                        pass
                    
                    # 清理运行记录
                    if event:
                        session_id = getattr(event, 'session_id', 'default')
                        if session_id in self.running_notebooks:
                            del self.running_notebooks[session_id]
                    
                    if zip_path:
                        return zip_path
                    else:
                        if event:
                            await event.send(event.plain_result("⚠️ 运行完成但未找到输出文件"))
                        return None
                else:
                    error_msg = getattr(result, 'error', '未知错误') if result else '无响应'
                    if event:
                        await event.send(event.plain_result(f"❌ 运行失败: {error_msg}"))
                    
                    # 清理下载目录
                    try:
                        shutil.rmtree(download_dir, ignore_errors=True)
                    except:
                        pass
                        
                    return None
                    
            except Exception as run_error:
                error_msg = str(run_error)
                if "Invalid folder" in error_msg or "not found" in error_msg.lower():
                    if event:
                        await event.send(event.plain_result("❌ Notebook路径无效或不存在"))
                        await event.send(event.plain_result("💡 提示: 确保下载的目录包含有效的notebook文件"))
                elif "already running" in error_msg.lower():
                    if event:
                        await event.send(event.plain_result("⚠️ Notebook已经在运行中，等待完成..."))
                    # 等待并尝试获取输出
                    await asyncio.sleep(30)
                    zip_path = await self.download_and_package_output(notebook_path, notebook_name)
                    return zip_path
                else:
                    if event:
                        await event.send(event.plain_result(f"❌ 运行过程中出错: {error_msg}"))
                
                # 清理下载目录
                try:
                    shutil.rmtree(download_dir, ignore_errors=True)
                except:
                    pass
                    
                return None
                
        except Exception as e:
            logger.error(f"运行Notebook失败: {e}")
            if event:
                session_id = getattr(event, 'session_id', 'default')
                if session_id in self.running_notebooks:
                    del self.running_notebooks[session_id]
                await event.send(event.plain_result(f"❌ 运行失败: {str(e)}"))
            return None

    def is_admin_user(self, user_id: str) -> bool:
        """检查用户是否是管理员"""
        return user_id in self.config.admin_users

    def should_keep_running(self, message: str) -> bool:
        """检查消息中是否包含关键词"""
        message_lower = message.lower()
        return any(keyword.lower() in message_lower for keyword in self.config.keywords)

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
            "/kaggle outputs - 查看输出文件\n"
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

    @kaggle_group.command("check")
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

    @kaggle_group.command("list")
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

    @kaggle_group.command("add")
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

    @kaggle_group.command("remove")
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

    @kaggle_group.command("run")
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

@register("kaggle_runner", "AstrBot", "Kaggle Notebook执行插件", "1.0.0")
class KaggleRunner(KagglePlugin):
    pass