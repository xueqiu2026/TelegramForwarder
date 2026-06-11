"""
SQLite → PostgreSQL 一次性数据迁移脚本
用法: python scripts/migrate_sqlite_to_pg.py

执行前提:
1. PostgreSQL 中已创建 telegram_forwarder 数据库
2. .env 中 DATABASE_URL 已改为 postgresql://...
3. 已运行过一次主程序（或手动执行 init_db()）在 PG 中创建表结构
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
from models.models import (
    Base, Chat, ForwardRule, Keyword, ReplaceRule, MediaTypes,
    MediaExtensions, RuleSync, PushConfig, RSSConfig, RSSPattern,
    User, SummaryHistory
)

SQLITE_URL = 'sqlite:///./db/forward.db'
PG_URL = os.getenv('DATABASE_URL')

if not PG_URL or 'postgresql' not in PG_URL:
    print("错误: .env 中的 DATABASE_URL 必须是 postgresql:// 连接字符串")
    sys.exit(1)

# 需要迁移的表（按依赖顺序）
TABLES = [
    ('chats', Chat),
    ('users', User),
    ('forward_rules', ForwardRule),
    ('keywords', Keyword),
    ('replace_rules', ReplaceRule),
    ('media_types', MediaTypes),
    ('media_extensions', MediaExtensions),
    ('rule_syncs', RuleSync),
    ('push_configs', PushConfig),
    ('rss_configs', RSSConfig),
    ('rss_patterns', RSSPattern),
    ('summary_history', SummaryHistory),
]

def migrate():
    sqlite_engine = create_engine(SQLITE_URL)
    pg_engine = create_engine(PG_URL)

    # 确保 SQLite 的表结构中包含所有最新的列，避免查询时 no such column 报错
    from models.models import migrate_db
    try:
        migrate_db(sqlite_engine)
        print("已成功在 SQLite 数据库上应用最新的列迁移结构")
    except Exception as sqlite_migrate_err:
        print(f"SQLite 临时列结构迁移失败（可能会导致后续 no such column 错误）: {sqlite_migrate_err}")

    # 确保 PG 中表结构已创建
    Base.metadata.create_all(pg_engine)

    SqliteSession = sessionmaker(bind=sqlite_engine)
    PgSession = sessionmaker(bind=pg_engine)

    sqlite_session = SqliteSession()
    pg_session = PgSession()

    print("=" * 50)
    print("开始 SQLite → PostgreSQL 数据迁移")
    print("=" * 50)

    # 倒序清空 PG 目标表，防外键关联删除报错
    print("正在清空 PostgreSQL 中的旧数据...")
    for table_name, model_class in reversed(TABLES):
        try:
            pg_session.query(model_class).delete()
            pg_session.commit()
        except Exception as delete_err:
            pg_session.rollback()
            print(f"  [{table_name}] 清空失败（可能已被关联或无数据）: {delete_err}")
    print("PostgreSQL 旧数据清空完成。\n")

    for table_name, model_class in TABLES:
        try:
            # 读取 SQLite 数据
            rows = sqlite_session.query(model_class).all()
            count = len(rows)

            if count == 0:
                print(f"  [{table_name}] 无数据，跳过")
                continue

            # 逐行读取字段值并拷贝写入 PG，规避原有 make_transient 的 Lazy-Load 外键跨会话关联报错
            for row in rows:
                row_dict = {col.name: getattr(row, col.name) for col in model_class.__table__.columns}
                new_row = model_class(**row_dict)
                pg_session.add(new_row)

            pg_session.commit()

            # 验证
            pg_count = pg_session.query(model_class).count()
            status = "SUCCESS" if pg_count == count else "FAILED"
            print(f"  [{table_name}] SQLite: {count} rows -> PG: {pg_count} rows ({status})")

        except Exception as e:
            pg_session.rollback()
            print(f"  [{table_name}] FAILED to migrate: {e}")

    # 重置 PostgreSQL 序列（auto increment）
    print("\n重置 PostgreSQL 序列...")
    with pg_engine.connect() as conn:
        for table_name, model_class in TABLES:
            try:
                result = conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table_name}), 0) + 1, false)"
                ))
                print(f"  [{table_name}] 序列已重置")
            except Exception as e:
                print(f"  [{table_name}] 序列重置跳过: {e}")

    sqlite_session.close()
    pg_session.close()

    print("\n" + "=" * 50)
    print("迁移完成！请启动主程序验证。")
    print("=" * 50)

if __name__ == '__main__':
    migrate()
