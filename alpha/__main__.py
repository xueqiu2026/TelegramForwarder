import argparse
import asyncio
import json
from pathlib import Path

from models.models import init_db, get_session, AlphaProjectAlias
from alpha.storage import snapshot, export_markdown
from alpha.pipeline import run_pipeline


def main():
    parser = argparse.ArgumentParser(description='机会草稿CLI，不发送Telegram消息')
    parser.add_argument('command', choices=['init', 'status', 'run', 'export', 'alias'])
    parser.add_argument('--model')
    parser.add_argument('--internal', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--alias')
    parser.add_argument('--project')
    parser.add_argument('--evidence')
    args = parser.parse_args()
    if args.command == 'alias':
        if not all((args.alias, args.project, args.evidence)):
            parser.error('alias 需要 --alias、--project 和 --evidence 原文依据')
        db = get_session()
        try:
            db.merge(AlphaProjectAlias(alias=args.alias.strip(), project=args.project.strip(),
                                       evidence=args.evidence.strip()))
            db.commit()
            print('项目别名已保存，后续分析将使用；未重跑历史结果')
        finally:
            db.close()
    elif args.command == 'init':
        init_db()
        print('已执行追加迁移；未开启采集、调度或发送')
    elif args.command == 'status':
        print(json.dumps(snapshot(), ensure_ascii=False, indent=2))
    elif args.command == 'run':
        print(json.dumps(asyncio.run(run_pipeline(model=args.model)), ensure_ascii=False, indent=2))
    else:
        body = export_markdown(internal=args.internal)
        if args.output:
            if args.output.exists():
                parser.error('输出文件已存在，请选择新路径，避免覆盖旧材料')
            args.output.write_text(body, encoding='utf-8')
        else:
            print(body)


if __name__ == '__main__':
    main()
