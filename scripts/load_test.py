"""
Load Testing Script for Ona Tili Bot & Mini App
===============================================
Yuklama sinovi:
- Bosqich 1: 100 ta virtual foydalanuvchi bir tekisda kiradi (Profil, Faol testlar).
- Bosqich 2: 30 ta foydalanuvchi bir vaqtda testni ochadi, autosave qiladi va topshiradi.

Natijalar:
- O'rtacha, p95 va p99 kechikishlar (latency)
- Xatolar soni va foizi (Qabul mezoni: p95 < 2.0s, xato < 1%)
- HTTP 429 (Rate Limit) va HTTP 503 (Pool Band) soni
- Server xotirasi (RSS)
"""
from __future__ import annotations
import os
import sys

# Repo ildizini sys.path ga qo'shish
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import time
import math
import asyncio
import argparse
from typing import List, Dict, Any

try:
    import aiohttp
except ImportError:
    print("Xatolik: aiohttp o'rnatilmagan. 'pip install aiohttp' bajaring.")
    sys.exit(1)

try:
    import psutil
except ImportError:
    psutil = None


def get_current_rss_mb() -> float:
    if psutil:
        try:
            return psutil.Process().memory_info().rss / (1024 * 1024)
        except Exception:
            pass
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return usage / (1024 * 1024) if sys.platform == "darwin" else usage / 1024
    except Exception:
        return 0.0


class LoadTestRunner:
    def __init__(self, base_url: str, duration_sec: float = 300.0, is_fast: bool = False):
        self.base_url = base_url.rstrip("/")
        self.duration_sec = duration_sec if not is_fast else 20.0
        self.is_fast = is_fast
        self.latencies: List[float] = []
        self.status_counts: Dict[int, int] = {}
        self.error_messages: List[str] = []
        self.lock = asyncio.Lock()

    async def _record(self, status: int, latency: float, error: str = ""):
        async with self.lock:
            self.latencies.append(latency)
            self.status_counts[status] = self.status_counts.get(status, 0) + 1
            if error:
                self.error_messages.append(error)

    async def simulate_user_flow(self, session: aiohttp.ClientSession, user_id: int, test_id: int):
        init_data = f"user=%7B%22id%22%3A{user_id}%2C%22first_name%22%3A%22VirtualUser{user_id}%22%7D"
        headers = {
            "Content-Type": "application/json",
            "X-Telegram-User-Id": str(user_id),
            "X-Telegram-Init-Data": init_data
        }

        # 1. Profilni yuklash
        t0 = time.monotonic()
        try:
            async with session.get(f"{self.base_url}/api/app/profile?tg_id={user_id}", headers=headers) as resp:
                await resp.read()
                await self._record(resp.status, time.monotonic() - t0)
        except Exception as e:
            await self._record(599, time.monotonic() - t0, f"Profile error: {e}")

        # 2. Faol testlar ro'yxatini olish
        t0 = time.monotonic()
        try:
            async with session.get(f"{self.base_url}/api/app/active-tests?tg_id={user_id}", headers=headers) as resp:
                await resp.read()
                await self._record(resp.status, time.monotonic() - t0)
        except Exception as e:
            await self._record(599, time.monotonic() - t0, f"Active tests error: {e}")

        # 3. Testni ochish (Savollar / Qoralama yuklash)
        t0 = time.monotonic()
        try:
            async with session.get(f"{self.base_url}/api/app/get-draft?test_id={test_id}&user_tg_id={user_id}", headers=headers) as resp:
                await resp.read()
                await self._record(resp.status, time.monotonic() - t0)
        except Exception as e:
            await self._record(599, time.monotonic() - t0, f"Get draft error: {e}")

        # 4. Autosave javoblarini saqlash
        t0 = time.monotonic()
        answers = {str(i): "A" for i in range(1, 36)}
        answers["36"] = "TEST"
        answers["40a"] = "A"
        answers["40b"] = "B"
        answers["45"] = "55.0"

        try:
            async with session.post(
                f"{self.base_url}/api/app/autosave-answer",
                headers=headers,
                json={
                    "test_id": test_id,
                    "user_tg_id": user_id,
                    "answers": answers,
                    "init_data": init_data
                }
            ) as resp:
                await resp.read()
                await self._record(resp.status, time.monotonic() - t0)
        except Exception as e:
            await self._record(599, time.monotonic() - t0, f"Autosave error: {e}")

        # 5. Testni topshirish (Submit)
        t0 = time.monotonic()
        try:
            async with session.post(
                f"{self.base_url}/api/submit-test",
                headers=headers,
                json={
                    "test_id": test_id,
                    "user_tg_id": user_id,
                    "fullname": f"Virtual User {user_id}",
                    "answers": answers,
                    "init_data": init_data
                }
            ) as resp:
                await resp.read()
                await self._record(resp.status, time.monotonic() - t0)
        except Exception as e:
            await self._record(599, time.monotonic() - t0, f"Submit error: {e}")

    async def run(self, num_phase1: int = 100, num_phase2: int = 30):
        print(f"\n🚀 YUKLAMA SINOVI BOSHLANDI: {self.base_url}")
        print(f"📊 Rejim: {'Tezkor simulyatsiya' if self.is_fast else '5 daqiqalik toʻliq sinov'}")
        print(f"📈 1-Bosqich: {num_phase1} ta foydalanuvchi bir tekisda")
        print(f"⚡ 2-Bosqich: {num_phase2} ta bir vaqtda faol topshiruvchi\n")

        initial_rss = get_current_rss_mb()
        print(f"Boshlang'ich RSS xotira: {initial_rss:.1f} MB")

        timeout = aiohttp.ClientTimeout(total=15)
        conn = aiohttp.TCPConnector(limit=50, limit_per_host=40, ssl=False)
        async with aiohttp.ClientSession(connector=conn, timeout=timeout) as session:
            # Mavjud faol test ID sini aniqlash
            target_test_id = 1
            try:
                async with session.get(f"{self.base_url}/api/app/active-tests") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        tests = data.get("tests", [])
                        if tests and isinstance(tests, list) and len(tests) > 0:
                            target_test_id = int(tests[0].get("id", 1))
            except Exception:
                pass

            # Virtual foydalanuvchilarni tasdiqlangan qilib tayyorlash
            all_uids = [900000000 + i for i in range(1, num_phase1 + 1)] + [900010000 + j for j in range(1, num_phase2 + 1)]
            try:
                import test_db
                conn = test_db.get_connection()
                try:
                    cur = conn.cursor()
                    now_ts = int(time.time())
                    for uid in all_uids:
                        cur.execute(
                            f"INSERT INTO users (tg_id, fullname, phone, username, status, registered_at) "
                            f"VALUES ({test_db._ph()}, {test_db._ph()}, {test_db._ph()}, {test_db._ph()}, {test_db._ph()}, {test_db._ph()}) "
                            f"ON CONFLICT (tg_id) DO UPDATE SET status = 'approved'",
                            (uid, f"Virtual Student {uid}", "+998901234567", f"vuser_{uid}", "approved", now_ts)
                        )
                    cur.execute("DELETE FROM submissions WHERE user_tg_id >= 900000000 AND user_tg_id <= 900020000")
                    conn.commit()
                    print(f"✅ {len(all_uids)} ta virtual test foydalanuvchisi bazada tayyorlandi.")
                finally:
                    test_db._close_conn(conn)
            except Exception as e:
                print(f"⚠️ Foydalanuvchilarni tayyorlashda ogohlantirish (davom etiladi): {e}")
            # 1-Bosqich: Foydalanuvchilarning bir tekisda kirishi
            print(f"\n--- 1-BOSQICH: {num_phase1} virtual foydalanuvchi ---")
            delay_between = self.duration_sec / max(1, num_phase1)
            tasks = []
            for i in range(1, num_phase1 + 1):
                uid = 900000000 + i
                tasks.append(asyncio.create_task(self.simulate_user_flow(session, uid, test_id=target_test_id)))
                if delay_between > 0.01:
                    await asyncio.sleep(delay_between)

            await asyncio.gather(*tasks, return_exceptions=True)
            print(f"✅ 1-bosqich yakunlandi. Jami so'rovlar: {len(self.latencies)}")

            # 2-Bosqich: 30 foydalanuvchi BIR VAQTNING O'ZIDA (Burst Concurrency)
            print(f"\n--- 2-BOSQICH: {num_phase2} foydalanuvchi bir vaqtda topshirish ---")
            burst_tasks = []
            for j in range(1, num_phase2 + 1):
                burst_uid = 900010000 + j
                burst_tasks.append(asyncio.create_task(self.simulate_user_flow(session, burst_uid, test_id=target_test_id)))

            await asyncio.gather(*burst_tasks, return_exceptions=True)
            print(f"✅ 2-bosqich yakunlandi. Umumiy so'rovlar: {len(self.latencies)}")

        final_rss = get_current_rss_mb()
        self.print_report(initial_rss, final_rss)

    def print_report(self, init_rss: float, final_rss: float):
        if not self.latencies:
            print("❌ Birorta ham so'rov bajarilmadi.")
            return

        sorted_lat = sorted(self.latencies)
        total_reqs = len(sorted_lat)
        avg_lat = sum(sorted_lat) / total_reqs

        p50 = sorted_lat[int(math.floor(total_reqs * 0.50))]
        p95 = sorted_lat[min(int(math.floor(total_reqs * 0.95)), total_reqs - 1)]
        p99 = sorted_lat[min(int(math.floor(total_reqs * 0.99)), total_reqs - 1)]
        max_lat = sorted_lat[-1]

        # Status hisoblari
        count_200 = sum(v for k, v in self.status_counts.items() if 200 <= k < 300)
        count_429 = self.status_counts.get(429, 0)
        count_503 = self.status_counts.get(503, 0)
        count_errors = sum(v for k, v in self.status_counts.items() if k >= 500 or k == 599)
        error_rate = (count_errors / total_reqs) * 100.0

        print("\n" + "=" * 60)
        print("               YUKLAMA SINOVI HISOBOTI")
        print("=" * 60)
        print(f"Jami so'rovlar soni:       {total_reqs}")
        print(f"Muvaffaqiyatli (2xx):      {count_200} ({count_200 / total_reqs * 100:.1f}%)")
        print(f"Rate limited (429):        {count_429}")
        print(f"Pool band (503):           {count_503}")
        print(f"Boshqa xatolar (5xx):      {count_errors}")
        print(f"Xatolik foizi:             {error_rate:.2f}%")
        print("-" * 60)
        print(f"O'rtacha kechikish (Avg):  {avg_lat * 1000:.1f} ms")
        print(f"Median kechikish (p50):    {p50 * 1000:.1f} ms")
        print(f"95-persentil (p95):        {p95 * 1000:.1f} ms ({p95:.3f} s)")
        print(f"99-persentil (p99):        {p99 * 1000:.1f} ms ({p99:.3f} s)")
        print(f"Maksimal kechikish (Max):  {max_lat * 1000:.1f} ms")
        print("-" * 60)
        print(f"Xotira (RSS):              {init_rss:.1f} MB -> {final_rss:.1f} MB (Farq: {final_rss - init_rss:+.1f} MB)")
        print("=" * 60)

        # Qabul mezonlari tekshiruvi: p95 < 2.0s va xatolik < 1%
        passed_p95 = p95 < 2.0
        passed_errors = error_rate < 1.0

        if passed_p95 and passed_errors:
            print("🎉 QABUL MEZONLARI BAJARILDI (p95 < 2s, xatolar < 1%)!")
        else:
            print("⚠️ QABUL MEZONLARI BAJARILMADI:")
            if not passed_p95:
                print(f"  - p95 kechikish {p95:.2f}s > 2.0s")
            if not passed_errors:
                print(f"  - Xatolik darajasi {error_rate:.2f}% >= 1.0%")

        print("=" * 60 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ona Tili Bot Yuklama Sinovi")
    parser.add_argument("--url", default="http://127.0.0.1:8082", help="Server URL manzili")
    parser.add_argument("--fast", action="store_true", help="Tezkor simulyatsiya rejimi")
    parser.add_argument("--users", type=int, default=100, help="1-bosqich foydalanuvchilar soni")
    parser.add_argument("--burst", type=int, default=30, help="2-bosqich parallel foydalanuvchilar soni")
    args = parser.parse_args()

    runner = LoadTestRunner(base_url=args.url, is_fast=args.fast)
    asyncio.run(runner.run(num_phase1=args.users, num_phase2=args.burst))
