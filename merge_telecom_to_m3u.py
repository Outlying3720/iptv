#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 A(telecom.txt) 的组播地址补充进 B(浙江电信-组播.m3u)。

规则：
1. 以“节目名 + 组播地址”对齐；A 中已存在于 B 的地址直接跳过（不重复添加）。
2. A 的节目在 B 中存在（按节目名匹配，含别名映射）：
   把 A 中 B 缺失的地址，插到该节目在 B 里首个条目的下一行，作为备用源。
3. A 的节目在 B 中不存在：
   追加到 B 的“其他”分组最后一条之后。

用法：
    python3 merge_telecom_to_m3u.py [A文件] [B文件] [输出文件]
默认：
    A=telecom.txt  B=浙江电信-组播.m3u  out=浙江电信-组播.merged.m3u
"""
import os
import re
import sys

# A 的节目名 -> B 的节目名（两边命名不一致的对应关系）
ALIAS = {
    "北京KAKU少儿": "卡酷少儿",
    "钱江都市": "浙江钱江都市",
    "浙江经视": "浙江经济生活",
    "凤凰卫视中文台": "凤凰中文",
    "凤凰卫视资讯台": "凤凰资讯",
    "CETV1": "中国教育1台",
    "CETV4": "中国教育4台",
    "BESTV1": "BesTV-1",
    "BESTV2": "BesTV-2",
    "BESTV3": "BesTV-3",
    "CGTN西语": "CGTN西班牙语",
    "CGTN俄语": "CGTN俄罗斯语",
    "CGTN阿语": "CGTN阿拉伯语",
}

GROUP_RE = re.compile(r'group-title="([^"]*)"')
ADDR_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}:\d+$")


def extract_addr(url):
    """从 rtp://ip:port 或 http://host:port/udp/ip:port 中取出 ip:port。"""
    addr = url.strip().rsplit("/", 1)[-1]
    return addr if ADDR_RE.match(addr) else ""


def read_a(path):
    """读 A，返回 [(节目名, ip:port)]，保持原始顺序。"""
    items = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.endswith("#genre#"):
                continue
            name, _, url = line.partition(",")
            addr = extract_addr(url)
            if name.strip() and addr:
                items.append((name.strip(), addr))
    return items


def read_b(path):
    """读 B，返回 (原始行列表, 条目列表)。"""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    entries = []
    for i, line in enumerate(lines):
        if not line.startswith("#EXTINF") or i + 1 >= len(lines):
            continue
        url = lines[i + 1]
        if not url.strip() or url.startswith("#"):
            continue
        gm = GROUP_RE.search(line)
        entries.append({
            "url_idx": i + 1,                                  # url 所在行号
            "name": line.rsplit(",", 1)[-1].strip(),           # 节目名
            "group": gm.group(1) if gm else "",
            "extinf": line,
            "addr": extract_addr(url),
        })
    return lines, entries


def merge(a_path, b_path, out_path):
    a_items = read_a(a_path)
    lines, entries = read_b(b_path)

    b_addrs = {e["addr"] for e in entries}
    first_entry = {}                       # B 节目名 -> 首个条目
    for e in entries:
        first_entry.setdefault(e["name"], e)

    prefix = "http://192.168.12.1:9999/udp/"   # 沿用 B 的播放地址写法
    if entries:                                # 从 B 的第一条 url 反推前缀
        first_url = lines[entries[0]["url_idx"]]
        m = re.match(r"(.*/udp/)", first_url)
        if m:
            prefix = m.group(1)

    insert_after = {}                      # url 行号 -> 需要插在其后的新行
    tail = []                              # B 中不存在的节目（追加到“其他”之后）
    skipped = 0
    n_backup = 0

    for name, addr in a_items:
        if addr in b_addrs:                # 该地址 B 已有，跳过
            skipped += 1
            continue
        # 别名只用于“匹配”，输出一律以 B 的名称为准
        target = first_entry.get(ALIAS.get(name, name))
        if target:                         # 节目在 B 中存在 -> 作为备用插到原条目后
            insert_after.setdefault(target["url_idx"], []).append(target["extinf"])  # B 的名称/分组
            insert_after[target["url_idx"]].append(prefix + addr)
            n_backup += 1
        else:                              # 节目在 B 中不存在 -> 追加
            tail.append('#EXTINF:-1 group-title="其他",%s' % name)
            tail.append(prefix + addr)

    # 把新节目追加到“其他”分组最后一条之后
    other = [e for e in entries if e["group"] == "其他"]
    use_tail_at_end = bool(tail) and not other
    if other and tail:
        insert_after.setdefault(other[-1]["url_idx"], []).extend(tail)

    out = []
    for i, line in enumerate(lines):
        out.append(line)
        if i in insert_after:
            out.extend(insert_after[i])
    if use_tail_at_end:
        out.extend(tail)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")

    print("A 条目 %d，B 条目 %d" % (len(a_items), len(entries)))
    print("跳过（B 已有地址）: %d" % skipped)
    print("插入备用行（原节目后）: %d" % n_backup)
    print("追加到“其他”之后的新节目: %d" % (len(tail) // 2))
    print("输出: %s" % out_path)


if __name__ == "__main__":
    base = os.path.dirname(os.path.abspath(__file__))
    a = sys.argv[1] if len(sys.argv) > 1 else os.path.join(base, "telecom.txt")
    b = sys.argv[2] if len(sys.argv) > 2 else os.path.join(base, "浙江电信-组播.m3u")
    out = sys.argv[3] if len(sys.argv) > 3 else os.path.join(base, "浙江电信-组播.merged.m3u")
    merge(a, b, out)