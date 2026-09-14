import os
import shutil
import xlrd
import xlwt
import openpyxl
from openpyxl import Workbook, load_workbook
import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading
import pandas as pd
import chardet  # 用于自动检测CSV编码

# --- 核心转换函数 ---

def is_html_file(file_path):
    """检测文件是否为HTML格式（伪装成xls的文件）"""
    try:
        with open(file_path, 'rb') as f:
            header = f.read(20)
            if header.startswith(b'\xef\xbb\xbf'):
                content = header[3:].decode('utf-8', errors='ignore')
            else:
                content = header.decode('utf-8', errors='ignore')
            return '<html' in content.lower() or '<!doctype html' in content.lower()
    except Exception:
        return False


def convert_html_to_xlsx_fast(src_file, dest_file):
    """
    使用Pandas的read_html进行HTML表格转换，底层C解析器，性能极高
    """
    try:
        # Pandas read_html会自动处理编码、表格解析和标签清理
        # lxml作为解析引擎，比html5lib更快
        dfs = pd.read_html(src_file, encoding='utf-8', flavor='lxml')
        
        if not dfs:
            return False, "未找到HTML表格"
        
        # 使用ExcelWriter批量写入，比openpyxl逐个cell写快得多
        with pd.ExcelWriter(dest_file, engine='openpyxl') as writer:
            for i, df in enumerate(dfs):
                # 限制sheet名长度在31字符以内
                sheet_name = f"Sheet{i+1}"
                # index=False表示不写入行号(0,1,2...)
                # header=True保留表头
                df.to_excel(writer, sheet_name=sheet_name, index=False, header=True)
        
        return True, ""
    
    except Exception as e:
        return False, f"Pandas转换失败: {str(e)}"

def convert_xls_to_xlsx_logic(src_file, dest_file):
    """
    [混合模式] xls -> xlsx
    1. 如果是 HTML 伪装 -> 走 Pandas 快速通道
    2. 如果是真正的 xls  -> 走原版 xlrd+openpyxl 稳定通道
    """
    
    # 1. 优先检测 HTML 伪装文件
    if is_html_file(src_file):
        return convert_html_to_xlsx_fast(src_file, dest_file)
    
    # 2. 使用原版代码逻辑（回滚到手动循环），保证对真实 xls 的兼容性
    try:
        rb = xlrd.open_workbook(src_file)
        wb = Workbook()
        wb.remove(wb.active) # 移除 openpyxl 默认创建的空 Sheet
        
        for sheet_idx in range(rb.nsheets):
            xls_sheet = rb.sheet_by_index(sheet_idx)
            
            # 获取 Sheet 名，做个简单的兜底，防止空名
            sheet_name = xls_sheet.name
            if not sheet_name:
                sheet_name = f"Sheet{sheet_idx + 1}"
            
            xlsx_sheet = wb.create_sheet(title=sheet_name)
            
            # 原版的逐行逐列复制逻辑，稳如老狗
            for row in range(xls_sheet.nrows):
                for col in range(xls_sheet.ncols):
                    val = xls_sheet.cell_value(row, col)
                    xlsx_sheet.cell(row=row+1, column=col+1, value=val)
        
        wb.save(dest_file)
        return True, ""
        
    except Exception as e:
        # 如果 xlrd 读取也报错了，再最后挣扎一下是不是 HTML 没检测出来
        if is_html_file(src_file):
            return convert_html_to_xlsx_fast(src_file, dest_file)
        return False, str(e)

def convert_xlsx_to_xls_logic(src_file, dest_file):
    """[模式2] xlsx -> xls"""
    try:
        wb_src = load_workbook(src_file, data_only=True)
        wb_dst = xlwt.Workbook(encoding='utf-8')
        for sheet_name in wb_src.sheetnames:
            ws_src = wb_src[sheet_name]
            ws_dst = wb_dst.add_sheet(sheet_name)
            if ws_src.max_row > 65535 or ws_src.max_column > 255:
                return False, f"超出xls限制(65k行/256列)"
            for r_idx, row in enumerate(ws_src.iter_rows(values_only=True)):
                for c_idx, value in enumerate(row):
                    if value is not None:
                        ws_dst.write(r_idx, c_idx, value)
        wb_dst.save(dest_file)
        return True, ""
    except Exception as e:
        return False, str(e)

def convert_format_change_logic(src_file, dest_file):
    """[模式3 & 4] xlsx <-> xlsm"""
    try:
        wb = load_workbook(src_file, keep_vba=True if dest_file.endswith('.xlsm') else False)
        wb.save(dest_file)
        return True, ""
    except Exception as e:
        return False, str(e)

# === 【新增】CSV 转 XLSX 核心逻辑 ===
def convert_csv_to_xlsx_logic(src_file, dest_file):
    """
    [模式4] csv -> xlsx
    自动检测文件编码，支持 UTF-8、GBK、GB2312、GB18030 等中文编码
    """
    try:
        # 1. 检测文件编码
        with open(src_file, 'rb') as f:
            raw_data = f.read(100000)  # 读取前100KB检测编码（足够判断）
            result = chardet.detect(raw_data)
            detected_encoding = result['encoding'] if result['encoding'] else 'utf-8'
            confidence = result['confidence'] if result['confidence'] else 0
        
        # 2. 尝试读取CSV（优先使用检测到的编码，失败则回退）
        encodings_to_try = [detected_encoding] if detected_encoding else []
        encodings_to_try += ['utf-8-sig', 'utf-8', 'gbk', 'gb2312', 'gb18030', 'latin1']
        
        df = None
        last_error = None
        
        for encoding in encodings_to_try:
            try:
                # 使用 pandas 读取 CSV
                # engine='python' 对不规则 CSV 更宽容，但慢一些；先用 'c' 引擎
                df = pd.read_csv(src_file, encoding=encoding, engine='python')
                break
            except Exception as e:
                last_error = e
                continue
        
        if df is None:
            return False, f"无法解析CSV文件(尝试编码: {', '.join(encodings_to_try)}): {str(last_error)}"
        
        # 3. 写入 Excel
        with pd.ExcelWriter(dest_file, engine='openpyxl') as writer:
            # 使用原文件名为 Sheet 名（去掉扩展名，限制31字符）
            sheet_name = os.path.splitext(os.path.basename(src_file))[0]
            if len(sheet_name) > 31:
                sheet_name = sheet_name[:28] + "..."
            
            df.to_excel(writer, sheet_name=sheet_name, index=False)
        
        return True, ""
        
    except Exception as e:
        return False, f"CSV转换失败: {str(e)}"


# --- 辅助函数：文件名冲突处理 ---

def get_unique_dest_path(folder, filename):
    """如果文件已存在，生成 filename(1).ext, filename(2).ext ..."""
    base, ext = os.path.splitext(filename)
    counter = 1
    new_filename = filename
    full_path = os.path.join(folder, new_filename)
    
    while os.path.exists(full_path):
        new_filename = f"{base}({counter}){ext}"
        full_path = os.path.join(folder, new_filename)
        counter += 1
        
    return full_path

# --- 批量处理调度器 (修改点) ---

# === 【修改点 1】新增 stop_event 参数 ===
def core_process_folder(src_folder, dst_folder, mode_index, copy_others, save_in_source, delete_source, log_callback, stop_event=None):
    
    if not save_in_source:
        if not os.path.exists(dst_folder):
            os.makedirs(dst_folder)
        abs_dst = os.path.abspath(dst_folder)
    else:
        abs_dst = None 

    # === 【修改】新增 CSV->XLSX 模式 (索引4) ===
    mode_config = {
        0: (['.xls'], '.xlsx', convert_xls_to_xlsx_logic),
        1: (['.xlsx'], '.xls', convert_xlsx_to_xls_logic),
        2: (['.xlsx'], '.xlsm', convert_format_change_logic),
        3: (['.xlsm'], '.xlsx', convert_format_change_logic),
        4: (['.csv'], '.xlsx', convert_csv_to_xlsx_logic)  # 新增
    }

    src_exts, target_ext, func = mode_config.get(mode_index)
    
    success_count = 0
    fail_count = 0
    copy_count = 0
    skipped_count = 0
    delete_count = 0

    log_callback(f"开始任务: {src_exts} -> {target_ext}")
    if save_in_source:
        log_callback("输出模式: 保存在原文件同级目录")
        log_callback(f"删除原文件: {'开启' if delete_source else '关闭'}")
    else:
        log_callback(f"输出目录: {dst_folder}")
        log_callback(f"文件打包模式: {'开启' if copy_others else '关闭'}")

    for root, dirs, files in os.walk(src_folder):
        # === 【修改点 2】文件夹级中断检测 ===
        if stop_event and stop_event.is_set():
            log_callback(">>> 用户强制停止任务！")
            return f"任务被强制终止。\n已成功: {success_count}, 失败: {fail_count}"
        # =================================

        if not save_in_source and abs_dst:
            abs_root = os.path.abspath(root)
            if abs_root.startswith(abs_dst):
                continue

        if save_in_source:
            target_path = root 
        else:
            relative_path = os.path.relpath(root, src_folder)
            target_path = os.path.join(dst_folder, relative_path)

        for file in files:
            # === 【修改点 3】文件级中断检测 ===
            if stop_event and stop_event.is_set():
                log_callback(">>> 用户强制停止任务！")
                return f"任务被强制终止。\n已成功: {success_count}, 失败: {fail_count}"
            # ===============================

            src_file_path = os.path.join(root, file)
            file_lower = file.lower()
            
            if file.startswith("~$"):
                continue

            is_target_file = any(file_lower.endswith(ext) for ext in src_exts)

            if is_target_file:
                if not os.path.exists(target_path): os.makedirs(target_path)

                base_name = os.path.splitext(file)[0]
                target_filename = base_name + target_ext
                dest_convert_path = get_unique_dest_path(target_path, target_filename)

                log_callback(f"转换: {file} -> {os.path.basename(dest_convert_path)}")
                
                status, msg = func(src_file_path, dest_convert_path)
                
                if status:
                    success_count += 1
                    if delete_source:
                        try:
                            os.remove(src_file_path)
                            delete_count += 1
                            log_callback(f"  └─ 已删除原文件: {file}")
                        except Exception as del_e:
                            log_callback(f"  └─ [删除失败] {file}: {del_e}")
                else:
                    log_callback(f"[失败] {file}: {msg}")
                    fail_count += 1
            
            else:
                if copy_others and not save_in_source:
                    if not os.path.exists(target_path): os.makedirs(target_path)
                    dest_copy_path = os.path.join(target_path, file)
                    if os.path.abspath(src_file_path) != os.path.abspath(dest_copy_path):
                        try:
                            shutil.copy2(src_file_path, dest_copy_path)
                            copy_count += 1
                        except Exception as e:
                            log_callback(f"[复制失败] {file}: {e}")
                else:
                    skipped_count += 1

    summary = f"任务结束。\n成功转换: {success_count}\n转换失败: {fail_count}"
    if save_in_source:
        summary += f"\n删除原文件: {delete_count}"
    else:
        summary += f"\n打包复制: {copy_count}"
    
    return summary

# --- 界面模块 (UI 优化重点) ---

class XLSToXLSXModule:
    def __init__(self):
        self.name = "Excel 格式互转"
        self.src_path = None
        self.dst_path = None
        # === 【修改】新增 CSV 转换模式 ===
        self.modes = [
            "1. 旧版转新版 (.xls -> .xlsx)",
            "2. 新版转旧版 (.xlsx -> .xls)",
            "3. 启用宏格式 (.xlsx -> .xlsm)",
            "4. 移除宏格式 (.xlsm -> .xlsx)",
            "5. CSV转Excel (.csv -> .xlsx)"  # 新增
        ]
        # self.app 会由 main.py 注入

    def render(self, parent_frame):
        # 清空当前页面
        for widget in parent_frame.winfo_children():
            widget.destroy()

        # ==================== 1. 创建全局滚动容器 ====================
        # 使用淡灰色滚动条，fg_color="transparent" 让它透明背景
        self.scroll_frame = ctk.CTkScrollableFrame(
            parent_frame,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color="#E0E0E0",        # 浅灰色滑块
            scrollbar_button_hover_color="#D0D0D0"   # 悬停颜色
        )
        self.scroll_frame.pack(fill="both", expand=True)

        # ==================== 2. 标题区 ====================
        title = ctk.CTkLabel(self.scroll_frame, text="Excel格式批量互转", font=("Microsoft YaHei", 22, "bold"))
        title.pack(pady=(20, 15), anchor="w", padx=20)

        # ==================== 3. 选项控制区 (白色卡片) ====================
        opt_frame = ctk.CTkFrame(self.scroll_frame, fg_color="white", corner_radius=8, border_width=1, border_color="#DDD")
        opt_frame.pack(fill="x", padx=20, pady=5)

        # (1) 模式选择
        ctk.CTkLabel(opt_frame, text="转换模式:", font=("Microsoft YaHei", 14), text_color="#333").pack(anchor="w", padx=15, pady=(15, 5))
        self.combo_mode = ctk.CTkComboBox(opt_frame, values=self.modes, width=350, state="readonly")
        self.combo_mode.pack(anchor="w", padx=15, pady=(0, 10))
        self.combo_mode.set(self.modes[0])

        # (2) 复选框区 (Grid布局)
        chk_frame = ctk.CTkFrame(opt_frame, fg_color="transparent")
        chk_frame.pack(fill="x", padx=15, pady=(5, 10))

        self.var_save_in_source = ctk.BooleanVar(value=False)
        self.chk_save_source = ctk.CTkCheckBox(chk_frame, text="保存在原目录 (转换后的文件放在原文件旁边)", 
                                             variable=self.var_save_in_source, 
                                             command=self.on_save_source_change,
                                             text_color="#333", font=("Microsoft YaHei", 12))
        self.chk_save_source.pack(anchor="w", pady=4)

        self.var_delete_source = ctk.BooleanVar(value=False)
        self.chk_delete_source = ctk.CTkCheckBox(chk_frame, text="删除原文件 (转换成功后删除源文件)", 
                                               variable=self.var_delete_source, 
                                               text_color="#d63031", font=("Microsoft YaHei", 12))
        self.chk_delete_source.pack(anchor="w", pady=4)

        self.var_copy_others = ctk.BooleanVar(value=False)
        self.chk_copy = ctk.CTkCheckBox(chk_frame, text="文件打包 (复制非目标文件到输出目录)", 
                                        variable=self.var_copy_others, 
                                        text_color="#333", font=("Microsoft YaHei", 12))
        self.chk_copy.pack(anchor="w", pady=4)

        # (3) 路径选择区
        path_frame = ctk.CTkFrame(opt_frame, fg_color="transparent")
        path_frame.pack(fill="x", padx=10, pady=10)

        # 源文件夹
        f1 = ctk.CTkFrame(path_frame, fg_color="transparent")
        f1.pack(fill="x", pady=5)
        self.entry_src = ctk.CTkEntry(f1, placeholder_text="源文件夹路径", height=35)
        self.entry_src.pack(side="left", fill="x", expand=True, padx=5)
        ctk.CTkButton(f1, text="选择源", width=90, command=self.select_src).pack(side="left", padx=5)

        # 输出文件夹
        f2 = ctk.CTkFrame(path_frame, fg_color="transparent")
        f2.pack(fill="x", pady=5)
        self.entry_dst = ctk.CTkEntry(f2, placeholder_text="输出文件夹 (默认 output)", height=35)
        self.entry_dst.pack(side="left", fill="x", expand=True, padx=5)
        self.btn_dst = ctk.CTkButton(f2, text="选择输出", width=90, command=self.select_dst)
        self.btn_dst.pack(side="left", padx=5)

        # ==================== 4. 操作按钮 ====================
        # 绑定到 run_task
        self.btn_run = ctk.CTkButton(self.scroll_frame, text="开始执行转换", command=self.run_task, 
                      fg_color="#0984e3", height=50, 
                      font=("Microsoft YaHei", 16, "bold"))
        self.btn_run.pack(pady=20, padx=20, fill="x")

        # ==================== 5. 日志区 ====================
        ctk.CTkLabel(self.scroll_frame, text="运行日志:", anchor="w", font=("Microsoft YaHei", 12, "bold")).pack(padx=20, anchor="w")
        
        self.textbox = ctk.CTkTextbox(
            self.scroll_frame, 
            height=360,
            fg_color="white", 
            text_color="#333", 
            border_width=1, 
            border_color="#CCC",
            font=("Consolas", 12)
        )
        self.textbox.pack(padx=20, pady=(5, 30), fill="both", expand=True)

    def on_save_source_change(self):
        if self.var_save_in_source.get():
            self.entry_dst.configure(state="disabled", fg_color="#F0F0F0")
            self.btn_dst.configure(state="disabled", fg_color="#999")
            self.chk_copy.configure(state="disabled")
        else:
            self.entry_dst.configure(state="normal", fg_color="white")
            self.btn_dst.configure(state="normal", fg_color="#3B8ED0")
            self.chk_copy.configure(state="normal")

    def select_src(self):
        path = filedialog.askdirectory()
        if path:
            self.src_path = path
            self.entry_src.delete(0, "end")
            self.entry_src.insert(0, path)
            if not self.var_save_in_source.get():
                default_out = os.path.join(path, "output")
                self.entry_dst.delete(0, "end")
                self.entry_dst.insert(0, default_out)

    def select_dst(self):
        path = filedialog.askdirectory()
        if path:
            self.dst_path = path
            self.entry_dst.delete(0, "end")
            self.entry_dst.insert(0, path)

    def log(self, message):
        self.textbox.insert("end", message + "\n")
        self.textbox.see("end")

    # === 【修改点 4】启动任务 ===
    def run_task(self):
        src = self.entry_src.get().strip()
        dst = self.entry_dst.get().strip()
        mode_str = self.combo_mode.get()
        copy_others = self.var_copy_others.get()
        save_in_source = self.var_save_in_source.get()
        delete_source = self.var_delete_source.get()
        
        mode_index = 0
        for i, m in enumerate(self.modes):
            if m == mode_str:
                mode_index = i
                break

        if not src: return messagebox.showwarning("提示", "请先选择源文件夹")
        if not save_in_source and not dst: dst = os.path.join(src, "output")

        if delete_source:
            if not messagebox.askyesno("危险操作警告", "您勾选了 [删除原文件]！\n转换成功后，原始文件将被永久删除。\n\n是否确认继续？"):
                return

        # 申请红旗
        stop_event = None
        if hasattr(self, 'app'): stop_event = self.app.register_task(self.module_index)
        
        self.btn_run.configure(state="disabled", text="转换中...")
        self.textbox.delete("1.0", "end")
        
        def thread_target():
            # 传入 stop_event
            result = core_process_folder(
                src, dst, mode_index, copy_others, 
                save_in_source, delete_source, 
                self.log,
                stop_event=stop_event
            )
            self.log("-" * 30)
            self.log(result)
            
            # 销假 & 恢复按钮
            if hasattr(self, 'app'): self.app.finish_task(self.module_index)
            self.btn_run.configure(state="normal", text="开始执行转换")
            
            if "用户强制停止" not in result:
                messagebox.showinfo("完成", "任务执行完毕")

        threading.Thread(target=thread_target, daemon=True).start()