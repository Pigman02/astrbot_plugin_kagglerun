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
            # 统一用 kaggle kernels pull -m 下载 notebook 和 metadata
            import tempfile
            import subprocess
            temp_dir = Path(tempfile.mkdtemp(prefix="kaggle_"))
            logger.info(f"创建临时目录: {temp_dir}")
            # 校验 notebook_path
            if not isinstance(notebook_path, str) or '/' not in notebook_path:
                if event:
                    await event.send(event.plain_result("❌ notebook_path 格式错误，必须为 username/slug"))
                logger.error(f"notebook_path 格式错误: {notebook_path}")
                return None
            username, slug = notebook_path.split('/', 1)
            if not username or not slug:
                if event:
                    await event.send(event.plain_result("❌ notebook_path 拆分失败"))
                logger.error(f"notebook_path 拆分失败: {notebook_path}")
                return None
            try:
                cmd = [
                    'kaggle', 'kernels', 'pull',
                    '-k', f'{username}/{slug}',
                    '-p', str(temp_dir),
                    '-m'
                ]
                logger.info(f"执行命令: {' '.join(cmd)}")
                result = subprocess.run(cmd, capture_output=True, text=True)
                logger.info(f"kaggle kernels pull 输出: {result.stdout}")
                if result.returncode != 0:
                    logger.error(f"kaggle kernels pull 失败: {result.stderr}")
                    if event:
                        await event.send(event.plain_result(f"❌ 下载notebook失败: {result.stderr}"))
                    return None
                if event:
                    await event.send(event.plain_result("✅ Notebook和metadata下载完成"))
                # 检查下载的文件
                downloaded_files = list(temp_dir.glob('*'))
                if not downloaded_files:
                    if event:
                        await event.send(event.plain_result("❌ 下载的文件为空"))
                    logger.error(f"下载的notebook文件为空: {notebook_path}")
                    return None
                if event:
                    await event.send(event.plain_result(f"📄 下载的文件: {[f.name for f in downloaded_files]}"))
                logger.info(f"下载的文件列表: {[f.name for f in downloaded_files]}")
            except Exception as pull_error:
                if event:
                    await event.send(event.plain_result(f"❌ 下载notebook失败: {str(pull_error)}"))
                logger.error(f"下载notebook失败: {pull_error}")
                return None
            
            if event:
                await event.send(event.plain_result("🚀 开始运行notebook..."))
            
            # 2. 关键修复：正确使用下载的目录进行push，并自动生成 kernel-metadata.json
            try:
                # 获取下载的notebook文件路径
                notebook_file = None
                valid_extensions = ['.ipynb', '.py']

                # 先在根目录查找
                for file in temp_dir.glob('*'):
                    if file.suffix.lower() in valid_extensions:
                        notebook_file = file
                        break

                # 如果根目录没找到，在子目录中查找
                if not notebook_file:
                    for file in temp_dir.rglob('*'):
                        if file.suffix.lower() in valid_extensions:
                            notebook_file = file
                            # 将文件移动到根目录以便push
                            target_path = temp_dir / file.name
                            if not target_path.exists():
                                shutil.move(str(file), str(target_path))
                            notebook_file = target_path
                            break

                if not notebook_file:
                    if event:
                        await event.send(event.plain_result("❌ 未找到notebook文件 (.ipynb 或 .py)"))
                    logger.error(f"未找到有效的notebook文件: {temp_dir}")
                    return None

                # 优化 kernel-metadata.json 生成
                username, slug = notebook_path.split('/', 1)
                metadata_path = temp_dir / "kernel-metadata.json"
                # 字段顺序更合理，支持自定义额外字段
                metadata = {
                    "id": f"{username}/{slug}",
                    "title": slug,
                    "code_file": notebook_file.name,
                    "language": "python",
                    "kernel_type": "notebook",
                    "is_private": False
                }
                # 获取原notebook的datasets依赖
                dataset_refs = set()
                try:
                    kernel_info = api.kernel_view(notebook_path)
                    logger.info(f"kernel_view返回: {kernel_info}")
                    datasets = []
                    if hasattr(kernel_info, 'datasets'):
                        datasets = getattr(kernel_info, 'datasets', [])
                    elif isinstance(kernel_info, dict):
                        datasets = kernel_info.get('datasets', [])
                    logger.info(f"解析到datasets: {datasets}")
                    for ds in datasets:
                        if isinstance(ds, dict):
                            ref = ds.get('ref') or (f"{ds.get('ownerSlug')}/{ds.get('datasetSlug')}")
                        else:
                            ref = getattr(ds, 'ref', None) or (f"{getattr(ds, 'ownerSlug', '')}/{getattr(ds, 'datasetSlug', '')}")
                        if ref and '/' in ref:
                            dataset_refs.add(ref)
                    logger.info(f"最终dataset_refs: {dataset_refs}")
                except Exception as e:
                    logger.warning(f"获取notebook依赖datasets失败: {e}")
                # 若dataset_refs为空，兼容用户自定义
                if not dataset_refs:
                    config_datasets = getattr(self.config, 'kaggle_datasets', None)
                    if config_datasets and isinstance(config_datasets, list):
                        dataset_refs.update(config_datasets)
                        logger.info(f"使用config.kaggle_datasets: {config_datasets}")
                # datasets字段始终为非空list
                metadata["datasets"] = list(dataset_refs)
                # 支持自定义额外字段
                extra_metadata = getattr(self.config, 'kaggle_extra_metadata', None)
                if extra_metadata and isinstance(extra_metadata, dict):
                    metadata.update(extra_metadata)
                with open(metadata_path, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=2, ensure_ascii=False)
                logger.info(f"最终写入kernel-metadata.json内容: {metadata}")

                # 使用绝对路径确保正确性
                abs_temp_dir = temp_dir.resolve()
                logger.info(f"准备运行notebook，目录: {abs_temp_dir}")
                result = api.kernels_push(str(abs_temp_dir))
                # Kaggle API 有时返回 None 但实际已成功，需兼容这种情况
                status_ok = False
                # 只要没有抛异常且没有明确 error 字段就认为成功
                if result is None:
                    status_ok = True
                elif isinstance(result, dict):
                    if result.get('status') == 'ok':
                        status_ok = True
                    elif result.get('error'):
                        status_ok = False
                    else:
                        # 没有 error 字段也视为成功
                        status_ok = True
                else:
                    if hasattr(result, 'status') and getattr(result, 'status') == 'ok':
                        status_ok = True
                    elif hasattr(result, 'error') and getattr(result, 'error'):
                        status_ok = False
                    else:
                        status_ok = True

                if status_ok:
                    if event:
                        await event.send(event.plain_result("✅ 运行完成，等待输出文件生成..."))
                    logger.info(f"Notebook运行成功: {notebook_path}")

                    # 等待更长时间让notebook完成运行
                    await asyncio.sleep(30)

                    # 3. 下载输出文件
                    logger.info(f"开始下载输出文件: {notebook_path}")
                    zip_path = await self.download_and_package_output(notebook_path, notebook_name)

                    # 清理运行记录
                    if event:
                        session_id = getattr(event, 'session_id', 'default')
                        if session_id in self.running_notebooks:
                            del self.running_notebooks[session_id]
                            logger.info(f"清理运行记录: {session_id}")

                    if zip_path:
                        logger.info(f"Notebook运行完成，输出文件: {zip_path}")
                        return zip_path
                    else:
                        if event:
                            await event.send(event.plain_result("⚠️ 运行完成但未找到输出文件"))
                        logger.warning(f"Notebook运行完成但未找到输出文件: {notebook_path}")
                        return None
                else:
                    # 失败时才报错
                    error_msg = None
                    if result:
                        if isinstance(result, dict):
                            error_msg = result.get('error', '未知错误')
                        else:
                            error_msg = getattr(result, 'error', '未知错误')
                    else:
                        error_msg = '无响应'
                    if event:
                        await event.send(event.plain_result(f"❌ 运行失败: {error_msg}"))
                    logger.error(f"Notebook运行失败: {error_msg}")
                    return None

            except Exception as run_error:
                error_msg = str(run_error)
                logger.error(f"运行notebook时发生异常: {error_msg}")
                if "Invalid folder" in error_msg or "not found" in error_msg.lower():
                    if event:
                        await event.send(event.plain_result("❌ Notebook路径无效或不存在"))
                        await event.send(event.plain_result("💡 提示: 确保下载的目录包含有效的notebook文件和 kernel-metadata.json"))
                        await event.send(event.plain_result(f"💡 当前路径: {notebook_path}"))
                elif "already running" in error_msg.lower():
                    if event:
                        await event.send(event.plain_result("⚠️ Notebook已经在运行中，等待完成..."))
                    logger.info(f"Notebook已在运行中，等待完成: {notebook_path}")
                    # 等待并尝试获取输出
                    await asyncio.sleep(60)
                    zip_path = await self.download_and_package_output(notebook_path, notebook_name)

                    # 清理运行记录
                    if event:
                        session_id = getattr(event, 'session_id', 'default')
                        if session_id in self.running_notebooks:
                            del self.running_notebooks[session_id]
                            logger.info(f"清理运行记录: {session_id}")

                    return zip_path
                else:
                    if event:
                        await event.send(event.plain_result(f"❌ 运行过程中出错: {error_msg}"))

                return None
                
        except Exception as e:
            logger.error(f"运行Notebook失败: {e}", exc_info=True)
            if event:
                session_id = getattr(event, 'session_id', 'default')
                if session_id in self.running_notebooks:
                    del self.running_notebooks[session_id]
                await event.send(event.plain_result(f"❌ 运行失败: {str(e)}"))
            return None
        finally:
            # 确保清理临时目录
            if temp_dir and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    logger.info(f"临时目录已清理: {temp_dir}")
                except Exception as e:
                    logger.error(f"清理临时目录失败: {e}")

    def should_keep_running(self, message: str) -> bool:
        """检查消息中是否包含关键词"""
        message_lower = message.lower()
        result = any(keyword.lower() in message_lower for keyword in self.config.keywords)
        logger.debug(f"检查消息是否包含关键词: {message} -> {result}")
        return result

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
