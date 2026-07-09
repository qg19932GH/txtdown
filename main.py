import tkinter as tk
from tkinter import ttk


class MainMenu:
    def __init__(self, root):
        self.root = root
        self.root.title("xChina 下载工具")
        self.root.geometry("520x240")
        self.root.resizable(False, False)

        ttk.Label(root, text="xChina 下载工具",
                  font=('Microsoft YaHei', 18, 'bold')).pack(pady=(25, 5))

        ttk.Label(root, text="请选择功能模块",
                  foreground='gray').pack(pady=(0, 20))

        btn_frame = ttk.Frame(root)
        btn_frame.pack(pady=10)

        self.photo_btn = ttk.Button(btn_frame, text="图片下载器",
                                     width=18, command=self.launch_photo)
        self.photo_btn.pack(pady=6)

        self.china_btn = ttk.Button(btn_frame, text="小说/套图下载器",
                                     width=18, command=self.launch_china)
        self.china_btn.pack(pady=6)

    def launch_photo(self):
        self.root.destroy()
        import xphoto_downloader
        xphoto_downloader.main()

    def launch_china(self):
        self.root.destroy()
        import xchina_downloader
        xchina_downloader.main()


def main():
    root = tk.Tk()
    MainMenu(root)
    root.mainloop()


if __name__ == '__main__':
    main()
