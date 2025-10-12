import os
import json
import asyncio
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
from webdriver_manager.firefox import GeckoDriverManager
import time

class KaggleAutomation:
    """Kaggle 自动化操作类"""
    
    def __init__(self, email=None, password=None):
        self.email = email
        self.password = password
        self.driver = None
        self.profile_dir = os.path.join(os.getcwd(), "kaggle_profile_firefox")
        self.is_running = False
        self.last_activity_time = None
        
    def setup_driver(self):
        """设置 Firefox 浏览器驱动"""
        options = Options()
        
        # 创建或使用现有的 Firefox 配置文件
        if not os.path.exists(self.profile_dir):
            os.makedirs(self.profile_dir)
        
        # 设置 Firefox 选项 - 全部使用无头模式
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--width=1920")
        options.add_argument("--height=1080")
        
        # 设置配置文件
        options.profile = self.profile_dir
        
        # 使用 webdriver-manager 自动管理驱动
        service = Service(GeckoDriverManager().install())
        
        # 初始化 Firefox 驱动
        self.driver = webdriver.Firefox(service=service, options=options)
        return self.driver
    
    def ensure_initialized(self):
        """确保驱动已初始化"""
        if not self.driver:
            self.setup_driver()
        return True
    
    def login(self):
        """登录 Kaggle 账户"""
        logger.info("🔍 检测登录状态...")
        self.driver.get("https://www.kaggle.com/account/login?phase=emailSignIn")
        time.sleep(5)
        
        current_url = self.driver.current_url
        logger.info(f"📍 当前页面: {current_url}")
        
        if "login" in current_url:
            if not self.email or not self.password:
                logger.error("❌ 需要登录但未提供账号密码")
                return False
            
            # 需要登录
            logger.info("🔐 执行自动登录...")
            try:
                email_input = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.NAME, "email"))
                )
                email_input.send_keys(self.email)
                
                password_input = self.driver.find_element(By.NAME, "password")
                password_input.send_keys(self.password)
                
                login_button = self.driver.find_element(By.XPATH, "//button[@type='submit']")
                login_button.click()
                
                # 等待跳转
                WebDriverWait(self.driver, 15).until(
                    lambda d: "login" not in d.current_url
                )
                logger.info("✅ 自动登录成功！")
                return True
            except Exception as e:
                logger.error(f"❌ 登录失败: {e}")
                return False
        else:
            logger.info("✅ 已登录状态")
            return True
    
    def check_login_status(self):
        """检查登录状态"""
        logger.info("🌐 访问 Kaggle 首页...")
        self.driver.get("https://www.kaggle.com")
        time.sleep(5)
        
        if "login" in self.driver.current_url:
            logger.info("❌ 未登录状态")
            return False
        else:
            logger.info("✅ 已登录状态")
            return True
    
    def run_notebook(self, notebook_slug):
        """
        运行指定的 notebook
        
        Args:
            notebook_slug: notebook 的 slug，格式为 "username/notebook-name"
        """
        try:
            # 检查登录状态
            if not self.check_login_status():
                if not self.login():
                    return False
            
            # 运行 notebook
            notebook_url = f"https://www.kaggle.com/code/{notebook_slug}/edit/run/265492693"
            logger.info(f"📓 访问 notebook: {notebook_url}")
            
            self.driver.get(notebook_url)
            time.sleep(10)
            
            # 保存版本
            logger.info("💾 保存版本...")
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
            
            logger.info("🎉 Notebook 运行完成！")
            self.is_running = True
            self.update_activity_time()
            return True
            
        except Exception as e:
            logger.error(f"❌ 运行 notebook 失败: {e}")
            self.save_screenshot("run_notebook_error.png")
            return False
    
    def stop_session(self):
        """停止当前会话"""
        try:
            # 检查登录状态
            if not self.check_login_status():
                if not self.login():
                    return False
            
            # 第一步：点击 View Active Events (P标签)
            logger.info("1. 点击 'View Active Events'...")
            view_active_events = WebDriverWait(self.driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//p[text()='View Active Events']"))
            )
            view_active_events.click()
            time.sleep(5)
            
            # 第二步：点击 Stop Session (Button标签)
            logger.info("2. 点击 'Stop Session'...")
            stop_session_btn = WebDriverWait(self.driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//button[.//span[text()='Stop Session']]"))
            )
            stop_session_btn.click()
            time.sleep(5)
            
            # 第三步：确认停止 (Button标签)
            logger.info("3. 确认停止...")
            confirm_stop_btn = WebDriverWait(self.driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//button[.//span[text()='Stop Session']]"))
            )
            confirm_stop_btn.click()
            time.sleep(5)
            
            logger.info("🛑 会话已停止！")
            self.is_running = False
            return True
            
        except Exception as e:
            logger.error(f"❌ 停止会话失败: {e}")
            self.save_screenshot("stop_session_error.png")
            return False
    
    def update_activity_time(self):
        """更新活动时间"""
        self.last_activity_time = datetime.now()
        logger.info(f"🕐 活动时间已更新: {self.last_activity_time}")
    
    def should_auto_stop(self, timeout_minutes):
        """检查是否应该自动停止"""
        if not self.is_running or not self.last_activity_time:
            return False
        
        timeout_duration = timedelta(minutes=timeout_minutes)
        time_since_last_activity = datetime.now() - self.last_activity_time
        
        if time_since_last_activity > timeout_duration:
            logger.info(f"🛑 检测到超时，自动停止会话。超时时间: {timeout_minutes}分钟")
            return True
        return False
    
    def save_screenshot(self, filename="kaggle_error.png"):
        """保存截图"""
        try:
            screenshot_path = os.path.join(os.getcwd(), filename)
            self.driver.save_screenshot(screenshot_path)
            logger.info(f"📸 截图已保存: {screenshot_path}")
        except Exception as e:
            logger.error(f"❌ 截图保存失败: {e}")
    
    def close(self):
        """关闭浏览器"""
        if self.driver:
            self.driver.quit()
            self.driver = None
            self.is_running = False
            self.last_activity_time = None
            logger.info("🔚 浏览器已关闭")

@register("kaggle_auto", "AstrBot", "Kaggle Notebook 自动化插件", "1.0.0")
class KaggleAutoStar(Star):
    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config = config
        self.notebooks: Dict[str, str] = {}
        self.plugin_data_dir = Path("data/plugin_data/kaggle_auto")
        self.notebooks_file = self.plugin_data_dir / "kaggle_notebooks.json"
        self.auto_stop_task = None
        
        # 初始化 Kaggle 管理器
        self.kaggle_manager = KaggleAutomation(
            email=self.config.kaggle_email,
            password=self.config.kaggle_password
        )
        
        # 初始化
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
            self.notebooks_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.notebooks_file, 'w', encoding='utf-8') as f:
                json.dump(self.notebooks, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存notebook列表失败: {e}")

    def start_auto_tasks(self):
        """启动自动任务"""
        # 自动停止任务
        if self.auto_stop_task:
            self.auto_stop_task.cancel()
        
        self.auto_stop_task = asyncio.create_task(self.auto_stop_monitor())

    async def auto_stop_monitor(self):
        """自动停止监控任务"""
        while True:
            try:
                await asyncio.sleep(60)  # 每分钟检查一次
                
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

    @filter.command_group("kaggle")
    def kaggle_group(self):
        """Kaggle命令组"""
        pass

    @kaggle_group.command("")
    async def kaggle_main(self, event: AstrMessageEvent, *args, **kwargs):
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
    async def kaggle_list(self, event: AstrMessageEvent, *args, **kwargs):
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
    async def kaggle_add(self, event: AstrMessageEvent, name: str, path: str, *args, **kwargs):
        """添加notebook"""
        if name in self.notebooks:
            yield event.plain_result(f"❌ 名称 '{name}' 已存在")
            return
        
        # 验证notebook路径格式
        if '/' not in path:
            yield event.plain_result("❌ Notebook路径格式错误，应为: username/slug")
            return
        
        self.notebooks[name] = path
        self.save_notebooks()
        yield event.plain_result(f"✅ 已添加: {name} -> {path}")
        yield event.plain_result(f"🔗 链接: https://www.kaggle.com/{path}")

    @kaggle_group.command("remove")
    async def kaggle_remove(self, event: AstrMessageEvent, name: str, *args, **kwargs):
        """删除notebook"""
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
    async def kaggle_run(self, event: AstrMessageEvent, name: str = None, *args, **kwargs):
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
        
        try:
            # 确保驱动已初始化
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
    async def kaggle_stop(self, event: AstrMessageEvent, *args, **kwargs):
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
    async def kaggle_status(self, event: AstrMessageEvent, *args, **kwargs):
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
    async def kaggle_help(self, event: AstrMessageEvent, *args, **kwargs):
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
    async def on_group_message(self, event: AstrMessageEvent, *args, **kwargs):
        """群聊消息事件处理"""
        try:
            message = event.message_str
            
            # 检查自动启动关键词
            if (self.config.auto_start_enabled and 
                self.should_auto_start(message) and 
                self.config.default_notebook and
                not self.kaggle_manager.is_running):
                
                notebook_info = self.get_notebook_by_identifier(self.config.default_notebook)
                if notebook_info:
                    notebook_name, notebook_path = notebook_info
                    logger.info(f"🚀 检测到自动启动关键词，启动默认notebook: {notebook_name}")
                    
                    # 发送启动通知
                    await event.send(event.plain_result(f"🚀 检测到启动关键词，正在自动运行 {notebook_name}..."))
                    
                    # 确保驱动已初始化
                    self.kaggle_manager.ensure_initialized()
                    
                    if self.kaggle_manager.run_notebook(notebook_path):
                        await event.send(event.plain_result(f"✅ {notebook_name} 自动启动完成！"))
                        if self.config.auto_stop_enabled:
                            await event.send(event.plain_result(f"⏰ 将在 {self.config.auto_stop_timeout} 分钟后自动停止"))
                    else:
                        await event.send(event.plain_result(f"❌ {notebook_name} 自动启动失败"))
            
            # 检查维持运行关键词
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
        
        # 取消自动任务
        if self.auto_stop_task:
            self.auto_stop_task.cancel()
            
        logger.info("🔚 Kaggle 自动化插件已卸载")
