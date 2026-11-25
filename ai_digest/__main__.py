import argparse
import asyncio
import json
from models.models import init_db
from ai_digest.pipeline import run_pipeline


def main():
    parser = argparse.ArgumentParser(description='AI版块离线增量分类，不另建TG会话；发送通过主进程手动/定时入口')
    parser.add_argument('--model')
    args = parser.parse_args()
    init_db()
    print(json.dumps(asyncio.run(run_pipeline(model=args.model)), ensure_ascii=False))


if __name__ == '__main__':
    main()
