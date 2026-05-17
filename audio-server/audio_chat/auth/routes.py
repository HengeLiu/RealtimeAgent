"""
认证相关 API 路由 - 一键登录取号 + JWT Token 管理
"""
import asyncio
import logging
import time
import uuid
from aiohttp import web
from .aliyun_auth import auth_service
from .jwt_handler import create_token_pair, refresh_tokens, verify_token

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()


@routes.post('/api/auth/get-mobile')
async def get_mobile(request: web.Request) -> web.Response:
    """
    一键登录取号（模拟版本 - 直接返回成功）
    
    请求体:
        {
            "token": "客户端获取的 token"
        }
    
    返回:
        {
            "success": true,
            "phone_number": "13800138000",
            "user_id": "user-8000",
            "message": "取号成功",
            "access_token": "...",
            "refresh_token": "...",
            "expires_in": 3600
        }
    """
    try:
        data = await request.json()
        token = data.get('token')
        out_id = data.get('out_id')
        
        if not token:
            return web.json_response({
                'success': False,
                'message': '缺少 token 参数'
            }, status=400)
        
        # 模拟登录成功 - 生成测试用户
        test_user_id = "user-test-001"
        test_phone_number = "13800138000"
        
        # 生成 JWT Token 对
        token_pair = create_token_pair(test_user_id, test_phone_number)
        
        logger.info(f"模拟登录成功: user_id={test_user_id}, phone={test_phone_number}")
        
        return web.json_response({
            'success': True,
            'phone_number': test_phone_number,
            'user_id': test_user_id,
            'message': '取号成功',
            'access_token': token_pair['access_token'],
            'refresh_token': token_pair['refresh_token'],
            'expires_in': token_pair['expires_in'],
            'token_type': token_pair['token_type']
        })
        
    except Exception as e:
        logger.error(f"一键登录取号失败: {e}")
        return web.json_response({
            'success': False,
            'message': str(e)
        }, status=500)


@routes.post('/api/auth/refresh')
async def refresh_token(request: web.Request) -> web.Response:
    """
    刷新 Token
    
    请求体:
        {
            "refresh_token": "..."
        }
    
    返回:
        {
            "success": true,
            "access_token": "...",
            "refresh_token": "...",
            "expires_in": 3600,
            "token_type": "bearer"
        }
    """
    try:
        data = await request.json()
        refresh_token_str = data.get('refresh_token')
        
        if not refresh_token_str:
            return web.json_response({
                'success': False,
                'message': '缺少 refresh_token 参数'
            }, status=400)
        
        # 刷新 Token
        new_tokens = refresh_tokens(refresh_token_str)
        
        if new_tokens is None:
            return web.json_response({
                'success': False,
                'message': 'Refresh Token 无效或已过期'
            }, status=401)
        
        logger.info(f"Token 刷新成功: user_id={new_tokens['user_id']}")
        
        return web.json_response({
            'success': True,
            'access_token': new_tokens['access_token'],
            'refresh_token': new_tokens['refresh_token'],
            'expires_in': new_tokens['expires_in'],
            'token_type': new_tokens['token_type']
        })
        
    except Exception as e:
        logger.error(f"Token 刷新失败: {e}")
        return web.json_response({
            'success': False,
            'message': str(e)
        }, status=500)


@routes.post('/api/auth/verify')
async def verify_token_endpoint(request: web.Request) -> web.Response:
    """
    验证 Access Token
    
    请求头:
        Authorization: Bearer <access_token>
    
    返回:
        {
            "success": true,
            "user_id": "...",
            "phone_number": "..."
        }
    """
    try:
        auth_header = request.headers.get('Authorization', '')
        
        if not auth_header.startswith('Bearer '):
            return web.json_response({
                'success': False,
                'message': '缺少或无效的 Authorization 头'
            }, status=401)
        
        access_token = auth_header.split(' ')[1]
        
        # 验证 Token
        token_data = verify_token(access_token, token_type="access")
        
        if token_data is None:
            return web.json_response({
                'success': False,
                'message': 'Access Token 无效或已过期'
            }, status=401)
        
        return web.json_response({
            'success': True,
            'user_id': token_data.user_id,
            'phone_number': token_data.phone_number
        })
        
    except Exception as e:
        logger.error(f"Token 验证失败: {e}")
        return web.json_response({
            'success': False,
            'message': str(e)
        }, status=500)


@routes.post('/api/test/peer-video')
async def test_peer_video(request: web.Request) -> web.Response:
    """
    测试眼镜手机直连
    
    请求体:
        {
            "user_id": "user-test-001",
            "object_name": "测试物体",
            "timeout_seconds": 10
        }
    
    返回:
        {
            "success": true,
            "message": "直连测试已启动",
            "task_id": "...",
            "phone_device_id": "...",
            "glass_device_id": "..."
        }
    """
    from audio_chat.protocol import Event
    from audio_chat.server import AUDIO_CHAT_SERVER_KEY
    
    try:
        data = await request.json()
        user_id = data.get('user_id', 'user-test-001')
        object_name = data.get('object_name', '测试物体')
        timeout_seconds = data.get('timeout_seconds', 10)
        
        logger.info(f"测试直连请求: user_id={user_id}, object_name={object_name}")
        
        http_server = request.app.get(AUDIO_CHAT_SERVER_KEY)
        if not http_server:
            return web.json_response({
                'success': False,
                'message': '服务未就绪'
            }, status=500)
        
        audio_app = http_server.audio_app
        control_service = audio_app.control_service
        
        device_set = control_service.get_active_device_set(user_id)
        if not device_set or not device_set.devices:
            return web.json_response({
                'success': False,
                'message': '没有在线设备，请先连接手机和眼镜'
            }, status=400)
        
        devices = device_set.devices
        
        phone_device = None
        glass_device = None
        for device in devices:
            device_role = device.properties.get('device_role', '')
            if device_role == 'phone':
                phone_device = device
            elif device_role == 'glass' or 'peer.video.sender' in device.properties:
                glass_device = device
        
        if not phone_device:
            return web.json_response({
                'success': False,
                'message': '没有在线的手机设备'
            }, status=400)
        
        if not glass_device:
            return web.json_response({
                'success': False,
                'message': '没有在线的眼镜设备'
            }, status=400)
        
        task_id = f"test-peer-video-{uuid.uuid4().hex[:8]}"
        peer_session_id = task_id
        
        phone_ip = phone_device.properties.get('local_ip', '192.168.31.50')
        ws_url = f"ws://{phone_ip}:19081/peer-video/{peer_session_id}"
        
        receiver_command_id = f"cmd-{uuid.uuid4().hex[:8]}"
        receiver_event = Event(
            event_name="command.requested",
            user_id=user_id,
            producer_id="server-main",
            payload={
                "command_id": receiver_command_id,
                "command": "peer.video.receiver.start",
                "params": {
                    "peer_session_id": peer_session_id,
                    "task_type": "test_peer_video",
                    "purpose": "测试直连",
                    "object_name": object_name,
                    "media_config": {"codec": "jpeg", "width": 960, "height": 540, "fps": 5},
                    "timeout_seconds": timeout_seconds
                }
            }
        )
        
        control_service.publish(receiver_event)
        logger.info(f"已向手机 {phone_device.device_id} 发送 peer.video.receiver.start 命令")
        
        await asyncio.sleep(2)
        
        sender_command_id = f"cmd-{uuid.uuid4().hex[:8]}"
        sender_event = Event(
            event_name="command.requested",
            user_id=user_id,
            producer_id="server-main",
            payload={
                "command_id": sender_command_id,
                "command": "peer.video.sender.start",
                "params": {
                    "peer_session_id": peer_session_id,
                    "task_type": "test_peer_video",
                    "purpose": "测试直连",
                    "source": {"stream_type": "sensor.rgb", "codec": "jpeg", "fps": 5, "width": 960, "height": 540},
                    "receiver": {
                        "transport": "websocket",
                        "url": ws_url
                    },
                    "timeout_seconds": timeout_seconds
                }
            }
        )
        
        control_service.publish(sender_event)
        logger.info(f"已向眼镜 {glass_device.device_id} 发送 peer.video.sender.start 命令, url={ws_url}")
        
        return web.json_response({
            'success': True,
            'message': '直连测试已启动',
            'task_id': task_id,
            'user_id': user_id,
            'object_name': object_name,
            'timeout_seconds': timeout_seconds,
            'phone_device_id': phone_device.device_id,
            'glass_device_id': glass_device.device_id,
            'peer_session_id': peer_session_id,
            'ws_url': ws_url
        })
        
    except Exception as e:
        logger.error(f"直连测试失败: {e}", exc_info=True)
        return web.json_response({
            'success': False,
            'message': str(e)
        }, status=500)


def setup_auth_routes(app: web.Application):
    """注册认证路由"""
    app.router.add_routes(routes)
    logger.info("认证路由已注册")
