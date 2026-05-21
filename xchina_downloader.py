#!/usr/bin/env python3
"""
小黄书 xChina 小说下载器 (GUI 版)
支持多 URL 批量下载，可配置代理
"""

import sys
import re
import os
import time
import json
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from urllib.parse import urljoin

import cloudscraper

BASE_URL = "https://xchina.co"
CONFIG_FILE = "xchina_config.json"

# ─── 配置读写 ───

def load_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_config(cfg):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=2)

# ─── 核心下载逻辑 ───

def create_scraper(proxy=None):
    kwargs = {
        'browser': {'browser': 'chrome', 'platform': 'windows', 'mobile': False}
    }
    scraper = cloudscraper.create_scraper(**kwargs)
    scraper.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    })
    if proxy:
        scraper.proxies = {'http': proxy, 'https': proxy}
    return scraper


def fetch(scraper, url, retries=2):
    for i in range(retries):
        try:
            resp = scraper.get(url, timeout=20)
            if resp.status_code == 200:
                return resp.text
        except Exception as e:
            if i < retries - 1:
                time.sleep(2)
    return None


def parse_chapter(html, url):
    info = {}
    m = re.search(r'<div\s+class="title"[^>]*>\s*([^<]+)\s*</div>', html)
    if m:
        info['fiction_title'] = m.group(1).strip()

    m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html)
    if m:
        parts = m.group(1).split(' - ')
        info['chapter_title'] = parts[0].strip()
        if len(parts) > 1 and 'fiction_title' not in info:
            info['fiction_title'] = parts[-1].strip()

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

    m = re.search(
        r'<a\s+href="([^"]+)"[^>]*>\s*<div[^>]*class="[^"]*next[^"]*"[^>]*>下一章',
        html, re.DOTALL
    )
    if m:
        info['next_url'] = urljoin(BASE_URL, m.group(1))

    return info


def get_chapters_from_toc(scraper, toc_url):
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


def download_one(scraper, url, log_callback=None):
    """下载单本小说，返回 (标题, 章节列表, 总字数)"""

    def log(msg):
        print(msg)
        if log_callback:
            log_callback(msg)

    log(f"访问: {url}")
    html = fetch(scraper, url)
    if not html:
        log("❌ 无法访问")
        return None, [], 0

    info = parse_chapter(html, url)
    is_content = 'content' in info and info['content']
    fiction_title = info.get('fiction_title', '未知小说')
    chapters = []

    if is_content:
        log(f"📖 {fiction_title}（逐章遍历中...）")
        current = url
        visited = set()
        ch_num = 0
        while current and current not in visited:
            visited.add(current)
            ch_num += 1
            h = fetch(scraper, current)
            if not h:
                log(f"  [{ch_num}] ❌ 下载失败")
                break
            ci = parse_chapter(h, current)
            ctitle = ci.get('chapter_title', f'第{ch_num}章')
            content = ci.get('content', '')
            chapters.append((ctitle, content))
            fiction_title = ci.get('fiction_title', fiction_title)
            log(f"  [{ch_num}] {ctitle} ({len(content)}字)")
            nxt = ci.get('next_url')
            if nxt in visited:
                break
            current = nxt
            time.sleep(0.3)
    else:
        log(f"📖 {fiction_title}（获取目录...）")
        ch_urls = get_chapters_from_toc(scraper, url)
        if not ch_urls:
            log("❌ 未找到章节")
            return fiction_title, [], 0

        total = len(ch_urls)
        log(f"📚 共 {total} 章")
        for i, ch_url in enumerate(ch_urls, 1):
            h = fetch(scraper, ch_url)
            if not h:
                log(f"  [{i}/{total}] ❌")
                continue
            ci = parse_chapter(h, ch_url)
            ctitle = ci.get('chapter_title', f'第{i}章')
            content = ci.get('content', '')
            chapters.append((ctitle, content))
            fiction_title = ci.get('fiction_title', fiction_title)
            log(f"  [{i}/{total}] {ctitle} ({len(content)}字)")
            time.sleep(0.3)

    return fiction_title, chapters, sum(len(c) for _, c in chapters)


def save_txt(title, chapters, output_dir='.'):
    safe = re.sub(r'[<>:"/\\|?*]', '_', title)
    path = os.path.join(output_dir, f"{safe}.txt")
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f"{title}\n{'='*50}\n\n")
        for ct, cc in chapters:
            f.write(f"{ct}\n{'-'*30}\n\n{cc}\n\n")
    return path

# ─── GUI ───

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("xChina 小说下载器")
        self.root.geometry("700x550")
        self.root.resizable(True, True)

        # 标题
        title_label = ttk.Label(root, text="📖 xChina 小说下载器",
                                font=('Microsoft YaHei', 16, 'bold'))
        title_label.pack(pady=(15, 5))

        subtitle = ttk.Label(root, text="粘贴小说 URL（每行一个），自动下载并保存为 TXT",
                             foreground='gray')
        subtitle.pack(pady=(0, 10))

        # ── URL 输入框 ──
        url_frame = ttk.LabelFrame(root, text=" 小说 URL（一行一个）", padding=5)
        url_frame.pack(fill='both', expand=True, padx=15, pady=(0, 5))

        self.url_text = scrolledtext.ScrolledText(url_frame, height=5, font=('Consolas', 10))
        self.url_text.pack(fill='both', expand=True)

        # ── 代理设置 ──
        proxy_frame = ttk.Frame(root)
        proxy_frame.pack(fill='x', padx=15, pady=(0, 5))

        ttk.Label(proxy_frame, text="代理:").pack(side='left')
        self.proxy_var = tk.StringVar()
        self.proxy_entry = ttk.Entry(proxy_frame, textvariable=self.proxy_var, width=35)
        self.proxy_entry.pack(side='left', padx=5)
        ttk.Label(proxy_frame, text="例: http://127.0.0.1:7890", foreground='gray').pack(side='left')

        # ── 按钮 ──
        btn_frame = ttk.Frame(root)
        btn_frame.pack(fill='x', padx=15, pady=(5, 5))

        self.download_btn = ttk.Button(btn_frame, text="⬇️ 开始下载",
                                       command=self.start_download)
        self.download_btn.pack(side='left', padx=(0, 10))

        self.stop_btn = ttk.Button(btn_frame, text="⏹ 停止", command=self.stop_download,
                                   state='disabled')
        self.stop_btn.pack(side='left')

        # ── 进度条 ──
        self.progress = ttk.Progressbar(root, mode='indeterminate')
        self.progress.pack(fill='x', padx=15, pady=(0, 5))

        # ── 日志输出 ──
        log_frame = ttk.LabelFrame(root, text=" 日志", padding=5)
        log_frame.pack(fill='both', expand=True, padx=15, pady=(0, 10))

        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, font=('Consolas', 9),
                                                   state='disabled')
        self.log_text.pack(fill='both', expand=True)

        # 状态栏
        self.status_var = tk.StringVar(value="就绪")
        status_bar = ttk.Label(root, textvariable=self.status_var, relief='sunken',
                               anchor='w', padding=(5, 2))
        status_bar.pack(fill='x', side='bottom')

        # 运行状态
        self.running = False
        self.scraper = None

        # 加载配置
        cfg = load_config()
        self.proxy_var.set(cfg.get('proxy', ''))

        # 关闭时保存配置
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def log(self, msg):
        self.log_text.configure(state='normal')
        self.log_text.insert('end', msg + '\n')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')

    def status(self, msg):
        self.status_var.set(msg)

    def start_download(self):
        urls_text = self.url_text.get('1.0', 'end-1c').strip()
        if not urls_text:
            messagebox.showwarning("提示", "请至少输入一个 URL")
            return

        urls = [u.strip() for u in urls_text.split('\n') if u.strip()]
        # 过滤无效 URL
        valid = [u for u in urls if '/fiction/id-' in u]
        if not valid:
            messagebox.showwarning("提示", "URL 格式不正确，需要包含 /fiction/id-")
            return

        invalid = len(urls) - len(valid)
        if invalid > 0:
            if not messagebox.askyesno("确认", f"有 {invalid} 个 URL 格式不正确，继续下载其余 {len(valid)} 个？"):
                return

        # 保存配置
        save_config({'proxy': self.proxy_var.get().strip()})

        self.running = True
        self.download_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        self.progress.start(10)
        self.log_text.configure(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.configure(state='disabled')

        proxy = self.proxy_var.get().strip() or None

        thread = threading.Thread(target=self._download_thread, args=(valid, proxy), daemon=True)
        thread.start()

    def _download_thread(self, urls, proxy):
        self.scraper = create_scraper(proxy)
        if proxy:
            self.log(f"🔧 使用代理: {proxy}")

        total_done = 0
        success_count = 0
        total_urls = len(urls)

        for i, url in enumerate(urls, 1):
            if not self.running:
                self.log("⏹ 用户停止")
                break

            self.log(f"\n{'='*50}")
            self.log(f"[{i}/{total_urls}] 开始下载")
            self.log(f"{'='*50}")

            title, chapters, chars = download_one(self.scraper, url, log_callback=self.log)

            if chapters:
                path = save_txt(title, chapters)
                self.log(f"\n✅ 已保存: {path}")
                self.log(f"   📖 {title} | 📑 {len(chapters)}章 | 📝 {chars}字")
                success_count += 1
            else:
                self.log(f"❌ 下载失败: {title or url}")

            total_done += 1

        self.log(f"\n{'='*50}")
        self.log(f"🎉 完成! {success_count}/{total_urls} 本下载成功")
        self.log(f"{'='*50}")

        self.root.after(0, self._download_done)

    def _download_done(self):
        self.running = False
        self.download_btn.configure(state='normal')
        self.stop_btn.configure(state='disabled')
        self.progress.stop()
        self.status("就绪")

    def stop_download(self):
        self.running = False
        self.stop_btn.configure(state='disabled')
        self.log("\n⏸ 正在停止...")
        self.status("停止中...")

    def on_close(self):
        save_config({'proxy': self.proxy_var.get().strip()})
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
