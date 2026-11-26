"""Idempotent data copy; destructive cleanup is an explicit, backed-up operation."""
from sqlalchemy import MetaData, Table, inspect, select
from models.models import AlphaProcessing, BusinessMigration


def migrate_ai_dedup(engine):
    from sqlalchemy import text
    from models.models import AIJob
    with engine.begin() as connection:
        if 'dedup' not in {c['name'] for c in inspect(connection).get_columns('ai_items')}:
            connection.execute(text('ALTER TABLE ai_items ADD COLUMN dedup JSON'))
        marker = 'p19-ai-three-hour-v1'
        if not connection.execute(select(BusinessMigration.name).where(BusinessMigration.name == marker)).first():
            connection.execute(AIJob.__table__.update().values(schedule_hours=[1, 4, 7, 10, 13, 16, 19, 22]))
            connection.execute(BusinessMigration.__table__.insert().values(name=marker))


def migrate_business_data(engine):
    columns = {c['name'] for c in inspect(engine).get_columns('collected_messages')}
    if 'analysis_status' not in columns:
        return
    raw = Table('collected_messages', MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        if connection.execute(select(BusinessMigration.name).where(BusinessMigration.name == 'alpha-processing-v1')).first():
            return
        for row in connection.execute(select(raw)).mappings():
            if connection.execute(select(AlphaProcessing.message_id).where(AlphaProcessing.message_id == row['id'])).first():
                continue
            connection.execute(AlphaProcessing.__table__.insert().values(
                message_id=row['id'], revision=row['revision'], analysis_status=row['analysis_status'],
                analysis=row.get('analysis'), analysis_version=row.get('analysis_version'),
                error=row.get('error'), usage=row.get('usage') or {}))
        connection.execute(BusinessMigration.__table__.insert().values(name='alpha-processing-v1'))


def finalize(engine):
    from sqlalchemy import text
    from models.models import Base
    with engine.begin() as connection:
        inspector = inspect(connection)
        for table in ('summary_history', 'forwarded_messages'):
            if table in inspector.get_table_names():
                connection.execute(text(f'DROP TABLE {table}'))
        obsolete = {'collected_messages': ['analysis_status', 'analysis', 'analysis_version', 'usage', 'error'],
                    'forward_rules': ['is_summary', 'summary_time', 'summary_prompt', 'summary_prompt_b',
                                      'summary_prompt_d', 'is_top_summary']}
        for table, names in obsolete.items():
            existing = {c['name'] for c in inspect(connection).get_columns(table)}
            if table == 'collected_messages' and 'analysis_status' in existing:
                total = connection.execute(text('SELECT COUNT(*) FROM collected_messages')).scalar()
                copied = connection.execute(text('SELECT COUNT(*) FROM alpha_processing')).scalar()
                if copied < total:
                    raise ValueError('Alpha迁移数量不足，拒绝清理旧字段')
            for idx in inspect(connection).get_indexes(table):
                if set(idx['column_names']) & set(names):
                    connection.execute(text('DROP INDEX ' + connection.dialect.identifier_preparer.quote(idx['name'])))
            for name in names:
                if name in existing:
                    connection.execute(text(f'ALTER TABLE {table} DROP COLUMN {name}'))
