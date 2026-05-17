"""
阿里云号码认证服务端接口 - 一键登录取号
"""
import os
import logging
from typing import Optional
from dataclasses import dataclass
from pathlib import Path

# 加载 .env 文件
env_file = Path(__file__).parent.parent.parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text().strip().split("\n"):
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            # 设置所有相关环境变量
            os.environ[key] = value

from audio_chat.admin import user_manager, analytics_manager

logger = logging.getLogger(__name__)

@dataclass
class AuthResult:
    success: bool
    phone_number: Optional[str] = None
    user_id: Optional[str] = None
    message: str = ""


class AliyunAuthService:
    """阿里云号码认证服务 - 一键登录取号"""

    def __init__(self):
        self.access_key_id = os.getenv("ALIYUN_ACCESS_KEY_ID", "")
        self.access_key_secret = os.getenv("ALIYUN_ACCESS_KEY_SECRET", "")
        self.initialized = False
        self.client = None

        logger.info(f"阿里云认证服务初始化: key_id存在={bool(self.access_key_id)}, key_secret存在={bool(self.access_key_secret)}")

        if self.access_key_id and self.access_key_secret:
            self._init_client()
    
    def _init_client(self):
        """初始化阿里云客户端"""
        try:
            from alibabacloud_dypnsapi20170525.client import Client as DypnsapiClient
            from alibabacloud_credentials.client import Client as CredentialClient
            from alibabacloud_credentials.models import Config as CredentialConfig
            from alibabacloud_tea_openapi import models as open_api_models

            # 使用显式凭据配置，而不是依赖环境变量自动检测
            credential_config = CredentialConfig(
                type='access_key',
                access_key_id=self.access_key_id,
                access_key_secret=self.access_key_secret
            )
            credential = CredentialClient(credential_config)
            config = open_api_models.Config(credential=credential)
            config.endpoint = 'dypnsapi.aliyuncs.com'

            self.client = DypnsapiClient(config)
            self.initialized = True
            logger.info("阿里云号码认证服务初始化成功")
        except Exception as e:
            logger.error(f"阿里云号码认证服务初始化失败: {e}")
            self.initialized = False
    
    async def get_mobile(self, token: str, out_id: Optional[str] = None) -> AuthResult:
        """
        一键登录取号
        
        Args:
            token: 客户端获取的 token
            out_id: 外部流水号（可选）
        
        Returns:
            AuthResult: 认证结果，包含手机号
        """
        if not self.initialized:
            logger.warning("阿里云服务未初始化，使用模拟验证")
            return self._mock_get_mobile(token)
        
        try:
            from alibabacloud_dypnsapi20170525 import models as dypnsapi_models
            from alibabacloud_tea_util import models as util_models

            request = dypnsapi_models.GetMobileRequest(
                access_token=token,
                out_id=out_id,
            )
            
            runtime = util_models.RuntimeOptions()
            response = await self.client.get_mobile_with_options_async(request, runtime)
            
            if response.body and response.body.code == "OK":
                phone_number = response.body.get_mobile_result_dto.mobile
                user_id = f"user-{phone_number[-4:]}" if phone_number else None

                if user_id and phone_number:
                    # 先查询用户是否已存在，避免重复写库
                    existing = user_manager.get_user_by_user_id(user_id)
                    if existing:
                        logger.info(f"用户已存在，跳过创建: {user_id}")

                logger.info(f"一键登录取号成功: {phone_number}")
                return AuthResult(
                    success=True,
                    phone_number=phone_number,
                    user_id=user_id,
                    message="取号成功"
                )
            else:
                error_msg = response.body.message if response.body else "未知错误"
                logger.warning(f"一键登录取号失败: {error_msg}")
                return AuthResult(success=False, message=error_msg)
                
        except Exception as e:
            logger.error(f"一键登录取号异常: {e}")
            return AuthResult(success=False, message=str(e))
    
    def _mock_get_mobile(self, token: str) -> AuthResult:
        """模拟取号（用于测试）"""
        logger.info(f"模拟一键登录取号: {token[:20]}...")
        return AuthResult(
            success=True,
            phone_number="138****8888",
            user_id="user-8888",
            message="模拟取号成功"
        )


auth_service = AliyunAuthService()
