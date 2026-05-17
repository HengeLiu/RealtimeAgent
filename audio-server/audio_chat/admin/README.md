# 用户管理后台

## 功能概述

用户管理后台提供完整的用户数据管理和数据分析功能：

### 1. 用户管理
- 用户注册和登录记录
- 用户信息管理
- 用户状态跟踪

### 2. 数据埋点
- 功能使用统计
- 用户行为追踪
- 会话时长记录

### 3. 数据分析
- 用户活跃度统计（DAU/WAU/MAU）
- 功能使用排行
- 用户行为分析

## 数据库结构

### users 表
用户基本信息表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| phone_number | TEXT | 手机号（唯一） |
| user_id | TEXT | 用户ID（唯一） |
| nickname | TEXT | 昵称 |
| avatar_url | TEXT | 头像URL |
| created_at | TEXT | 创建时间 |
| last_login_at | TEXT | 最后登录时间 |
| login_count | INTEGER | 登录次数 |
| status | TEXT | 状态 |
| metadata | TEXT | 元数据（JSON） |

### feature_usage 表
功能使用埋点表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| user_id | TEXT | 用户ID |
| device_id | TEXT | 设备ID |
| feature_name | TEXT | 功能名称 |
| action | TEXT | 动作 |
| metadata | TEXT | 元数据（JSON） |
| created_at | TEXT | 创建时间 |

### user_sessions 表
用户会话表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| user_id | TEXT | 用户ID |
| device_id | TEXT | 设备ID |
| session_start | TEXT | 会话开始时间 |
| session_end | TEXT | 会话结束时间 |
| duration | INTEGER | 持续时间（秒） |
| events_count | INTEGER | 事件数量 |

## API 接口

### 用户管理接口

#### GET /api/admin/users
获取用户列表

**参数：**
- `limit`: 每页数量（默认100）
- `offset`: 偏移量（默认0）

**返回：**
```json
{
  "success": true,
  "data": {
    "users": [...],
    "total": 100,
    "limit": 100,
    "offset": 0
  }
}
```

#### GET /api/admin/users/{user_id}
获取用户详情

**返回：**
```json
{
  "success": true,
  "data": {
    "user": {...},
    "feature_usage": [...],
    "sessions": [...]
  }
}
```

### 数据分析接口

#### GET /api/admin/analytics/overview
获取数据概览

**返回：**
```json
{
  "success": true,
  "data": {
    "total_users": 100,
    "dau": 10,
    "wau": 30,
    "mau": 80,
    "total_events": 1000,
    "total_sessions": 500,
    "avg_session_duration": 180.5
  }
}
```

#### GET /api/admin/analytics/feature-usage
获取功能使用统计

**参数：**
- `days`: 统计天数（默认7）

**返回：**
```json
{
  "success": true,
  "data": [
    {
      "feature_name": "auth",
      "action": "one_click_login",
      "count": 50
    }
  ]
}
```

#### GET /api/admin/analytics/dau
获取日活统计

**参数：**
- `days`: 统计天数（默认7）

**返回：**
```json
{
  "success": true,
  "data": [
    {
      "date": "2026-05-16",
      "count": 10
    }
  ]
}
```

#### POST /api/admin/track
记录埋点事件

**请求体：**
```json
{
  "user_id": "user-8000",
  "device_id": "device-001",
  "feature_name": "voice_chat",
  "action": "start",
  "metadata": {
    "duration": 60
  }
}
```

## Web 管理界面

访问地址：`http://localhost:8765/admin`

### 功能列表

1. **数据概览**
   - 总用户数
   - 日活/周活/月活用户
   - 总事件数
   - 总会话数
   - 平均会话时长

2. **用户列表**
   - 查看所有用户
   - 查看用户详情
   - 查看用户行为记录

3. **功能使用统计**
   - 功能使用排行
   - 按时间筛选

4. **日活趋势**
   - 日活用户趋势图

## 使用示例

### 1. 一键登录自动埋点

当用户通过一键登录成功后，系统会自动：
1. 创建或更新用户信息
2. 记录登录事件
3. 更新登录次数

```python
# 自动触发
user = user_manager.create_user(
    phone_number="13800138000",
    user_id="user-8000"
)

analytics_manager.track_feature_usage(
    user_id="user-8000",
    device_id="device-001",
    feature_name="auth",
    action="one_click_login"
)
```

### 2. 手动埋点

在业务代码中手动记录用户行为：

```python
from audio_chat.admin import analytics_manager

# 记录语音通话
analytics_manager.track_feature_usage(
    user_id="user-8000",
    device_id="device-001",
    feature_name="voice_chat",
    action="start",
    metadata={"duration": 120}
)

# 记录图片识别
analytics_manager.track_feature_usage(
    user_id="user-8000",
    device_id="device-001",
    feature_name="image_recognition",
    action="capture",
    metadata={"result": "success"}
)
```

### 3. 查询用户数据

```python
from audio_chat.admin import user_manager, analytics_manager

# 获取用户信息
user = user_manager.get_user_by_user_id("user-8000")

# 获取用户行为记录
usage = analytics_manager.get_user_feature_usage("user-8000")

# 获取用户会话记录
sessions = analytics_manager.get_user_sessions("user-8000")
```

## 数据埋点建议

### 推荐埋点事件

1. **认证相关**
   - `auth.one_click_login` - 一键登录
   - `auth.logout` - 登出

2. **语音交互**
   - `voice_chat.start` - 开始语音通话
   - `voice_chat.end` - 结束语音通话
   - `voice_chat.interrupt` - 打断

3. **图像识别**
   - `image_recognition.capture` - 拍照
   - `image_recognition.result` - 识别结果

4. **设备控制**
   - `device.connect` - 设备连接
   - `device.disconnect` - 设备断开

5. **功能使用**
   - `feature.use` - 功能使用
   - `feature.error` - 功能错误

## 数据库位置

数据库文件位于：`audio-server/data/users.db`

## 注意事项

1. 数据库文件会自动创建
2. 所有埋点数据都会记录时间戳
3. 用户手机号会自动脱敏显示
4. 建议定期备份数据库文件
