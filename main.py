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
    # 可在 config 里配置 kaggle_datasets: List[str]
    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config = config
        self.active_sessions: Dict[str, datetime] = {}
        self.running_notebooks: Dict[str, str] = {}
        # 修改存储路径为相对路径
        self.plugin_data_dir = Path("data/plugin_data/astrbot_plugin_kagglerun")
        self.notebooks_file = self.plugin_data_dir / "kaggle_notebooks.json"
        self.notebooks: Dict[str, str] = {}
        self.output_dir = self.plugin_data_dir / "outputs"
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
            self.plugin_data_dir.mkdir(parents=True, exist_ok=True)
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

    async def download_and_package_output(self, notebook_path: str, notebook_name: str) -> Optional[Path]:
        """下载并打包输出文件"""
        temp_dir = None
        try:
            api = self.get_kaggle_api()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_name = f"{timestamp}_{notebook_name}"
            if '/' not in notebook_path:
                logger.error(f"Invalid notebook path: {notebook_path}")
                return None
            username, slug = notebook_path.split('/', 1)
            temp_dir = self.output_dir / "temp" / output_name
            temp_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Downloading output for: {username}/{slug} to {temp_dir}")
            try:
                api.kernels_output(f"{username}/{slug}", path=str(temp_dir))
                logger.info(f"成功下载输出文件到: {temp_dir}")
            except Exception as e:
                logger.warning(f"kernels_output failed: {e}, trying alternative approach...")
                try:
                    api.kernel_output(f"{username}/{slug}", path=str(temp_dir))
                    logger.info(f"通过备用方法成功下载输出文件到: {temp_dir}")
                except Exception as e2:
                    logger.error(f"All output download methods failed: {e2}")
                    return None
            files = list(temp_dir.glob('*'))
            logger.info(f"Found {len(files)} output files: {[f.name for f in files]}")
            if not files:
                logger.warning(f"没有找到输出文件: {notebook_path}")
                return None
            zip_filename = f"{output_name}.zip"
            zip_path = self.output_dir / zip_filename
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file in temp_dir.rglob('*'):
                    if file.is_file():
                        arcname = file.relative_to(temp_dir)
                        zipf.write(file, arcname)
            logger.info(f"Output packaged: {zip_path}")
            return zip_path
        except Exception as e:
            logger.error(f"打包输出文件失败: {e}")
            return None
        finally:
            if temp_dir and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    logger.info(f"临时目录已清理: {temp_dir}")
                except Exception as e:
                    logger.error(f"清理临时目录失败: {e}")

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

    async def run_notebook(self, notebook_path: str, notebook_name: str, event: AstrMessageEvent = None) -> Optional[Path]:
        """运行notebook并返回输出文件路径 - 修复路径问题"""
        temp_dir = None
        try:
            api = self.get_kaggle_api()
            
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
                    logger.warning(f"Notebook状态无效: {status} for {notebook_path}")
                    return None
                    
            except Exception as e:
                if "Not Found" in str(e) or "404" in str(e):
                    if event:
                        await event.send(event.plain_result(f"❌ Notebook不存在: {notebook_path}"))
                    logger.error(f"Notebook不存在: {notebook_path}")
                    return None
                else:
                    if event:
                        await event.send(event.plain_result(f"⚠️ 验证时出现错误: {str(e)}"))
                    logger.warning(f"验证notebook时出现错误: {e}")
            
            # 记录运行中的notebook
            if event:
                session_id = getattr(event, 'session_id', 'default')
                self.running_notebooks[session_id] = notebook_name
                logger.info(f"记录运行中的notebook: {notebook_name} (会话ID: {session_id})")
            
            if event:
                await event.send(event.plain_result("📥 正在下载notebook..."))
            
            # 1. 首先pull获取notebook
            import tempfile
            import subprocess
            import re
            try:
                # 1. 创建临时目录
                temp_dir = Path(tempfile.mkdtemp(prefix="kaggle_upload_"))
                if event:
                    await event.send(event.plain_result(f"📁 创建临时目录: {temp_dir}"))

                # 2. 复制 notebook 文件
                nb_file = None
                valid_extensions = ['.ipynb', '.py']
                for ext in valid_extensions:
                    candidate = Path(notebook_name).with_suffix(ext)
                    if Path(candidate).exists():
                        nb_file = Path(candidate)
                        break
                if not nb_file and Path(notebook_name).exists():
                    nb_file = Path(notebook_name)
                if not nb_file:
                    if event:
                        await event.send(event.plain_result("❌ 未找到notebook文件 (.ipynb 或 .py)"))
                    logger.error(f"未找到notebook文件: {notebook_name}")
                    return None
                shutil.copy(nb_file, temp_dir / nb_file.name)

                # 3. 生成 kernel-metadata.json
                def slugify(title):
                    slug = title.lower()
                    slug = re.sub(r'[^a-z0-9\\s-]', '', slug)
                    slug = re.sub(r'\\s+', '-', slug)
                    slug = re.sub(r'-+', '-', slug)
                    return slug.strip('-')

                title = notebook_name
                slug = slugify(title)
                username = self.config.kaggle_username if hasattr(self.config, 'kaggle_username') else 'your_username'
                datasets = getattr(self.config, 'kaggle_datasets', [])
                is_private = getattr(self.config, 'kaggle_is_private', True)
                metadata = {
                    "id": f"{username}/{slug}",
                    "title": title,
                    "code_file": nb_file.name,
                    "language": "python",
                    "kernel_type": "notebook",
                    "is_private": is_private,
                    "datasets": datasets
                }
                extra_metadata = getattr(self.config, 'kaggle_extra_metadata', None)
                if extra_metadata and isinstance(extra_metadata, dict):
                    metadata.update(extra_metadata)
                with open(temp_dir / "kernel-metadata.json", "w", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=2, ensure_ascii=False)
                logger.info(f"写入kernel-metadata.json: {metadata}")
                if event:
                    await event.send(event.plain_result(f"📝 已生成kernel-metadata.json: {metadata}"))

                # 4. 推送 notebook
                cmd = f'kaggle kernels push -p "{str(temp_dir)}"'
                if event:
                    await event.send(event.plain_result(f"🚀 执行: {cmd}"))
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                logger.info(f"kaggle kernels push stdout: {result.stdout}")
                logger.info(f"kaggle kernels push stderr: {result.stderr}")
                if event:
                    await event.send(event.plain_result(f"stdout: {result.stdout}"))
                    await event.send(event.plain_result(f"stderr: {result.stderr}"))
                if result.returncode == 0:
                    if event:
                        await event.send(event.plain_result("✅ Notebook已推送并运行（请到Kaggle网页查看结果）"))
                    logger.info(f"Notebook已推送: {notebook_name}")
                else:
                    if event:
                        await event.send(event.plain_result("❌ 推送失败，请检查日志"))
                    logger.error(f"推送失败: {result.stderr}")
                    return None

                # 5. 可选：清理临时目录
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.info(f"已清理临时目录: {temp_dir}")
                return None
            except Exception as e:
                logger.error(f"运行Notebook失败: {e}", exc_info=True)
                if event:
                    await event.send(event.plain_result(f"❌ 运行失败: {str(e)}"))
                return None
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
        # 移除了管理员验证
        
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
        # 移除了管理员验证
        
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
        
        await event.send(event.plain_result("🚀 运行中..."))
        
        zip_path = await self.run_notebook(notebook_path, notebook_name, event)
        
        if zip_path and self.config.send_to_group:
            try:
                from astrbot.api.message_components import File
                await event.send(event.chain_result([
                    File.fromFileSystem(str(zip_path), zip_path.name)
                ]))
                logger.info(f"成功发送输出文件到群聊: {zip_path.name}")
            except Exception as e:
                logger.error(f"发送文件失败: {e}")
                yield event.plain_result(f"📦 完成: {zip_path.name}")
        elif zip_path:
            yield event.plain_result(f"📦 完成: {zip_path.name}")
            logger.info(f"Notebook运行完成: {zip_path.name}")
        else:
            yield event.plain_result("❌ 运行失败")
            logger.error(f"Notebook运行失败: {notebook_name}")

@register("kaggle_runner", "AstrBot", "Kaggle Notebook执行插件", "1.0.0")
class KaggleRunner(KagglePlugin):
    pass
