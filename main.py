import os
import json
import asyncio
import sys
import time
import re
from typing import Dict, Optional, Any, Tuple
from datetime import datetime
from pathlib import Path

# AstrBot 核心导入
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.star import StarTools 

# Playwright 导入
from playwright.async_api import async_playwright, Playwright, BrowserContext, Page

class KaggleManager:
    """Kaggle 自动化管理器 (任务制模式: 每次操作打开窗口，做完即关)"""
    
    def __init__(self, email: str, password: str, data_dir: Path):
        self.email = email
        self.password = password
        self.data_dir = data_dir
        
        # Playwright 对象 (不再长期持有)
        self.playwright: Optional[Playwright] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        
        # 状态记录
        self.is_running = False
        self.last_activity_time = None
        
        # 锁：确保同一时间只有一个浏览器窗口打开
        self._browser_lock = asyncio.Lock()
        
        # 用户数据目录
        self.user_data_dir = self.data_dir / "browser_data"
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

    async def _ensure_browser_installed(self):
        """后台检测并安装 Firefox"""
        logger.info("🔍 [Playwright] 检查环境...")
        try:
            cmd = [sys.executable, "-m", "playwright", "install", "firefox"]
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            try:
                await asyncio.wait_for(process.communicate(), timeout=300)
            except asyncio.TimeoutError:
                process.kill()
            if process.returncode != 0:
                logger.debug("Playwright install check finished (non-zero is ok if installed)")
        except Exception as e:
            logger.error(f"环境检查异常: {e}")

    async def launch_browser(self):
        """启动浏览器 (每次任务开始时调用)"""
        if self.page and not self.page.is_closed():
            return # 已经打开了

        await self._ensure_browser_installed()

        logger.info("🚀 启动浏览器窗口...")
        self.playwright = await async_playwright().start()
        
        args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ]
        firefox_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"

        # 使用持久化上下文，确保 Cookie 被保存，下次打开不用重新登录
        self.context = await self.playwright.firefox.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=True, # 调试时改为 False 可看到弹窗
            viewport={"width": 1920, "height": 1080},
            args=args,
            user_agent=firefox_ua
        )
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

    async def close_browser(self):
        """关闭浏览器 (每次任务结束后调用)"""
        logger.info("💤 任务完成，关闭浏览器...")
        try:
            if self.context:
                await asyncio.wait_for(self.context.close(), timeout=5.0)
            if self.playwright:
                await asyncio.wait_for(self.playwright.stop(), timeout=5.0)
        except Exception:
            pass
        finally:
            self.context = None
            self.playwright = None
            self.page = None

    async def check_login_status(self) -> bool:
        """检查登录状态"""
        try:
            await self.page.goto("https://www.kaggle.com/account/login?phase=emailSignIn", wait_until="domcontentloaded")
            await asyncio.sleep(2)
            return "login" not in self.page.url
        except Exception:
            return False

    async def login(self) -> bool:
        """执行登录"""
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

    async def run_notebook(self, notebook_path: str) -> Tuple[bool, str]:
        """
        启动 Notebook
        流程: 打开浏览器 -> 登录 -> 访问 -> 点击Save Version -> 点击Save -> 关闭浏览器
        """
        if self._browser_lock.locked():
            return False, "⏳ 正在执行其他任务，请稍候..."
        
        if self.is_running:
             return False, "⚠️ 记录显示已有任务在运行。如需重启请先执行 stop。"

        async with self._browser_lock:
            try:
                await self.launch_browser()
                
                # 1. 登录检查
                if not await self.check_login_status():
                    if not await self.login():
                        return False, "❌ 登录失败"

                # 2. 访问页面
                notebook_url = f"https://www.kaggle.com/code/{notebook_path}/edit"
                logger.info(f"📓 访问: {notebook_url}")
                await self.page.goto(notebook_url, timeout=60000, wait_until="domcontentloaded")

                # 3. 点击 Save Version
                try:
                    # 使用您指定的逻辑: get_by_role("button", name="Save Version")
                    save_ver_btn = self.page.get_by_role("button", name="Save Version")
                    await save_ver_btn.click(timeout=30000)
                except Exception:
                    return False, "❌ 未找到 'Save Version' 按钮"

                # 4. 点击 Save
                try:
                    # 使用您指定的逻辑: get_by_role("button", name="Save")
                    save_btn = self.page.get_by_role("button", name="Save", exact=True)
                    await save_btn.click(timeout=15000)
                except Exception:
                    return False, "❌ 未找到确认 'Save' 按钮"

                self.is_running = True
                self.last_activity_time = datetime.now()
                return True, "✅ 启动指令已发送！"

            except Exception as e:
                logger.error(f"启动异常: {e}")
                return False, f"❌ 异常: {str(e)}"
            finally:
                # 无论成功失败，都关闭浏览器
                await self.close_browser()

    async def stop_session(self) -> bool:
        """
        停止会话
        流程: 打开浏览器 -> 登录 -> 首页 -> View Active Events -> 列表项 -> Stop -> 关闭浏览器
        """
        if self._browser_lock.locked(): return False

        async with self._browser_lock:
            try:
                await self.launch_browser()
                
                if not await self.check_login_status():
                    if not await self.login(): return False

                await self.page.goto("https://www.kaggle.com", wait_until="domcontentloaded")

                # 1. 点击 View Active Events
                # 结合了您的逻辑，优先使用 button role，如果不匹配则退化到 text
                try:
                    # 正则匹配 "Active Events"，不区分前面是 View 还是数字
                    active_btn = self.page.get_by_role("button", name=re.compile(r"Active Events"))
                    if await active_btn.count() == 0:
                         # 备选：如果它不是 button，尝试找 text
                         active_btn = self.page.get_by_text(re.compile(r"Active Events"))
                    
                    if await active_btn.count() > 0 and await active_btn.first.is_visible():
                        await active_btn.first.click()
                        await asyncio.sleep(1)
                    else:
                        logger.warning("未找到 Active Events 按钮")
                        return False
                except Exception:
                    return False

                # 2. 在列表中找到对应 Notebook 的 More options
                # 您的原始逻辑：page.get_by_role("listitem", name="Status for...").get_by_label("More options...")
                # 优化：使用正则替换掉具体的 "stable-diffusion..." 名字，使其通用
                try:
                    # 匹配任何以 "Status for" 开头的列表项
                    list_item = self.page.get_by_role("listitem", name=re.compile(r"Status for .*"))
                    
                    if await list_item.count() > 0:
                        # 在这个列表项里找 "More options"
                        # 使用正则匹配 "More options for"
                        more_btn = list_item.first.get_by_label(re.compile(r"More options for .*"))
                        await more_btn.click()
                    else:
                        # 备选方案：直接找页面上所有的 More options 按钮
                        more_btn = self.page.get_by_label(re.compile(r"More options for .*"))
                        if await more_btn.count() > 0:
                            await more_btn.first.click()
                        else:
                            logger.warning("未找到正在运行的会话")
                            return False
                except Exception:
                    return False

                # 3. 点击 Stop Session
                await asyncio.sleep(1)
                stop_btn = self.page.get_by_text("Stop Session")
                if await stop_btn.count() > 0:
                    await stop_btn.click()
                    self.is_running = False
                    return True
                else:
                    return False

            except Exception as e:
                logger.error(f"停止异常: {e}")
                return False
            finally:
                # 任务结束，关闭浏览器
                await self.close_browser()

    def should_auto_stop(self, timeout_minutes: int) -> bool:
        if not self.last_activity_time or not self.is_running: return False
        elapsed = datetime.now() - self.last_activity_time
        return elapsed.total_seconds() >= timeout_minutes * 60

@register("kaggle_auto", "AstrBot", "Kaggle Notebook 自动化插件", "1.0.0")
class KaggleAutoStar(Star):
    def __init__(self, context: Context, config: Any):
        super().__init__(context)
        self.config = config
        
        self.plugin_data_dir = Path(StarTools.get_data_dir("astrbot_plugin_kagglerun"))
        self.plugin_data_dir.mkdir(parents=True, exist_ok=True)
        self.notebooks_file = self.plugin_data_dir / "notebooks.json"
        
        self.notebooks: Dict[str, str] = {}
        self.manager = KaggleManager(self.config.kaggle_email, self.config.kaggle_password, self.plugin_data_dir)
        self.last_reply_time = 0
        
        self.load_notebooks_sync()
        self.monitor_task = asyncio.create_task(self.auto_stop_monitor())

    def load_notebooks_sync(self):
        if self.notebooks_file.exists():
            try:
                with open(self.notebooks_file, 'r', encoding='utf-8') as f:
                    self.notebooks = json.load(f)
            except: self.notebooks = {}

    async def save_notebooks(self):
        def _write():
            with open(self.notebooks_file, 'w', encoding='utf-8') as f:
                json.dump(self.notebooks, f, indent=2, ensure_ascii=False)
        await asyncio.to_thread(_write)

    async def auto_stop_monitor(self):
        """后台监控任务"""
        while True:
            try:
                await asyncio.sleep(60)
                if self.config.auto_stop_enabled and self.manager.is_running:
                    if self.manager.should_auto_stop(self.config.auto_stop_timeout):
                        logger.info("⏰ 触发自动停止")
                        # 这会触发 launch_browser -> stop -> close_browser
                        await self.manager.stop_session()
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(60)

    async def terminate(self):
        logger.info("🛑 卸载插件...")
        if self.monitor_task: self.monitor_task.cancel()
        # 确保浏览器被关闭
        await self.manager.close_browser()

    # ================= 指令处理 =================
    @filter.command_group("kaggle")
    def kaggle_group(self): pass

    @kaggle_group.command("help")
    async def help(self, event: AstrMessageEvent):
        yield event.plain_result("/kaggle list | add | remove | run | stop | status")

    @kaggle_group.command("add")
    async def add(self, event: AstrMessageEvent, name: str, path: str):
        self.notebooks[name] = path
        await self.save_notebooks()
        yield event.plain_result(f"已添加: {name}")

    @kaggle_group.command("remove")
    async def remove(self, event: AstrMessageEvent, name: str):
        if name in self.notebooks:
            del self.notebooks[name]
            await self.save_notebooks()
            yield event.plain_result(f"✅ 已删除: {name}")

    @kaggle_group.command("list")
    async def list_nb(self, event: AstrMessageEvent):
        msg = "\n".join([f"- {k}: {v}" for k,v in self.notebooks.items()])
        yield event.plain_result(f"Notebooks:\n{msg}" if msg else "无记录")

    @kaggle_group.command("run")
    async def run(self, event: AstrMessageEvent, name: str = None):
        target = name or self.config.default_notebook
        if not target or target not in self.notebooks:
            yield event.plain_result("❌ 未找到该 Notebook")
            return
        
        yield event.plain_result(f"🚀 正在启动 {target} (将打开独立浏览器窗口)...")
        success, msg = await self.manager.run_notebook(self.notebooks[target])
        
        if success and self.config.auto_stop_enabled:
            msg += f"\n(将在 {self.config.auto_stop_timeout} 分钟后自动停止)"
        yield event.plain_result(msg)

    @kaggle_group.command("stop")
    async def stop(self, event: AstrMessageEvent):
        yield event.plain_result("正在停止 (将打开独立浏览器窗口)...")
        if await self.manager.stop_session():
            yield event.plain_result("✅ 已停止")
        else:
            yield event.plain_result("❌ 停止失败")

    @kaggle_group.command("status")
    async def status(self, event: AstrMessageEvent):
        state = "🟢 运行中" if self.manager.is_running else "⚪ 空闲"
        yield event.plain_result(f"状态: {state}\n自动停止: {self.config.auto_stop_enabled}")

    # ================= 消息监听 =================
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_msg(self, event: AstrMessageEvent):
        if not event.message_str: return
        if event.get_self_id() == event.get_sender_id(): return

        msg = event.message_str.lower()
        
        try:
            # 自动启动
            if (self.config.auto_start_enabled and 
                not self.manager.is_running and 
                self.config.default_notebook):
                
                if any(kw.lower() in msg for kw in self.config.auto_start_keywords):
                    target = self.config.default_notebook
                    path = self.notebooks.get(target)
                    if path:
                        # 尝试启动
                        success, _ = await self.manager.run_notebook(path)
                        if success:
                            await event.send(event.plain_result(f"✅ 自动启动 {target}"))

            # 保活
            if (self.config.auto_stop_enabled and self.manager.is_running):
                if any(kw.lower() in msg for kw in self.config.keep_running_keywords):
                    self.manager.last_activity_time = datetime.now()
                    now = time.time()
                    if now - self.last_reply_time > 300:
                        self.last_reply_time = now
                        await event.send(event.plain_result("⏳ 已延长运行时长"))
        except Exception:
            pass
