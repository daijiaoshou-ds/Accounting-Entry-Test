import os
import sys
import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading
# import fitz  

# --- 资源路径辅助函数 ---
from modules.path_manager import get_asset_path  # 使用统一的 path_manager，如果没引入，也可以用下面的备用


def _robust_replace(src, dst, attempts=8, delay=0.15):
    """Windows 下刚写完的大文件（含嵌入字体）常被杀软/索引服务短暂占用，
    os.replace 会抛 WinError 32/5。这里做有限次重试，仍失败则抛出原异常。"""
    import time
    last = None
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return True
        except OSError as e:
            last = e
            time.sleep(delay * (i + 1))
    raise last


def _silent_remove(path):
    """尽力删除临时文件；被占用时不要抛异常，
    否则会掩盖「replace 失败」的真实原因并让整个任务以异常收场。"""
    import time
    for _ in range(5):
        try:
            if os.path.exists(path):
                os.remove(path)
            return
        except OSError:
            time.sleep(0.15)
    pass

def get_resource_path_local(relative_path):
    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# --- 核心逻辑 ---

# === 【修改点 1】新增 stop_event 参数 ===
def add_index_to_pdf(file_path, font_path, log_callback, stop_event=None):
    """
    使用 TextWriter 修复中文显示问题，并定位到右上角。
    兼容：页面旋转(90/180/270)、CropBox 偏移、窄页/超长文件名(自动缩字号)。
    """
    import fitz
    
    doc = None
    temp_path = file_path + ".tmp" # 提前定义，方便 finally 清理
    
    try:
        try:
            custom_font = fitz.Font(fontfile=font_path)
            if not custom_font.name:
                return False, f"字体加载异常: {font_path}"
        except Exception as e:
            return False, f"加载字体文件失败: {e}"

        doc = fitz.open(file_path)
        filename = os.path.basename(file_path)
        prefix_name = os.path.splitext(filename)[0]
        total_pages = len(doc)
        
        FONT_SIZE = 18          
        MARGIN_RIGHT = 30       
        MARGIN_TOP = 30
        MARGIN_LEFT = 10
        MIN_FONT_SIZE = 8.0     # 页面太窄时的字号下限
        TEXT_COLOR = (1, 0, 0)  

        shrank_once = [False]  # 记录是否缩小过字号（避免刷屏日志）

        # 遍历每一页
        for page_num, page in enumerate(doc):
            # === 【修改点 2】页面级中断检测 ===
            if stop_event and stop_event.is_set():
                doc.close()
                _silent_remove(temp_path) # 清理垃圾（占用时静默跳过）
                return False, ">>> 用户强制停止（当前文件未修改）"
            # =================================

            # --- 文本与自适应字号 ---
            suffix = f"{page_num + 1}/{total_pages}"
            text = f"{prefix_name} {suffix}"
            ava_width = page.rect.width - MARGIN_RIGHT - MARGIN_LEFT
            fs = FONT_SIZE
            text_len = custom_font.text_length(text, fontsize=fs)
            if text_len > ava_width and ava_width > 0:
                # 文字比页面可视宽度还长：先缩小字号
                fs = max(MIN_FONT_SIZE, FONT_SIZE * ava_width / text_len)
                text_len = custom_font.text_length(text, fontsize=fs)
                if fs <= MIN_FONT_SIZE and text_len > ava_width:
                    # 字号到下限仍放不下：截断文件名部分，保留 "n/N" 页码
                    name_part = prefix_name
                    while name_part and custom_font.text_length(f"{name_part} {suffix}", fontsize=fs) > ava_width:
                        name_part = name_part[:-1]
                    text = f"{name_part} {suffix}"
                    text_len = custom_font.text_length(text, fontsize=fs)
                shrank_once[0] = True

            # --- 显示空间目标（可视区右上角，左上原点，y 向下）---
            x = page.rect.width - text_len - MARGIN_RIGHT
            y = MARGIN_TOP + fs

            # --- 显示坐标 -> 媒体 PDF 坐标（y 向上）---
            # 映射由像素级标定验证（以 MuPDF 渲染结果为准），覆盖旋转与 CropBox 偏移
            H = page.mediabox.height
            crop = page.cropbox
            rot = page.rotation
            if rot == 90:
                px, py = crop.x0 + y, H - crop.y1 + x
                dx, dy = 0.0, 1.0
            elif rot == 180:
                px, py = crop.x1 - x, H - crop.y1 + y
                dx, dy = -1.0, 0.0
            elif rot == 270:
                px, py = crop.x1 - y, H - crop.y0 - x
                dx, dy = 0.0, -1.0
            else:
                px, py = crop.x0 + x, H - crop.y0 - y
                dx, dy = 1.0, 0.0

            # write_text 内部会自动附加一个 cm 平移（cropbox 起点 + 旋转补偿），
            # 这里预先把它消掉，保证最终落点精确等于 (px, py)
            cb = page.cropbox_position
            delta = (page.rect.height - page.rect.width) if rot in (90, 270) else 0
            auto_x, auto_y = cb.x, cb.y + page.mediabox.y0 - delta

            # 内容流变换矩阵：把 Tm=(0,0) 的基线起点映射到 (px,py)，方向映射为 (dx,dy)
            M = fitz.Matrix(dx, dy, -dy, dx, px - auto_x, py - auto_y)

            tw = fitz.TextWriter(page.rect)
            tw.append(pos=(0, page.rect.height), text=text, font=custom_font, fontsize=fs)
            tw.write_text(page, color=TEXT_COLOR, render_mode=2, matrix=M)

        if shrank_once[0]:
            log_callback(f"提示: 部分页面宽度有限，已自动缩小索引字号")

        doc.save(temp_path)
        doc.close()
        doc = None 

        _robust_replace(temp_path, file_path)
        return True, f"成功: {filename}"

    except Exception as e:
        import traceback
        try:
            if doc: doc.close()
        except Exception:
            pass
        doc = None
        _silent_remove(temp_path)   # 尽力清理，绝不因清理失败再抛异常
        return False, f"失败 {os.path.basename(file_path)}: {str(e)}"
    finally:
        try:
            if doc: doc.close()
        except Exception:
            pass

# === 【修改点 3】新增 stop_event 参数 ===
def batch_process_pdf(target_path, log_callback, stop_event=None):
    """批量处理逻辑"""
    # 尝试使用统一的 path_manager，如果没有则用本地备用
    try:
        from modules.path_manager import get_asset_path
        font_path = get_asset_path(os.path.join("assets", "fonts", "simsun.ttc"))
    except ImportError:
        font_path = get_resource_path_local(os.path.join("assets", "fonts", "simsun.ttc"))
    
    log_callback(f"正在加载字体: {font_path}")
    if not os.path.exists(font_path):
        return f"错误: 找不到字体文件!\n请确认文件存在于:\n{font_path}"

    files_to_process = []
    
    if os.path.isfile(target_path):
        if target_path.lower().endswith('.pdf'):
            files_to_process.append(target_path)
    else:
        for root, dirs, files in os.walk(target_path):
            for file in files:
                if file.lower().endswith('.pdf'):
                    files_to_process.append(os.path.join(root, file))

    if not files_to_process:
        return "未找到 PDF 文件。"

    log_callback(f"找到 {len(files_to_process)} 个 PDF 文件，准备处理...")
    
    success_count = 0
    fail_count = 0

    for i, file_path in enumerate(files_to_process):
        # === 【修改点 4】文件级中断检测 ===
        if stop_event and stop_event.is_set():
            log_callback(">>> 用户强制停止任务！")
            break
        # ===============================

        # 传递 stop_event 给单个文件处理函数
        status, msg = add_index_to_pdf(file_path, font_path, log_callback, stop_event)
        
        if status:
            success_count += 1
            log_callback(msg)
        else:
            log_callback(msg)
            # 如果是因为用户停止导致的 False，我们就不算作“失败”，而是直接退出
            if "用户强制停止" in msg:
                break
            fail_count += 1

    return f"处理完成。\n成功: {success_count}\n失败: {fail_count}"

# --- 界面模块 ---

class PDFIndexerModule:
    def __init__(self):
        self.name = "PDF 索引号生成"
        self.target_path = ""
        # self.app 会由 main.py 注入

    def render(self, parent_frame):
        for widget in parent_frame.winfo_children(): widget.destroy()

        scroll = ctk.CTkScrollableFrame(parent_frame, fg_color="transparent", scrollbar_button_color="#E0E0E0", scrollbar_button_hover_color="#D0D0D0")
        scroll.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(scroll, text="PDF索引号生成", font=("Microsoft YaHei", 22, "bold"), text_color="#333").pack(pady=15, anchor="w", padx=20)

        tips = """
        【功能说明】
        1. 自动读取 PDF 文件名作为索引前缀。
        2. 格式：文件名 页码/总页数 (例如：XX-1 1/6)。
        3. 位置：右上角 (自动计算宽度对齐)。
        4. 样式：红色、粗体、字号 18。
        5. 注意：操作不可逆，请备份文件。
        """
        tips_frame = ctk.CTkFrame(scroll, fg_color="#F8F9FA", corner_radius=6)
        tips_frame.pack(fill="x", padx=20, pady=(0, 20))
        ctk.CTkLabel(tips_frame, text=tips, justify="left", text_color="#555", font=("Consolas", 12)).pack(padx=15, pady=10, anchor="w")

        op_frame = ctk.CTkFrame(scroll, fg_color="white", corner_radius=8, border_width=1, border_color="#DDD")
        op_frame.pack(fill="x", padx=20, pady=10)
        
        ctk.CTkLabel(op_frame, text="选择文件或文件夹:", font=("Microsoft YaHei", 14), text_color="#333").pack(anchor="w", padx=15, pady=(15, 5))
        
        self.entry_path = ctk.CTkEntry(op_frame, placeholder_text="支持单选 PDF 或整个文件夹...", height=35)
        self.entry_path.pack(fill="x", padx=15, pady=5)
        
        btn_box = ctk.CTkFrame(op_frame, fg_color="transparent")
        btn_box.pack(fill="x", padx=10, pady=15)
        
        ctk.CTkButton(btn_box, text="选择文件夹", width=100, command=self.select_folder).pack(side="left", padx=5)
        ctk.CTkButton(btn_box, text="选择单文件", width=100, command=self.select_file).pack(side="left", padx=5)
        
        # 绑定 run_task
        self.btn_run = ctk.CTkButton(btn_box, text="开始添加索引", width=120, fg_color="#d63031", command=self.run_task)
        self.btn_run.pack(side="right", padx=5)

        ctk.CTkLabel(scroll, text="运行日志", text_color="#333", font=("Microsoft YaHei", 14, "bold")).pack(anchor="w", padx=20, pady=(10, 5))
        self.textbox = ctk.CTkTextbox(scroll, height=250, fg_color="white", text_color="#333", border_width=1, border_color="#CCC")
        self.textbox.pack(padx=20, pady=(0, 20), fill="both")

    def select_folder(self):
        p = filedialog.askdirectory()
        if p: self.entry_path.delete(0, "end"); self.entry_path.insert(0, p); self.target_path = p

    def select_file(self):
        p = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if p: self.entry_path.delete(0, "end"); self.entry_path.insert(0, p); self.target_path = p

    def log(self, msg):
        self.textbox.insert("end", msg + "\n")
        self.textbox.see("end")

    # === 【修改点 5】启动任务 ===
    def run_task(self):
        path = self.entry_path.get().strip()
        if not path or not os.path.exists(path): return messagebox.showerror("错误", "路径不存在")
        if not messagebox.askyesno("警告", "此操作将修改原始文件！\n建议先备份数据。\n是否继续？"): return

        # 申请红旗
        stop_event = None
        if hasattr(self, 'app'): stop_event = self.app.register_task(self.module_index)
        
        self.btn_run.configure(state="disabled", text="处理中...")
        self.textbox.delete("1.0", "end")
        
        def t():
            # 传入 stop_event
            result = batch_process_pdf(path, self.log, stop_event=stop_event)
            self.log("-" * 30)
            self.log(result)
            
            # 销假 & 恢复按钮
            if hasattr(self, 'app'): self.app.finish_task(self.module_index)
            self.btn_run.configure(state="normal", text="开始添加索引")
            
            # 只有在非中断状态下才弹成功窗
            if "用户强制停止" not in result:
                messagebox.showinfo("完成", "任务结束")

        threading.Thread(target=t, daemon=True).start()