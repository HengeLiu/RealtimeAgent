"""兼容旧导入路径的 API 薄壳。"""

from _sdk_bridge import bridge_package

bridge_package(globals(), "api")
