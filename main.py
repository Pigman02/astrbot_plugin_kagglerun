import asyncio
import os
import json
from pathlib import Path
from datetime import datetime, timedelta

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

class KaggleChatPlugin(Star):
    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config = config
        self.running_tasks = {}

    def get_kaggle_api(self):
        from kaggle.api.kaggle_api_extended import KaggleApi
        api = KaggleApi()
        api.authenticate()
        return api

    async def stop_kernel_after(self, kernel_ref, minutes, event):
        await asyncio.sleep(minutes * 60)
        try:
            api = self.get_kaggle_api()
            kernels = api.kernels_list()
            for kernel in kernels:
                if getattr(kernel, 'ref', '') == kernel_ref:
                    api.kernels_stop(getattr(kernel, 'id', ''))
                    await event.send(event.plain_result(f"⏹️ 已自动停止: {kernel_ref}"))
                    logger.info(f"自动停止: {kernel_ref}")
                    return
        except Exception as e:
            error_msg = str(e) if e is not None else "未知错误"
            logger.error(f"自动停止失败: {error_msg}")

    @filter.command_group("kaggle")
    def kaggle_group(self):
        pass

    @kaggle_group.command("run")
    async def run_notebook(self, event: AstrMessageEvent, kernel_ref: str):
        """运行指定 Kaggle Notebook"""
        await event.send(event.plain_result(f"🚀 开始运行: {kernel_ref}"))
        try:
            api = self.get_kaggle_api()
            status = api.kernels_status(kernel_ref)
            if getattr(status, 'status', '') not in ['complete', 'success', 'finished']:
                await event.send(event.plain_result("⚠️ Notebook 可能正在运行或状态异常"))
            result = api.kernels_push(kernel_ref)
            await event.send(event.plain_result("✅ 已提交运行"))
            # 自动停止
            minutes = getattr(self.config, 'auto_stop_minutes', 30)
            asyncio.create_task(self.stop_kernel_after(kernel_ref, minutes, event))
        except Exception as e:
            error_msg = str(e) if e is not None else "未知错误"
            await event.send(event.plain_result(f"❌ 运行失败: {error_msg}"))
            logger.error(f"运行notebook失败: {error_msg}")

@register("kaggle_chat", "AstrBot", "Kaggle 群聊运行插件", "1.0.0")
class KaggleChat(KaggleChatPlugin):
    pass
