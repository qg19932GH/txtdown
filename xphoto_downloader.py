#!/usr/bin/env python3
"""
xChina 图片下载器 (GUI 版)
从 photo/id-xxx.html 相册页面下载所有图片
"""

import re
import os
import time
import json
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
from urllib.parse import urljoin
from functools import partial

import cloudscraper

# v2.2 - 修复图片下载需要Referer头

BASE_URL = "https://xchina.co"
IMG_BASE = "https://img.xchina.io/photos"
CONFIG_FILE = "xphoto_config.json"


def load_config():
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_config(cfg):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)


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


def fetch(scraper, url, retries=3):
    for i in range(retries):
        try:
            resp = scraper.get(url, timeout=30)
            if resp.status_code == 200:
                return resp.text
        except Exception:
            if i < retries - 1:
                time.sleep(2 ** i)
    return None


def fetch_bytes(scraper, url, referer=None, retries=3):
    headers = {}
    if referer:
        headers['Referer'] = referer
    for i in range(retries):
        try:
            resp = scraper.get(url, timeout=30, stream=True, headers=headers)
            if resp.status_code == 200:
                return resp.content
        except Exception:
            if i < retries - 1:
                time.sleep(2 ** i)
    return None


def get_album_info(html):
    """从相册页面提取 ID、标题、图片总数"""
    album_id = None
    title = None
    count = None

    # 提取相册 ID
    m = re.search(r'/photo/id-([A-Za-z0-9+/=]+)\.html', html)
    if not m:
        m = re.search(r'"objId":"([A-Za-z0-9+/=]+)"', html)
    if m:
        album_id = m.group(1)

    # 提取标题
    m = re.search(r'<title>\s*(.*?)\s*</title>', html)
    if m:
        title = m.group(1).strip()
        for sep in [' | ', ' - ', ' · ']:
            if sep in title:
                title = title.split(sep)[0].strip()

    if not title:
        m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html)
        if m:
            title = m.group(1).strip()

    # 提取图片总数 (格式: 780P)
    m = re.search(r'"fa-image".*?<div class="text">(\d+)P', html, re.DOTALL)
    if m:
        count = int(m.group(1))

    if not count:
        photoshow_count = len(re.findall(r'href="[^"]*photoShow\.html', html))
        if photoshow_count > 0:
            m = re.search(r'pager.*?href="[^"]*?/(\d+)\.html"', html)
            if m:
                last_page = int(m.group(1))
                per_page = photoshow_count
                if last_page > 1:
                    count = last_page * per_page

    return album_id, title, count


def extract_thumbnails(html):
    """
    从相册页面提取缩略图信息
    返回 [(image_number, title), ...]
    例如: [('00001', '标题'), ('00002', '标题'), ...]
    """
    thumbnails = []
    # 匹配: background-image:url('https://img.xchina.io/photos/xxx/00001_600x0.webp') ... No. 1
    for m in re.finditer(
        r"background-image:url\('https://img\.xchina\.io/photos/([^/]+)/(\d+)_600x0\.webp'\).*?title=\"([^\"]+)\"",
        html
    ):
        img_num = m.group(2)
        img_title = m.group(3)
        thumbnails.append((img_num, img_title))

    return thumbnails


def get_photoshow_page_info(html):
    """
    从 photoShow 页面提取相册 ID 和图片序号
    返回 (album_id, image_number)
    """
    album_id = None
    img_num = None

    m = re.search(r'let id = "([A-Za-z0-9+/=]+)"', html)
    if m:
        album_id = m.group(1)

    m = re.search(r'let filename = "(\d+)\.jpg"', html)
    if m:
        img_num = m.group(1)

    return album_id, img_num


def get_page_title(html):
    """从HTML中提取页面标题"""
    m = re.search(r'<title>\s*(.*?)\s*</title>', html)
    if m:
        title = m.group(1).strip()
        for sep in [' | ', ' - ', ' · ']:
            if sep in title:
                title = title.split(sep)[0].strip()
        if title:
            return title
    return None


def download_photo_set(scraper, start_url, output_dir, log_callback, stop_event, progress_callback):
    """
    从相册页面或 photoShow 页面下载所有图片
    """
    def log(msg):
        print(msg)
        if log_callback:
            log_callback(msg)

    def safe_name(title):
        name = re.sub(r'[<>:"/\\|?*]', '_', title or '未知相册')
        reserved = {'CON', 'PRN', 'AUX', 'NUL',
                    'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
                    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'}
        if name.upper() in reserved:
            name = f"相册_{name}"
        return name

    is_photoshow = 'photoShow.html' in start_url

    if is_photoshow:
        # ── 从 photoShow 页面下载 ──
        log(f"访问 photoShow 页面: {start_url}")
        html = fetch(scraper, start_url)
        if not html:
            log("无法访问页面")
            return 0, 0

        album_id, img_num = get_photoshow_page_info(html)
        if not album_id:
            log("未找到相册 ID")
            return 0, 0

        count_m = re.search(r'let photoCount = (\d+)', html)
        count = int(count_m.group(1)) if count_m else None
        if not count:
            log("未找到图片总数")
            return 0, 0

        title = get_page_title(html)
        log(f"相册: {title}, 共 {count} 张")

        save_dir = os.path.join(output_dir, safe_name(title))
        os.makedirs(save_dir, exist_ok=True)
        log(f"保存目录: {save_dir}")

        return _download_by_sequence(scraper, album_id, count, save_dir, start_url, log, stop_event, progress_callback)

    else:
        # ── 从相册页面下载 ──
        log(f"访问相册页面: {start_url}")
        first_html = fetch(scraper, start_url)
        if not first_html:
            log("无法访问页面")
            return 0, 0

        album_id, title, count = get_album_info(first_html)
        if not album_id:
            log("未找到相册 ID")
            return 0, 0

        if not title:
            title = "未知相册"

        log(f"相册: {title}")
        log(f"相册 ID: {album_id}")

        save_dir = os.path.join(output_dir, safe_name(title))
        os.makedirs(save_dir, exist_ok=True)
        log(f"保存目录: {save_dir}")

        if count:
            # 方式1: 已知总数，直接按序号下载
            log(f"共 {count} 张图片")
            return _download_by_sequence(scraper, album_id, count, save_dir, start_url, log, stop_event, progress_callback)

        else:
            # 方式2: 未知总数，从相册页提取缩略图信息
            # 先获取第一页的缩略图
            all_thumbnails = extract_thumbnails(first_html)
            log(f"第 1 页找到 {len(all_thumbnails)} 张图片")

            # 提取下一页链接
            next_page = None
            m = re.search(r'href="(/photo/id-[^"]*/\d+\.html)"[^>]*class="next"', first_html)
            if not m:
                m = re.search(r'class="next"[^>]*href="([^"]+)"', first_html)
            if m:
                next_page = m.group(1)

            # 遍历所有分页
            while next_page:
                if stop_event and not stop_event.is_set():
                    log("用户停止")
                    break

                full_url = urljoin(BASE_URL, next_page)
                page_num = re.search(r'/(\d+)\.html', next_page)
                if page_num:
                    log(f"获取第 {page_num.group(1)} 页...")

                html = fetch(scraper, full_url)
                if not html:
                    log("  获取失败")
                    break

                thumbs = extract_thumbnails(html)
                all_thumbnails.extend(thumbs)
                log(f"  找到 {len(thumbs)} 张，累计 {len(all_thumbnails)}")

                next_page = None
                m = re.search(r'href="(/photo/id-[^"]*/\d+\.html)"[^>]*class="next"', html)
                if not m:
                    m = re.search(r'class="next"[^>]*href="([^"]+)"', html)
                if m:
                    next_page = m.group(1)

                time.sleep(0.5)

            if not all_thumbnails:
                log("未找到图片")
                return 0, 0

            log(f"共找到 {len(all_thumbnails)} 张图片")

            # 按序号下载
            success_count = 0
            total_size = 0

            for idx, (img_num, img_title) in enumerate(all_thumbnails, 1):
                if stop_event and not stop_event.is_set():
                    log("用户停止")
                    break

                filename = f"{img_num}.jpg"
                img_url = f"{IMG_BASE}/{album_id}/{filename}"
                filepath = os.path.join(save_dir, filename)

                if os.path.exists(filepath):
                    size = os.path.getsize(filepath)
                    log(f"  [{idx}/{len(all_thumbnails)}] {filename} 已存在")
                    success_count += 1
                    total_size += size
                    if progress_callback:
                        progress_callback(idx)
                    continue

                log(f"  [{idx}/{len(all_thumbnails)}] 下载 {img_title}...")
                content = fetch_bytes(scraper, img_url, referer=start_url)
                if content:
                    with open(filepath, 'wb') as f:
                        f.write(content)
                    size = len(content)
                    size_str = f"{size / 1024:.1f}KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.2f}MB"
                    log(f"    {filename} ({size_str})")
                    success_count += 1
                    total_size += size
                else:
                    log("    下载失败")

                if progress_callback:
                    progress_callback(idx)

                time.sleep(0.3)

            return success_count, total_size


def _download_by_sequence(scraper, album_id, count, save_dir, referer_url, log, stop_event, progress_callback):
    """按序号批量下载图片"""
    success_count = 0
    total_size = 0

    for i in range(1, count + 1):
        if stop_event and not stop_event.is_set():
            log("用户停止")
            break

        filename = f"{i:05d}.jpg"
        img_url = f"{IMG_BASE}/{album_id}/{filename}"
        filepath = os.path.join(save_dir, filename)

        if os.path.exists(filepath):
            size = os.path.getsize(filepath)
            log(f"  [{i}/{count}] {filename} 已存在")
            success_count += 1
            total_size += size
            if progress_callback:
                progress_callback(i)
            continue

        log(f"  [{i}/{count}] 下载 {filename}...")
        content = fetch_bytes(scraper, img_url, referer=referer_url)
        if content:
            with open(filepath, 'wb') as f:
                f.write(content)
            size = len(content)
            size_str = f"{size / 1024:.1f}KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.2f}MB"
            log(f"    {filename} ({size_str})")
            success_count += 1
            total_size += size
        else:
            log("    下载失败")

        if progress_callback:
            progress_callback(i)

        time.sleep(0.3)

    return success_count, total_size


class PhotoApp:
    def __init__(self, root):
        self.root = root
        self.root.title("xChina 图片下载器")
        self.root.geometry("720x600")
        self.root.resizable(True, True)

        ttk.Label(root, text="xChina 图片下载器",
                  font=('Microsoft YaHei', 16, 'bold')).pack(pady=(15, 5))

        ttk.Label(root, text="粘贴相册页面 URL 或 photoShow URL，下载所有图片",
                  foreground='gray').pack(pady=(0, 10))

        url_frame = ttk.LabelFrame(root, text=" URL", padding=5)
        url_frame.pack(fill='x', padx=15, pady=(0, 5))

        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(url_frame, textvariable=self.url_var, font=('Consolas', 10))
        self.url_entry.pack(fill='x', padx=5, pady=5)
        self._add_context_menu(self.url_entry)

        ttk.Label(url_frame,
                  text='相册页: /photo/id-xxx.html  或  图片页: /photoShow.html?id=xxx',
                  foreground='gray', font=('Microsoft YaHei', 8)).pack(side='left', padx=(10, 5))

        dir_frame = ttk.Frame(root)
        dir_frame.pack(fill='x', padx=15, pady=(0, 5))

        ttk.Label(dir_frame, text="保存目录:").pack(side='left')
        self.dir_var = tk.StringVar(value=os.path.join(os.getcwd(), "photos"))
        self.dir_entry = ttk.Entry(dir_frame, textvariable=self.dir_var, width=35, font=('Consolas', 10))
        self.dir_entry.pack(side='left', padx=5)
        self._add_context_menu(self.dir_entry)

        ttk.Button(dir_frame, text="浏览...", command=self.browse_dir).pack(side='left', padx=(5, 0))

        proxy_frame = ttk.Frame(root)
        proxy_frame.pack(fill='x', padx=15, pady=(0, 5))

        ttk.Label(proxy_frame, text="代理:").pack(side='left')
        self.proxy_var = tk.StringVar()
        self.proxy_entry = ttk.Entry(proxy_frame, textvariable=self.proxy_var, width=35, font=('Consolas', 10))
        self.proxy_entry.pack(side='left', padx=5)
        self._add_context_menu(self.proxy_entry)
        ttk.Label(proxy_frame, text="例: http://127.0.0.1:7890", foreground='gray',
                  font=('Microsoft YaHei', 8)).pack(side='left')

        btn_frame = ttk.Frame(root)
        btn_frame.pack(fill='x', padx=15, pady=(5, 5))

        self.download_btn = ttk.Button(btn_frame, text="开始下载", command=self.start_download)
        self.download_btn.pack(side='left', padx=(0, 10))

        self.stop_btn = ttk.Button(btn_frame, text="停止", command=self.stop_download, state='disabled')
        self.stop_btn.pack(side='left')

        self.progress = ttk.Progressbar(root, mode='determinate', maximum=100)
        self.progress['value'] = 0
        self.progress.pack(fill='x', padx=15, pady=(0, 5))

        log_frame = ttk.LabelFrame(root, text=" 日志", padding=5)
        log_frame.pack(fill='both', expand=True, padx=15, pady=(0, 10))

        self.log_text = scrolledtext.ScrolledText(log_frame, height=18, font=('Consolas', 9), state='disabled')
        self.log_text.pack(fill='both', expand=True)
        self._add_context_menu(self.log_text)

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(root, textvariable=self.status_var, relief='sunken', anchor='w', padding=(5, 2)).pack(fill='x', side='bottom')

        self.running_event = threading.Event()
        self.scraper = None

        cfg = load_config()
        self.proxy_var.set(cfg.get('proxy', ''))
        self.dir_var.set(cfg.get('output_dir', self.dir_var.get()))

        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _add_context_menu(self, widget):
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="剪切", command=lambda: widget.event_generate('<<Cut>>'))
        menu.add_command(label="复制", command=lambda: widget.event_generate('<<Copy>>'))
        menu.add_command(label="粘贴", command=lambda: self._safe_paste(widget))
        menu.add_separator()
        menu.add_command(label="全选", command=lambda: self._select_all(widget))

        def show_menu(event):
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        widget.bind('<Button-3>', show_menu)
        widget.bind('<Button-2>', show_menu)

    @staticmethod
    def _safe_paste(widget):
        try:
            widget.event_generate('<<Paste>>')
        except tk.TclError:
            pass

    @staticmethod
    def _select_all(widget):
        try:
            widget.tag_add('sel', '1.0', 'end')
        except tk.TclError:
            try:
                widget.select_range(0, 'end')
            except tk.TclError:
                pass

    def log(self, msg):
        self.log_text.configure(state='normal')
        self.log_text.insert('end', msg + '\n')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')

    def status(self, msg):
        self.status_var.set(msg)

    def browse_dir(self):
        dirpath = filedialog.askdirectory(title="选择保存目录")
        if dirpath:
            self.dir_var.set(dirpath)

    def start_download(self):
        start_url = self.url_var.get().strip()
        if not start_url:
            messagebox.showwarning("提示", "请输入 URL")
            return

        if not start_url.startswith('http'):
            start_url = BASE_URL + ('/' if not start_url.startswith('/') else '') + start_url

        output_dir = self.dir_var.get().strip()
        if not output_dir:
            output_dir = os.path.join(os.getcwd(), "photos")

        save_config({
            'proxy': self.proxy_var.get().strip(),
            'output_dir': output_dir,
        })

        self.running_event.set()
        self.download_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        self.progress['value'] = 0
        self.progress.update_idletasks()
        self.log_text.configure(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.configure(state='disabled')

        proxy = self.proxy_var.get().strip() or None

        thread = threading.Thread(
            target=self._download_thread,
            args=(start_url, output_dir, proxy),
            daemon=True
        )
        thread.start()

    def _download_thread(self, start_url, output_dir, proxy):
        self.scraper = create_scraper(proxy)
        if proxy:
            self.root.after(0, self.log, f"使用代理: {proxy}")

        def on_progress(n):
            self.root.after(0, partial(self._update_progress, n))

        count, total_size = download_photo_set(
            self.scraper, start_url, output_dir,
            log_callback=lambda msg: self.root.after(0, self.log, msg),
            stop_event=self.running_event,
            progress_callback=on_progress
        )

        size_str = f"{total_size / 1024:.1f}KB" if total_size < 1024 * 1024 else f"{total_size / 1024 / 1024:.2f}MB"
        self.root.after(0, self.log, f"\n{'='*50}")
        self.root.after(0, self.log, f"全部完成! {count} 张图片，共 {size_str}")
        self.root.after(0, self.log, f"{'='*50}")

        self.root.after(0, self._download_done)

    def _download_done(self):
        self.running_event.clear()
        self.download_btn.configure(state='normal')
        self.stop_btn.configure(state='disabled')
        self.progress['value'] = 100
        self.status("就绪")
        if self.scraper:
            self.scraper.close()
            self.scraper = None

    def _update_progress(self, img_num):
        val = min(99, img_num * 2)
        self.progress['value'] = val
        self.status(f"已下载 {img_num} 张图片")

    def stop_download(self):
        self.running_event.clear()
        self.stop_btn.configure(state='disabled')
        self.log("\n正在停止...")
        self.status("停止中...")
        if self.scraper:
            self.scraper.close()
            self.scraper = None

    def on_close(self):
        save_config({
            'proxy': self.proxy_var.get().strip(),
            'output_dir': self.dir_var.get().strip(),
        })
        self.root.destroy()


def main():
    root = tk.Tk()
    PhotoApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
