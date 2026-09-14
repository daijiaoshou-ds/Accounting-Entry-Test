import os
import sys

# ==================== 基础路径获取 ====================

def get_app_root():
    """获取程序运行时的物理根目录 (EXE所在目录 或 代码根目录)"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.abspath(".")

# 已经废弃，使用 get_app_root 代替
def get_internal_root():
    """获取程序内部临时目录 (PyInstaller 解压目录)"""
    try:
        return sys._MEIPASS
    except Exception:
        return os.path.abspath(".")

# ==================== 1. 用户数据 (读写/外部) ====================

def get_user_data_dir():
    target_dir = os.path.join(get_app_root(), "user_data")
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
    return target_dir

def get_config_path(filename):
    return os.path.join(get_user_data_dir(), filename)

def get_schema_dir():
    path = os.path.join(get_user_data_dir(), "schemas")
    if not os.path.exists(path):
        os.makedirs(path)
    return path

# ==================== 2. 界面资源 (只读/内部打包) ====================
# 图标、字体必须打包，否则由单文件exe运行时找不到会报错

def get_asset_path(relative_path):
    """
    获取打包进 EXE 的资源 (assets/fonts, assets/icon.ico)
    """
    return os.path.join(get_app_root(), relative_path)

# ==================== 3. AI 模型资源 (只读/外部挂载) ====================
# NLP模型、U2Net模型 太大，不打包，放在 EXE 旁边的 assets 文件夹里

def get_model_dir_root():
    """
    获取外部模型根目录: .../assets/models
    用于设置 U2NET_HOME 环境变量
    """
    return os.path.join(get_app_root(), "assets", "models")

def get_model_path(model_name):
    """
    获取 NLP 具体模型路径 (如 text2vec-base-chinese)
    """
    # 优先去外部 assets/models/nlp 找
    target_path = os.path.join(get_model_dir_root(), "nlp", model_name)
    
    if os.path.exists(target_path):
        return target_path
    
    return None