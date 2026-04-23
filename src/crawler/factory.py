"""
爬虫后端工厂：根据系统配置 crawler_backend 返回对应实现。

配置值：
  "api"      → ApiBackend（直接 HTTP，速度快，不需要 Selenium Grid）
  "selenium" → SeleniumBackend（远程 Chrome，兼容复杂交互/验证码）

通过 /api/settings 中的 crawler_backend 字段读取，默认值为 "api"。
"""
from src.crawler.base import CrawlerBackend


def get_backend(db=None) -> CrawlerBackend:
    """
    从数据库配置读取 crawler_backend，返回对应后端实例。
    db 为可选参数；若不传则新建临时会话读取配置，用完即关。
    """
    backend_type = _read_config(db)
    if backend_type == "selenium":
        from src.crawler.selenium_backend import SeleniumBackend
        return SeleniumBackend()
    else:
        from src.crawler.api_backend import ApiBackend
        return ApiBackend()


def _read_config(db=None) -> str:
    """读取 crawler_backend 配置，默认返回 'api'"""
    if db is not None:
        return _query_setting(db)
    # 无 db 传入时，自行开关会话
    from src.database import SessionLocal
    session = SessionLocal()
    try:
        return _query_setting(session)
    finally:
        session.close()


def _query_setting(db) -> str:
    from src.models import Setting
    row = db.query(Setting).filter_by(key="crawler_backend").first()
    return row.value if row else "api"
