#!/usr/bin/env python3
"""扫描 ZTE IPTV channel id，并与现有 DIYP 列表对比找出未收录频道。

原理（已实测）:
    每条频道地址形如:
        http://124.90.44.40:6060/010/2/<32位ID>?virtualDomain=010.live_hls.zte.com
    - 有效 ID: 跟随跳转后最终 HTTP 200，正文以 #EXTM3U 开头
    - 无效 ID: 最终 HTTP 404，正文为空
    据此可批量探测哪些 ID 是真实频道。

ID 结构: 32 位 = 20 位前缀 + 12 位数字后缀（各"家族"前缀不同）。
"""

import argparse
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

BASE = "http://124.90.44.40:6060/010/2/{id}?virtualDomain=010.live_hls.zte.com"
UA = {"User-Agent": "okhttp/3.12.11"}

# 各家族: (20位前缀, [(区间起, 区间止), ...])  后缀补足到 12 位
FAMILIES = {
    "00000001000000060000": [(300, 900)],
    "00000009000000060000": [(2000, 2100)],
    "01000010000000060000": [(2800, 2950)],
    "20002000000000010000": [(2750000, 2752000), (2775000, 2777500)],
}

_print_lock = threading.Lock()


def load_existing(path):
    """从 DIYP txt 读取 {id: 频道名}。"""
    mapping = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.search(r"/010/2/(\w+)\?", line)
            if m:
                name = line.split(",", 1)[0].strip()
                mapping[m.group(1)] = name
    return mapping


def gen_ids(families):
    for prefix, ranges in families.items():
        for start, end in ranges:
            for n in range(start, end + 1):
                yield prefix + str(n).zfill(12)


def probe(session, cid, timeout):
    """返回 cid 若有效，否则 None。"""
    url = BASE.format(id=cid)
    try:
        r = session.get(url, headers=UA, timeout=timeout,
                         allow_redirects=True, stream=True)
        if r.status_code != 200:
            return None
        chunk = next(r.iter_content(64), b"") or b""
        r.close()
        return cid if chunk.startswith(b"#EXTM3U") else None
    except requests.RequestException:
        return None


def main():
    ap = argparse.ArgumentParser(description="扫描 ZTE channel id 并与现有列表对比")
    ap.add_argument("--list", default="浙江联通-含cctv4.txt", help="现有 DIYP txt 路径")
    ap.add_argument("--workers", type=int, default=16, help="并发线程数")
    ap.add_argument("--timeout", type=float, default=10, help="单请求超时(秒)")
    ap.add_argument("--out", default="scan_result.txt", help="发现的新 ID 输出文件")
    args = ap.parse_args()

    existing = load_existing(args.list)
    print(f"现有列表: {len(existing)} 个频道 ({len(set(existing))} 个唯一ID)")

    all_ids = list(gen_ids(FAMILIES))
    total = len(all_ids)
    print(f"待扫描 ID 总数: {total}")

    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_maxsize=args.workers)
    session.mount("http://", adapter)

    found = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(probe, session, cid, args.timeout): cid for cid in all_ids}
        for fut in as_completed(futs):
            done += 1
            cid = fut.result()
            if cid:
                found.append(cid)
            if done % 100 == 0 or done == total:
                with _print_lock:
                    sys.stdout.write(f"\r进度 {done}/{total}  已发现 {len(found)}")
                    sys.stdout.flush()
    print()

    found_set = set(found)
    new_ids = sorted(found_set - set(existing))
    known_found = sorted(found_set & set(existing))

    print("\n===== 扫描结果 =====")
    print(f"扫描到有效频道: {len(found_set)}")
    print(f"其中已在列表中: {len(known_found)}")
    print(f"列表中未收录(新): {len(new_ids)}")

    # 列表里有、但本次没扫到的（可能超出扫描区间或临时不可用）
    not_found = sorted(set(existing) - found_set)
    if not_found:
        print(f"\n提示: 列表中有 {len(not_found)} 个 ID 本次未扫到 "
              f"(可能超出扫描区间或临时离线):")
        for cid in not_found:
            print(f"  {existing[cid]}: {cid}")

    if new_ids:
        print(f"\n----- 新发现 {len(new_ids)} 个未收录 ID -----")
        for cid in new_ids:
            print(f"  {cid}  "
                  f"http://124.90.44.40:6060/010/2/{cid}?virtualDomain=010.live_hls.zte.com")
        with open(args.out, "w", encoding="utf-8") as f:
            for cid in new_ids:
                f.write(f"未知频道,http://124.90.44.40:6060/010/2/{cid}"
                        f"?virtualDomain=010.live_hls.zte.com\n")
        print(f"\n已写入: {args.out}")
    else:
        print("\n没有发现列表之外的新频道。")


if __name__ == "__main__":
    main()
