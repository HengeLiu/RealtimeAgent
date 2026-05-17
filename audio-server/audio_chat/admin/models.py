"""
用户管理数据库模型
"""
import sqlite3
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class User:
    """用户数据模型"""
    id: Optional[int] = None
    phone_number: str = ""
    user_id: str = ""
    nickname: str = ""
    avatar_url: str = ""
    created_at: str = ""
    last_login_at: str = ""
    login_count: int = 0
    status: str = "active"
    metadata: str = "{}"


@dataclass
class FeatureUsage:
    """功能使用埋点数据模型"""
    id: Optional[int] = None
    user_id: str = ""
    device_id: str = ""
    feature_name: str = ""
    action: str = ""
    metadata: str = "{}"
    created_at: str = ""


@dataclass
class UserSession:
    """用户会话数据模型"""
    id: Optional[int] = None
    user_id: str = ""
    device_id: str = ""
    session_start: str = ""
    session_end: str = ""
    duration: int = 0
    events_count: int = 0


class Database:
    """SQLite 数据库管理"""
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            from pathlib import Path
            audio_server_root = Path(__file__).parent.parent.parent
            db_path = str(audio_server_root / "data" / "users.db")
        
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
    
    def _init_db(self):
        """初始化数据库表"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_number TEXT UNIQUE NOT NULL,
                user_id TEXT UNIQUE NOT NULL,
                nickname TEXT DEFAULT '',
                avatar_url TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                last_login_at TEXT,
                login_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                metadata TEXT DEFAULT '{}'
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feature_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                feature_name TEXT NOT NULL,
                action TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                session_start TEXT NOT NULL,
                session_end TEXT,
                duration INTEGER DEFAULT 0,
                events_count INTEGER DEFAULT 0
            )
        """)
        
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone_number)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_feature_usage_user_id ON feature_usage(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_feature_usage_created_at ON feature_usage(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON user_sessions(user_id)")
        
        conn.commit()
        conn.close()
        logger.info(f"数据库初始化完成: {self.db_path}")


class UserManager:
    """用户管理"""
    
    def __init__(self, db: Database):
        self.db = db
    
    def create_user(self, phone_number: str, user_id: str, **kwargs) -> User:
        """创建用户"""
        conn = self.db._get_connection()
        cursor = conn.cursor()
        
        now = datetime.now().isoformat()
        user = User(
            phone_number=phone_number,
            user_id=user_id,
            nickname=kwargs.get('nickname', ''),
            avatar_url=kwargs.get('avatar_url', ''),
            created_at=now,
            last_login_at=now,
            login_count=1,
            status='active',
            metadata=json.dumps(kwargs.get('metadata', {}))
        )
        
        try:
            cursor.execute("""
                INSERT INTO users (phone_number, user_id, nickname, avatar_url, created_at, 
                                   last_login_at, login_count, status, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user.phone_number, user.user_id, user.nickname, user.avatar_url,
                  user.created_at, user.last_login_at, user.login_count, user.status, user.metadata))
            
            user.id = cursor.lastrowid
            conn.commit()
            logger.info(f"创建用户成功: {user_id}")
            return user
        except sqlite3.IntegrityError:
            logger.warning(f"用户已存在: {user_id}")
            return self.update_login(user_id)
        finally:
            conn.close()
    
    def update_login(self, user_id: str) -> Optional[User]:
        """更新登录信息"""
        conn = self.db._get_connection()
        cursor = conn.cursor()
        
        now = datetime.now().isoformat()
        cursor.execute("""
            UPDATE users 
            SET last_login_at = ?, login_count = login_count + 1
            WHERE user_id = ?
        """, (now, user_id))
        
        conn.commit()
        conn.close()
        
        return self.get_user_by_user_id(user_id)
    
    def get_user_by_user_id(self, user_id: str) -> Optional[User]:
        """根据 user_id 获取用户"""
        conn = self.db._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return User(**dict(row))
        return None
    
    def get_user_by_phone(self, phone_number: str) -> Optional[User]:
        """根据手机号获取用户"""
        conn = self.db._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM users WHERE phone_number = ?", (phone_number,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return User(**dict(row))
        return None
    
    def get_all_users(self, limit: int = 100, offset: int = 0) -> List[User]:
        """获取所有用户"""
        conn = self.db._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM users 
            ORDER BY created_at DESC 
            LIMIT ? OFFSET ?
        """, (limit, offset))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [User(**dict(row)) for row in rows]
    
    def get_users_count(self) -> int:
        """获取用户总数"""
        conn = self.db._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM users")
        count = cursor.fetchone()[0]
        conn.close()
        
        return count
    
    def update_user(self, user_id: str, **kwargs) -> Optional[User]:
        """更新用户信息"""
        conn = self.db._get_connection()
        cursor = conn.cursor()
        
        fields = []
        values = []
        for key, value in kwargs.items():
            if key in ['nickname', 'avatar_url', 'status', 'metadata']:
                fields.append(f"{key} = ?")
                values.append(value)
        
        if not fields:
            return self.get_user_by_user_id(user_id)
        
        values.append(user_id)
        cursor.execute(f"""
            UPDATE users 
            SET {', '.join(fields)}
            WHERE user_id = ?
        """, values)
        
        conn.commit()
        conn.close()
        
        return self.get_user_by_user_id(user_id)


class AnalyticsManager:
    """数据分析管理"""
    
    def __init__(self, db: Database):
        self.db = db
    
    def track_feature_usage(self, user_id: str, device_id: str, feature_name: str, 
                           action: str, metadata: Dict[str, Any] = None):
        """记录功能使用埋点"""
        conn = self.db._get_connection()
        cursor = conn.cursor()
        
        now = datetime.now().isoformat()
        cursor.execute("""
            INSERT INTO feature_usage (user_id, device_id, feature_name, action, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, device_id, feature_name, action, 
              json.dumps(metadata or {}), now))
        
        conn.commit()
        conn.close()
        
        logger.debug(f"埋点记录: {user_id} - {feature_name}.{action}")
    
    def start_session(self, user_id: str, device_id: str) -> int:
        """开始会话"""
        conn = self.db._get_connection()
        cursor = conn.cursor()
        
        now = datetime.now().isoformat()
        cursor.execute("""
            INSERT INTO user_sessions (user_id, device_id, session_start, events_count)
            VALUES (?, ?, ?, 0)
        """, (user_id, device_id, now))
        
        session_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return session_id
    
    def end_session(self, session_id: int, events_count: int = 0):
        """结束会话"""
        conn = self.db._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT session_start FROM user_sessions WHERE id = ?
        """, (session_id,))
        
        row = cursor.fetchone()
        if row:
            session_start = datetime.fromisoformat(row['session_start'])
            session_end = datetime.now()
            duration = int((session_end - session_start).total_seconds())
            
            cursor.execute("""
                UPDATE user_sessions 
                SET session_end = ?, duration = ?, events_count = ?
                WHERE id = ?
            """, (session_end.isoformat(), duration, events_count, session_id))
            
            conn.commit()
        
        conn.close()
    
    def get_feature_usage_stats(self, days: int = 7) -> List[Dict[str, Any]]:
        """获取功能使用统计"""
        conn = self.db._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT feature_name, action, COUNT(*) as count
            FROM feature_usage
            WHERE created_at >= datetime('now', ?)
            GROUP BY feature_name, action
            ORDER BY count DESC
        """, (f'-{days} days',))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def get_daily_active_users(self, days: int = 7) -> List[Dict[str, Any]]:
        """获取日活用户"""
        conn = self.db._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT DATE(created_at) as date, COUNT(DISTINCT user_id) as count
            FROM feature_usage
            WHERE created_at >= datetime('now', ?)
            GROUP BY DATE(created_at)
            ORDER BY date DESC
        """, (f'-{days} days',))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def get_user_feature_usage(self, user_id: str, limit: int = 100) -> List[FeatureUsage]:
        """获取用户的功能使用记录"""
        conn = self.db._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM feature_usage
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (user_id, limit))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [FeatureUsage(**dict(row)) for row in rows]
    
    def get_user_sessions(self, user_id: str, limit: int = 50) -> List[UserSession]:
        """获取用户的会话记录"""
        conn = self.db._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM user_sessions
            WHERE user_id = ?
            ORDER BY session_start DESC
            LIMIT ?
        """, (user_id, limit))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [UserSession(**dict(row)) for row in rows]
    
    def get_overview_stats(self) -> Dict[str, Any]:
        """获取概览统计"""
        conn = self.db._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT COUNT(DISTINCT user_id) FROM feature_usage
            WHERE created_at >= datetime('now', '-1 day')
        """)
        dau = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT COUNT(DISTINCT user_id) FROM feature_usage
            WHERE created_at >= datetime('now', '-7 days')
        """)
        wau = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT COUNT(DISTINCT user_id) FROM feature_usage
            WHERE created_at >= datetime('now', '-30 days')
        """)
        mau = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM feature_usage")
        total_events = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM user_sessions WHERE duration > 0")
        total_sessions = cursor.fetchone()[0]
        
        cursor.execute("SELECT AVG(duration) FROM user_sessions WHERE duration > 0")
        avg_session_duration = cursor.fetchone()[0] or 0
        
        conn.close()
        
        return {
            "total_users": total_users,
            "dau": dau,
            "wau": wau,
            "mau": mau,
            "total_events": total_events,
            "total_sessions": total_sessions,
            "avg_session_duration": round(avg_session_duration, 2)
        }


db = Database()
user_manager = UserManager(db)
analytics_manager = AnalyticsManager(db)
