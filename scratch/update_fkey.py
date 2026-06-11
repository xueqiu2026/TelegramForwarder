from sqlalchemy import create_engine, text
import os
from dotenv import load_dotenv

load_dotenv()

def update_fkey():
    db_url = os.getenv('DATABASE_URL')
    if not db_url or 'postgresql' not in db_url:
        print("无 PostgreSQL 数据库，无需更新约束")
        return
        
    engine = create_engine(db_url)
    with engine.connect() as conn:
        try:
            # 尝试删除旧外键
            conn.execute(text("ALTER TABLE forwarded_messages DROP CONSTRAINT IF EXISTS forwarded_messages_rule_id_fkey"))
            # 重新添加带有 ON DELETE CASCADE 的外键
            conn.execute(text("""
                ALTER TABLE forwarded_messages 
                ADD CONSTRAINT forwarded_messages_rule_id_fkey 
                FOREIGN KEY (rule_id) REFERENCES forward_rules(id) ON DELETE CASCADE
            """))
            conn.commit()
            print("PostgreSQL 数据库 forwarded_messages.rule_id 外键 ON DELETE CASCADE 约束更新成功！")
        except Exception as e:
            conn.rollback()
            print(f"外键约束更新失败: {e}")

if __name__ == '__main__':
    update_fkey()
