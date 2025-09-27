import os
import httpx
from dotenv import load_dotenv

load_dotenv()
PADEL_API_KEY = os.getenv("PADEL_API_KEY")
PADEL_API_URL = "https://en.fantasypadeltour.com/api"


async def main():
    headers = {"Authorization": f"Bearer {PADEL_API_KEY}", "Accept": "application/json"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{PADEL_API_URL}/tournaments/161/matches", headers=headers
        )
        print(resp.json())


import asyncio

asyncio.run(main())
