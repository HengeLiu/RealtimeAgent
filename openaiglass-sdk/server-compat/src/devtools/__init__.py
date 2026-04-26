"""兼容旧导入路径的开发工具薄壳。"""

from _sdk_bridge import bridge_package

bridge_package(globals(), "devtools")
