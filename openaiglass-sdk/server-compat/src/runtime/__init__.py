"""兼容旧导入路径的运行时薄壳。"""

from _sdk_bridge import bridge_package

bridge_package(globals(), "runtime")
