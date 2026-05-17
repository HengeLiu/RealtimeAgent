# 阿里云一键登录取号集成说明

## 概述

本项目已集成阿里云号码认证服务，支持**一键登录取号**功能，用户无需输入密码即可快速登录。

## 架构

```
┌─────────────────┐
│  Android App    │
│  - 用户界面      │
│  - Aliyun SDK   │
└────────┬────────┘
         │ HTTPS
         ▼
┌─────────────────┐
│  Server         │
│  - Auth API     │
│  - GetMobile    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  阿里云服务      │
│  - 号码认证     │
└─────────────────┘
```

## 服务端配置

### 1. 安装依赖

```bash
uv sync
```

或手动安装：

```bash
pip install alibabacloud-dypnsapi20170525==2.0.0
```

### 2. 配置环境变量

```bash
export ALIYUN_ACCESS_KEY_ID="your_access_key_id"
export ALIYUN_ACCESS_KEY_SECRET="your_access_key_secret"
```

### 3. 启动服务器

```bash
uv run audio-chat.server.run --config examples/for-blind-app/audio-server/server.yaml
```

### 4. 测试 API

```bash
python test_auth_api.py
```

## API 接口

### 一键登录取号

**POST** `/api/auth/get-mobile`

请求：
```json
{
  "token": "客户端获取的 token"
}
```

响应：
```json
{
  "success": true,
  "phone_number": "13800138000",
  "user_id": "user-8000",
  "message": "取号成功"
}
```

## Android 客户端配置

### 1. 复制 SDK 文件

将以下 AAR 文件复制到 `examples/android-phone/app/libs/` 目录：
- `auth_number_product-2.14.22-log-online-standard-cuum-release.aar`
- `logger-2.2.2-release.aar`
- `main-2.2.3-release.aar`

### 2. 配置 Scheme Code

在 `MainViewModel.kt` 中更新你的 Scheme Code：

```kotlin
aliyunAuthManager?.init("your_scheme_code")
```

### 3. 构建 APK

```bash
cd examples/android-phone
gradle assembleDebug
```

### 4. 安装 APK

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

## 模拟模式

如果未配置阿里云密钥，服务端会自动使用模拟模式：

- 一键登录取号：返回模拟的手机号 `138****8888`

## 文件结构

### 服务端

```
audio-server/audio_chat/auth/
├── __init__.py           # 模块入口
├── aliyun_auth.py        # 阿里云认证服务
└── routes.py             # API 路由
```

### Android 客户端

```
examples/android-phone/app/src/main/java/com/audiochat/phone/
├── auth/
│   └── AliyunAuthManager.kt    # 阿里云认证管理器
└── ui/
    ├── UserScreen.kt           # 用户界面
    ├── DebugScreen.kt          # 调试界面
    └── MainViewModel.kt        # ViewModel
```

## 注意事项

1. **生产环境**：必须在服务端配置真实的阿里云密钥
2. **测试环境**：可以使用模拟模式进行开发测试
3. **安全性**：不要将密钥提交到代码仓库
4. **签名配置**：需要在阿里云控制台配置应用签名

## 故障排查

### 服务端无法启动

检查依赖是否安装：
```bash
pip list | grep alibabacloud
```

### 一键登录失败

1. 检查 Scheme Code 是否正确
2. 检查应用签名是否匹配
3. 查看服务端日志

## 参考资料

- [阿里云号码认证服务文档](https://help.aliyun.com/product/75010.html)
- [Android SDK 集成指南](https://help.aliyun.com/document_detail/144231.html)
- [服务端 API 参考 - GetMobile](https://help.aliyun.com/document_detail/101614.html)
