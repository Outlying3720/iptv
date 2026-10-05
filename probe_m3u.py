#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测 m3u 中每个源能否播放，输出 validate.m3u / invalidate.m3u。

探测方式：对每个源发起 HTTP 请求，下载 1 秒数据，校验是否为有效的 MPEG-TS 流
（0x47 同步字节按 188 字节步长连续对齐）。纯标准库实现，无需 ffmpeg。

两阶段判定（代理 503 是“暂时不可用”，与并发负载相关，不能一次定生死）：
  阶段1  并发扫描，每个源只试一次；
  阶段2  阶段1 失败的源进入串行重试队列，失败则轮转到队尾，最多重试 --retries 次。

用法：
    python3 probe_m3u.py [输入.m3u] [--concurrency 3] [--duration 1] [--timeout 8] [--retries 3]
"""
import argparse
import os
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

TS_PACKET = 188          # MPEG-TS 包长
SYNC = 0x47              # TS 同步字节
MIN_PACKETS = 20         # 至少连续多少个有效包才判为有效流


def parse_m3u(path):
    """解析 m3u，返回 (header 行, [(extinf, url), ...])，保持原顺序。"""
    header = "#EXTM3U"
    entries = []
    pending = None
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line.startswith("#EXTM3U"):
                header = line
            elif line.startswith("#EXTINF"):
                pending = line
            elif line.strip() and not line.startswith("#"):
                entries.append((pending if pending is not None else "#EXTINF:-1", line.strip()))
                pending = None
    return header, entries


def is_valid_ts(data):
    """校验是否为有效 MPEG-TS：找到同步对齐后，连续有效包数达标。"""
    n = len(data)
    if n < TS_PACKET * MIN_PACKETS:      # 数据太少，无法判定
        return False
    best = 0
    for start in range(TS_PACKET):       # 首包偏移 0~187
        if data[start] != SYNC:
            continue
        cnt = 0
        p = start
        while p < n and data[p] == SYNC:
            cnt += 1
            p += TS_PACKET
        if cnt > best:
            best = cnt
    return best >= MIN_PACKETS


def probe(url, duration, timeout, max_bytes=8 * 1024 * 1024):
    """单次探测：下载 duration 秒数据并校验。返回 (是否有效, 原因)。"""
    req = urllib.request.Request(url, headers={"User-Agent": "probe/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False, "http_%d" % resp.status
            deadline = time.monotonic() + duration
            buf = bytearray()
            while len(buf) < max_bytes:
                if time.monotonic() >= deadline:
                    break
                try:
                    chunk = resp.read(65536)
                except Exception:        # 读超时/被重置
                    break
                if not chunk:
                    break
                buf += chunk
    except urllib.error.HTTPError as e:
        return False, "http_%d" % e.code
    except Exception as e:
        return False, type(e).__name__

    if not buf:
        return False, "no_data"
    if not is_valid_ts(bytes(buf)):
        return False, "not_ts"
    return True, "ok"


def write_m3u(path, header, entries):
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n")
        for extinf, url in entries:
            f.write(extinf + "\n")
            f.write(url + "\n")


def main():
    ap = argparse.ArgumentParser(description="探测 m3u 源可用性")
    ap.add_argument("input", nargs="?", default="浙江电信-组播.m3u", help="输入 m3u")
    ap.add_argument("--concurrency", type=int, default=3, help="并发数，默认 3")
    ap.add_argument("--duration", type=float, default=1.0, help="下载秒数，默认 1")
    ap.add_argument("--timeout", type=float, default=8.0, help="单源超时秒数，默认 8")
    ap.add_argument("--retries", type=int, default=3, help="串行重试队列的最大尝试次数，默认 3")
    ap.add_argument("--valid", default=None, help="有效源输出路径")
    ap.add_argument("--invalid", default=None, help="无效源输出路径")
    args = ap.parse_args()

    header, entries = parse_m3u(args.input)
    base = os.path.dirname(os.path.abspath(args.input))
    vpath = args.valid or os.path.join(base, "validate.m3u")
    ipath = args.invalid or os.path.join(base, "invalidate.m3u")

    def name_of(i):
        return entries[i][0].rsplit(",", 1)[-1].strip()

    # 阶段1：并发扫描，每个源只试一次
    print("阶段1：并发 %d 扫描 %d 个源，每个下载 %.1fs" % (args.concurrency, len(entries), args.duration))
    results = {}
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex:
        futs = {ex.submit(probe, url, args.duration, args.timeout): i
                for i, (_, url) in enumerate(entries)}
        for fut in as_completed(futs):
            i = futs[fut]
            done += 1
            ok, reason = fut.result()
            if ok:
                results[i] = (True, reason)
            print("[%d/%d] %-3s %-10s %s" % (done, len(entries), "OK" if ok else "BAD", reason, name_of(i)),
                  flush=True)

    # 阶段2：失败源进串行重试队列；失败则轮转到队尾，队列内最多尝试 retries 次
    queue = deque((i, args.retries) for i in range(len(entries)) if i not in results)
    print("\n阶段2：%d 个失败源进入串行重试队列（每个最多 %d 次）" % (len(queue), args.retries))
    while queue:
        i, left = queue.popleft()
        ok, reason = probe(entries[i][1], args.duration, args.timeout)
        if ok:
            results[i] = (True, reason)
            print("  重试成功 %s" % name_of(i), flush=True)
            continue
        left -= 1
        if left > 0:
            queue.append((i, left))                 # 失败放队尾，稍后再试
            print("  重试失败 %-10s %s（剩 %d 次）" % (reason, name_of(i), left), flush=True)
        else:
            results[i] = (False, reason)
            print("  重试耗尽 %-10s %s" % (reason, name_of(i)), flush=True)

    valid = [entries[i] for i in range(len(entries)) if results[i][0]]
    invalid = [(entries[i], results[i][1]) for i in range(len(entries)) if not results[i][0]]

    write_m3u(vpath, header, valid)
    write_m3u(ipath, header, [e for e, _ in invalid])

    print("\n有效 %d -> %s" % (len(valid), vpath))
    print("无效 %d -> %s" % (len(invalid), ipath))
    if invalid:
        print("无效明细：")
        for (extinf, url), reason in invalid:
            print("  [%s] %s | %s" % (reason, extinf.rsplit(",", 1)[-1].strip(), url))


if __name__ == "__main__":
    sys.exit(main())