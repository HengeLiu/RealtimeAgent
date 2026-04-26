"""兼容旧导入路径的协议层薄壳。"""

from _sdk_bridge import bridge_package

bridge_package(globals(), "protocol")
