import os
import json
import asyncio
import zipfile
import shutil
from pathlib import Path
from typing import Dict, Optional, Tuple, List
from datetime import datetime

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

class KagglePlugin(Star):
    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config = config
        # 按照文档要求，数据存储在data目录下
        self.data_dir = Path("data/kaggle_plugin")
        self.output_dir = self.data_dir / "outputs"
        self.temp_dir = self.data_dir / "temp"
        self.notebooks_file = self.data_dir / "notebooks.json"
        self.notebooks: Dict[str, str] = {}
        
        self.setup_directories()
        self.setup_kaggle_api()
        self.load_notebooks()

    def setup_directories(self):
        """设置必要的目录结构"""
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Kaggle插件目录设置完成: {self.data_dir}")
        except Exception as e:
            logger.error(f"设置目录失败: {e}")

    def setup_kaggle_api(self):
        """设置Kaggle API配置"""
        try:
            kaggle_dir = Path.home() / '.kaggle'
            kaggle_dir.mkdir(exist_ok=True)
            
            kaggle_config = {
                "username": self.config.kaggle_username,
                "key": self.config.kaggle_api_key
            }
            
            config_path = kaggle_dir / 'kaggle.json'
            with open(config_path, 'w') as f:
                json.dump(kaggle_config, f)
            config_path.chmod(0o600)
            
            logger.info("Kaggle API配置完成")
        except Exception as e:
            logger.error(f"Kaggle API配置失败: {e}")

    def load_notebooks(self):
        """加载notebook列表"""
        try:
            if self.notebooks_file.exists():
                with open(self.notebooks_file, 'r', encoding='utf-8') as f:
                    self.notebooks = json.load(f)
                logger.info(f"已加载 {len(self.notebooks)} 个notebook")
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

    async def run_kaggle_notebook(self, notebook_path: str, notebook_name: str, event: AstrMessageEvent) -> Optional[Path]:
        """
        运行Kaggle Notebook的核心流程：先pull再push
        返回打包后的输出文件路径
        """
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
            api = KaggleApi()
            api.authenticate()

            if event:
                await event.send(event.plain_result("🔍 验证Notebook状态..."))

            # 1. 验证Notebook是否存在
            try:
                status = api.kernels_status(notebook_path)
                if not status:
                    await event.send(event.plain_result("❌ Notebook不存在或无法访问"))
                    return None
                await event.send(event.plain_result(f"📊 Notebook状态: {getattr(status, 'status', 'unknown')}"))
            except Exception as e:
                if "Not Found" in str(e):
                    await event.send(event.plain_result("❌ Notebook不存在"))
                else:
                    await event.send(event.plain_result(f"❌ Notebook验证失败: {str(e)}"))
                return None

            # 2. PULL阶段 - 下载Notebook
            pull_dir = self.temp_dir / f"pull_{notebook_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            pull_dir.mkdir(parents=True, exist_ok=True)

            if event:
                await event.send(event.plain_result("📥 正在下载Notebook..."))

            try:
                api.kernels_pull(notebook_path, path=str(pull_dir))
                
                # 检查下载的文件
                downloaded_files = list(pull_dir.glob('*'))
                if not downloaded_files:
                    await event.send(event.plain_result("❌ 下载的文件为空"))
                    shutil.rmtree(pull_dir, ignore_errors=True)
                    return None
                    
                await event.send(event.plain_result(f"✅ 下载完成: {[f.name for f in downloaded_files]}"))
                
            except Exception as pull_error:
                await event.send(event.plain_result(f"❌ 下载失败: {str(pull_error)}"))
                shutil.rmtree(pull_dir, ignore_errors=True)
                return None

            # 3. PUSH阶段 - 运行Notebook
            if event:
                await event.send(event.plain_result("🚀 开始运行Notebook..."))

            try:
                result = api.kernels_push(str(pull_dir))
                
                if result and hasattr(result, 'status') and getattr(result, 'status') == 'ok':
                    await event.send(event.plain_result("✅ 运行提交成功，等待执行完成..."))
                    
                    # 等待执行完成
                    await asyncio.sleep(30)
                    
                    # 4. 下载输出文件
                    output_path = await self.download_output_files(notebook_path, notebook_name, event)
                    
                    # 清理临时目录
                    shutil.rmtree(pull_dir, ignore_errors=True)
                    
                    return output_path
                else:
                    error_msg = getattr(result, 'error', '未知错误') if result else '无响应'
                    await event.send(event.plain_result(f"❌ 运行失败: {error_msg}"))
                    shutil.rmtree(pull_dir, ignore_errors=True)
                    return None
                    
            except Exception as push_error:
                await event.send(event.plain_result(f"❌ 运行过程中出错: {str(push_error)}"))
                shutil.rmtree(pull_dir, ignore_errors=True)
                return None
                
        except Exception as e:
            logger.error(f"运行Notebook失败: {e}")
            await event.send(event.plain_result(f"❌ 运行失败: {str(e)}"))
            return None

    async def download_output_files(self, notebook_path: str, notebook_name: str, event: AstrMessageEvent) -> Optional[Path]:
        """下载并打包输出文件"""
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
            api = KaggleApi()
            api.authenticate()

            await event.send(event.plain_result("📦 正在下载输出文件..."))

            # 创建输出目录
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = "".join(c for c in notebook_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
            output_dir = self.output_dir / f"{timestamp}_{safe_name}"
            output_dir.mkdir(parents=True, exist_ok=True)

            # 下载输出文件
            try:
                api.kernels_output(notebook_path, path=str(output_dir))
            except Exception as e:
                await event.send(event.plain_result(f"⚠️ 输出文件下载失败: {str(e)}"))
                shutil.rmtree(output_dir, ignore_errors=True)
                return None

            # 检查是否有文件下载
            files = list(output_dir.glob('*'))
            if not files:
                await event.send(event.plain_result("⚠️ 未找到输出文件"))
                shutil.rmtree(output_dir, ignore_errors=True)
                return None

            await event.send(event.plain_result(f"✅ 找到 {len(files)} 个输出文件"))

            # 创建ZIP压缩包
            zip_filename = f"{safe_name}_{timestamp}.zip"
            zip_path = self.output_dir / zip_filename

            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file in output_dir.rglob('*'):
                    if file.is_file():
                        arcname = file.relative_to(output_dir)
                        zipf.write(file, arcname)

            # 清理临时输出目录
            shutil.rmtree(output_dir, ignore_errors=True)

            await event.send(event.plain_result(f"✅ 输出文件已打包: {zip_path.name}"))
            return zip_path

        except Exception as e:
            logger.error(f"下载输出文件失败: {e}")
            await event.send(event.plain_result(f"❌ 输出文件处理失败: {str(e)}"))
            return None

    def is_admin_user(self, event: AstrMessageEvent) -> bool:
        """检查用户是否是管理员"""
        try:
            return event.is_admin()
        except:
            return False

    # 命令注册
    @filter.command_group("kaggle")
    def kaggle_group(self):
        """Kaggle Notebook管理命令组"""
        pass

    @kaggle_group.command("")
    async def kaggle_main(self, event: AstrMessageEvent):
        """显示Kaggle帮助信息"""
        help_text = (
            "📋 Kaggle Notebook管理器\n\n"
            "可用命令:\n"
            "/kaggle list - 查看可用notebook列表\n"
            "/kaggle add <名称> <路径> - 添加notebook\n"
            "/kaggle remove <名称> - 删除notebook\n"
            "/kaggle run <名称> - 运行notebook\n"
            "/kaggle test - 测试API连接\n"
            "/kaggle outputs - 查看输出文件列表\n"
            "/kaggle status - 查看插件状态"
        )
        yield event.plain_result(help_text)

    @kaggle_group.command("list")
    async def kaggle_list(self, event: AstrMessageEvent):
        """列出所有notebook"""
        if not self.notebooks:
            yield event.plain_result("📝 还没有添加任何notebook")
            return
        
        message = "📋 Notebook列表:\n"
        for i, (name, path) in enumerate(self.notebooks.items(), 1):
            message += f"{i}. {name} -> {path}\n"
        
        yield event.plain_result(message)

    @kaggle_group.command("add")
    async def kaggle_add(self, event: AstrMessageEvent, name: str, path: str):
        """添加notebook"""
        if not self.is_admin_user(event):
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
            if "Not Found" in str(e):
                yield event.plain_result(f"❌ Notebook不存在: {path}")
            else:
                yield event.plain_result(f"❌ 验证失败: {str(e)}")

    @kaggle_group.command("remove")
    async def kaggle_remove(self, event: AstrMessageEvent, name: str):
        """删除notebook"""
        if not self.is_admin_user(event):
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
    async def kaggle_run(self, event: AstrMessageEvent, name: str):
        """运行指定的Kaggle Notebook"""
        notebook_info = self.get_notebook_by_identifier(name)
        if not notebook_info:
            yield event.plain_result("❌ Notebook不存在")
            return

        notebook_name, notebook_path = notebook_info
        
        # 发送初始消息
        yield event.plain_result(f"🚀 开始运行: {notebook_name}")
        
        # 运行Notebook
        output_path = await self.run_kaggle_notebook(notebook_path, notebook_name, event)
        
        if output_path:
            # 发送文件到会话
            try:
                file_component = Comp.File.fromFileSystem(str(output_path))
                yield event.chain_result([file_component])
            except Exception as e:
                logger.error(f"发送文件失败: {e}")
                yield event.plain_result(f"✅ 运行完成，文件位置: {output_path}")
        else:
            yield event.plain_result("❌ 运行失败")

    @kaggle_group.command("test")
    async def kaggle_test(self, event: AstrMessageEvent):
        """测试Kaggle API连接"""
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
            api = KaggleApi()
            api.authenticate()
            
            # 测试API连接
            kernels = api.kernels_list(page_size=1)
            yield event.plain_result("✅ Kaggle API连接正常")
            
        except Exception as e:
            yield event.plain_result(f"❌ API连接失败: {str(e)}")

    @kaggle_group.command("outputs")
    async def kaggle_outputs(self, event: AstrMessageEvent):
        """查看输出文件列表"""
        try:
            output_files = list(self.output_dir.glob("*.zip"))
            if not output_files:
                yield event.plain_result("📝 暂无输出文件")
                return
            
            message = "📦 输出文件列表:\n"
            for i, file in enumerate(sorted(output_files, key=lambda x: x.stat().st_mtime, reverse=True), 1):
                file_time = datetime.fromtimestamp(file.stat().st_mtime)
                size_mb = file.stat().st_size / (1024 * 1024)
                message += f"{i}. {file.name} ({size_mb:.1f}MB, {file_time.strftime('%Y-%m-%d %H:%M')})\n"
            
            yield event.plain_result(message)
            
        except Exception as e:
            yield event.plain_result(f"❌ 获取输出文件列表失败: {str(e)}")

    @kaggle_group.command("status")
    async def kaggle_status(self, event: AstrMessageEvent):
        """查看插件状态"""
        status_text = (
            f"📊 Kaggle插件状态\n"
            f"• Notebook数量: {len(self.notebooks)}\n"
            f"• 输出文件数量: {len(list(self.output_dir.glob('*.zip')))}\n"
            f"• 数据目录: {self.data_dir}\n"
            f"• 输出目录: {self.output_dir}"
        )
        yield event.plain_result(status_text)

    async def terminate(self):
        """插件卸载时的清理操作"""
        try:
            # 清理临时目录
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir, ignore_errors=True)
            logger.info("Kaggle插件清理完成")
        except Exception as e:
            logger.error(f"插件清理失败: {e}")

# 插件注册 - 按照AstrBot文档规范
@register(
    name="kaggle_runner",
    author="YourName",
    description="Kaggle Notebook执行和管理插件",
    version="1.0.0",
    repo_url="https://github.com/your-repo/astrbot-kaggle-plugin"
)
class KaggleRunner(KagglePlugin):
    pass