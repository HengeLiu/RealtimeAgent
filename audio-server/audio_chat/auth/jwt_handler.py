"""
JWT Token 管理模块

主要功能：
- 生成 Access Token（短期有效，用于API认证）
- 生成 Refresh Token（长期有效，用于刷新Access Token）
- 验证 Token
- Token 刷新机制

安全特性：
- Access Token 有效期短（60分钟）
- Refresh Token 有效期长（7天）
- 每次刷新时生成新的双Token
- 使用环境变量存储密钥
"""
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import jwt
import logging

logger = logging.getLogger(__name__)

SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "audio-chat-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 7

# Admin API 独立密钥，用于管理后台接口认证
ADMIN_SECRET_TOKEN = os.environ.get("ADMIN_SECRET_TOKEN")


def verify_admin_token(token: str) -> bool:
    """验证 Admin Token。

    参数：`token` 为请求头中的 token。
    返回：验证通过返回 True，否则返回 False。
    """
    if not token or not ADMIN_SECRET_TOKEN:
        return False
    return secrets.compare_digest(token, ADMIN_SECRET_TOKEN)


class TokenData:
    """Token 数据结构"""
    def __init__(
        self,
        user_id: str,
        phone_number: Optional[str] = None,
        token_type: str = "access",
        jti: Optional[str] = None
    ):
        self.user_id = user_id
        self.phone_number = phone_number
        self.token_type = token_type
        self.jti = jti or secrets.token_urlsafe(16)


def create_access_token(
    user_id: str,
    phone_number: Optional[str] = None,
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    创建 Access Token
    
    参数：
        user_id: 用户ID
        phone_number: 手机号（可选）
        expires_delta: 过期时间增量（可选）
    
    返回：
        JWT Access Token 字符串
    """
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    
    token_data = {
        "sub": user_id,
        "phone_number": phone_number,
        "exp": expire,
        "iat": datetime.utcnow(),
        "type": "access",
        "jti": secrets.token_urlsafe(16)
    }
    
    encoded_jwt = jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)
    logger.info(f"创建 Access Token: user_id={user_id}, expires_at={expire}")
    return encoded_jwt


def create_refresh_token(
    user_id: str,
    phone_number: Optional[str] = None,
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    创建 Refresh Token
    
    参数：
        user_id: 用户ID
        phone_number: 手机号（可选）
        expires_delta: 过期时间增量（可选）
    
    返回：
        JWT Refresh Token 字符串
    """
    expire = datetime.utcnow() + (
        expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )
    
    token_data = {
        "sub": user_id,
        "phone_number": phone_number,
        "exp": expire,
        "iat": datetime.utcnow(),
        "type": "refresh",
        "jti": secrets.token_urlsafe(16)
    }
    
    encoded_jwt = jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)
    logger.info(f"创建 Refresh Token: user_id={user_id}, expires_at={expire}")
    return encoded_jwt


def verify_token(token: str, token_type: str = "access") -> Optional[TokenData]:
    """
    验证 Token
    
    参数：
        token: JWT Token 字符串
        token_type: Token 类型（"access" 或 "refresh"）
    
    返回：
        TokenData 对象（验证成功）或 None（验证失败）
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        if payload.get("type") != token_type:
            logger.warning(f"Token 类型不匹配: expected={token_type}, got={payload.get('type')}")
            return None
        
        user_id: str = payload.get("sub")
        if user_id is None:
            logger.warning("Token 缺少 user_id")
            return None
        
        token_data = TokenData(
            user_id=user_id,
            phone_number=payload.get("phone_number"),
            token_type=token_type,
            jti=payload.get("jti")
        )
        
        logger.debug(f"Token 验证成功: user_id={user_id}, type={token_type}")
        return token_data
        
    except jwt.ExpiredSignatureError:
        logger.warning("Token 已过期")
        return None
    except jwt.DecodeError:
        logger.warning("Token 解码失败")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Token 验证失败: {e}")
        return None


def create_token_pair(
    user_id: str,
    phone_number: Optional[str] = None
) -> Dict[str, Any]:
    """
    创建 Token 对（Access Token + Refresh Token）
    
    参数：
        user_id: 用户ID
        phone_number: 手机号（可选）
    
    返回：
        包含 access_token, refresh_token, expires_in 等信息的字典
    """
    access_token = create_access_token(user_id, phone_number)
    refresh_token = create_refresh_token(user_id, phone_number)
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user_id": user_id,
        "phone_number": phone_number
    }


def refresh_tokens(refresh_token: str) -> Optional[Dict[str, Any]]:
    """
    使用 Refresh Token 刷新 Token 对
    
    参数：
        refresh_token: Refresh Token 字符串
    
    返回：
        新的 Token 对（验证成功）或 None（验证失败）
    """
    token_data = verify_token(refresh_token, token_type="refresh")
    
    if token_data is None:
        logger.warning("Refresh Token 验证失败")
        return None
    
    logger.info(f"刷新 Token: user_id={token_data.user_id}")
    return create_token_pair(
        user_id=token_data.user_id,
        phone_number=token_data.phone_number
    )
