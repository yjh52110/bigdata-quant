"""Colab 环境实测基准。

贴进 Colab 单元格运行，产出的全是真实测量值，不含任何估算。

回答四个问题：
  1. 分到的机器规格是什么（CPU / 内存 / 磁盘 / GPU）
  2. 云盘读写速度多少（决定"数据放云盘"这条路是否成立）
  3. 公网下载速度多少（决定采集要多久）
  4. DuckDB 在云盘数据上的查询速度（决定"直接查云盘"是否可行）

第 4 项最关键：它直接验证"云盘只能当冷归档、不能当查询后端"这个判断。
"""

import os
import shutil
import subprocess
import time

RESULTS = {}


def _t(label, fn):
    t0 = time.time()
    out = fn()
    el = time.time() - t0
    RESULTS[label] = el
    return out, el


def section(title):
    print(f"\n{'=' * 58}\n{title}\n{'=' * 58}")


def bench_machine():
    section("1. 机器规格")
    cpu = os.cpu_count()
    try:
        ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
    except (ValueError, OSError):
        ram = -1
    total, _, free = shutil.disk_usage("/content" if os.path.exists("/content") else "/")
    print(f"  CPU 核心   : {cpu}")
    print(f"  内存       : {ram:.1f} GB")
    print(f"  磁盘       : {free / 1024**3:.1f} GB 可用 / {total / 1024**3:.1f} GB")
    try:
        g = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                           capture_output=True, timeout=10).stdout.decode().strip()
        print(f"  GPU        : {g or '无'}")
    except Exception:
        print("  GPU        : 无")
    RESULTS.update({"cpu": cpu, "ram_gb": round(ram, 1), "disk_free_gb": round(free / 1024 ** 3, 1)})


def bench_drive(size_mb=200):
    section(f"2. 云盘读写速度（{size_mb} MB）")
    root = "/content/drive/MyDrive"
    if not os.path.isdir(root):
        print("  云盘未挂载，跳过。先运行 from google.colab import drive; drive.mount('/content/drive')")
        return
    path = os.path.join(root, "_chainquant_bench.bin")
    blob = os.urandom(1024 * 1024)

    def _write():
        with open(path, "wb") as f:
            for _ in range(size_mb):
                f.write(blob)
        os.fsync
        return None

    _, wt = _t("drive_write_s", _write)
    print(f"  写入: {size_mb / wt:6.1f} MB/s  （耗时 {wt:.1f}s）")

    def _read():
        n = 0
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                n += len(chunk)
        return n

    _, rt = _t("drive_read_s", _read)
    print(f"  读取: {size_mb / rt:6.1f} MB/s  （耗时 {rt:.1f}s）")
    RESULTS["drive_write_mbps"] = round(size_mb / wt, 1)
    RESULTS["drive_read_mbps"] = round(size_mb / rt, 1)
    os.remove(path)


def bench_local_disk(size_mb=200):
    section(f"3. 本地磁盘速度对比（{size_mb} MB）")
    path = "/content/_bench_local.bin" if os.path.exists("/content") else "/tmp/_bench_local.bin"
    blob = os.urandom(1024 * 1024)

    def _write():
        with open(path, "wb") as f:
            for _ in range(size_mb):
                f.write(blob)

    _, wt = _t("local_write_s", _write)

    def _read():
        with open(path, "rb") as f:
            while f.read(1024 * 1024):
                pass

    _, rt = _t("local_read_s", _read)
    print(f"  写入: {size_mb / wt:6.1f} MB/s")
    print(f"  读取: {size_mb / rt:6.1f} MB/s")
    RESULTS["local_write_mbps"] = round(size_mb / wt, 1)
    RESULTS["local_read_mbps"] = round(size_mb / rt, 1)
    os.remove(path)


def bench_download():
    section("4. 公网下载速度")
    import requests

    # 币安真实归档，约 2 MB，代表行情采集速度
    url = ("https://data.binance.vision/data/spot/monthly/klines/"
           "BTCUSDT/1m/BTCUSDT-1m-2025-01.zip")
    t0 = time.time()
    r = requests.get(url, timeout=180)
    el = time.time() - t0
    mb = len(r.content) / 1024 ** 2
    print(f"  币安归档: {mb:.1f} MB / {el:.1f}s = {mb / el:.1f} MB/s")
    RESULTS["binance_dl_mbps"] = round(mb / el, 1)

    # AWS 公开数据集，代表链上数据采集速度
    aws = ("https://aws-public-blockchain.s3.us-east-2.amazonaws.com/"
           "v1.0/eth/blocks/date=2026-07-01/")
    try:
        import re
        lst = requests.get(aws.replace("v1.0/eth", "?list-type=2&prefix=v1.0/eth")
                           .replace("aws-public-blockchain.s3", "aws-public-blockchain.s3"), timeout=60)
        key = re.findall(r"<Key>([^<]*\.parquet)</Key>", lst.text)
        if key:
            t0 = time.time()
            n = 0
            with requests.get(f"https://aws-public-blockchain.s3.us-east-2.amazonaws.com/{key[0]}",
                              stream=True, timeout=300) as rr:
                for c in rr.iter_content(1024 * 1024):
                    n += len(c)
                    if n > 50 * 1024 ** 2:  # 取样 50MB 即可
                        break
            el = time.time() - t0
            print(f"  AWS 链上: {n / 1024**2:.0f} MB / {el:.1f}s = {n / 1024**2 / el:.1f} MB/s")
            RESULTS["aws_dl_mbps"] = round(n / 1024 ** 2 / el, 1)
    except Exception as e:
        print(f"  AWS 测速跳过: {e}")


def bench_duckdb():
    section("5. DuckDB 查询：本地 vs 云盘")
    try:
        import duckdb
        import polars as pl
    except ImportError:
        print("  未安装 duckdb/polars，跳过。pip install duckdb polars")
        return

    import random
    rows = 2_000_000
    df = pl.DataFrame({
        "ts": list(range(rows)),
        "price": [random.random() * 100 for _ in range(rows)],
        "vol": [random.random() for _ in range(rows)],
    })

    local = "/content/_bench.parquet" if os.path.exists("/content") else "/tmp/_bench.parquet"
    df.write_parquet(local, compression="zstd")
    sz = os.path.getsize(local) / 1024 ** 2
    print(f"  测试数据: {rows:,} 行, {sz:.1f} MB")

    q = "SELECT count(*), avg(price), max(vol) FROM read_parquet('{}')"
    con = duckdb.connect(":memory:")

    _, lt = _t("duckdb_local_s", lambda: con.execute(q.format(local)).fetchall())
    print(f"  本地盘查询: {lt * 1000:7.0f} ms")
    RESULTS["duckdb_local_ms"] = round(lt * 1000)

    drive_root = "/content/drive/MyDrive"
    if os.path.isdir(drive_root):
        dpath = os.path.join(drive_root, "_bench.parquet")
        shutil.copy(local, dpath)
        _, dt = _t("duckdb_drive_s", lambda: con.execute(q.format(dpath)).fetchall())
        print(f"  云盘查询  : {dt * 1000:7.0f} ms   （慢 {dt / lt:.1f} 倍）")
        RESULTS["duckdb_drive_ms"] = round(dt * 1000)
        RESULTS["drive_slowdown_x"] = round(dt / lt, 1)
        os.remove(dpath)
        print()
        if dt / lt > 3:
            print("  ⇒ 云盘明显更慢，验证了「云盘只当冷归档、查询走本地」的设计。")
        else:
            print("  ⇒ 本次差距不大，但单文件测试不能代表几千个文件的场景。")
    else:
        print("  云盘未挂载，跳过对比")
    os.remove(local)


def main():
    print("ChainQuant Colab 实测基准 —— 所有数字均为真实测量")
    bench_machine()
    bench_drive()
    bench_local_disk()
    bench_download()
    bench_duckdb()

    section("汇总（可直接贴回对话）")
    import json
    print(json.dumps(RESULTS, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
