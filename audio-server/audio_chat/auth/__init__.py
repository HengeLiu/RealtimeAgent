"""
阿里云号码认证模块
"""
from .aliyun_auth import auth_service, AliyunAuthService, AuthResult
from .routes import setup_auth_routes

__all__ = ['auth_service', 'AliyunAuthService', 'AuthResult', 'setup_auth_routes']
