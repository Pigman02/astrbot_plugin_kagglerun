import subprocess
import os
import threading

# SakuraFRP配置参数 - 已去除敏感信息
serverAddr = "example.com"
serverPort = 8080
user = "anonymous_user"

# 代理配置
proxy_config = {
    "name": "example_proxy",
    "type": "tcp",
    "localIP": "127.0.0.1",
    "localPort": 7860,
    "remotePort": 50000,
}

# 使用SakuraFRP的配置格式
sakura_config_content = f"""[common]
user = {user}
sakura_mode = true
login_fail_exit = false
server_addr = {serverAddr}
server_port = {serverPort}

[{proxy_config['name']}]
type = {proxy_config['type']}
local_ip = {proxy_config['localIP']}
local_port = {proxy_config['localPort']}
remote_port = {proxy_config['remotePort']}
"""

print("生成SakuraFRP配置文件...")

# 写入配置文件
config_filename = '/kaggle/working/sakura_frpc.ini'
with open(config_filename, 'w') as config_file:
    config_file.write(sakura_config_content)
print(f"配置文件已创建: {config_filename}")

# 复制FRP客户端并设置权限
frpc_path = '/kaggle/working/sakura_frpc'
try:
    # 尝试从常见位置复制frpc客户端
    frp_found = False
    possible_paths = [
        '/kaggle/input/example-frp/frpc',
        '/kaggle/input/network-tools/frpc',
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            subprocess.run(['cp', path, frpc_path], check=True)
            print(f"从 {path} 复制frpc成功")
            frp_found = True
            break
    
    if not frp_found:
        # 尝试下载示例客户端
        print("尝试下载示例FRP客户端...")
        try:
            example_url = "https://example.com/frpc_linux_amd64"
            subprocess.run(['wget', '-q', example_url, '-O', frpc_path],
                          check=True, timeout=120)
            print("下载示例FRP客户端成功")
            frp_found = True
        except Exception as download_error:
            print(f"下载FRP客户端失败: {download_error}")
    
    if frp_found:
        # 设置执行权限
        subprocess.run(['chmod', '+x', frpc_path], check=True)
        print("FRP客户端权限设置成功")
        
        # 检查FRP客户端版本
        try:
            version_result = subprocess.run([frpc_path, '-v'],
                                          capture_output=True, text=True, timeout=10)
            print(f"FRP客户端版本: {version_result.stdout.strip()}")
        except:
            print("无法获取FRP客户端版本信息")
    else:
        print("无法找到或下载FRP客户端，但继续执行其他代码")
        
except Exception as e:
    print(f"设置FRP客户端时出错: {e}")

# 安装并运行FRPC
def install_frpc(config_path, local_port, remote_port, log_file_path):
    print(f'正在启动frp，本地端口{local_port} -> 远程端口{remote_port}')
    try:
        with open(log_file_path, 'w') as log_file:
            process = subprocess.Popen([frpc_path, '-c', config_path],
                                     stdout=log_file, stderr=log_file)
        # 等待一段时间让FRP启动
        subprocess.run(['sleep', '4'])
        # 显示日志内容
        subprocess.run(['cat', log_file_path])
        return process
    except Exception as e:
        print(f"启动FRP时出错: {e}")
        return None

# 在后台线程中启动FRP
def start_frp_in_background():
    log_filename = '/kaggle/working/sakura_frpc_log.txt'
    print("开始在后台启动SakuraFRP代理...")
    frp_process = install_frpc(config_filename, proxy_config['localPort'], proxy_config['remotePort'], log_filename)
    
    if frp_process:
        print(f"\nFRP代理已在后台启动!")
        print(f"📍 本地端口: {proxy_config['localPort']}")
        print(f"🌐 远程端口: {proxy_config['remotePort']}")
        print(f"🔗 公网域名地址: {serverAddr}:{proxy_config['remotePort']}")
        print(f"📝 日志文件: {log_filename}")
    else:
        print("FRP启动失败，但继续执行其他代码")

# 创建并启动后台线程
frp_thread = threading.Thread(target=start_frp_in_background)
frp_thread.daemon = True
frp_thread.start()

print("FRP隧道已在后台启动，继续执行其他代码...")

# 这里可以继续添加您的其他代码
print("开始执行主要任务...")

print("程序继续执行中...")
