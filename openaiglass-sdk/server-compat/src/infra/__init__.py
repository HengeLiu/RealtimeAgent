"""兼容旧导入路径的基础设施薄壳。"""

from _sdk_bridge import bridge_package

bridge_package(globals(), "infra")
