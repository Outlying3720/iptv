#!/usr/bin/env python3
"""DIYP(txt) 直播源 -> 酷9 M3U 转换脚本（带 ZTE tvod 回看）。

DIYP txt 格式:
    分组名,#genre#      # 分组头
    频道名,播放地址      # 频道行
    (空行分隔)

酷9 回看说明:
    - txt 的 PB= 只能对整个分组做“参数追加”，无法改写 URL 内部字段，
      也无法逐频道设置，因此无法表达 ZTE 的 live_hls->tvod_hls 改写。
    - M3U 的 catchup="default" 允许 catchup-source 为“完整 URL 模板”，
      可自由改写地址并注入 ${(b)}/${(e)} 时间占位符，满足需求。

ZTE 回看地址生成规则（基于直播地址）:
    直播:  ...?virtualDomain=010.live_hls.zte.com
    回看:  ...?virtualDomain=010.tvod_hls.zte.com&programbegin=<begin>+00&programend=<end>+00
    其中 begin/end 为 UTC 时间, 格式 yyyyMMddHHmmss, 尾部 +00 为时区偏移。
"""

import argparse
import sys


def transform_catchup_source(live_url: str, tz: str) -> str:
    """由直播地址生成酷9 catchup-source 完整回看模板。"""
    # 1) live_hls -> tvod_hls
    tvod_url = live_url.replace("live_hls", "tvod_hls")
    # 2) 追加回看时间段占位符（酷9会在回看时替换 ${(b)}/${(e)}）
    begin = "${(b)yyyyMMddHHmmss|%s}+00" % tz
    end = "${(e)yyyyMMddHHmmss|%s}+00" % tz
    sep = "&" if "?" in tvod_url else "?"
    return f"{tvod_url}{sep}programbegin={begin}&programend={end}"


def parse_diyp(lines):
    """解析 DIYP txt，产出 (group, name, url) 三元组序列。"""
    current_group = "未分组"
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # 分组头: 名称,#genre#
        if line.endswith(",#genre#") or ",#genre#" in line:
            current_group = line.split(",", 1)[0].strip()
            continue
        # 频道行: 名称,地址
        if "," not in line:
            continue
        name, url = line.split(",", 1)
        name, url = name.strip(), url.strip()
        if not url.lower().startswith(("http://", "https://", "rtsp://", "rtmp://")):
            continue
        yield current_group, name, url


def build_m3u(entries, tz, epg_url=None):
    out = []
    header = "#EXTM3U"
    if epg_url:
        header += f' x-tvg-url="{epg_url}"'
    out.append(header)
    for group, name, url in entries:
        catchup_source = transform_catchup_source(url, tz)
        extinf = (
            f'#EXTINF:-1 tvg-name="{name}" group-title="{group}" '
            f'catchup="default" catchup-source="{catchup_source}",{name}'
        )
        out.append(extinf)
        out.append(url)
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description="DIYP txt -> 酷9 M3U（带ZTE回看）")
    ap.add_argument("input", help="输入的 DIYP txt 文件路径")
    ap.add_argument("-o", "--output", help="输出 m3u 路径（默认同名 .m3u）")
    ap.add_argument("--tz", default="UTC",
                    help="回看时间格式使用的时区（默认 UTC，匹配 +00 后缀）")
    ap.add_argument("--epg", default=None, help="可选：EPG(x-tvg-url)地址")
    args = ap.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        lines = f.readlines()

    entries = list(parse_diyp(lines))
    if not entries:
        print("未解析到任何频道，请检查输入格式。", file=sys.stderr)
        sys.exit(1)

    m3u = build_m3u(entries, args.tz, args.epg)

    out_path = args.output
    if not out_path:
        out_path = args.input.rsplit(".", 1)[0] + ".m3u"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(m3u)

    print(f"完成：{len(entries)} 个频道 -> {out_path}")


if __name__ == "__main__":
    main()
