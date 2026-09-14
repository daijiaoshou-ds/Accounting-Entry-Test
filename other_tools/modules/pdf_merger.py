import os
import pandas as pd
import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading
import sys

# import fitz  # PyMuPDF
# from reportlab.lib import colors
# from reportlab.lib.pagesizes import A4
# from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
# from reportlab.pdfbase import pdfmetrics
# from reportlab.pdfbase.ttfonts import TTFont
# import pythoncom
# from docx2pdf import convert as docx_convert

# --- 资源路径辅助 ---
# 优先尝试使用统一路径管理器
try:
    from modules.path_manager import get_asset_path
except ImportError:
    def get_asset_path(relative_path):
        if hasattr(sys, '_MEIPASS'):
            base_path = sys._MEIPASS
        else:
            base_path = os.path.abspath(".")
        return os.path.join(base_path, relative_path)

def get_resource_path(relative_path):
    return get_asset_path(relative_path)

# --- 转换逻辑 (保持不变) ---

def convert_image_to_pdf(img_path, output_path):
    """图片转PDF"""
    # 懒导入
    import fitz  # PyMuPDF

    try:
        img_doc = fitz.open(img_path)
        pdf_bytes = img_doc.convert_to_pdf()
        img_doc.close()
        temp_doc = fitz.open("pdf", pdf_bytes)
        temp_doc.save(output_path)
        return temp_doc
    except Exception as e:
        print(f"图片转换错: {e}")
        return None

def convert_excel_to_pdf(excel_path, font_path, output_path):
    """Excel转PDF"""
    # 懒导入
    import fitz
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    try:
        df = pd.read_excel(excel_path)
        df = df.fillna("")
        data = [df.columns.to_list()] + df.values.tolist()
        doc = SimpleDocTemplate(output_path, pagesize=A4)
        elements = []
        pdfmetrics.registerFont(TTFont('SimSun', font_path))
        table = Table(data)
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'SimSun'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(table)
        doc.build(elements)
        return fitz.open(output_path)
    except Exception as e:
        print(f"Excel转换错: {e}")
        return None

def convert_word_to_pdf(word_path, output_path):
    """Word转PDF"""
    # 懒导入
    import fitz
    import pythoncom
    from docx2pdf import convert as docx_convert


    try:
        pythoncom.CoInitialize()
        docx_convert(word_path, output_path)
        if os.path.exists(output_path):
            return fitz.open(output_path)
        return None
    except Exception as e:
        print(f"Word转换失败: {e}")
        return None

# --- 核心处理逻辑 ---

# === 【修改点 1】新增 stop_event 参数 ===
def core_merge_process(src_folder, recursive, include_others, include_word, keep_converted, split_enabled, split_step, log_callback, stop_event=None):
    
    import fitz

    font_path = get_resource_path(os.path.join("assets", "fonts", "simsun.ttc"))
    if not os.path.exists(font_path) and include_others:
        return "错误: 缺少字体文件 simsun.ttc"

    log_callback(f"正在扫描: {src_folder}")
    
    # 1. 收集文件
    files_to_merge = []
    if recursive:
        for root, dirs, files in os.walk(src_folder):
            for f in files:
                files_to_merge.append(os.path.join(root, f))
    else:
        for f in os.listdir(src_folder):
            full_path = os.path.join(src_folder, f)
            if os.path.isfile(full_path):
                files_to_merge.append(full_path)
    
    files_to_merge.sort(key=lambda x: x.lower())

    # 2. 合并过程
    files_to_delete = [] 
    count = 0
    img_exts = ('.png', '.jpg', '.jpeg', '.bmp')
    xls_exts = ('.xlsx', '.xls')
    doc_exts = ('.docx', '.doc')
    
    output_doc = fitz.open() 
    
    if include_word:
        try: pythoncom.CoInitialize()
        except: pass

    total_files = len(files_to_merge)

    for idx, fpath in enumerate(files_to_merge):
        # === 【修改点 2】合并循环中断 ===
        if stop_event and stop_event.is_set():
            output_doc.close() # 关闭主文档
            # 清理产生的临时文件
            for tmp in files_to_delete:
                try: os.remove(tmp)
                except: pass
            log_callback(">>> 用户强制停止任务！")
            return "任务已终止，未保存结果。"
        # =============================

        fname = os.path.basename(fpath)
        ext = os.path.splitext(fname)[1].lower()
        
        if fname.startswith("~$") or ".temp.pdf" in fname or "合并结果" in fname:
            continue

        current_doc = None
        
        # === A. 原生 PDF ===
        if ext == '.pdf':
            try:
                current_doc = fitz.open(fpath)
            except:
                log_callback(f"[跳过] 损坏PDF: {fname}")
        
        # === B. 需要转换的文件 ===
        elif include_others or (include_word and ext in doc_exts):
            if keep_converted:
                target_pdf_path = os.path.splitext(fpath)[0] + ".pdf"
            else:
                target_pdf_path = fpath + ".temp.pdf"

            if include_others and ext in img_exts:
                current_doc = convert_image_to_pdf(fpath, target_pdf_path)
                
            elif include_others and ext in xls_exts:
                current_doc = convert_excel_to_pdf(fpath, font_path, target_pdf_path)
                
            elif include_word and ext in doc_exts:
                log_callback(f"[{idx+1}/{total_files}] 转换 Word: {fname}")
                current_doc = convert_word_to_pdf(fpath, target_pdf_path)

            if current_doc:
                if not keep_converted:
                    files_to_delete.append(target_pdf_path)
                else:
                    log_callback(f"  -> 已生成独立PDF: {os.path.basename(target_pdf_path)}")

        # === C. 合并到总文档 ===
        if current_doc:
            try:
                output_doc.insert_pdf(current_doc)
                count += 1
            except Exception as e:
                log_callback(f"[合并错] {fname}: {e}")
            current_doc.close() 

    if count == 0:
        return "未找到可合并的文件。"

    total_pages = len(output_doc)
    log_callback(f"合并完成，共 {total_pages} 页。正在保存总文件...")

    # 3. 保存逻辑
    base_name = "合并结果"
    check_i = 0
    while True:
        suffix = f"({check_i})" if check_i > 0 else ""
        candidate = os.path.join(src_folder, f"{base_name}{suffix}.pdf")
        if not os.path.exists(candidate):
            final_base_path = os.path.join(src_folder, f"{base_name}{suffix}")
            break
        check_i += 1

    try:
        if split_enabled:
            # 分拆逻辑
            if split_step < 1: split_step = 1
            if split_step > total_pages: split_step = total_pages
            
            part_num = 1
            for start_page in range(0, total_pages, split_step):
                # === 【修改点 3】分拆循环中断 ===
                if stop_event and stop_event.is_set():
                    output_doc.close()
                    # 分拆了一半的文件保留，不再清理，但停止后续操作
                    log_callback(">>> 用户强制停止分拆！")
                    return "分拆任务已部分完成并终止。"
                # =============================

                end_page = min(start_page + split_step, total_pages)
                sub_doc = fitz.open()
                sub_doc.insert_pdf(output_doc, from_page=start_page, to_page=end_page-1)
                part_name = f"{final_base_path}-{part_num}.pdf"
                sub_doc.save(part_name)
                sub_doc.close()
                log_callback(f"生成分卷: {os.path.basename(part_name)} ({end_page-start_page}页)")
                part_num += 1
            msg = f"成功！已合并 {count} 个文件。\n分拆为 {part_num-1} 个部分。"
        else:
            # 整体保存
            final_path = final_base_path + ".pdf"
            output_doc.save(final_path)
            msg = f"成功！已合并 {count} 个文件。\n保存至: {os.path.basename(final_path)}"

        output_doc.close()

    except Exception as e:
        return f"保存失败: {e}"

    # 4. 清理
    for tmp in files_to_delete:
        try: os.remove(tmp)
        except: pass

    return msg

# --- 界面模块 ---

class PDFMergerModule:
    def __init__(self):
        self.name = "PDF 合并/分拆/转换"
        self.src_folder = ""
        # self.app 会由 main.py 注入

    def render(self, parent_frame):
        for widget in parent_frame.winfo_children(): widget.destroy()

        scroll = ctk.CTkScrollableFrame(parent_frame, fg_color="transparent", scrollbar_button_color="#E0E0E0", scrollbar_button_hover_color="#D0D0D0")
        scroll.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(scroll, text="PDF合并 & 格式转换 (支持Word/Excel/图)", font=("Microsoft YaHei", 20, "bold"), text_color="#333").pack(pady=(10, 10), anchor="w", padx=10)

        opt_frame = ctk.CTkFrame(scroll, fg_color="white", corner_radius=6, border_width=1, border_color="#E0E0E0")
        opt_frame.pack(fill="x", padx=10, pady=5)

        # 1. 文件夹选择
        row1 = ctk.CTkFrame(opt_frame, fg_color="transparent")
        row1.pack(fill="x", padx=10, pady=(15, 10))
        ctk.CTkLabel(row1, text="目标文件夹:", width=80, anchor="w", text_color="#555").pack(side="left")
        self.entry_path = ctk.CTkEntry(row1, placeholder_text="请选择文件夹...", height=35)
        self.entry_path.pack(side="left", fill="x", expand=True, padx=5)
        ctk.CTkButton(row1, text="浏览", width=60, height=35, command=self.select_folder).pack(side="left")

        # 2. 合并/转换选项
        self.var_recursive = ctk.BooleanVar(value=False)
        self.var_word = ctk.BooleanVar(value=False)
        self.var_others = ctk.BooleanVar(value=False)
        self.var_keep = ctk.BooleanVar(value=False)

        row2 = ctk.CTkFrame(opt_frame, fg_color="transparent")
        row2.pack(fill="x", padx=12, pady=5)
        ctk.CTkCheckBox(row2, text="递归子文件夹", variable=self.var_recursive, text_color="#333", height=20, font=("Microsoft YaHei", 12)).pack(side="left", padx=(85, 20))
        ctk.CTkCheckBox(row2, text="转换 Word", variable=self.var_word, text_color="#333", height=20, font=("Microsoft YaHei", 12)).pack(side="left", padx=(0, 20))
        ctk.CTkCheckBox(row2, text="转换 Excel/图片", variable=self.var_others, text_color="#333", height=20, font=("Microsoft YaHei", 12)).pack(side="left")

        row2_5 = ctk.CTkFrame(opt_frame, fg_color="transparent")
        row2_5.pack(fill="x", padx=12, pady=(5, 15))
        ctk.CTkCheckBox(row2_5, text="保留转换后的PDF单文件 (不删除中间文件)", variable=self.var_keep, text_color="#007AFF", height=20, font=("Microsoft YaHei", 12, "bold")).pack(side="left", padx=(85, 0))

        # 3. 分拆选项
        split_bg = ctk.CTkFrame(opt_frame, fg_color="#F8F9FA", corner_radius=4)
        split_bg.pack(fill="x", padx=10, pady=(0, 15))

        self.var_split = ctk.BooleanVar(value=False)
        row3 = ctk.CTkFrame(split_bg, fg_color="transparent")
        row3.pack(fill="x", padx=5, pady=8)

        def toggle_split():
            state = "normal" if self.var_split.get() else "disabled"
            self.entry_split_num.configure(state=state)
            if state == "normal": self.entry_split_num.configure(fg_color="white")
            else: self.entry_split_num.configure(fg_color="#F0F0F0")

        cb_split = ctk.CTkCheckBox(row3, text="合并后执行分拆", variable=self.var_split, command=toggle_split, text_color="#333", height=20, font=("Microsoft YaHei", 12, "bold"))
        cb_split.pack(side="left", padx=(5, 15))
        ctk.CTkLabel(row3, text="每", text_color="#555").pack(side="left")
        self.entry_split_num = ctk.CTkEntry(row3, width=60, height=28, state="disabled", fg_color="#F0F0F0")
        self.entry_split_num.pack(side="left", padx=5)
        self.entry_split_num.insert(0, "1") 
        ctk.CTkLabel(row3, text="页存为一个新文件", text_color="#555").pack(side="left")

        # --- 运行按钮 ---
        self.btn_run = ctk.CTkButton(scroll, text="开始执行", fg_color="#0984e3", height=36, font=("Microsoft YaHei", 15, "bold"), command=self.run_task)
        self.btn_run.pack(fill="x", padx=10, pady=10)

        # --- 日志 ---
        ctk.CTkLabel(scroll, text="执行日志", text_color="#333", font=("Microsoft YaHei", 13, "bold")).pack(anchor="w", padx=10, pady=(0, 5))
        self.textbox = ctk.CTkTextbox(scroll, height=200, fg_color="white", text_color="#333", border_width=1, border_color="#CCC")
        self.textbox.pack(padx=10, pady=(0, 10), fill="both", expand=True)

    def select_folder(self):
        p = filedialog.askdirectory()
        if p:
            self.entry_path.delete(0, "end")
            self.entry_path.insert(0, p)
            self.src_folder = p

    def log(self, msg):
        self.textbox.insert("end", msg + "\n")
        self.textbox.see("end")

    # === 【修改点 4】启动任务 ===
    def run_task(self):
        folder = self.entry_path.get().strip()
        if not folder or not os.path.exists(folder):
            messagebox.showerror("错误", "请选择有效的文件夹")
            return
        
        recursive = self.var_recursive.get()
        others = self.var_others.get()
        word = self.var_word.get()
        keep = self.var_keep.get() 
        
        split_enabled = self.var_split.get()
        split_step = 1
        if split_enabled:
            try:
                val = int(self.entry_split_num.get())
                if val < 1: raise ValueError
                split_step = val
            except:
                messagebox.showerror("参数错误", "分拆页数必须是大于 0 的整数")
                return

        # 申请红旗
        stop_event = None
        if hasattr(self, 'app'): stop_event = self.app.register_task(self.module_index)
        
        self.btn_run.configure(state="disabled", text="处理中...")
        self.textbox.delete("1.0", "end")
        
        def t():
            # 传入 stop_event
            result = core_merge_process(
                folder, recursive, others, word, keep, 
                split_enabled, split_step, self.log,
                stop_event=stop_event
            )
            self.log("-" * 30)
            self.log(result)
            
            # 销假 & 恢复按钮
            if hasattr(self, 'app'): self.app.finish_task(self.module_index)
            self.btn_run.configure(state="normal", text="开始执行")
            
            if "用户强制停止" not in result:
                messagebox.showinfo("完成", "任务结束")

        threading.Thread(target=t, daemon=True).start()