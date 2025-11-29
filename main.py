import os
import json
import asyncio
import sys
import time
# [修复1] 引入 Any 用于配置类型提示
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
        
        # [优化3] 并发控制锁
        self._install_lock = asyncio.Lock() # 安装锁
        self._run_lock = asyncio.Lock()     # 运行锁 (核心)
        
        # 用户数据目录
        self.user_data_dir = self.data_dir / "browser_data"
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

    async def _ensure_browser_installed(self):
        """后台检测并安装 Firefox 浏览器"""
        async with self._install_lock:
            logger.info("🔍 [Playwright] 正在检查 Firefox 环境...")
            try:
                cmd = [sys.executable, "-m", "playwright", "install", "firefox"]
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
                except asyncio.TimeoutError:
                    process.kill()
                    raise Exception("下载浏览器超时，请检查网络或尝试手动安装")

                if process.returncode != 0:
                    err_msg = stderr.decode().strip()
                    if "Failed to install" not in err_msg and "Err" not in err_msg:
                        logger.debug(f"Playwright install output: {err_msg}")
                    else:
                        logger.error(f"❌ 浏览器安装失败: {err_msg}")
                        raise Exception(err_msg)
                logger.info("✅ [Playwright] Firefox 环境就绪")
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
        
        args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ]
        
        # [修复2] 修正 User-Agent 为合法的 Firefox UA，防止指纹风控
        firefox_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"

        self.context = await self.playwright.firefox.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=True,
            viewport={"width": 1920, "height": 1080},
            args=args,
            user_agent=firefox_ua # 使用匹配内核的 UA
        )
        
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

    async def close(self):
        """安全关闭所有资源"""
        logger.info("🔌 正在关闭浏览器资源...")
        try:
            if self.context:
                await asyncio.wait_for(self.context.close(), timeout=5.0)
            
            if self.playwright:
                await asyncio.wait_for(self.playwright.stop(), timeout=5.0)
                
        except asyncio.TimeoutError:
            logger.warning("⚠️ 关闭浏览器资源超时")
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

    async def run_notebook(self, notebook_path: str) -> Tuple[bool, str]:
        """
        [修复3] 运行 Notebook (增加并发锁)
        返回: (是否成功, 提示信息)
        """
        # 1. 检查是否正在运行
        if self.is_running:
            return False, "⚠️ 已有 Notebook 正在运行中，请先停止当前会话。"
            
        # 2. 检查是否正在启动中 (锁是否被占用)
        if self._run_lock.locked():
            return False, "⏳ 正在启动中，请勿重复操作..."

        # 3. 获取锁并执行
        async with self._run_lock:
            try:
                await self.init_browser()
                
                # 登录检查
                if not await self.check_login_status():
                    if not await self.login(): 
                        return False, "❌ 登录失败，请检查账号密码。"

                notebook_url = f"https://www.kaggle.com/code/{notebook_path}/edit"
                logger.info(f"📓 访问 Notebook: {notebook_url}")
                await self.page.goto(notebook_url, timeout=60000, wait_until="domcontentloaded")
                
                # 点击 Save Version
                try:
                    save_btn = self.page.locator("//button[.//span[text()='Save Version']]")
                    await save_btn.wait_for(state="visible", timeout=30000)
                    await save_btn.click()
                except Exception:
                     return False, "❌ 找不到 'Save Version' 按钮，可能是 Kaggle 界面变动或加载失败。"
                
                # 点击确认 Save
                try:
                    confirm_btn = self.page.locator("//button[.//span[text()='Save']]")
                    await confirm_btn.wait_for(state="visible", timeout=15000)
                    await confirm_btn.click()
                except Exception:
                    return False, "❌ 找不到确认保存按钮。"
                
                self.is_running = True
                self.last_activity_time = datetime.now()
                return True, "✅ 启动成功！"
                
            except Exception as e:
                logger.error(f"运行失败: {e}")
                return False, f"❌ 运行发生异常: {str(e)}"

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

            if not await click_any(["//p[contains(text(), 'View Active Events')]"]): return False
            await asyncio.sleep(1)
            if not await click_any(["//button[contains(text(), 'more_horiz')]"]): return False
            await asyncio.sleep(1)
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
    # [修复1] 修正类型提示为 Any，防止 IDE 误报
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
        while True:
            try:
                await asyncio.sleep(60)
                if self.config.auto_stop_enabled and self.manager.is_running:
                    if self.manager.should_auto_stop(self.config.auto_stop_timeout):
                        logger.info("⏰ 触发自动停止")
                        await self.manager.stop_session()
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(60)

    async def terminate(self):
        logger.info("🛑 Kaggle 插件正在卸载...")
        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        
        if self.manager:
            await self.manager.close()
        logger.info("✅ 资源释放完成")

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
        else:
            yield event.plain_result(f"❌ 未找到名为 {name} 的 Notebook")

    @kaggle_group.command("list")
    async def list_nb(self, event: AstrMessageEvent):
        msg = "\n".join([f"- {k}: {v}" for k,v in self.notebooks.items()])
        yield event.plain_result(f"Notebooks:\n{msg}" if msg else "无记录")

    @kaggle_group.command("run")
    async def run(self, event: AstrMessageEvent, name: str = None):
        target = name or self.config.default_notebook
        if not target or target not in self.notebooks:
            yield event.plain_result("❌ 未找到该 Notebook，请检查名称。")
            return
        
        # [优化3] 接收 manager 返回的状态和消息
        yield event.plain_result(f"🚀 正在尝试启动 {target}...")
        
        success, msg = await self.manager.run_notebook(self.notebooks[target])
        
        if success:
            # 成功时，追加自动停止提示
            if self.config.auto_stop_enabled:
                msg += f"\n(将在 {self.config.auto_stop_timeout} 分钟无活动后自动停止)"
            yield event.plain_result(msg)
        else:
            # 失败/忙碌时，直接返回错误原因
            yield event.plain_result(msg)

    @kaggle_group.command("stop")
    async def stop(self, event: AstrMessageEvent):
        yield event.plain_result("正在停止...")
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
                        # 尝试启动，使用 run_notebook 的锁机制来处理并发
                        # 如果锁住了，run_notebook 会返回 False，这里静默失败即可，或者打个日志
                        success, _ = await self.manager.run_notebook(path)
                        if success:
                            await event.send(event.plain_result(f"✅ 检测到关键词，已自动启动 {target}"))
                        # 如果失败（比如正在启动中），这里不做多余回复，避免打扰群聊

            # 保活
            if (self.config.auto_stop_enabled and self.manager.is_running):
                if any(kw.lower() in msg for kw in self.config.keep_running_keywords):
                    self.manager.last_activity_time = datetime.now()
                    
                    now = time.time()
                    if now - self.last_reply_time > 300:
                        self.last_reply_time = now
                        await event.send(event.plain_result("⏳ 检测到活跃指令，已自动延长 Kaggle 运行时长。"))
                    else:
                        logger.debug("保活触发 (静默)")
                    
        except Exception as e:
            logger.error(f"Kaggle 监听器错误: {e}")
