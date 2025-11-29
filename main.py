import os
import json
import asyncio
import sys
import time
import re  # [新增] 引入正则模块，用于模糊匹配选择器
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
        
        # 并发控制锁
        self._install_lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
        
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
        
        # 伪装参数
        args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ]
        # Firefox 伪装 UA
        firefox_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"

        self.context = await self.playwright.firefox.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=True, # 调试时可改为 False
            viewport={"width": 1920, "height": 1080},
            args=args,
            user_agent=firefox_ua
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
            
            # 使用 get_by_role 或 locator 均可，这里保持原样因为它很稳定
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
        """运行 Notebook"""
        if self.is_running:
            return False, "⚠️ 已有 Notebook 正在运行中，请先停止当前会话。"
        if self._run_lock.locked():
            return False, "⏳ 正在启动中，请勿重复操作..."

        async with self._run_lock:
            try:
                await self.init_browser()
                if not await self.check_login_status():
                    if not await self.login(): 
                        return False, "❌ 登录失败，请检查账号密码。"

                notebook_url = f"https://www.kaggle.com/code/{notebook_path}/edit"
                logger.info(f"📓 访问 Notebook: {notebook_url}")
                await self.page.goto(notebook_url, timeout=60000, wait_until="domcontentloaded")
                
                # [更新] 使用语义化选择器点击 "Save Version"
                try:
                    # 优先尝试 get_by_role，这比 XPath 更稳
                    save_btn = self.page.get_by_role("button", name="Save Version")
                    await save_btn.click(timeout=30000)
                except Exception:
                     # 备用方案
                     return False, "❌ 找不到 'Save Version' 按钮，页面可能未加载完成。"
                
                # [更新] 点击确认 "Save"
                try:
                    # exact=True 确保只匹配 "Save" 而不是 "Save & Run" 之类的
                    confirm_btn = self.page.get_by_role("button", name="Save", exact=True)
                    await confirm_btn.click(timeout=15000)
                except Exception:
                    return False, "❌ 找不到确认保存按钮。"
                
                self.is_running = True
                self.last_activity_time = datetime.now()
                return True, "✅ 启动成功！"
                
            except Exception as e:
                logger.error(f"运行失败: {e}")
                return False, f"❌ 运行异常: {str(e)}"

    async def stop_session(self) -> bool:
        """
        停止会话
        [重点更新] 使用正则模糊匹配，不再依赖具体的 Notebook 名称
        """
        try:
            if not self.page: return False
            await self.page.goto("https://www.kaggle.com", wait_until="domcontentloaded")
            if "login" in self.page.url: return False

            # 1. 点击底部的 Active Events
            # 使用正则匹配，忽略可能存在的数字或View前缀
            active_bar = self.page.get_by_text(re.compile(r"Active Events"))
            if await active_bar.count() > 0:
                # 如果有多个（极少见），点第一个可见的
                for i in range(await active_bar.count()):
                    if await active_bar.nth(i).is_visible():
                        await active_bar.nth(i).click()
                        break
            else:
                logger.warning("未找到活动会话栏 (Active Events)")
                # 如果找不到，说明可能根本没运行，但也可能是收起来了，暂且返回失败
                return False

            await asyncio.sleep(1) # 等待列表动画

            # 2. 点击 "More options..." 菜单按钮
            # 关键：使用正则 ^More options for 匹配开头
            # 这样无论后面跟的是 stable-diffusion 还是其他名字，都能选中
            more_btn = self.page.get_by_label(re.compile(r"^More options for"))
            
            if await more_btn.count() > 0:
                # 默认停止列表中的第一个运行实例
                await more_btn.first.click()
            else:
                logger.warning("未找到更多选项按钮 (More options)")
                return False
            
            await asyncio.sleep(1) # 等待菜单弹出

            # 3. 点击 Stop Session
            stop_btn = self.page.get_by_text("Stop Session")
            if await stop_btn.count() > 0:
                await stop_btn.click()
                logger.info("🎉 成功点击 Stop Session")
                self.is_running = False
                return True
            else:
                logger.warning("未找到停止按钮")
                return False

        except Exception as e:
            logger.error(f"停止失败: {e}")
            return False

    def should_auto_stop(self, timeout_minutes: int) -> bool:
        if not self.last_activity_time or not self.is_running: return False
        elapsed = datetime.now() - self.last_activity_time
        return elapsed.total_seconds() >= timeout_minutes * 60

@register("kaggle_auto", "AstrBot", "Kaggle Notebook 自动化插件", "1.0.0")
class KaggleAutoStar(Star):
    def __init__(self, context: Context, config: Any):
        super().__init__(context)
        self.config = config
        
        # 路径规范化
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
        
        yield event.plain_result(f"🚀 正在尝试启动 {target}...")
        
        success, msg = await self.manager.run_notebook(self.notebooks[target])
        
        if success:
            if self.config.auto_stop_enabled:
                msg += f"\n(将在 {self.config.auto_stop_timeout} 分钟无活动后自动停止)"
            yield event.plain_result(msg)
        else:
            yield event.plain_result(msg)

    @kaggle_group.command("stop")
    async def stop(self, event: AstrMessageEvent):
        yield event.plain_result("正在停止...")
        if await self.manager.stop_session():
            yield event.plain_result("✅ 已停止")
        else:
            yield event.plain_result("❌ 停止失败，未找到运行中的会话。")
            
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
                        # 尝试启动，如果锁住了会静默失败
                        success, _ = await self.manager.run_notebook(path)
                        if success:
                            await event.send(event.plain_result(f"✅ 检测到关键词，已自动启动 {target}"))

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
