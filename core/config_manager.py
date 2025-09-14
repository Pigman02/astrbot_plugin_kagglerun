import json
from pathlib import Path
from typing import Dict

class ConfigManager:
    def __init__(self, config):
        self.config = config
        self.notebooks_file = Path("data/kaggle_notebooks.json")
        self.notebooks: Dict[str, str] = {}
        
    def load_notebooks(self):
        """加载notebook列表"""
        try:
            if self.notebooks_file.exists():
                with open(self.notebooks_file, 'r', encoding='utf-8') as f:
                    self.notebooks = json.load(f)
            else:
                self.notebooks = {}
                self.save_notebooks()
        except Exception as e:
            print(f"加载notebook列表失败: {e}")
            self.notebooks = {}

    def save_notebooks(self):
        """保存notebook列表"""
        try:
            self.notebooks_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.notebooks_file, 'w', encoding='utf-8') as f:
                json.dump(self.notebooks, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存notebook列表失败: {e}")

    def add_notebook(self, name: str, path: str) -> bool:
        """添加notebook"""
        if name in self.notebooks:
            return False
        self.notebooks[name] = path
        self.save_notebooks()
        return True

    def remove_notebook(self, identifier: str) -> bool:
        """删除notebook"""
        from utils.helpers import parse_identifier
        result = parse_identifier(identifier, self.notebooks)
        if not result:
            return False
        
        name, _ = result
        del self.notebooks[name]
        self.save_notebooks()
        return True

    def get_notebook(self, identifier: str) -> Optional[Tuple[str, str]]:
        """获取notebook信息"""
        from utils.helpers import parse_identifier
        return parse_identifier(identifier, self.notebooks)