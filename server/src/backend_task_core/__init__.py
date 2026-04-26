"""兼容旧导入路径的后台任务薄壳。"""

from _sdk_bridge import bridge_package

bridge_package(globals(), "backend_task_core")
