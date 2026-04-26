"""兼容旧导入路径的应用入口薄壳。"""

from _sdk_bridge import bridge_package

bridge_package(globals(), "app")
