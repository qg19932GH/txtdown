#!/usr/bin/env python3
"""
小黄书 xChina 小说下载器
用法：
  python xchina_downloader.py <url>

支持两种 URL：
  1. 章节列表页: https://xchina.co/fiction/id-<hex>.html
  2. 章节内容页: https://xchina.co/fiction/id-<base64>.html

输出：以小说标题命名的 .txt 文件
"""

import sys
import re
import os
import time
import cloudscraper
from urllib.parse import urljoin

BASE_URL = "https://xchina.co"


def create_scraper():
    scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
    )
    scraper.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    })
    return scraper


def fetch(scraper, url, retries=2):
    for i in range(retries):
        try:
            resp = scraper.get(url, timeout=15)
            if resp.status_code == 200:
                return resp.text
        except Exception as e:
            if i < retries - 1:
                time.sleep(2)
    return None


def parse_chapter(html, url):
    """从章节内容页提取信息"""
    info = {}

    # 小说标题
    m = re.search(r'<div\s+class="title"[^>]*>\s*([^<]+)\s*</div>', html)
    if m:
        info['fiction_title'] = m.group(1).strip()

    # 章节标题 (og:title)
    m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html)
    if m:
        parts = m.group(1).split(' - ')
        info['chapter_title'] = parts[0].strip()
        if len(parts) > 1 and 'fiction_title' not in info:
            info['fiction_title'] = parts[-1].strip()

    # 正文
    m = re.search(r'<div\s+class="fiction-body"[^>]*>(.*?)</div>', html, re.DOTALL)
    if m:
        paras = re.findall(r'<p>(.*?)</p>', m.group(1), re.DOTALL)
        if paras:
            clean = []
            for p in paras:
                t = re.sub(r'<br\s*/?>', '\n', p)
                t = re.sub(r'<[^>]+>', '', t)
                t = t.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>')
                t = t.replace('&amp;', '&').replace('&quot;', '"')
                t = t.strip()
                if t:
                    clean.append(t)
            info['content'] = '\n\n'.join(clean)

    # 下一章
    m = re.search(
        r'<a\s+href="([^"]+)"[^>]*>\s*<div[^>]*class="[^"]*next[^"]*"[^>]*>下一章',
        html, re.DOTALL
    )
    if m:
        info['next_url'] = urljoin(BASE_URL, m.group(1))

    return info


def get_chapters_from_toc(scraper, toc_url):
    """从目录页提取所有 base64 章节 URL"""
    html = fetch(scraper, toc_url)
    if not html:
        return []
    urls = []
    seen = set()
    for m in re.finditer(r'href="(/fiction/id-([A-Za-z0-9+/=]{30,})\.html)"', html):
        u = urljoin(BASE_URL, m.group(1))
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def download_novel(url, output_dir='.'):
    scraper = create_scraper()

    print(f"⏳ 正在访问: {url}")
    html = fetch(scraper, url)
    if not html:
        print("❌ 无法访问该页面，请检查 URL")
        return None

    info = parse_chapter(html, url)
    is_content_page = 'content' in info and info['content']

    # 确定小说标题和下载策略
    all_chapters = []
    fiction_title = info.get('fiction_title', '未知小说')

    if is_content_page:
        # 内容页 → 用"下一章"链式遍历
        print(f"📖 小说: {fiction_title}")
        print(f"📝 当前: {info.get('chapter_title', '未知章节')}")
        print(f"🔗 逐章下载中...\n")

        current = url
        visited = set()
        ch_num = 0

        while current and current not in visited:
            visited.add(current)
            ch_num += 1
            print(f"  [{ch_num}] ", end='', flush=True)
            h = fetch(scraper, current)
            if not h:
                print("❌ 下载失败")
                break

            ci = parse_chapter(h, current)
            ctitle = ci.get('chapter_title', f'第{ch_num}章')
            content = ci.get('content', '')
            all_chapters.append((ctitle, content))
            fiction_title = ci.get('fiction_title', fiction_title)
            print(f"{ctitle} ({len(content)}字)")

            next_url = ci.get('next_url')
            if next_url in visited:
                break
            current = next_url
            time.sleep(0.5)
    else:
        # 目录页 → 提取所有章节 URL 后批量下载
        print(f"📖 小说: {fiction_title}")
        print(f"📋 正在获取章节目录...")

        chapter_urls = get_chapters_from_toc(scraper, url)
        if not chapter_urls:
            print("❌ 未找到章节链接")
            return None

        print(f"📚 共 {len(chapter_urls)} 章，开始下载...\n")
        total = len(chapter_urls)

        for i, ch_url in enumerate(chapter_urls, 1):
            print(f"  [{i}/{total}] ", end='', flush=True)
            h = fetch(scraper, ch_url)
            if not h:
                print("❌")
                continue

            ci = parse_chapter(h, ch_url)
            ctitle = ci.get('chapter_title', f'第{i}章')
            content = ci.get('content', '')
            all_chapters.append((ctitle, content))
            fiction_title = ci.get('fiction_title', fiction_title)
            print(f"{ctitle} ({len(content)}字)")
            time.sleep(0.5)

    if not all_chapters:
        print("❌ 未下载到任何章节")
        return None

    # 输出文件
    safe = re.sub(r'[<>:"/\\|?*]', '_', fiction_title)
    outpath = os.path.join(output_dir, f"{safe}.txt")

    with open(outpath, 'w', encoding='utf-8') as f:
        f.write(f"{fiction_title}\n{'='*50}\n\n")
        for ctitle, content in all_chapters:
            f.write(f"{ctitle}\n{'-'*30}\n\n{content}\n\n")

    total_chars = sum(len(c) for _, c in all_chapters)
    print(f"\n✅ 下载完成！")
    print(f"   📖 {fiction_title}")
    print(f"   📑 {len(all_chapters)} 章 / {total_chars} 字")
    print(f"   💾 {outpath}")
    return outpath


def main():
    if len(sys.argv) < 2:
        print("=" * 50)
        print("  小黄书 xChina 小说下载器")
        print("=" * 50)
        print()
        print("用法: xchina_downloader.exe <url>")
        print()
        print("示例:")
        print("  xchina_downloader.exe https://xchina.co/fiction/id-61a5216f4ab99.html")
        print("  xchina_downloader.exe https://xchina.co/fiction/id-dGhpc19...==.html")
        print()
        url = input("请输入小说 URL: ").strip()
        if not url:
            print("未输入 URL，退出。")
            return
    else:
        url = sys.argv[1].strip()

    if '/fiction/id-' not in url:
        print("❌ URL 格式不正确，需要包含 /fiction/id-")
        print("   例如: https://xchina.co/fiction/id-61a5216f4ab99.html")
        return

    result = download_novel(url)
    if result:
        print(f"\n按回车键退出...", end='')
        input()
    else:
        print(f"\n下载失败，按回车键退出...", end='')
        input()


if __name__ == '__main__':
    main()
