import os
import json
import asyncio
import sys
from typing import Dict, Optional
from datetime import datetime
from pathlib import Path

# AstrBot 核心导入
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

# Playwright 导入
from playwright.async_api import async_playwright, Playwright, Browser, BrowserContext, Page

class KaggleManager:
    """Kaggle 自动化管理器 (逻辑层)"""
    
    def __init__(self, email: str, password: str, data_dir: Path):
        self.email = email
        self.password = password
        self.data_dir = data_dir
        
        # Playwright 对象
        self.playwright: Optional[Playwright] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        
        # 状态
        self.is_running = False
        self.last_activity_time = None
        
        # 用户数据目录
        self.user_data_dir = self.data_dir / "browser_data"
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

    async def _ensure_browser_installed(self):
        """后台检测并安装浏览器"""
        logger.info("🔍 正在检查 Firefox 环境...")
        try:
            cmd = [sys.executable, "-m", "playwright", "install", "firefox"]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await process.communicate()
            
            if process.returncode != 0:
                err_msg = stderr.decode().strip()
                # 忽略一些非致命警告
                if "Failed to install" in err_msg:
                    logger.error(f"❌ 浏览器安装失败: {err_msg}")
                    raise Exception(err_msg)
            logger.info("✅ Firefox 环境就绪")
        except Exception as e:
            logger.error(f"环境检查异常: {e}")
            raise

    async def init_browser(self):
        """初始化浏览器资源"""
        if self.page and not self.page.is_closed():
            return

        await self._ensure_browser_installed()

        logger.info("🚀 启动 Playwright (Firefox)...")
        self.playwright = await async_playwright().start()
        
        # 启动持久化上下文
        self.context = await self.playwright.firefox.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=True, # 生产环境建议 True
            viewport={"width": 1920, "height": 1080},
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

    async def close(self):
        """安全关闭所有资源"""
        logger.info("🔌 正在关闭浏览器资源...")
        try:
            if self.context:
                await self.context.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception as e:
            logger.error(f"关闭浏览器资源时出错 (可忽略): {e}")
        finally:
            self.context = None
            self.playwright = None
            self.page = None
            self.is_running = False

    async def check_login_status(self) -> bool:
        if not self.page: await self.init_browser()
        try:
            await self.page.goto("https://www.kaggle.com/account/login?phase=emailSignIn", wait_until="domcontentloaded")
            await asyncio.sleep(3)
            return "login" not in self.page.url
        except Exception:
            return False

    async def login(self) -> bool:
        if not self.email or not self.password: return False
        try:
            if "login" not in self.page.url:
                await self.page.goto("https://www.kaggle.com/account/login?phase=emailSignIn")
            
            await self.page.wait_for_selector("input[name='email']", timeout=15000)
            await self.page.fill("input[name='email']", self.email)
            await self.page.fill("input[name='password']", self.password)
            await self.page.click("button[type='submit']")
            await self.page.wait_for_url(lambda url: "login" not in url, timeout=30000)
            return True
        except Exception as e:
            logger.error(f"登录失败: {e}")
            return False

    async def run_notebook(self, notebook_path: str) -> bool:
        try:
            await self.init_browser()
            if not await self.check_login_status():
                if not await self.login(): return False

            notebook_url = f"https://www.kaggle.com/code/{notebook_path}/edit/run/265492693"
            await self.page.goto(notebook_url, timeout=60000, wait_until="domcontentloaded")
            
            # 点击 Save Version
            save_btn = self.page.locator("//button[.//span[text()='Save Version']]")
            await save_btn.wait_for(state="visible", timeout=30000)
            await save_btn.click()
            
            # 点击确认 Save
            confirm_btn = self.page.locator("//button[.//span[text()='Save']]")
            await confirm_btn.wait_for(state="visible", timeout=15000)
            await confirm_btn.click()
            
            self.is_running = True
            self.last_activity_time = datetime.now()
            return True
        except Exception as e:
            logger.error(f"运行失败: {e}")
            return False

    async def stop_session(self) -> bool:
        try:
            if not self.page: return False
            await self.page.goto("https://www.kaggle.com", wait_until="domcontentloaded")
            if "login" in self.page.url: return False

            async def click_any(selectors):
                for s in selectors:
                    loc = self.page.locator(s)
                    if await loc.count() > 0 and await loc.first.is_visible():
                        await loc.first.click()
                        return True
                return False

            # 1. View Active Events
            if not await click_any(["//p[contains(text(), 'View Active Events')]"]): return False
            await asyncio.sleep(1)
            # 2. More Options
            if not await click_any(["//button[contains(text(), 'more_horiz')]"]): return False
            await asyncio.sleep(1)
            # 3. Stop Session
            if not await click_any(["//p[contains(text(), 'Stop Session')]"]): return False
            
            self.is_running = False
            return True
        except Exception as e:
            logger.error(f"停止失败: {e}")
            return False

    def should_auto_stop(self, timeout_minutes: int) -> bool:
        if not self.last_activity_time or not self.is_running: return False
        elapsed = datetime.now() - self.last_activity_time
        return elapsed.total_seconds() >= timeout_minutes * 60

@register("kaggle_auto", "AstrBot", "Kaggle Notebook 自动化插件", "1.0.0")
class KaggleAutoStar(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        
        # 路径配置
        self.plugin_data_dir = Path(context.base_config_dir) / "data" / "plugins" / "astrbot_plugin_kaggle"
        self.plugin_data_dir.mkdir(parents=True, exist_ok=True)
        self.notebooks_file = self.plugin_data_dir / "notebooks.json"
        
        self.notebooks: Dict[str, str] = {}
        self.manager = KaggleManager(self.config.kaggle_email, self.config.kaggle_password, self.plugin_data_dir)
        
        self.load_notebooks()
        
        # 启动监控任务，并保存引用以便取消
        self.monitor_task = asyncio.create_task(self.auto_stop_monitor())

    def load_notebooks(self):
        if self.notebooks_file.exists():
            try:
                with open(self.notebooks_file, 'r', encoding='utf-8') as f:
                    self.notebooks = json.load(f)
            except: self.notebooks = {}

    def save_notebooks(self):
        with open(self.notebooks_file, 'w', encoding='utf-8') as f:
            json.dump(self.notebooks, f, indent=2, ensure_ascii=False)

    async def auto_stop_monitor(self):
        """后台监控任务"""
        while True:
            try:
                await asyncio.sleep(60)
                if self.config.auto_stop_enabled and self.manager.is_running:
                    if self.manager.should_auto_stop(self.config.auto_stop_timeout):
                        logger.info("⏰ 触发自动停止")
                        await self.manager.stop_session()
            except asyncio.CancelledError:
                logger.info("监控任务已取消")
                break
            except Exception:
                await asyncio.sleep(60)

    # ================= 核心修正点：Terminate 方法 =================
    async def terminate(self):
        """
        插件卸载/Bot关闭时的生命周期钩子。
        必须清理所有后台任务和外部进程。
        """
        logger.info("🛑 Kaggle 插件正在卸载，开始清理资源...")
        
        # 1. 取消 Python 层的后台任务
        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        
        # 2. 关闭 Playwright 浏览器进程
        # 这一步至关重要，否则服务器上会残留大量 firefox 僵尸进程
        if self.manager:
            await self.manager.close()
            
        logger.info("✅ Kaggle 插件资源已全部释放")

    # ================= 指令处理 =================
    @filter.command_group("kaggle")
    def kaggle_group(self): pass

    @kaggle_group.command("help")
    async def help(self, event: AstrMessageEvent):
        yield event.plain_result("/kaggle list | add | remove | run | stop | status")

    @kaggle_group.command("add")
    async def add(self, event: AstrMessageEvent, name: str, path: str):
        self.notebooks[name] = path
        self.save_notebooks()
        yield event.plain_result(f"已添加: {name}")

    @kaggle_group.command("list")
    async def list_nb(self, event: AstrMessageEvent):
        msg = "\n".join([f"- {k}: {v}" for k,v in self.notebooks.items()])
        yield event.plain_result(f"Notebooks:\n{msg}" if msg else "无记录")

    @kaggle_group.command("run")
    async def run(self, event: AstrMessageEvent, name: str = None):
        target = name or self.config.default_notebook
        if not target or target not in self.notebooks:
            yield event.plain_result("未找到该 Notebook")
            return
        
        yield event.plain_result(f"🚀 正在启动 {target}，请稍候...")
        if await self.manager.run_notebook(self.notebooks[target]):
            yield event.plain_result(f"✅ {target} 启动成功")
        else:
            yield event.plain_result("❌ 启动失败")

    @kaggle_group.command("stop")
    async def stop(self, event: AstrMessageEvent):
        yield event.plain_result("正在停止...")
        if await self.manager.stop_session():
            yield event.plain_result("✅ 已停止")
        else:
            yield event.plain_result("❌ 停止失败")

    # ================= 核心修正点：标准消息监听器 =================
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_msg(self, event: AstrMessageEvent):
        """
        监听群组消息。
        注意：
        1. 使用 try-except 防止后台报错影响主线程
        2. 检查 sender_id != self_id 防止死循环 (虽然 EventMessageType.GROUP_MESSAGE 通常不包含自己，但加上更安全)
        3. 这里不 yield 结果，而是使用 event.send() 主动发送，这是符合逻辑的。
        """
        # 1. 基础过滤
        if not event.message_str:
            return
            
        # 2. 防止死循环（如果平台适配器没过滤自己的话）
        self_id = event.get_self_id()
        sender_id = event.get_sender_id()
        if self_id and sender_id and self_id == sender_id:
            return

        msg = event.message_str.lower()
        
        try:
            # 逻辑 A: 自动启动
            if (self.config.auto_start_enabled and 
                not self.manager.is_running and 
                self.config.default_notebook):
                
                if any(kw.lower() in msg for kw in self.config.auto_start_keywords):
                    target = self.config.default_notebook
                    path = self.notebooks.get(target)
                    if path:
                        logger.info(f"🚀 关键词触发: {target}")
                        # 主动发送消息
                        await event.send(event.plain_result(f"检测到关键词，自动启动 {target}..."))
                        
                        if await self.manager.run_notebook(path):
                            await event.send(event.plain_result(f"✅ {target} 启动成功"))
                        else:
                            await event.send(event.plain_result("❌ 自动启动失败"))

            # 逻辑 B: 保活 (重置计时器)
            if (self.config.auto_stop_enabled and 
                self.manager.is_running):
                
                if any(kw.lower() in msg for kw in self.config.keep_running_keywords):
                    logger.debug("自动保活触发")
                    self.manager.last_activity_time = datetime.now()
                    
        except Exception as e:
            # 监听器内部错误只打印日志，不抛出，避免影响其他插件
            logger.error(f"Kaggle 监听器错误: {e}")
