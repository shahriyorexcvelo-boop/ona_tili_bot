"""
Load Testing Script for Ona Tili Bot & Mini App
===============================================
Yuklama sinovi:
- Bosqich 1: 100 ta virtual foydalanuvchi bir tekisda kiradi (Profil, Faol testlar, Testni ochish).
- Bosqich 2: 30 ta foydalanuvchi bir vaqtda testni ochadi, autosave qiladi va topshiradi.

Xavfsizlik:
- Faqat localhost / 127.0.0.1 ga ruxsat beriladi.
- Boshqa URL uchun --allow-prod talab qilinadi.
- Production PostgreSQL ga to'g'ridan-to'g'ri ulanmaydi (faqat lokal SQLite).
- Soxta ID lar 900000000 dan boshlanadi.
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
import urllib.parse
from typing import List, Dict, Any, Optional

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
        self.first_error_per_endpoint: Dict[str, str] = {}
        self.endpoint_latencies: Dict[str, List[float]] = {
            "profile": [],
            "active_tests": [],
            "get_draft": [],
            "autosave": [],
            "submit": []
        }
        self.lock = asyncio.Lock()

    async def _record(self, endpoint_name: str, status: int, latency: float, error_body: str = ""):
        async with self.lock:
            self.latencies.append(latency)
            self.endpoint_latencies.setdefault(endpoint_name, []).append(latency)
            self.status_counts[status] = self.status_counts.get(status, 0) + 1
            if status >= 400 and endpoint_name not in self.first_error_per_endpoint:
                clean_err = error_body.strip().replace("\n", " ")[:200]
                self.first_error_per_endpoint[endpoint_name] = f"HTTP {status}: {clean_err}"

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
            async with session.get(f"{self.base_url}/api/app/profile?tg_id={user_id}&init_data={urllib.parse.quote(init_data)}", headers=headers) as resp:
                body = await resp.text()
                await self._record("profile", resp.status, time.monotonic() - t0, body)
        except Exception as e:
            await self._record("profile", 599, time.monotonic() - t0, str(e))

        # 2. Faol testlar ro'yxatini olish
        t0 = time.monotonic()
        try:
            async with session.get(f"{self.base_url}/api/app/active-tests?tg_id={user_id}&init_data={urllib.parse.quote(init_data)}", headers=headers) as resp:
                body = await resp.text()
                await self._record("active_tests", resp.status, time.monotonic() - t0, body)
        except Exception as e:
            await self._record("active_tests", 599, time.monotonic() - t0, str(e))

        # 3. Testni ochish (Savollar / Qoralama yuklash)
        t0 = time.monotonic()
        try:
            async with session.get(f"{self.base_url}/api/app/get-draft?test_id={test_id}&user_tg_id={user_id}&init_data={urllib.parse.quote(init_data)}", headers=headers) as resp:
                body = await resp.text()
                await self._record("get_draft", resp.status, time.monotonic() - t0, body)
        except Exception as e:
            await self._record("get_draft", 599, time.monotonic() - t0, str(e))

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
                body = await resp.text()
                await self._record("autosave", resp.status, time.monotonic() - t0, body)
        except Exception as e:
            await self._record("autosave", 599, time.monotonic() - t0, str(e))

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
                body = await resp.text()
                await self._record("submit", resp.status, time.monotonic() - t0, body)
        except Exception as e:
            await self._record("submit", 599, time.monotonic() - t0, str(e))

    def seed_local_sqlite(self, num_phase1: int, num_phase2: int):
        """Faqat lokal SQLite da virtual foydalanuvchilar va test tayyorlash."""
        db_url = os.getenv("DATABASE_URL", "").strip()
        if db_url and ("postgres" in db_url or "supabase" in db_url or "neon" in db_url):
            print("⚠️ Production DATABASE_URL aniqlandi: load_test bazaga ulanmadi.")
            return

        os.environ["DATABASE_URL"] = ""
        try:
            import test_db
            conn = test_db.get_connection()
            try:
                cur = conn.cursor()
                now_ts = int(time.time())
                all_uids = [900000000 + i for i in range(1, num_phase1 + 1)] + [900010000 + j for j in range(1, num_phase2 + 1)]
                for uid in all_uids:
                    cur.execute(
                        f"INSERT INTO users (tg_id, fullname, phone, username, status, registered_at) "
                        f"VALUES ({test_db._ph()}, {test_db._ph()}, {test_db._ph()}, {test_db._ph()}, {test_db._ph()}, {test_db._ph()}) "
                        f"ON CONFLICT (tg_id) DO UPDATE SET status = 'approved'",
                        (uid, f"Virtual Student {uid}", "+998901234567", f"vuser_{uid}", "approved", now_ts)
                    )
                cur.execute("DELETE FROM submissions WHERE user_tg_id >= 900000000 AND user_tg_id <= 900020000")
                conn.commit()
                print(f"✅ Lokal SQLite: {len(all_uids)} ta test foydalanuvchisi tayyorlandi.")
            finally:
                test_db._close_conn(conn)
        except Exception as e:
            print(f"Lokal SQLite seeding ogohlantirish: {e}")

    async def run(self, num_phase1: int = 100, num_phase2: int = 30):
        print(f"\n🚀 YUKLAMA SINOVI BOSHLANDI: {self.base_url}")
        print(f"📊 Rejim: {'Tezkor simulyatsiya' if self.is_fast else '5 daqiqalik toʻliq sinov'}")
        print(f"📈 1-Bosqich: {num_phase1} ta foydalanuvchi bir tekisda")
        print(f"⚡ 2-Bosqich: {num_phase2} ta bir vaqtda faol topshiruvchi\n")

        initial_rss = get_current_rss_mb()
        print(f"Boshlang'ich RSS xotira: {initial_rss:.1f} MB")

        # Lokal SQLite ga tayyorgarlik
        self.seed_local_sqlite(num_phase1, num_phase2)

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

        # Status hisoblari (BOSQICH 7.2 talabi)
        c_200 = sum(v for k, v in self.status_counts.items() if 200 <= k < 300)
        c_401 = self.status_counts.get(401, 0)
        c_403 = self.status_counts.get(403, 0)
        c_404 = self.status_counts.get(404, 0)
        c_429 = self.status_counts.get(429, 0)
        c_500 = self.status_counts.get(500, 0)
        c_502 = self.status_counts.get(502, 0)
        c_503 = self.status_counts.get(503, 0)
        c_other_5xx = sum(v for k, v in self.status_counts.items() if k >= 500 and k not in (500, 502, 503))
        total_errors = sum(v for k, v in self.status_counts.items() if k >= 400)
        total_5xx = sum(v for k, v in self.status_counts.items() if k >= 500)
        err_rate = (total_5xx / total_reqs) * 100.0

        print("\n" + "=" * 60)
        print("               YUKLAMA SINOVI HISOBOTI")
        print("=" * 60)
        print(f"Jami so'rovlar soni:       {total_reqs}")
        print(f"Muvaffaqiyatli (2xx):      {c_200} ({c_200 / total_reqs * 100:.1f}%)")
        print("-" * 60)
        print("HTTP Status kodlari bo'yicha hisob:")
        print(f"  200 OK:                  {c_200}")
        print(f"  401 Unauthorized:        {c_401}")
        print(f"  403 Forbidden:           {c_403}")
        print(f"  404 Not Found:           {c_404}")
        print(f"  429 Too Many Requests:   {c_429}")
        print(f"  500 Internal Error:      {c_500}")
        print(f"  502 Bad Gateway:         {c_502}")
        print(f"  503 Service Unavailable: {c_503}")
        if c_other_5xx:
            print(f"  5xx boshqa xatolar:      {c_other_5xx}")
        print(f"5xx Xatolik foizi:         {err_rate:.2f}%")
        print("-" * 60)
        print("Kechikishlar (Latency):")
        print(f"  O'rtacha (Avg):          {avg_lat * 1000:.1f} ms")
        print(f"  Median (p50):            {p50 * 1000:.1f} ms")
        print(f"  95-persentil (p95):      {p95 * 1000:.1f} ms ({p95:.3f} s)")
        print(f"  99-persentil (p99):      {p99 * 1000:.1f} ms ({p99:.3f} s)")
        print(f"  Maksimal (Max):          {max_lat * 1000:.1f} ms")
        print("-" * 60)
        print(f"Xotira (RSS):              {init_rss:.1f} MB -> {final_rss:.1f} MB (Farq: {final_rss - init_rss:+.1f} MB)")

        if self.first_error_per_endpoint:
            print("-" * 60)
            print("Har bir endpointdagi dastlabki xato javobi (200 belgigacha):")
            for ep, err_text in self.first_error_per_endpoint.items():
                print(f"  [{ep}]: {err_text}")

        print("=" * 60)

        passed_p95 = p95 < 2.0
        passed_errors = err_rate < 1.0

        if passed_p95 and passed_errors:
            print("🎉 QABUL MEZONLARI BAJARILDI (p95 < 2s, 5xx xatolar < 1%)!")
        else:
            print("⚠️ QABUL MEZONLARI BAJARILMADI:")
            if not passed_p95:
                print(f"  - p95 kechikish {p95:.2f}s >= 2.0s")
            if not passed_errors:
                print(f"  - 5xx Xatolik darajasi {err_rate:.2f}% >= 1.0%")

        print("=" * 60 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ona Tili Bot Yuklama Sinovi")
    parser.add_argument("--url", default="http://127.0.0.1:8082", help="Server URL manzili (default: http://127.0.0.1:8082)")
    parser.add_argument("--fast", action="store_true", help="Tezkor simulyatsiya rejimi")
    parser.add_argument("--users", type=int, default=100, help="1-bosqich foydalanuvchilar soni")
    parser.add_argument("--burst", type=int, default=30, help="2-bosqich parallel foydalanuvchilar soni")
    parser.add_argument("--allow-prod", action="store_true", help="Production/tashqi serverga yuklama sinoviga ruxsat berish (standart: rad etiladi)")
    args = parser.parse_args()

    # Xavfsizlik tekshiruvi: faqat localhost yoki 127.0.0.1 ga ruxsat
    parsed_url = urllib.parse.urlparse(args.url)
    hostname = (parsed_url.hostname or "").lower()
    if hostname not in ("localhost", "127.0.0.1", "::1") and not args.allow_prod:
        print(f"\n❌ XATOLIK: '{args.url}' tashqi server manzili!")
        print("Xavfsizlik qoidasi bo'yicha faqat localhost yoki 127.0.0.1 ga ruxsat berilgan.")
        print("Agar ataylab tashqi serverga yubormoqchi bo'lsangiz, --allow-prod bayrog'ini ishlating.\n")
        sys.exit(1)

    runner = LoadTestRunner(base_url=args.url, is_fast=args.fast)
    asyncio.run(runner.run(num_phase1=args.users, num_phase2=args.burst))
