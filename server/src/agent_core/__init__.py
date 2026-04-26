"""兼容旧导入路径的 agent-core 薄壳。"""

from _sdk_bridge import bridge_package

bridge_package(globals(), "agent_core")
