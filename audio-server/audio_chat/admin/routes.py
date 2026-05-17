"""
管理后台 API 路由
"""
import logging
from pathlib import Path
from aiohttp import web
from .models import user_manager, analytics_manager
from dataclasses import asdict

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()


@routes.get('/admin')
async def admin_page(request: web.Request) -> web.Response:
    """管理后台页面"""
    try:
        html_path = Path(__file__).parent / 'templates' / 'index.html'
        with open(html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        return web.Response(text=html_content, content_type='text/html')
    except Exception as e:
        logger.error(f"加载管理页面失败: {e}")
        return web.Response(text=f"加载失败: {e}", status=500)


@routes.get('/api/admin/users')
async def get_users(request: web.Request) -> web.Response:
    """获取用户列表"""
    try:
        limit = int(request.query.get('limit', 100))
        offset = int(request.query.get('offset', 0))
        
        users = user_manager.get_all_users(limit=limit, offset=offset)
        total = user_manager.get_users_count()
        
        return web.json_response({
            'success': True,
            'data': {
                'users': [asdict(user) for user in users],
                'total': total,
                'limit': limit,
                'offset': offset
            }
        })
    except Exception as e:
        logger.error(f"获取用户列表失败: {e}")
        return web.json_response({
            'success': False,
            'message': str(e)
        }, status=500)


@routes.get('/api/admin/users/{user_id}')
async def get_user_detail(request: web.Request) -> web.Response:
    """获取用户详情"""
    try:
        user_id = request.match_info['user_id']
        user = user_manager.get_user_by_user_id(user_id)
        
        if not user:
            return web.json_response({
                'success': False,
                'message': '用户不存在'
            }, status=404)
        
        feature_usage = analytics_manager.get_user_feature_usage(user_id, limit=50)
        sessions = analytics_manager.get_user_sessions(user_id, limit=20)
        
        return web.json_response({
            'success': True,
            'data': {
                'user': asdict(user),
                'feature_usage': [asdict(usage) for usage in feature_usage],
                'sessions': [asdict(session) for session in sessions]
            }
        })
    except Exception as e:
        logger.error(f"获取用户详情失败: {e}")
        return web.json_response({
            'success': False,
            'message': str(e)
        }, status=500)


@routes.get('/api/admin/analytics/overview')
async def get_analytics_overview(request: web.Request) -> web.Response:
    """获取数据概览"""
    try:
        stats = analytics_manager.get_overview_stats()
        
        return web.json_response({
            'success': True,
            'data': stats
        })
    except Exception as e:
        logger.error(f"获取数据概览失败: {e}")
        return web.json_response({
            'success': False,
            'message': str(e)
        }, status=500)


@routes.get('/api/admin/analytics/feature-usage')
async def get_feature_usage_stats(request: web.Request) -> web.Response:
    """获取功能使用统计"""
    try:
        days = int(request.query.get('days', 7))
        stats = analytics_manager.get_feature_usage_stats(days=days)
        
        return web.json_response({
            'success': True,
            'data': stats
        })
    except Exception as e:
        logger.error(f"获取功能使用统计失败: {e}")
        return web.json_response({
            'success': False,
            'message': str(e)
        }, status=500)


@routes.get('/api/admin/analytics/dau')
async def get_dau_stats(request: web.Request) -> web.Response:
    """获取日活统计"""
    try:
        days = int(request.query.get('days', 7))
        stats = analytics_manager.get_daily_active_users(days=days)
        
        return web.json_response({
            'success': True,
            'data': stats
        })
    except Exception as e:
        logger.error(f"获取日活统计失败: {e}")
        return web.json_response({
            'success': False,
            'message': str(e)
        }, status=500)


@routes.post('/api/admin/track')
async def track_event(request: web.Request) -> web.Response:
    """记录埋点事件"""
    try:
        data = await request.json()
        user_id = data.get('user_id')
        device_id = data.get('device_id')
        feature_name = data.get('feature_name')
        action = data.get('action')
        metadata = data.get('metadata', {})
        
        if not all([user_id, device_id, feature_name, action]):
            return web.json_response({
                'success': False,
                'message': '缺少必要参数'
            }, status=400)
        
        analytics_manager.track_feature_usage(
            user_id, device_id, feature_name, action, metadata
        )
        
        return web.json_response({
            'success': True,
            'message': '埋点记录成功'
        })
    except Exception as e:
        logger.error(f"记录埋点失败: {e}")
        return web.json_response({
            'success': False,
            'message': str(e)
        }, status=500)


def setup_admin_routes(app: web.Application):
    """注册管理路由"""
    app.router.add_routes(routes)
    logger.info("管理路由已注册")
