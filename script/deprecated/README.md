# Deprecated Scripts

本目录存放已经退出主流程的历史启动脚本。

当前约定：

1. 当前阶段仍在使用的联调脚本，不带阶段名称。
2. 进入新阶段开发后，上一阶段联调脚本移动到本目录。
3. 归档脚本文件名必须带阶段名称标记，例如 `run_server_phase_c.sh`。

当前归档脚本：

1. `run_server_phase_b.sh`
   - Phase B 本地服务端启动脚本
2. `run_server_phase_b_remote.sh`
   - Phase B 远程服务端发布脚本
3. `run_glass_phase_b.sh`
   - Phase B 眼镜端启动脚本
4. `run_server_phase_c.sh`
   - Phase C 本地服务端启动脚本，已被 `script/run_server.sh local ...` 取代
5. `run_server_phase_c_remote.sh`
   - Phase C 远程服务端发布脚本，已被 `script/run_server.sh remote ...` 取代
6. `run_glass_phase_c.sh`
   - Phase C 眼镜端启动脚本，已被 `script/run_glass.sh` 取代

使用原则：

1. 日常联调统一使用：
   - `bash script/run_server.sh ...`
   - `bash script/run_glass.sh ...`
2. 仅在回溯 Phase B 历史实现、复盘旧问题或对照文档时再使用本目录脚本。
