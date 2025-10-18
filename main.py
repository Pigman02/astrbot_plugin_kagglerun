import os
import json
import asyncio
import platform
import requests
import tarfile
import zipfile
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

class KaggleAutomation:
    """Kaggle 自动化操作类"""
    
    def __init__(self, email=None, password=None, plugin_data_dir=None):
        self.email = email
        self.password = password
        self.driver = None
        
        # 使用插件数据目录
        if plugin_data_dir:
            self.base_dir = Path(plugin_data_dir)
        else:
            # 默认路径：从插件目录出发的相对路径
            current_file = Path(__file__).parent
            self.base_dir = current_file.parent.parent / "plugin_data" / "astrbot_plugin_kagglerun"
        
        self.profile_dir = self.base_dir / "kaggle_profile_firefox"
        self.is_running = False
        self.last_activity_time = None
        
        # 确保目录存在
        os.makedirs(self.base_dir, exist_ok=True)
        logger.info(f"📁 Kaggle自动化数据目录: {self.base_dir}")
        
    def setup_driver(self):
        """设置 Firefox 浏览器驱动"""
        options = Options()
        
        # 创建或使用现有的 Firefox 配置文件
        if not os.path.exists(self.profile_dir):
            os.makedirs(self.profile_dir, exist_ok=True)
        
        # 设置 Firefox 选项
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.profile = str(self.profile_dir)
        
        try:
            # 方法1: 先尝试系统驱动
            self.driver = webdriver.Firefox(options=options)
            logger.info("✅ 使用系统 Firefox 驱动成功")
            return self.driver
        except Exception as e:
            logger.warning(f"系统驱动失败: {e}")
            
            # 方法2: 直接从 GitHub Release 下载
            return self.download_direct_from_release(options)

    def download_direct_from_release(self, options):
        """直接从 GitHub Release 下载，使用固定存储目录"""
        # 检测系统和架构
        system = platform.system().lower()
        arch = platform.machine().lower()
        
        logger.info(f"🔍 检测系统: {system}, 架构: {arch}")
        
        # 系统映射
        system_map = {
            'linux': 'linux',
            'darwin': 'macos',
            'windows': 'win',
        }
        
        # 架构映射
        arch_map = {
            'aarch64': 'aarch64',
            'arm64': 'aarch64',
            'x86_64': '64',
            'amd64': '64',
            'i386': '32',
            'i686': '32',
        }
        
        system_name = system_map.get(system, 'linux')
        arch_name = arch_map.get(arch, '64')
        
        # 构建下载URL和文件名
        if system_name == 'win':
            extension = 'zip'
            filename = f'geckodriver-v0.36.0-win{arch_name}.{extension}'
        elif system_name == 'macos':
            extension = 'tar.gz'
            filename = f'geckodriver-v0.36.0-macos.{extension}'
        else:
            if arch_name == 'aarch64':
                extension = 'tar.gz'
                filename = f'geckodriver-v0.36.0-linux-{arch_name}.{extension}'
            else:
                extension = 'tar.gz'
                filename = f'geckodriver-v0.36.0-linux{arch_name}.{extension}'
        
        download_url = f'https://github.com/mozilla/geckodriver/releases/download/v0.36.0/{filename}'
        
        # 固定存储目录
        storage_dir = self.base_dir / "geckodriver_cache" / "v0.36.0"
        os.makedirs(storage_dir, exist_ok=True)
        
        archive_path = storage_dir / filename
        driver_path = storage_dir / 'geckodriver'
        
        # 如果驱动已存在，直接使用
        if os.path.exists(driver_path):
            logger.info(f"✅ 使用缓存驱动: {driver_path}")
            service = Service(str(driver_path))
            self.driver = webdriver.Firefox(service=service, options=options)
            return self.driver
        
        logger.info(f"📥 下载URL: {download_url}")
        
        try:
            # 下载文件
            logger.info("⬇️ 开始下载驱动...")
            response = requests.get(download_url, stream=True)
            response.raise_for_status()
            
            with open(archive_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            logger.info(f"✅ 文件下载完成: {archive_path}")
            
            # 解压文件
            logger.info("📦 解压文件...")
            extracted_files = []
            
            if extension == 'tar.gz':
                with tarfile.open(archive_path, 'r:gz') as tar:
                    # 获取解压前的文件列表
                    members = tar.getmembers()
                    tar.extractall(storage_dir)
                    extracted_files = [member.name for member in members]
            elif extension == 'zip':
                with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                    extracted_files = zip_ref.namelist()
                    zip_ref.extractall(storage_dir)
            
            logger.info(f"📄 解压出的文件: {extracted_files}")
            
            # 查找真正的 geckodriver 可执行文件
            geckodriver_found = False
            for root, dirs, files in os.walk(storage_dir):
                for file in files:
                    full_path = os.path.join(root, file)
                    # 检查是否是真正的可执行文件，不是压缩包
                    if 'geckodriver' in file.lower() and not file.endswith(('.tar.gz', '.zip')):
                        # 如果是真正的可执行文件，移动到标准位置
                        if full_path != str(driver_path):
                            # 如果目标文件已存在，先删除
                            if os.path.exists(driver_path):
                                os.remove(driver_path)
                            os.rename(full_path, driver_path)
                            logger.info(f"✅ 移动驱动文件: {full_path} -> {driver_path}")
                        geckodriver_found = True
                        break
                if geckodriver_found:
                    break
            
            if not geckodriver_found:
                raise Exception("未在解压文件中找到 geckodriver 可执行文件")
            
            # 设置执行权限
            os.chmod(driver_path, 0o755)
            logger.info(f"✅ 驱动准备完成: {driver_path}")
            
            # 删除压缩包
            if os.path.exists(archive_path):
                os.remove(archive_path)
                logger.info(f"🗑️ 删除压缩包: {archive_path}")
            
            # 创建驱动
            service = Service(str(driver_path))
            self.driver = webdriver.Firefox(service=service, options=options)
            logger.info("✅ 驱动初始化成功")
            return self.driver
            
        except Exception as e:
            logger.error(f"❌ 直接下载失败: {e}")
            # 清理失败的文件
            if os.path.exists(archive_path):
                os.remove(archive_path)
            # 清理可能不完整的驱动文件
            if os.path.exists(driver_path):
                os.remove(driver_path)
            raise

    def ensure_initialized(self):
        """确保驱动已初始化"""
        if not self.driver:
            self.setup_driver()
        return True

    def login(self):
        """登录 Kaggle"""
        try:
            self.driver.get("https://www.kaggle.com/account/login?phase=emailSignIn")
            time.sleep(5)
            
            current_url = self.driver.current_url
            print(f"📍 当前页面: {current_url}")
            
            if "login" in current_url:
                if not self.email or not self.password:
                    print("❌ 需要登录但未提供账号密码")
                    return False
                
                print("🔐 执行自动登录...")
                email_input = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.NAME, "email"))
                )
                email_input.send_keys(self.email)
                
                password_input = self.driver.find_element(By.NAME, "password")
                password_input.send_keys(self.password)
                
                login_button = self.driver.find_element(By.XPATH, "//button[@type='submit']")
                login_button.click()
                
                WebDriverWait(self.driver, 15).until(
                    lambda d: "login" not in d.current_url
                )
                print("✅ 自动登录成功！")
                return True
            else:
                print("✅ 已登录状态")
                return True
                
        except Exception as e:
            logger.error(f"登录失败: {e}")
            return False

    def check_login_status(self):
        """检查登录状态"""
        print("🔍 检测登录状态...")
        self.driver.get("https://www.kaggle.com/account/login?phase=emailSignIn")
        time.sleep(5)
        
        current_url = self.driver.current_url
        print(f"📍 当前页面: {current_url}")
        
        if "login" in current_url:
            print("❌ 未登录状态")
            return False
        else:
            print("✅ 已登录状态")
            return True

    def run_notebook(self, notebook_path: str) -> bool:
        """运行指定的 notebook"""
        try:
            if not self.check_login_status():
                if not self.login():
                    return False
            
            notebook_url = f"https://www.kaggle.com/code/{notebook_path}/edit/run/265492693"
            print(f"📓 访问 notebook: {notebook_url}")
            
            self.driver.get(notebook_url)
            time.sleep(10)
            
            print("💾 保存版本...")
            save_version_btn = WebDriverWait(self.driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//button[.//span[text()='Save Version']]"))
            )
            save_version_btn.click()
            time.sleep(5)
            
            save_btn = WebDriverWait(self.driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//button[.//span[text()='Save']]"))
            )
            save_btn.click()
            time.sleep(5)
            
            print("🎉 无头模式自动化完成！")
            self.is_running = True
            self.last_activity_time = datetime.now()
            return True
            
        except Exception as e:
            logger.error(f"运行 notebook 失败: {e}")
            self.is_running = False
            return False

    def stop_session(self) -> bool:
        """停止当前会话 - 使用精确的按钮操作方式"""
        try:
            # 访问 Kaggle 首页
            print("🌐 访问 Kaggle 首页...")
            self.driver.get("https://www.kaggle.com")
            time.sleep(5)
            
            if "login" in self.driver.current_url:
                print("❌ 未登录状态")
                return False
            
            print("✅ 已登录状态")
            
            # 第一步：点击 View Active Events (P标签)
            print("1. 点击 'View Active Events'...")
            first_button_selectors = [
                "//p[contains(@class, 'sc-gGKoUb') and contains(text(), 'View Active Events')]",
                "//p[contains(text(), 'View Active Events')]",
                "//*[contains(@class, 'sc-gGKoUb') and contains(text(), 'View Active Events')]"
            ]
            
            first_button = None
            for selector in first_button_selectors:
                try:
                    first_button = WebDriverWait(self.driver, 10).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                    print(f"✅ 找到第一个按钮: {selector}")
                    break
                except:
                    continue
            
            if not first_button:
                print("❌ 未找到第一个按钮")
                return False
            
            self.driver.execute_script("arguments[0].click();", first_button)
            print("✅ 点击第一个按钮成功")
            time.sleep(3)
            
            # 第二步：点击 more_horiz 按钮
            print("2. 点击 'more_horiz' 按钮...")
            second_button_selectors = [
                "//button[contains(@class, 'sc-dcMTLQ') and contains(@class, 'ga-DKQj') and contains(text(), 'more_horiz')]",
                "//button[@aria-label='More options for stable-diffusion-webui-bot']",
                "//button[@title='More options for stable-diffusion-webui-bot']",
                "//button[contains(@class, 'sc-dcMTLQ') and contains(text(), 'more_horiz')]"
            ]
            
            second_button = None
            for selector in second_button_selectors:
                try:
                    second_button = WebDriverWait(self.driver, 10).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                    print(f"✅ 找到第二个按钮: {selector}")
                    break
                except:
                    continue
            
            if not second_button:
                print("❌ 未找到第二个按钮")
                return False
            
            self.driver.execute_script("arguments[0].click();", second_button)
            print("✅ 点击第二个按钮成功")
            time.sleep(3)
            
            # 第三步：点击 Stop Session (P标签)
            print("3. 点击 'Stop Session'...")
            third_button_selectors = [
                "//p[contains(@class, 'sc-hwddKA') and contains(text(), 'Stop Session')]",
                "//p[contains(text(), 'Stop Session')]",
                "//*[contains(@class, 'sc-hwddKA') and contains(text(), 'Stop Session')]"
            ]
            
            third_button = None
            for selector in third_button_selectors:
                try:
                    third_button = WebDriverWait(self.driver, 10).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                    print(f"✅ 找到第三个按钮: {selector}")
                    break
                except:
                    continue
            
            if not third_button:
                print("❌ 未找到第三个按钮")
                return False
            
            self.driver.execute_script("arguments[0].click();", third_button)
            print("✅ 点击第三个按钮成功")
            print("🎉 所有操作完成！Session 已停止")
            self.is_running = False
            return True
            
        except Exception as e:
            print(f"❌ 操作失败: {e}")
            self.is_running = False
            return False

    def should_auto_stop(self, timeout_minutes: int) -> bool:
        """检查是否应该自动停止"""
        if not self.last_activity_time or not self.is_running:
            return False
        
        elapsed = datetime.now() - self.last_activity_time
        return elapsed.total_seconds() >= timeout_minutes * 60

    def update_activity_time(self):
        """更新活动时间"""
        self.last_activity_time = datetime.now()

    def close(self):
        """关闭浏览器"""
        if self.driver:
            self.driver.quit()
            self.driver = None
            self.is_running = False

@register("kaggle_auto", "AstrBot", "Kaggle Notebook 自动化插件", "1.0.0")
class KaggleAutoStar(Star):
    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config = config
        
        current_file = Path(__file__).parent
        self.plugin_data_dir = current_file.parent.parent / "plugin_data" / "astrbot_plugin_kagglerun"
        
        self.notebooks: Dict[str, str] = {}
        self.notebooks_file = self.plugin_data_dir / "kaggle_notebooks.json"
        self.auto_stop_task = None
        
        self.kaggle_manager = KaggleAutomation(
            email=self.config.kaggle_email,
            password=self.config.kaggle_password,
            plugin_data_dir=self.plugin_data_dir
        )
        
        self.setup_directories()
        self.load_notebooks()
        self.start_auto_tasks()

    def setup_directories(self):
        """设置目录"""
        try:
            self.plugin_data_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"插件目录设置完成: {self.plugin_data_dir}")
        except Exception as e:
            logger.error(f"设置目录失败: {e}")

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
            self.plugin_data_dir.mkdir(parents=True, exist_ok=True)
            with open(self.notebooks_file, 'w', encoding='utf-8') as f:
                json.dump(self.notebooks, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存notebook列表失败: {e}")

    def start_auto_tasks(self):
        """启动自动任务"""
        if self.auto_stop_task:
            self.auto_stop_task.cancel()
        
        self.auto_stop_task = asyncio.create_task(self.auto_stop_monitor())

    async def auto_stop_monitor(self):
        """自动停止监控任务"""
        while True:
            try:
                await asyncio.sleep(60)
                
                if (self.kaggle_manager.is_running and 
                    self.config.auto_stop_enabled):
                    
                    if self.kaggle_manager.should_auto_stop(self.config.auto_stop_timeout):
                        logger.info("🛑 执行自动停止...")
                        if self.kaggle_manager.stop_session():
                            logger.info("✅ 自动停止成功")
                        else:
                            logger.error("❌ 自动停止失败")
                            
            except asyncio.CancelledError:
                logger.info("自动停止监控任务已取消")
                break
            except Exception as e:
                logger.error(f"自动停止监控错误: {e}")
                await asyncio.sleep(300)

    def get_notebook_by_identifier(self, identifier) -> Optional[Tuple[str, str]]:
        """通过序号或名称获取notebook"""
        try:
            identifier = str(identifier)
            
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
        except Exception as e:
            logger.error(f"获取notebook失败: {e}")
            return None

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
            "/kaggle stop - 停止会话\n"
            "/kaggle status - 查看状态\n"
            "/kaggle help - 显示帮助信息"
        )

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
        if name in self.notebooks:
            yield event.plain_result(f"❌ 名称 '{name}' 已存在")
            return
        
        if '/' not in path:
            yield event.plain_result("❌ Notebook路径格式错误，应为: username/slug")
            return
        
        self.notebooks[name] = path
        self.save_notebooks()
        yield event.plain_result(f"✅ 已添加: {name} -> {path}")
        yield event.plain_result(f"🔗 链接: https://www.kaggle.com/{path}")

    @kaggle_group.command("remove")
    async def kaggle_remove(self, event: AstrMessageEvent, name: str):
        """删除notebook"""
        if name in self.notebooks:
            del self.notebooks[name]
            self.save_notebooks()
            yield event.plain_result(f"✅ 已删除: {name}")
            return
        
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
        
        try:
            self.kaggle_manager.ensure_initialized()
            
            yield event.plain_result(f"🚀 开始运行 notebook: {notebook_name}")
            
            if self.kaggle_manager.run_notebook(notebook_path):
                yield event.plain_result(f"✅ Notebook {notebook_name} 运行完成！")
                if self.config.auto_stop_enabled:
                    yield event.plain_result(f"⏰ 将在 {self.config.auto_stop_timeout} 分钟后自动停止")
            else:
                yield event.plain_result(f"❌ Notebook {notebook_name} 运行失败")
                
        except Exception as e:
            yield event.plain_result(f"❌ 运行失败: {str(e)}")

    @kaggle_group.command("stop")
    async def kaggle_stop(self, event: AstrMessageEvent):
        """停止当前 Kaggle 会话"""
        try:
            yield event.plain_result("🛑 正在停止 Kaggle 会话...")
            
            if self.kaggle_manager.stop_session():
                yield event.plain_result("✅ Kaggle 会话已停止！")
            else:
                yield event.plain_result("❌ 停止 Kaggle 会话失败")
                
        except Exception as e:
            yield event.plain_result(f"❌ 停止失败: {str(e)}")

    @kaggle_group.command("status")
    async def kaggle_status(self, event: AstrMessageEvent):
        """查看状态"""
        status_info = f"""
📊 Kaggle 自动化状态:

🏃 运行状态: {'✅ 运行中' if self.kaggle_manager.is_running else '🛑 未运行'}
⏰ 自动停止: {'✅ 启用' if self.config.auto_stop_enabled else '❌ 禁用'}
🕐 停止超时: {self.config.auto_stop_timeout} 分钟
📝 Notebook数量: {len(self.notebooks)} 个
🔑 自动启动关键词: {', '.join(self.config.auto_start_keywords) if self.config.auto_start_keywords else '无'}
🔄 维持运行关键词: {', '.join(self.config.keep_running_keywords) if self.config.keep_running_keywords else '无'}
"""
        yield event.plain_result(status_info)

    @kaggle_group.command("help")
    async def kaggle_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = """
🤖 Kaggle 自动化助手使用指南:

/kaggle list - 查看notebook列表
/kaggle add <名称> <路径> - 添加notebook
/kaggle remove <名称> - 删除notebook
/kaggle run [名称] - 运行notebook
/kaggle stop - 停止当前会话
/kaggle status - 查看状态
/kaggle help - 显示此帮助信息

📝 使用示例:
/kaggle add sd-bot pigman2021/stable-diffusion-webui-bot
/kaggle run sd-bot

⚡ 自动功能:
- 自动停止: 运行后自动在设定时间后停止
- 关键词启动: 群聊中发送特定关键词自动启动默认notebook
- 维持运行: 检测到特定关键词会重置停止计时器

⚠️ 注意:
1. 请在插件配置中设置 Kaggle 邮箱和密码
2. notebook路径格式为 "用户名/notebook名称"
"""
        yield event.plain_result(help_text)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """群聊消息事件处理"""
        try:
            message = event.message_str
            
            if (self.config.auto_start_enabled and 
                self.should_auto_start(message) and 
                self.config.default_notebook and
                not self.kaggle_manager.is_running):
                
                notebook_info = self.get_notebook_by_identifier(self.config.default_notebook)
                if notebook_info:
                    notebook_name, notebook_path = notebook_info
                    logger.info(f"🚀 检测到自动启动关键词，启动默认notebook: {notebook_name}")
                    
                    await event.send(event.plain_result(f"🚀 检测到启动关键词，正在自动运行 {notebook_name}..."))
                    
                    self.kaggle_manager.ensure_initialized()
                    
                    if self.kaggle_manager.run_notebook(notebook_path):
                        await event.send(event.plain_result(f"✅ {notebook_name} 自动启动完成！"))
                        if self.config.auto_stop_enabled:
                            await event.send(event.plain_result(f"⏰ 将在 {self.config.auto_stop_timeout} 分钟后自动停止"))
                    else:
                        await event.send(event.plain_result(f"❌ {notebook_name} 自动启动失败"))
            
            if (self.kaggle_manager.is_running and 
                self.config.auto_stop_enabled and
                self.should_keep_running(message)):
                
                self.kaggle_manager.update_activity_time()
                logger.info("🔄 检测到维持运行关键词，重置停止计时器")
                
        except Exception as e:
            logger.error(f"群聊消息处理错误: {e}")

    def should_keep_running(self, message: str) -> bool:
        """检查消息中是否包含维持运行的关键词"""
        if not self.config.keep_running_keywords:
            return False
        
        message_lower = message.lower()
        for keyword in self.config.keep_running_keywords:
            if keyword.lower() in message_lower:
                logger.info(f"🔍 检测到维持运行关键词: {keyword}")
                return True
        return False

    def should_auto_start(self, message: str) -> bool:
        """检查消息中是否包含自动启动的关键词"""
        if not self.config.auto_start_keywords:
            return False
        
        message_lower = message.lower()
        for keyword in self.config.auto_start_keywords:
            if keyword.lower() in message_lower:
                logger.info(f"🚀 检测到自动启动关键词: {keyword}")
                return True
        return False

    async def terminate(self):
        """插件卸载时调用"""
        if self.kaggle_manager:
            self.kaggle_manager.close()
        
        if self.auto_stop_task:
            self.auto_stop_task.cancel()
            
        logger.info("🔚 Kaggle 自动化插件已卸载")
