import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

def create_database():
    # 连接到默认的 postgres 数据库来创建新数据库
    conn = None
    try:
        conn = psycopg2.connect(
            dbname='postgres',
            user='pm_user',
            password='pm123456',
            host='localhost',
            port='5432'
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        # 检查数据库是否存在
        cursor.execute("SELECT 1 FROM pg_catalog.pg_database WHERE datname = 'telegram_forwarder';")
        exists = cursor.fetchone()
        
        if not exists:
            print("正在创建数据库 telegram_forwarder...")
            cursor.execute("CREATE DATABASE telegram_forwarder OWNER pm_user;")
            print("数据库 telegram_forwarder 创建成功。")
        else:
            print("数据库 telegram_forwarder 已存在，无需创建。")
            
        cursor.close()
    except Exception as e:
        print(f"数据库创建失败: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    create_database()
