#!/usr/bin/env python3
"""
xChina 下载器 (GUI + CLI 版)
支持小说和套图批量下载，可配置代理
"""

import sys
import re
import os
import html
import time
import json
import threading
import tkinter as tk
from functools import partial
from tkinter import ttk, scrolledtext, messagebox
from urllib.parse import urljoin

import cloudscraper

BASE_URL = "https://xchina.co"
IMG_BASE = "https://img.xchina.io/photos"
CONFIG_FILE = "xchina_config.json"


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
        'browser': {'browser': 'chrome', 'platform': 'android', 'mobile': True}
    }
    scraper = cloudscraper.create_scraper(**kwargs)
    scraper.headers.update({
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    })
    if proxy:
        scraper.proxies = {'http': proxy, 'https': proxy}
    return scraper


def fetch(scraper, url, retries=3):
    for i in range(retries):
        try:
            resp = scraper.get(url, timeout=20)
            if resp.status_code == 200:
                return resp.text
        except Exception as e:
            if i < retries - 1:
                wait = 2 ** i
                time.sleep(wait)
    return None


# --- 小说下载逻辑 ---

def parse_chapter(html_content, url):
    info = {}
    m = re.search(r'<div\s+class="title"[^>]*>\s*([^<]+)\s*</div>', html_content)
    if m:
        info['fiction_title'] = m.group(1).strip()

    m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html_content)
    if m:
        parts = m.group(1).split(' - ')
        info['chapter_title'] = parts[0].strip()
        if len(parts) > 1 and 'fiction_title' not in info:
            info['fiction_title'] = parts[-1].strip()

    m = re.search(r'<div\s+class="fiction-body"[^>]*>(.*?)</div>', html_content, re.DOTALL)
    if m:
        paras = re.findall(r'<p>(.*?)</p>', m.group(1), re.DOTALL)
        if paras:
            clean = []
            for p in paras:
                t = re.sub(r'<br\s*/?>', '\n', p)
                t = re.sub(r'<[^>]+>', '', t)
                t = html.unescape(t)
                t = t.strip()
                if t:
                    clean.append(t)
            info['content'] = '\n\n'.join(clean)

    m = re.search(
        r'<a\s+href="([^"]+)"[^>]*>\s*<div[^>]*class="[^"]*next[^"]*"[^>]*>下一章',
        html_content, re.DOTALL
    )
    if m:
        info['next_url'] = urljoin(BASE_URL, m.group(1))

    return info


def get_chapters_from_toc(scraper, toc_url):
    html_content = fetch(scraper, toc_url)
    if not html_content:
        return []
    urls = []
    seen = set()
    for m in re.finditer(r'href="(/fiction/id-([A-Za-z0-9+/=]{30,})\.html)"', html_content):
        u = urljoin(BASE_URL, m.group(1))
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def download_fiction(scraper, url, log_callback=None, stop_event=None, progress_callback=None):
    """下载单本小说，返回 (标题, 章节列表, 总字数)"""

    def log(msg):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    log(f"访问: {url}")
    html_content = fetch(scraper, url)
    if not html_content:
        log("无法访问")
        return None, [], 0

    info = parse_chapter(html_content, url)
    is_content = 'content' in info and info['content']
    fiction_title = info.get('fiction_title', '未知小说')
    chapters = []

    if is_content:
        log(f" {fiction_title}（逐章遍历中...）")
        current = url
        visited = set()
        ch_num = 0
        while current and current not in visited:
            if stop_event and not stop_event.is_set():
                log("已停止")
                break
            visited.add(current)
            ch_num += 1
            h = fetch(scraper, current)
            if not h:
                log(f"  [{ch_num}] 下载失败")
                break
            ci = parse_chapter(h, current)
            ctitle = ci.get('chapter_title', f'第{ch_num}章')
            content = ci.get('content', '')
            chapters.append((ctitle, content))
            fiction_title = ci.get('fiction_title', fiction_title)
            log(f"  [{ch_num}] {ctitle} ({len(content)}字)")
            if progress_callback:
                progress_callback(ch_num)
            nxt = ci.get('next_url')
            if nxt in visited:
                break
            current = nxt
            time.sleep(0.3)
    else:
        log(f" {fiction_title}（获取目录...）")
        ch_urls = get_chapters_from_toc(scraper, url)
        if not ch_urls:
            log("未找到章节")
            return fiction_title, [], 0

        total = len(ch_urls)
        log(f"共 {total} 章")
        for i, ch_url in enumerate(ch_urls, 1):
            if stop_event and not stop_event.is_set():
                log("已停止")
                break
            h = fetch(scraper, ch_url)
            if not h:
                log(f"  [{i}/{total}] 失败")
                continue
            ci = parse_chapter(h, ch_url)
            ctitle = ci.get('chapter_title', f'第{i}章')
            content = ci.get('content', '')
            chapters.append((ctitle, content))
            fiction_title = ci.get('fiction_title', fiction_title)
            log(f"  [{i}/{total}] {ctitle} ({len(content)}字)")
            if progress_callback:
                progress_callback(i)
            time.sleep(0.3)

    return fiction_title, chapters, sum(len(c) for _, c in chapters)


def save_txt(title, chapters, output_dir='.'):
    safe = re.sub(r'[<>:"/\\|?*]', '_', title)
    reserved = {'CON', 'PRN', 'AUX', 'NUL',
                'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
                'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'}
    if safe.upper() in reserved:
        safe = f"小说_{safe}"
    path = os.path.join(output_dir, f"{safe}.txt")
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f"{title}\n{'='*50}\n\n")
        for ct, cc in chapters:
            f.write(f"{ct}\n{'-'*30}\n\n{cc}\n\n")
    return path


# --- 套图下载逻辑 ---

def extract_album_id(url):
    """从 URL 中提取相册 ID"""
    m = re.search(r'/photo/id-([A-Za-z0-9]+)', url)
    if m:
        return m.group(1)
    return None


def parse_photo_info(html_content):
    """从相册页面解析标题和图片总数"""
    info = {}

    # 优先用 og:title 获取标题（不含分类信息），否则用 h1
    m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html_content)
    if m:
        title = m.group(1).split(' - ')[0].strip()
        info['title'] = title
    else:
        m = re.search(r'<h1[^>]*>([^<]+)</h1>', html_content)
        if m:
            info['title'] = m.group(1).strip()

    # 从 info-card 中的 <i class="fas fa-image"></i> 附近匹配图片数量
    # 例如: <i class="fas fa-image"></i></div><div class="text">780P</div>
    #      <div class="text">130P + 1V</div>
    m = re.search(r'fa-image[^>]*>.*?</div>.*?<div[^>]*class="[^"]*text[^"]*"[^>]*>(\d+)P', html_content, re.DOTALL)
    if m:
        info['count'] = int(m.group(1))
    else:
        # fallback: 匹配 text 类下的数字P
        m = re.search(r'class="[^"]*text[^"]*"[^>]*>(\d{3,})P', html_content)
        if m:
            info['count'] = int(m.group(1))

    m = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', html_content)
    if m:
        info['cover'] = m.group(1)

    return info


def download_image(scraper, img_url, save_path, referer, log_callback=None, retries=3):
    """下载单张图片，返回 (成功, 文件大小)"""
    headers = {'Referer': referer}
    for i in range(retries):
        try:
            resp = scraper.get(img_url, timeout=20, headers=headers)
            if resp.status_code == 200:
                content = resp.content
                size = len(content)
                if size > 5000:
                    with open(save_path, 'wb') as f:
                        f.write(content)
                    return True, size
                else:
                    if log_callback:
                        log_callback(f"    警告: 图片太小 ({size} bytes)，可能是假图")
                    return False, size
            elif resp.status_code == 403:
                if log_callback:
                    log_callback(f"    403 Forbidden，重试中...")
                time.sleep(1)
            else:
                if log_callback:
                    log_callback(f"    HTTP {resp.status_code}，重试中...")
                time.sleep(1)
        except Exception as e:
            if log_callback:
                log_callback(f"    异常: {e}，重试中...")
            if i < retries - 1:
                time.sleep(2 ** i)
    return False, 0


def detect_image_path(scraper, html_content, log_callback=None):
    """从 photoShow 页面检测图片路径和文件名格式"""
    def log(msg):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    # 获取第一个 photoShow 链接
    m = re.search(r'href="([^"]*photoShow\.html[^"]*)"', html_content)
    if not m:
        return None, None

    ps_url = urljoin(BASE_URL, m.group(1))
    ps_html = fetch(scraper, ps_url)
    if not ps_html:
        return None, None

    # 从 photoShow 页面提取实际图片 URL
    img_url = re.search(r'"url":"([^"]+)"', ps_html)
    if not img_url:
        img_url = re.search(r'preload[^>]*href="([^"]+)"', ps_html)
    if not img_url:
        img_url = re.search(r'<img\s+src="([^"]+)"', ps_html)

    if not img_url:
        return None, None

    actual_url = img_url.group(1)
    actual_url = actual_url.replace('\\/', '/')

    # 提取路径格式: e.g. https://img.xchina.io/photos2/6584143fa3e3f/0001.jpg
    m2 = re.search(r'(img\.xchina\.io/photos\d*)/([^/]+)/(\d+)\.\w+$', actual_url)
    if m2:
        base = f"https://{m2.group(1)}/{{album_id}}"
        digits = len(m2.group(3))
        return base, digits

    return None, None


def download_photo_album(scraper, url, output_dir='.', log_callback=None, stop_event=None, progress_callback=None):
    """下载整个套图，返回 (标题, 成功数, 总大小)"""

    def log(msg):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    album_id = extract_album_id(url)
    if not album_id:
        log("无法从 URL 中提取相册 ID")
        return None, 0, 0

    log(f"访问相册页: {url}")
    html_content = fetch(scraper, url)
    if not html_content:
        log("无法访问相册页")
        return None, 0, 0

    info = parse_photo_info(html_content)
    title = info.get('title', f'套图_{album_id}')
    count = info.get('count', 0)

    # 检测图片路径和文件名格式
    img_base_template, digits = detect_image_path(scraper, html_content, log_callback=log)
    if img_base_template:
        log(f"检测到图片路径: {img_base_template}, 文件名位数: {digits}")

    if count == 0:
        log("无法解析图片数量，尝试从分页提取...")
        # 从分页链接中提取最大页数来估算
        pager_nums = re.findall(r'pager-num[^\"]*">(\d+)', html_content)
        if pager_nums:
            max_page = max(int(n) for n in pager_nums)
            items_per_page = 15
            count = max_page * items_per_page
            log(f"估算图片总数: {count}")
        else:
            # 从图片列表中计数
            img_count = len(re.findall(r'photo-image', html_content))
            pager_nums = re.findall(r'pager-num[^\"]*">(\d+)', html_content)
            max_page = max((int(n) for n in pager_nums), default=1)
            count = max(img_count, max_page * 15)
            log(f"估算图片总数: {count}")

    if count == 0:
        log("无法确定图片数量")
        return title, 0, 0

    safe = re.sub(r'[<>:"/\\|?*]', '_', title)
    save_dir = os.path.join(output_dir, safe)
    os.makedirs(save_dir, exist_ok=True)

    log(f"  {title} | 共 {count} 张 | 保存到: {save_dir}")

    success = 0
    total_size = 0
    failed = []

    # 使用检测到的路径和格式，否则用默认值
    actual_img_base = img_base_template.replace('{album_id}', album_id) if img_base_template else f"{IMG_BASE}/{album_id}"
    actual_digits = digits if digits else 5
    filename_fmt = f"{{i:0{actual_digits}d}}.jpg"

    for i in range(1, count + 1):
        if stop_event and not stop_event.is_set():
            log("已停止")
            break

        filename = filename_fmt.format(i=i)
        img_url = f"{actual_img_base}/{filename}"
        save_path = os.path.join(save_dir, filename)

        if os.path.exists(save_path) and os.path.getsize(save_path) > 5000:
            size = os.path.getsize(save_path)
            success += 1
            total_size += size
            if progress_callback:
                progress_callback(success)
            continue

        ok, size = download_image(scraper, img_url, save_path, url, log_callback=None)

        if ok:
            success += 1
            total_size += size
            log(f"  [{i}/{count}] {filename} ({size/1024:.1f}KB) OK")
        else:
            failed.append(i)
            log(f"  [{i}/{count}] {filename} 失败 ({size} bytes)")

        if progress_callback:
            progress_callback(success)
        time.sleep(0.2)

    if failed:
        log(f"  共 {len(failed)} 张失败: {failed[:20]}{'...' if len(failed) > 20 else ''}")

    return title, success, total_size


# --- URL 类型检测 ---

def detect_url_type(url):
    """检测 URL 类型: 'fiction', 'photo', 或 None"""
    if '/fiction/id-' in url:
        return 'fiction'
    elif '/photo/id-' in url or '/photoShow.html' in url:
        return 'photo'
    return None


# --- GUI ---

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("xChina 下载器")
        self.root.geometry("750x580")
        self.root.resizable(True, True)

        title_label = ttk.Label(root, text="xChina 下载器",
                                font=('Microsoft YaHei', 16, 'bold'))
        title_label.pack(pady=(15, 5))

        subtitle = ttk.Label(root, text="粘贴小说 URL (/fiction/id-...) 或套图 URL (/photo/id-...)，每行一个",
                             foreground='gray')
        subtitle.pack(pady=(0, 10))

        url_frame = ttk.LabelFrame(root, text=" URL（一行一个，支持小说和套图）", padding=5)
        url_frame.pack(fill='both', expand=True, padx=15, pady=(0, 5))

        self.url_text = scrolledtext.ScrolledText(url_frame, height=5, font=('Consolas', 10))
        self.url_text.pack(fill='both', expand=True)
        self._add_context_menu(self.url_text)

        # 输出目录
        dir_frame = ttk.Frame(root)
        dir_frame.pack(fill='x', padx=15, pady=(0, 5))
        ttk.Label(dir_frame, text="输出目录:").pack(side='left')
        self.output_dir_var = tk.StringVar(value='.')
        self.output_dir_entry = ttk.Entry(dir_frame, textvariable=self.output_dir_var, width=35)
        self.output_dir_entry.pack(side='left', padx=5)
        self._add_context_menu(self.output_dir_entry)
        ttk.Button(dir_frame, text="浏览...", command=self.browse_dir).pack(side='left', padx=2)

        proxy_frame = ttk.Frame(root)
        proxy_frame.pack(fill='x', padx=15, pady=(0, 5))

        ttk.Label(proxy_frame, text="代理:").pack(side='left')
        self.proxy_var = tk.StringVar()
        self.proxy_entry = ttk.Entry(proxy_frame, textvariable=self.proxy_var, width=35)
        self.proxy_entry.pack(side='left', padx=5)
        self._add_context_menu(self.proxy_entry)
        ttk.Label(proxy_frame, text="例: http://127.0.0.1:7890", foreground='gray').pack(side='left')

        btn_frame = ttk.Frame(root)
        btn_frame.pack(fill='x', padx=15, pady=(5, 5))

        self.download_btn = ttk.Button(btn_frame, text="开始下载",
                                       command=self.start_download)
        self.download_btn.pack(side='left', padx=(0, 10))

        self.stop_btn = ttk.Button(btn_frame, text="停止", command=self.stop_download,
                                   state='disabled')
        self.stop_btn.pack(side='left')

        self.progress = ttk.Progressbar(root, mode='determinate', maximum=100)
        self.progress['value'] = 0
        self.progress.pack(fill='x', padx=15, pady=(0, 5))

        log_frame = ttk.LabelFrame(root, text=" 日志", padding=5)
        log_frame.pack(fill='both', expand=True, padx=15, pady=(0, 10))

        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, font=('Consolas', 9),
                                                   state='disabled')
        self.log_text.pack(fill='both', expand=True)
        self._add_context_menu(self.log_text)

        self.status_var = tk.StringVar(value="就绪")
        status_bar = ttk.Label(root, textvariable=self.status_var, relief='sunken',
                               anchor='w', padding=(5, 2))
        status_bar.pack(fill='x', side='bottom')

        self.running_event = threading.Event()
        self.scraper = None

        cfg = load_config()
        self.proxy_var.set(cfg.get('proxy', ''))
        self.output_dir_var.set(cfg.get('output_dir', '.'))

        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _add_context_menu(self, widget):
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="剪切", accelerator="Ctrl+X",
                        command=lambda: widget.event_generate('<<Cut>>'))
        menu.add_command(label="复制", accelerator="Ctrl+C",
                        command=lambda: widget.event_generate('<<Copy>>'))
        menu.add_command(label="粘贴", accelerator="Ctrl+V",
                        command=lambda: self._safe_paste(widget))
        menu.add_separator()
        menu.add_command(label="全选", accelerator="Ctrl+A",
                        command=lambda: self._select_all(widget))

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
        import tkinter.filedialog as fd
        d = fd.askdirectory()
        if d:
            self.output_dir_var.set(d)

    def start_download(self):
        urls_text = self.url_text.get('1.0', 'end-1c').strip()
        if not urls_text:
            messagebox.showwarning("提示", "请至少输入一个 URL")
            return

        urls = [u.strip() for u in urls_text.split('\n') if u.strip()]
        valid = [u for u in urls if detect_url_type(u)]
        if not valid:
            messagebox.showwarning("提示", "URL 格式不正确，需要包含 /fiction/id- 或 /photo/id-")
            return

        invalid = len(urls) - len(valid)
        if invalid > 0:
            if not messagebox.askyesno("确认", f"有 {invalid} 个 URL 格式不正确，继续下载其余 {len(valid)} 个？"):
                return

        save_config({'proxy': self.proxy_var.get().strip(), 'output_dir': self.output_dir_var.get().strip()})

        self.running_event.set()
        self.download_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        self.progress['value'] = 0
        self.progress.update_idletasks()
        self.log_text.configure(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.configure(state='disabled')

        proxy = self.proxy_var.get().strip() or None
        output_dir = self.output_dir_var.get().strip() or '.'

        thread = threading.Thread(target=self._download_thread, args=(valid, proxy, output_dir), daemon=True)
        thread.start()

    def _download_thread(self, urls, proxy, output_dir):
        self.scraper = create_scraper(proxy)
        if proxy:
            self.root.after(0, self.log, f"使用代理: {proxy}")

        success_count = 0
        total_urls = len(urls)
        items_downloaded = 0
        max_items = 1000

        def on_progress(n):
            nonlocal items_downloaded
            items_downloaded = n
            self.root.after(0, partial(self._update_progress, items_downloaded, max_items))

        for i, url in enumerate(urls, 1):
            if not self.running_event.is_set():
                self.root.after(0, self.log, "用户停止")
                break

            url_type = detect_url_type(url)
            self.root.after(0, self.log, f"\n{'='*50}")
            self.root.after(0, self.log, f"[{i}/{total_urls}] 开始下载 ({url_type})")
            self.root.after(0, self.log, f"{'='*50}")

            def make_callback():
                def cb(msg):
                    self.root.after(0, self.log, msg)
                return cb

            cb = make_callback()

            if url_type == 'fiction':
                title, chapters, chars = download_fiction(self.scraper, url, log_callback=cb,
                                                          stop_event=self.running_event,
                                                          progress_callback=on_progress)
                if chapters:
                    path = save_txt(title, chapters, output_dir)
                    self.root.after(0, self.log, f"\n已保存: {path}")
                    self.root.after(0, self.log, f"  {title} | {len(chapters)}章 | {chars}字")
                    success_count += 1
                else:
                    self.root.after(0, self.log, f"下载失败: {title or url}")

            elif url_type == 'photo':
                title, count, total_size = download_photo_album(self.scraper, url, output_dir,
                                                                log_callback=cb,
                                                                stop_event=self.running_event,
                                                                progress_callback=on_progress)
                if count > 0:
                    mb = total_size / 1024 / 1024
                    self.root.after(0, self.log, f"\n套图下载完成!")
                    self.root.after(0, self.log, f"  {title} | {count}张 | {mb:.1f}MB")
                    success_count += 1
                else:
                    self.root.after(0, self.log, f"下载失败: {title or url}")

        self.root.after(0, self.log, f"\n{'='*50}")
        self.root.after(0, self.log, f"全部完成! {success_count}/{total_urls} 个下载成功")
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

    def _update_progress(self, downloaded, max_items):
        if max_items > 0:
            val = min(99, int(downloaded / max_items * 100))
        else:
            val = min(99, downloaded * 2)
        self.progress['value'] = val

    def stop_download(self):
        self.running_event.clear()
        self.stop_btn.configure(state='disabled')
        self.log("\n正在停止...")
        self.status("停止中...")
        if self.scraper:
            self.scraper.close()
            self.scraper = None

    def on_close(self):
        save_config({'proxy': self.proxy_var.get().strip(), 'output_dir': self.output_dir_var.get().strip()})
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
