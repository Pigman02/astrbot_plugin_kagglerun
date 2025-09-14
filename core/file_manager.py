import zipfile
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional

class FileManager:
    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.setup_directories()
        
    def setup_directories(self):
        """设置输出目录"""
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"设置输出目录失败: {e}")

    async def create_zip_archive(self, source_dir: Path, zip_path: Path) -> bool:
        """创建ZIP压缩包"""
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file in source_dir.rglob('*'):
                    if file.is_file():
                        arcname = file.relative_to(source_dir)
                        zipf.write(file, arcname)
            return True
        except Exception as e:
            print(f"创建压缩包失败: {e}")
            return False

    async def download_and_package_output(self, notebook_path: str, notebook_name: str) -> Optional[Path]:
        """下载并打包输出文件"""
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_name = f"{timestamp}_{notebook_name}"
            
            api = KaggleApi()
            api.authenticate()
            
            if '/' not in notebook_path:
                return None
            
            username, slug = notebook_path.split('/', 1)
            
            # 创建临时目录并下载
            temp_dir = self.output_dir / "temp" / output_name
            temp_dir.mkdir(parents=True, exist_ok=True)
            api.kernels_output(f"{username}/{slug}", path=str(temp_dir))
            
            # 创建ZIP文件
            zip_filename = f"{output_name}.zip"
            zip_path = self.output_dir / zip_filename
            
            success = await self.create_zip_archive(temp_dir, zip_path)
            
            shutil.rmtree(temp_dir, ignore_errors=True)
            
            return zip_path if success else None
            
        except Exception as e:
            print(f"打包输出文件失败: {e}")
            return None

    def list_output_files(self, limit: int = 5):
        """列出输出文件"""
        files = list(self.output_dir.glob('*.zip'))
        files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        return files[:limit]