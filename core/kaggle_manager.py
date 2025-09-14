import asyncio
from typing import Optional, Dict
from datetime import datetime
from pathlib import Path

class KaggleManager:
    def __init__(self, file_manager, config_manager):
        self.file_manager = file_manager
        self.config_manager = config_manager
        self.running_notebooks: Dict[str, str] = {}
        self.cleanup_task = None
        
    def start_cleanup_task(self, retention_days: int):
        """启动清理任务"""
        self.cleanup_task = asyncio.create_task(self.cleanup_old_files(retention_days))

    async def cleanup_old_files(self, retention_days: int):
        """清理旧文件任务"""
        while True:
            try:
                await asyncio.sleep(3600)
                
                if not self.file_manager.output_dir.exists():
                    continue
                    
                cutoff_time = datetime.now() - timedelta(days=retention_days)
                
                for file_path in self.file_manager.output_dir.glob('*.zip'):
                    if file_path.is_file():
                        file_time = datetime.fromtimestamp(file_path.stat().st_mtime)
                        if file_time < cutoff_time:
                            file_path.unlink()
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"清理文件失败: {e}")
                await asyncio.sleep(300)

    async def stop_kaggle_notebook(self, notebook_path: str) -> bool:
        """强制停止运行的notebook"""
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
            api = KaggleApi()
            api.authenticate()
            
            if '/' not in notebook_path:
                return False
            
            username, slug = notebook_path.split('/', 1)
            
            kernels = api.kernels_list()
            for kernel in kernels:
                if kernel['ref'] == f"{username}/{slug}":
                    api.kernels_stop(kernel['id'])
                    return True
            
            return False
        except Exception as e:
            print(f"停止notebook失败: {e}")
            return False

    async def run_notebook(self, notebook_path: str, notebook_name: str, session_id: str = None) -> Optional[Path]:
        """运行notebook并返回输出文件路径"""
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
            api = KaggleApi()
            api.authenticate()
            
            # 记录运行中的notebook
            if session_id:
                self.running_notebooks[session_id] = notebook_name
            
            # 运行notebook
            result = api.kernels_push(notebook_path)
            
            if result.get('status') == 'ok':
                # 下载并打包输出
                zip_path = await self.file_manager.download_and_package_output(notebook_path, notebook_name)
                
                # 清理运行记录
                if session_id and session_id in self.running_notebooks:
                    del self.running_notebooks[session_id]
                
                return zip_path
            return None
                
        except Exception as e:
            print(f"运行Notebook失败: {e}")
            if session_id and session_id in self.running_notebooks:
                del self.running_notebooks[session_id]
            return None

    def stop_cleanup(self):
        """停止清理任务"""
        if self.cleanup_task:
            self.cleanup_task.cancel()