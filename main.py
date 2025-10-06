from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api import AstrBotConfig
import asyncio
import threading
import json
import os
import time as time_module
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

@register("kaggle runner", "Developer", "Kaggle Notebook 运行器", "1.0.0")
class KaggleRunnerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.running_tasks = {}
        self.task_start_time = {}
        self.keyword_refresh_times = {}
        self.notebooks_file = os.path.join("data", "kaggle_notebooks.json")
        self._ensure_data_dir()
        self._load_notebooks()
        
        # 启动自动停止检测任务
        asyncio.create_task(self._auto_stop_monitor())
    
    def _ensure_data_dir(self):
        """确保数据目录存在"""
        data_dir = os.path.dirname(self.notebooks_file)
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
    
    def _load_notebooks(self):
        """加载保存的 notebooks"""
        try:
            if os.path.exists(self.notebooks_file):
                with open(self.notebooks_file, 'r', encoding='utf-8') as f:
                    self.notebooks = json.load(f)
            else:
                self.notebooks = {}
        except Exception as e:
            logger.error(f"加载 notebooks 失败: {e}")
            self.notebooks = {}
    
    def _save_notebooks(self):
        """保存 notebooks 到文件"""
        try:
            with open(self.notebooks_file, 'w', encoding='utf-8') as f:
                json.dump(self.notebooks, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存 notebooks 失败: {e}")
    
    async def _auto_stop_monitor(self):
        """自动停止监控任务"""
        while True:
            try:
                current_time = datetime.now()
                auto_stop_minutes = self.config.get("auto_stop_minutes", 30)
                
                users_to_stop = []
                for user_id, start_time in self.task_start_time.items():
                    if (current_time - start_time) > timedelta(minutes=auto_stop_minutes):
                        users_to_stop.append(user_id)
                
                for user_id in users_to_stop:
                    if user_id in self.running_tasks:
                        logger.info(f"自动停止用户 {user_id} 的任务（运行超过 {auto_stop_minutes} 分钟）")
                        del self.running_tasks[user_id]
                        del self.task_start_time[user_id]
                        # 这里可以发送通知消息
                        
            except Exception as e:
                logger.error(f"自动停止监控错误: {e}")
            
            await asyncio.sleep(60)  # 每分钟检查一次
    
    def _refresh_task_time(self, user_id: str):
        """刷新任务时间（当检测到关键词时调用）"""
        self.task_start_time[user_id] = datetime.now()
        logger.info(f"用户 {user_id} 的任务时间已刷新")
    
    @filter.command("kaggle add")
    async def kaggle_add(self, event: AstrMessageEvent, name: str, notebook_slug: str):
        """添加 Kaggle notebook 到收藏
        用法: /kaggle add <name> <notebook_slug>
        示例: /kaggle add sd-bot username/stable-diffusion-bot
        """
        if name in self.notebooks:
            yield event.plain_result(f"❌ 名称 '{name}' 已存在")
            return
        
        self.notebooks[name] = notebook_slug
        self._save_notebooks()
        
        yield event.plain_result(f"✅ 已添加 notebook: {name} -> {notebook_slug}")
    
    @filter.command("kaggle remove")
    async def kaggle_remove(self, event: AstrMessageEvent, name: str):
        """从收藏中移除 Kaggle notebook
        用法: /kaggle remove <name>
        示例: /kaggle remove sd-bot
        """
        if name not in self.notebooks:
            yield event.plain_result(f"❌ 未找到名称 '{name}'")
            return
        
        removed_slug = self.notebooks.pop(name)
        self._save_notebooks()
        
        yield event.plain_result(f"✅ 已移除 notebook: {name} ({removed_slug})")
    
    @filter.command("kaggle list")
    async def kaggle_list(self, event: AstrMessageEvent):
        """列出所有收藏的 Kaggle notebooks"""
        if not self.notebooks:
            yield event.plain_result("📝 暂无收藏的 notebooks")
            return
        
        result = "📚 收藏的 Kaggle notebooks:\n"
        for name, slug in self.notebooks.items():
            result += f"• {name}: {slug}\n"
        
        yield event.plain_result(result)
    
    @filter.command("kaggle run")
    async def kaggle_run(self, event: AstrMessageEvent, name: str = None, notebook_slug: str = None):
        """运行 Kaggle notebook
        用法: /kaggle run [name] 或 /kaggle run <notebook_slug>
        示例: /kaggle run sd-bot 或 /kaggle run username/notebook-name
        """
        user_id = event.get_sender_id()
        
        # 检查是否已有任务在运行
        if user_id in self.running_tasks and self.running_tasks[user_id].is_alive():
            yield event.plain_result("❌ 您已有一个任务正在运行，请等待完成")
            return
        
        # 确定要运行的 notebook
        target_slug = None
        if name:
            if name in self.notebooks:
                target_slug = self.notebooks[name]
            else:
                yield event.plain_result(f"❌ 未找到名称 '{name}'，使用 /kaggle list 查看所有收藏")
                return
        elif notebook_slug:
            target_slug = notebook_slug
        else:
            yield event.plain_result("❌ 请提供 notebook 名称或完整链接")
            return
        
        # 检查账号配置
        email = self.config.get("kaggle_email", "")
        password = self.config.get("kaggle_password", "")
        if not email or not password:
            yield event.plain_result("❌ 请先在 WebUI 中配置 Kaggle 账号和密码")
            return
        
        yield event.plain_result(f"🚀 开始运行 Kaggle notebook: {target_slug}")
        
        def run_callback(success, message):
            # 在事件循环中发送结果
            asyncio.run_coroutine_threadsafe(
                self._send_callback_result(event, message, user_id), 
                asyncio.get_event_loop()
            )
        
        # 在新线程中运行
        task = threading.Thread(
            target=self._run_kaggle_notebook,
            args=(target_slug, run_callback)
        )
        task.daemon = True
        task.start()
        
        self.running_tasks[user_id] = task
        self.task_start_time[user_id] = datetime.now()
        
        auto_stop_minutes = self.config.get("auto_stop_minutes", 30)
        yield event.plain_result(f"⏳ 任务已启动，正在后台运行...\n⏰ 自动停止时间: {auto_stop_minutes} 分钟")
    
    def _run_kaggle_notebook(self, notebook_slug: str, callback):
        """在单独线程中运行 Kaggle notebook"""
        try:
            # 从配置中获取账号信息
            email = self.config.get("kaggle_email", "")
            password = self.config.get("kaggle_password", "")
            
            if not email or not password:
                callback(False, "❌ 请先在 WebUI 中配置 Kaggle 账号和密码")
                return
            
            profile_dir = os.path.join(os.getcwd(), "kaggle_profile")
            
            options = Options()
            options.add_argument(f"--user-data-dir={profile_dir}")
            options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-images")
            
            driver = webdriver.Chrome(options=options)
            
            try:
                logger.info(f"开始运行 Kaggle notebook: {notebook_slug}")
                
                # 登录检测
                driver.get("https://www.kaggle.com/account/login?phase=emailSignIn")
                time_module.sleep(5)
                
                current_url = driver.current_url
                
                if "login" in current_url:
                    email_input = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.NAME, "email"))
                    )
                    email_input.send_keys(email)
                    
                    password_input = driver.find_element(By.NAME, "password")
                    password_input.send_keys(password)
                    
                    login_button = driver.find_element(By.XPATH, "//button[@type='submit']")
                    login_button.click()
                    
                    WebDriverWait(driver, 15).until(
                        lambda d: "login" not in d.current_url
                    )
                    logger.info("Kaggle 登录成功")
                
                # 运行 notebook - 使用基础的编辑页面
                notebook_url = f"https://www.kaggle.com/code/{notebook_slug}"
                driver.get(notebook_url)
                time_module.sleep(10)
                
                # 尝试找到并点击运行按钮
                run_selectors = [
                    "//button[contains(., 'Run')]",
                    "//button[contains(., '运行')]",
                    "//button[contains(@class, 'run')]",
                    "//span[contains(., 'Run')]/parent::button",
                    "//span[contains(., '运行')]/parent::button"
                ]
                
                run_button = None
                for selector in run_selectors:
                    try:
                        run_button = WebDriverWait(driver, 10).until(
                            EC.element_to_be_clickable((By.XPATH, selector))
                        )
                        logger.info(f"找到运行按钮: {selector}")
                        break
                    except:
                        continue
                
                if run_button:
                    driver.execute_script("arguments[0].click();", run_button)
                    logger.info("点击运行按钮成功")
                    time_module.sleep(5)
                    
                    callback(True, f"✅ Kaggle notebook '{notebook_slug}' 已开始运行！")
                else:
                    callback(False, f"❌ 未找到运行按钮，请检查 notebook 链接")
                
            except Exception as e:
                logger.error(f"Kaggle 运行错误: {e}")
                callback(False, f"❌ 运行失败: {str(e)}")
                
            finally:
                driver.quit()
                
        except Exception as e:
            logger.error(f"浏览器启动错误: {e}")
            callback(False, f"❌ 浏览器启动失败: {str(e)}")
    
    async def _send_callback_result(self, event: AstrMessageEvent, message: str, user_id: str):
        """发送回调结果"""
        if user_id in self.running_tasks:
            del self.running_tasks[user_id]
        if user_id in self.task_start_time:
            del self.task_start_time[user_id]
        
        # 使用主动消息发送结果
        await self.context.send_message(
            event.unified_msg_origin,
            message
        )
    
    @filter.command("kaggle stop")
    async def kaggle_stop(self, event: AstrMessageEvent):
        """停止当前用户的 Kaggle 任务"""
        user_id = event.get_sender_id()
        
        if user_id in self.running_tasks:
            yield event.plain_result("🛑 正在停止任务...")
            if user_id in self.running_tasks:
                del self.running_tasks[user_id]
            if user_id in self.task_start_time:
                del self.task_start_time[user_id]
            yield event.plain_result("✅ 任务已停止")
        else:
            yield event.plain_result("❌ 没有正在运行的任务")
    
    @filter.command("kaggle status")
    async def kaggle_status(self, event: AstrMessageEvent):
        """查看当前运行状态"""
        user_id = event.get_sender_id()
        
        if user_id in self.running_tasks and self.running_tasks[user_id].is_alive():
            if user_id in self.task_start_time:
                elapsed = datetime.now() - self.task_start_time[user_id]
                elapsed_minutes = int(elapsed.total_seconds() / 60)
                auto_stop_minutes = self.config.get("auto_stop_minutes", 30)
                remaining_minutes = max(0, auto_stop_minutes - elapsed_minutes)
                
                yield event.plain_result(f"🟢 有任务正在运行中...\n⏰ 已运行: {elapsed_minutes} 分钟，剩余: {remaining_minutes} 分钟")
            else:
                yield event.plain_result("🟢 有任务正在运行中...")
        else:
            yield event.plain_result("🔴 当前没有运行任务")
    
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """监听群聊消息，检测关键词刷新任务时间"""
        user_id = event.get_sender_id()
        
        # 检查用户是否有运行中的任务
        if user_id in self.running_tasks and self.running_tasks[user_id].is_alive():
            message_text = event.message_str.lower()
            
            # 从配置中获取刷新关键词
            refresh_keywords = self.config.get("refresh_keywords", "运行中,训练中,processing,training")
            keyword_list = [kw.strip().lower() for kw in refresh_keywords.split(",")]
            
            # 检查是否包含关键词
            for keyword in keyword_list:
                if keyword and keyword in message_text:
                    self._refresh_task_time(user_id)
                    
                    # 发送刷新通知（可选）
                    auto_stop_minutes = self.config.get("auto_stop_minutes", 30)
                    # await self.context.send_message(
                    #     event.unified_msg_origin,
                    #     f"⏰ 检测到关键词 '{keyword}'，任务时间已刷新，剩余 {auto_stop_minutes} 分钟"
                    # )
                    break
    
    async def terminate(self):
        """插件卸载时清理资源"""
        logger.info("Kaggle Runner 插件正在卸载...")
        for task in self.running_tasks.values():
            if task.is_alive():
                task.join(timeout=5)
