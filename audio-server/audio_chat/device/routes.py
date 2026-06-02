"""
设备配对 API 路由
"""
import logging
from aiohttp import web
from .pairing import PairingService

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()

PAIRING_SERVICE_KEY = "pairing_service"


@routes.post('/api/device/pairing-code')
async def generate_pairing_code(request: web.Request) -> web.Response:
    """
    生成配对码（App 调用）

    请求头:
        Authorization: Bearer <access_token>

    请求体:
        { "user_id": "user-1234" }

    返回:
        { "pairing_code": "A3X9K2", "expires_in": 600 }
    """
    try:
        pairing_service: PairingService = request.app[PAIRING_SERVICE_KEY]

        # 从 JWT 或请求体获取 user_id
        data = await request.json()
        user_id = data.get('user_id')
        if not user_id:
            return web.json_response({'error': 'missing user_id'}, status=400)

        code = pairing_service.generate_pairing_code(user_id)
        return web.json_response({
            'pairing_code': code,
            'expires_in': 600,
        })
    except Exception as e:
        logger.error(f"Generate pairing code failed: {e}")
        return web.json_response({'error': str(e)}, status=500)


@routes.post('/api/device/pair')
async def pair_device(request: web.Request) -> web.Response:
    """
    设备配对（ESP32 调用）

    请求体:
        {
            "pairing_code": "A3X9K2",
            "hardware_id": "hw-a1b2c3d4e5f6",
            "device_name": "ESP32 Glass"
        }

    返回:
        {
            "user_id": "user-1234",
            "device_id": "dev-glass-c3d4e5f6",
            "auth_token": "eyJ...",
            "server_host": "192.168.31.8",
            "server_port": 8766
        }
    """
    try:
        pairing_service: PairingService = request.app[PAIRING_SERVICE_KEY]

        data = await request.json()
        pairing_code = data.get('pairing_code')
        hardware_id = data.get('hardware_id')
        device_name = data.get('device_name', '')

        if not pairing_code or not hardware_id:
            return web.json_response({'error': 'missing pairing_code or hardware_id'}, status=400)

        result = pairing_service.validate_and_pair(pairing_code, hardware_id, device_name)

        return web.json_response({
            'user_id': result.user_id,
            'device_id': result.device_id,
            'auth_token': result.auth_token,
            'server_host': result.server_host,
            'server_port': result.server_port,
        })
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Device pairing failed: {e}")
        return web.json_response({'error': str(e)}, status=500)


def register_device_routes(app: web.Application, pairing_service: PairingService):
    """注册设备配对路由"""
    app[PAIRING_SERVICE_KEY] = pairing_service
    app.router.add_routes(routes)
    logger.info("Device pairing routes registered")
