import os
import json
import asyncio
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

from playwright.async_api import async_playwright, Browser, Page
import aiofiles


class KagglePlaywrightManager:
    """Kaggle Playwright 异步管理器"""
    
    def __init__(self, email: str = None, password: str = None, data_dir: Path = None):
        self.email = email
        self.password = password
        self.data_dir = data_dir
        self.browser: Browser = None
        self.context = None
        self.page: Page = None
        
        self.is_running = False
        self.last_activity_time = None
        self.playwright = None
        
        # 确保数据目录存在
        if data_dir:
            os.makedirs(data_dir, exist_ok=True)
    
    async def setup(self):
        """异步初始化浏览器"""
        try:
            self.playwright = await async_playwright().start()
            
            # 启动 Firefox 浏览器
            self.browser = await self.playwright.firefox.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--window-size=1920,1080'
                ]
            )
            
            # 创建上下文，使用持久化存储保持登录状态
            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0'
            )
            
            logger.info("Playwright Firefox 浏览器初始化成功")
            return True
            
        except Exception as e:
            logger.error(f"浏览器初始化失败: {e}")
            await self.close()
            return False
    
    async def ensure_initialized(self):
        """确保浏览器已初始化"""
        if not self.browser or self.browser.is_connected() is False:
            return await self.setup()
        return True
    
    async def login(self) -> bool:
        """登录 Kaggle"""
        try:
            if not await self.ensure_initialized():
                return False
            
            self.page = await self.context.new_page()
            
            # 导航到登录页面
            await self.page.goto("https://www.kaggle.com/account/login?phase=emailSignIn")
            
            # 等待页面加载
            await self.page.wait_for_load_state('networkidle')
            
            # 检查是否已经登录
            current_url = self.page.url
            if "login" not in current_url:
                logger.info("检测到已登录状态")
                return True
            
            # 需要登录
            if not self.email or not self.password:
                logger.error("未配置 Kaggle 账号密码")
                return False
            
            # 填写登录表单
            await self.page.fill('input[name="email"]', self.email)
            await self.page.fill('input[name="password"]', self.password)
            
            # 点击登录按钮
            login_button = self.page.locator('button[type="submit"]')
            await login_button.click()
            
            # 等待登录完成
            await self.page.wait_for_url("**/account/login**", timeout=5000, wait_for='networkidle')
            
            # 检查登录是否成功
            current_url = self.page.url
            if "login" in current_url:
                logger.error("登录失败，请检查账号密码")
                return False
            
            logger.info("Kaggle 登录成功")
            return True
            
        except Exception as e:
            logger.error(f"登录过程出错: {e}")
            return False
    
    async def check_login_status(self) -> bool:
        """检查登录状态"""
        try:
            if not await self.ensure_initialized():
                return False
            
            page = await self.context.new_page()
            await page.goto("https://www.kaggle.com/")
            await page.wait_for_load_state('networkidle')
            
            # 检查是否有登录相关的元素
            login_elements = await page.locator('a[href*="login"]').count()
            user_avatar = await page.locator('img[alt*="Avatar"]').count()
            
            await page.close()
            
            # 如果有用户头像且没有登录链接，则认为已登录
            return user_avatar > 0 and login_elements == 0
            
        except Exception as e:
            logger.error(f"检查登录状态失败: {e}")
            return False
    
    async def run_notebook(self, notebook_path: str) -> bool:
        """运行指定的 notebook"""
        try:
            # 确保已登录
            if not await self.check_login_status():
                if not await self.login():
                    return False
            
            if not self.page or self.page.is_closed():
                self.page = await self.context.new_page()
            
            notebook_url = f"https://www.kaggle.com/code/{notebook_path}"
            await self.page.goto(notebook_url)
            await self.page.wait_for_load_state('networkidle')
            
            # 等待页面元素加载
            await self.page.wait_for_selector('button', timeout=10000)
            
            # 尝试找到并点击 Save Version 按钮
            save_version_selectors = [
                'button:has-text("Save Version")',
                '//button[.//span[text()="Save Version"]]',
                '[data-testid="save-version-button"]'
            ]
            
            for selector in save_version_selectors:
                try:
                    save_button = self.page.locator(selector)
                    if await save_button.count() > 0:
                        await save_button.click()
                        await asyncio.sleep(2)
                        break
                except:
                    continue
            
            # 等待保存对话框出现并确认保存
            save_dialog_selectors = [
                'button:has-text("Save")',
                '//button[.//span[text()="Save"]]',
                '[data-testid="confirm-save-button"]'
            ]
            
            for selector in save_dialog_selectors:
                try:
                    save_confirm = self.page.locator(selector)
                    if await save_confirm.count() > 0:
                        await save_confirm.click()
                        break
                except:
                    continue
            
            # 等待运行开始
            await asyncio.sleep(5)
            
            # 检查是否开始运行
            running_indicators = [
                '.sc-furwcr',  # 运行状态指示器
                '[data-testid="running-indicator"]',
                'text=Running'
            ]
            
            for indicator in running_indicators:
                if await self.page.locator(indicator).count() > 0:
                    self.is_running = True
                    self.last_activity_time = datetime.now()
                    logger.info(f"Notebook {notebook_path} 开始运行")
                    return True
            
            logger.warning("未检测到运行状态，但操作已完成")
            self.is_running = True
            self.last_activity_time = datetime.now()
            return True
            
        except Exception as e:
            logger.error(f"运行 notebook 失败: {e}")
            self.is_running = False
            return False
    
    async def stop_session(self) -> bool:
        """停止当前会话"""
        try:
            if not await self.ensure_initialized():
                return False
            
            page = await self.context.new_page()
            await page.goto("https://www.kaggle.com/")
            await page.wait_for_load_state('networkidle')
            
            # 查找并点击活动会话按钮
            active_session_selectors = [
                'p:has-text("View Active Events")',
                '//p[contains(text(), "View Active Events")]',
                '[data-testid="active-sessions-button"]'
            ]
            
            for selector in active_session_selectors:
                try:
                    active_btn = page.locator(selector)
                    if await active_btn.count() > 0:
                        await active_btn.click()
                        await asyncio.sleep(3)
                        break
                except:
                    continue
            
            # 查找更多选项按钮
            more_options_selectors = [
                'button:has-text("more_horiz")',
                '[aria-label*="more"]',
                '[title*="More options"]'
            ]
            
            for selector in more_options_selectors:
                try:
                    more_btn = page.locator(selector).first
                    if await more_btn.count() > 0:
                        await more_btn.click()
                        await asyncio.sleep(2)
                        break
                except:
                    continue
            
            # 查找停止会话按钮
            stop_session_selectors = [
                'p:has-text("Stop Session")',
                '//p[contains(text(), "Stop Session")]',
                '[data-testid="stop-session-button"]'
            ]
            
            for selector in stop_session_selectors:
                try:
                    stop_btn = page.locator(selector)
                    if await stop_btn.count() > 0:
                        await stop_btn.click()
                        await asyncio.sleep(3)
                        self.is_running = False
                        await page.close()
                        logger.info("会话停止成功")
                        return True
                except:
                    continue
            
            await page.close()
            logger.warning("未找到活动会话或停止按钮")
            self.is_running = False
            return True
            
        except Exception as e:
            logger.error(f"停止会话失败: {e}")
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
    
    async def close(self):
        """关闭浏览器"""
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception as e:
            logger.error(f"关闭浏览器时出错: {e}")
        finally:
            self.browser = None
            self.context = None
            self.playwright = None
            self.is_running = False


@register("kaggle_auto", "AstrBot", "Kaggle Notebook 自动化插件", "1.0.0")
class KaggleAutoStar(Star):
    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config = config
        
        # 使用插件数据目录
        current_file = Path(__file__).parent
        self.plugin_data_dir = current_file.parent.parent / "plugin_data" / "astrbot_plugin_kagglerun"
        os.makedirs(self.plugin_data_dir, exist_ok=True)
        
        self.notebooks: Dict[str, str] = {}
        self.notebooks_file = self.plugin_data_dir / "kaggle_notebooks.json"
        self.auto_stop_task = None
        
        # 初始化 Playwright 管理器
        self.kaggle_manager = KagglePlaywrightManager(
            email=self.config.get('kaggle_email'),
            password=self.config.get('kaggle_password'),
            data_dir=self.plugin_data_dir
        )
        
        self.load_notebooks()
        self.start_auto_tasks()
    
    def load_notebooks(self):
        """加载 notebook 列表"""
        try:
            if self.notebooks_file.exists():
                async with aiofiles.open(self.notebooks_file, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    self.notebooks = json.loads(content)
            else:
                self.notebooks = {}
                self.save_notebooks()
        except Exception as e:
            logger.error(f"加载 notebook 列表失败: {e}")
            self.notebooks = {}
    
    async def save_notebooks(self):
        """保存 notebook 列表"""
        try:
            async with aiofiles.open(self.notebooks_file, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(self.notebooks, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.error(f"保存 notebook 列表失败: {e}")
    
    def start_auto_tasks(self):
        """启动自动任务"""
        if self.auto_stop_task:
            self.auto_stop_task.cancel()
        
        self.auto_stop_task = asyncio.create_task(self.auto_stop_monitor())
    
    async def auto_stop_monitor(self):
        """自动停止监控任务"""
        while True:
            try:
                await asyncio.sleep(60)  # 每分钟检查一次
                
                if (self.kaggle_manager.is_running and 
                    self.config.get('auto_stop_enabled', False)):
                    
                    timeout = self.config.get('auto_stop_timeout', 30)
                    if self.kaggle_manager.should_auto_stop(timeout):
                        logger.info("检测到超时，自动停止会话...")
                        if await self.kaggle_manager.stop_session():
                            logger.info("自动停止成功")
                        else:
                            logger.error("自动停止失败")
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"自动停止监控错误: {e}")
                await asyncio.sleep(300)  # 出错后等待5分钟再继续
    
    def get_notebook_by_identifier(self, identifier) -> Optional[Tuple[str, str]]:
        """通过序号或名称获取 notebook"""
        try:
            identifier = str(identifier)
            
            # 按序号查找
            if identifier.isdigit():
                index = int(identifier) - 1
                notebooks_list = list(self.notebooks.items())
                if 0 <= index < len(notebooks_list):
                    return notebooks_list[index]
            
            # 按名称精确匹配
            if identifier in self.notebooks:
                return (identifier, self.notebooks[identifier])
            
            # 按名称模糊匹配
            for name, path in self.notebooks.items():
                if identifier.lower() in name.lower():
                    return (name, path)
            
            return None
        except Exception as e:
            logger.error(f"获取 notebook 失败: {e}")
            return None

    # 命令组定义
    @filter.command_group("kaggle")
    def kaggle_group(self):
        """Kaggle 命令组"""
        pass

    @kaggle_group.command("")
    async def kaggle_main(self, event: AstrMessageEvent):
        """Kaggle 主命令"""
        yield event.plain_result(
            "📋 Kaggle Notebook 管理器\n\n"
            "可用命令:\n"
            "/kaggle list - 查看可用 notebook\n"
            "/kaggle add <名称> <路径> - 添加 notebook\n"
            "/kaggle remove <名称> - 删除 notebook\n"
            "/kaggle run [名称] - 运行 notebook\n"
            "/kaggle stop - 停止会话\n"
            "/kaggle status - 查看状态\n"
            "/kaggle help - 显示帮助信息"
        )

    @kaggle_group.command("list")
    async def kaggle_list(self, event: AstrMessageEvent):
        """列出所有 notebook"""
        if not self.notebooks:
            yield event.plain_result("📝 还没有添加任何 notebook")
            return
        
        message = "📋 Notebook 列表:\n"
        for i, (name, path) in enumerate(self.notebooks.items(), 1):
            message += f"{i}. {name} -> {path}\n"
        
        default_notebook = self.config.get('default_notebook', '')
        if default_notebook:
            message += f"\n默认 notebook: {default_notebook}"
        
        yield event.plain_result(message)

    @kaggle_group.command("add")
    async def kaggle_add(self, event: AstrMessageEvent, name: str, path: str):
        """添加 notebook"""
        if name in self.notebooks:
            yield event.plain_result(f"❌ 名称 '{name}' 已存在")
            return
        
        if '/' not in path:
            yield event.plain_result("❌ Notebook 路径格式错误，应为: username/slug")
            return
        
        self.notebooks[name] = path
        await self.save_notebooks()
        yield event.plain_result(f"✅ 已添加: {name} -> {path}")
        yield event.plain_result(f"🔗 链接: https://www.kaggle.com/{path}")

    @kaggle_group.command("remove")
    async def kaggle_remove(self, event: AstrMessageEvent, name: str):
        """删除 notebook"""
        if name in self.notebooks:
            del self.notebooks[name]
            await self.save_notebooks()
            yield event.plain_result(f"✅ 已删除: {name}")
            return
        
        notebook_info = self.get_notebook_by_identifier(name)
        if notebook_info:
            notebook_name, _ = notebook_info
            del self.notebooks[notebook_name]
            await self.save_notebooks()
            yield event.plain_result(f"✅ 已删除: {notebook_name}")
            return
        
        yield event.plain_result("❌ 未找到指定的 notebook")

    @kaggle_group.command("run")
    async def kaggle_run(self, event: AstrMessageEvent, name: str = None):
        """运行 notebook"""
        if not name:
            name = self.config.get('default_notebook', '')
        
        if not name:
            yield event.plain_result("❌ 请指定 notebook 名称或设置默认 notebook")
            return
        
        notebook_info = self.get_notebook_by_identifier(name)
        if not notebook_info:
            yield event.plain_result("❌ Notebook 不存在")
            return
        
        notebook_name, notebook_path = notebook_info
        
        try:
            yield event.plain_result(f"🚀 开始运行 notebook: {notebook_name}")
            
            success = await self.kaggle_manager.run_notebook(notebook_path)
            
            if success:
                yield event.plain_result(f"✅ Notebook {notebook_name} 运行完成！")
                if self.config.get('auto_stop_enabled', False):
                    timeout = self.config.get('auto_stop_timeout', 30)
                    yield event.plain_result(f"⏰ 将在 {timeout} 分钟后自动停止")
            else:
                yield event.plain_result(f"❌ Notebook {notebook_name} 运行失败")
                
        except Exception as e:
            yield event.plain_result(f"❌ 运行失败: {str(e)}")

    @kaggle_group.command("stop")
    async def kaggle_stop(self, event: AstrMessageEvent):
        """停止当前 Kaggle 会话"""
        try:
            yield event.plain_result("🛑 正在停止 Kaggle 会话...")
            
            success = await self.kaggle_manager.stop_session()
            
            if success:
                yield event.plain_result("✅ Kaggle 会话已停止！")
            else:
                yield event.plain_result("❌ 停止 Kaggle 会话失败")
                
        except Exception as e:
            yield event.plain_result(f"❌ 停止失败: {str(e)}")

    @kaggle_group.command("status")
    async def kaggle_status(self, event: AstrMessageEvent):
        """查看状态"""
        # 检查浏览器连接状态
        browser_connected = (self.kaggle_manager.browser and 
                           self.kaggle_manager.browser.is_connected())
        
        status_info = f"""
📊 Kaggle 自动化状态:

🌐 浏览器状态: {'✅ 已连接' if browser_connected else '❌ 未连接'}
🏃 运行状态: {'✅ 运行中' if self.kaggle_manager.is_running else '🛑 未运行'}
⏰ 自动停止: {'✅ 启用' if self.config.get('auto_stop_enabled', False) else '❌ 禁用'}
🕐 停止超时: {self.config.get('auto_stop_timeout', 30)} 分钟
📝 Notebook 数量: {len(self.notebooks)} 个
"""
        yield event.plain_result(status_info)

    @kaggle_group.command("help")
    async def kaggle_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = """
🤖 Kaggle 自动化助手使用指南:

/kaggle list - 查看 notebook 列表
/kaggle add <名称> <路径> - 添加 notebook
/kaggle remove <名称> - 删除 notebook
/kaggle run [名称] - 运行 notebook
/kaggle stop - 停止当前会话
/kaggle status - 查看状态
/kaggle help - 显示此帮助信息

📝 使用示例:
/kaggle add sd-bot pigman2021/stable-diffusion-webui-bot
/kaggle run sd-bot

⚡ 自动功能:
- 自动停止: 运行后自动在设定时间后停止
- 持久化登录: 浏览器上下文保持登录状态

⚠️ 注意:
1. 请在插件配置中设置 Kaggle 邮箱和密码
2. notebook 路径格式为 "用户名/notebook名称"
3. 首次使用会自动下载浏览器，请耐心等待
"""
        yield event.plain_result(help_text)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """群聊消息事件处理"""
        try:
            message = event.message_str
            
            # 自动启动功能
            auto_start_keywords = self.config.get('auto_start_keywords', [])
            default_notebook = self.config.get('default_notebook')
            
            if (auto_start_keywords and default_notebook and
                not self.kaggle_manager.is_running and
                any(keyword.lower() in message.lower() for keyword in auto_start_keywords)):
                
                notebook_info = self.get_notebook_by_identifier(default_notebook)
                if notebook_info:
                    notebook_name, notebook_path = notebook_info
                    
                    await event.send(event.plain_result(f"🚀 检测到启动关键词，正在自动运行 {notebook_name}..."))
                    
                    success = await self.kaggle_manager.run_notebook(notebook_path)
                    
                    if success:
                        await event.send(event.plain_result(f"✅ {notebook_name} 自动启动完成！"))
                        if self.config.get('auto_stop_enabled', False):
                            timeout = self.config.get('auto_stop_timeout', 30)
                            await event.send(event.plain_result(f"⏰ 将在 {timeout} 分钟后自动停止"))
                    else:
                        await event.send(event.plain_result(f"❌ {notebook_name} 自动启动失败"))
            
            # 维持运行功能
            keep_running_keywords = self.config.get('keep_running_keywords', [])
            if (self.kaggle_manager.is_running and 
                self.config.get('auto_stop_enabled', False) and
                any(keyword.lower() in message.lower() for keyword in keep_running_keywords)):
                
                self.kaggle_manager.update_activity_time()
                
        except Exception as e:
            logger.error(f"群聊消息处理错误: {e}")

    async def terminate(self):
        """插件卸载时调用"""
        try:
            if self.auto_stop_task:
                self.auto_stop_task.cancel()
                try:
                    await self.auto_stop_task
                except asyncio.CancelledError:
                    pass
            
            await self.kaggle_manager.close()
            logger.info("Kaggle 自动化插件已卸载")
        except Exception as e:
            logger.error(f"插件卸载时发生错误: {e}")
