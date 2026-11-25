"""Run while the application is stopped. Back up before initialization or schema cleanup."""
import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine
from models.models import init_db
from models.p19_migration import finalize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backup', required=True, help='New absolute backup file; never overwritten')
    parser.add_argument('--finalize', action='store_true')
    args = parser.parse_args()
    output = Path(args.backup)
    if not output.is_absolute() or output.exists() or not output.parent.is_dir():
        parser.error('备份须为父目录存在的绝对新文件路径')
    url = os.environ.get('DATABASE_URL')
    if not url:
        parser.error('缺少DATABASE_URL')
    engine = create_engine(url)
    if engine.dialect.name == 'sqlite':
        with sqlite3.connect(engine.url.database) as source, sqlite3.connect(str(output)) as target:
            source.backup(target)
    elif engine.dialect.name == 'postgresql':
        env = dict(os.environ)
        env['PGPASSWORD'] = engine.url.password or ''
        subprocess.run(['pg_dump', '-h', engine.url.host or 'localhost', '-p', str(engine.url.port or 5432),
                        '-U', engine.url.username, '-d', engine.url.database, '-Fc', '-f', str(output)],
                       env=env, check=True)
    else:
        parser.error('仅支持SQLite/PostgreSQL')
    if output.stat().st_size == 0:
        raise RuntimeError('备份为空，停止迁移')
    initialized = init_db(allow_legacy=True)
    if args.finalize:
        finalize(initialized)
    initialized.dispose()
    engine.dispose()
    print('备份和迁移完成；应用配置与AI发送开关未启用')


if __name__ == '__main__':
    main()
