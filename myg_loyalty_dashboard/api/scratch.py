import asyncio
from db import db

async def check():
    await db.connect()
    res = await db.fetch("SELECT table_name FROM information_schema.views WHERE table_schema='public'")
    print("Views:", [r['table_name'] for r in res])
    res = await db.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
    print("Tables:", [r['table_name'] for r in res])
    await db.close()

if __name__ == '__main__':
    asyncio.run(check())
