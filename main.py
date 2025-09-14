import os
from astrbot.api.star import Context, Star, register
from .core.kaggle_manager import KaggleManager
from .core.file_manager import FileManager
from .core.config_manager import ConfigManager
from .core.command_handler import CommandHandler

class KagglePlugin(Star):
    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config = config
        
        # 初始化各个模块
        self.file_manager = FileManager(self.config.output_dir)
        self.config_manager = ConfigManager(config)
        self.kaggle_manager = KaggleManager(self.file_manager, self.config_manager)
        self.command_handler = CommandHandler(self.kaggle_manager, self.config_manager, config)
        
        # 加载配置和启动任务
        self.config_manager.load_notebooks()
        self.setup_kaggle_api()
        self.kaggle_manager.start_cleanup_task(self.config.retention_days)

    def setup_kaggle_api(self):
        """设置Kaggle API配置"""
        try:
            kaggle_dir = os.path.expanduser('~/.kaggle')
            os.makedirs(kaggle_dir, exist_ok=True)
            
            kaggle_config = {
                "username": self.config.kaggle_username,
                "key": self.config.kaggle_api_key
            }
            
            config_path = os.path.join(kaggle_dir, 'kaggle.json')
            with open(config_path, 'w') as f:
                json.dump(kaggle_config, f)
            os.chmod(config_path, 0o600)
            
        except Exception as e:
            print(f"Kaggle API配置失败: {e}")

    async def terminate(self):
        """插件卸载时清理"""
        self.kaggle_manager.stop_cleanup()

# 注册命令处理器的方法
def register_commands(plugin_instance):
    """注册所有命令"""
    command_handler = plugin_instance.command_handler
    
    # 注册所有命令方法
    plugin_instance.kaggle_group = command_handler.kaggle_group
    plugin_instance.kaggle_main = command_handler.kaggle_main
    plugin_instance.kaggle_list = command_handler.kaggle_list
    plugin_instance.kaggle_add = command_handler.kaggle_add
    plugin_instance.kaggle_remove = command_handler.kaggle_remove
    plugin_instance.kaggle_run = command_handler.kaggle_run
    plugin_instance.kaggle_outputs = command_handler.kaggle_outputs
    plugin_instance.kaggle_off = command_handler.kaggle_off
    plugin_instance.kaggle_status = command_handler.kaggle_status
    plugin_instance.kaggle_config = command_handler.kaggle_config

@register("kaggle_runner", "AstrBot", "Kaggle Notebook执行插件", "1.0.0")
class KaggleRunner(KagglePlugin):
    def __init__(self, context: Context, config):
        super().__init__(context, config)
        register_commands(self)
