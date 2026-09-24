"""Local Windows review app for coolant events."""
from __future__ import annotations
import argparse
import copy
import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import sys
from auto_roi import AreaTracker,regions_from_edges,source_to_strip

from drip_core import (ROOT,DEFAULT_CONFIG,LOGICAL_CPUS,DEFAULT_CPU_THREADS,analyze,open_video,
                       pixel_box,save_json,write_csv,write_patterns_csv,write_location_groups_csv,
                       Cancelled,export_event,classify_regular_patterns,classify_location_groups,classify_visual_behavior)
import cv2
import numpy as np
from PIL import Image, ImageTk

SOURCE_DIR=Path(r'C:\Users\sam_lee\OneDrive\WK\Problem Solving\HM Coolant Stain\SIDE TRIMMER')
SAMPLES=['2026_08_31 6_33_07.mp4','2026_08_31__06_33_24.mp4','2026_08_31__06_34_13.mp4',
         '2026_09_04__10_26_42.mp4','2026_09_04__10_26_56.mp4','2026_09_15__09_47_49.mp4']

class App:
    def __init__(self, root):
        self.root=root
        root.title('Drip Review | Side Trimmer')
        root.geometry('1380x980')
        root.minsize(1100,780)
        root.configure(bg='#eef2f6')
        self.config=copy.deepcopy(DEFAULT_CONFIG)
        self.config['auto_area']=True
        cfg=ROOT/'settings.json'
        if cfg.exists():
            try:self.config=json.loads(cfg.read_text(encoding='utf-8'))
            except (ValueError,OSError):pass
        self.config.setdefault('auto_area',True)
        for key in ('cpu_threads','roi_startup_seconds','roi_startup_stride','roi_camera_check_seconds','roi_stable_recheck_seconds'):
            self.config.setdefault(key,DEFAULT_CONFIG[key])
        self.config.setdefault('cpu_mode','auto')
        defaults={r['name']:r for r in DEFAULT_CONFIG['regions']}
        legacy={'Left': [[.04,.72],[.30,.72],[.30,.98],[.04,.98]],
                'Center': [[.34,.72],[.66,.72],[.66,.98],[.34,.98]],
                'Right': [[.70,.72],[.96,.72],[.96,.98],[.70,.98]]}
        existing={r['name'] for r in self.config.get('regions',[])}
        for region in self.config.get('regions',[]):
            old=region.get('strip_polygon')
            if region['name'] in defaults and (old is None or
                    (region['name'] in legacy and len(old)==len(legacy[region['name']]) and
                     np.allclose(old,legacy[region['name']],atol=1e-6))):
                region['strip_polygon']=copy.deepcopy(defaults[region['name']]['strip_polygon'])
        if 'Center' not in existing:
            self.config.setdefault('regions',[]).insert(1,copy.deepcopy(defaults['Center']))
        self.config['version']=DEFAULT_CONFIG['version']
        self.auto_tracker=None
        self.auto_state=None
        self.preview_regions=None
        self.area_timeline=None
        self.reviewing=False
        self.files=[]
        self.camera_mode=False
        self.camera_device=None
        self.cap=None
        self.current=None
        self.meta=None
        self.frame=None
        self.index=0
        self.playing=False
        self.play_job=None
        self.report=None
        self.report_dir=None
        self.busy=False
        self.messages=queue.Queue()
        self.cancel=threading.Event()
        self.run_gate=threading.Event()
        self.run_gate.set()
        self.drag_start=None
        self.selecting=False
        self.polygon_drawing=False
        self.polygon_editing=False
        self.polygon_points=[]
        self.polygon_new_name=None
        self.drag_vertex=None
        self.presets_path=ROOT/'area_presets.json'
        try:self.area_presets=json.loads(self.presets_path.read_text(encoding='utf-8')) if self.presets_path.exists() else {}
        except (OSError,ValueError):self.area_presets={}
        self.updating_slider=False
        self.viewport=None
        balanced=max(2,round(LOGICAL_CPUS*.35))
        fast=DEFAULT_CPU_THREADS
        maximum=max(2,LOGICAL_CPUS-2)
        self.cpu_choices={'Auto-select / 自動選擇':'auto',
                          f'Balanced / 平衡 ({balanced} threads)':balanced,
                          f'Fast / 快速 ({fast} threads)':fast,
                          f'Maximum / 最高 ({maximum} threads)':maximum}
        saved_threads=self.config.get('cpu_threads','auto')
        if self.config.get('cpu_mode')=='auto' or saved_threads=='auto':self.cpu_choice_default='Auto-select / 自動選擇'
        else:
            saved_threads=int(saved_threads)
            manual={label:value for label,value in self.cpu_choices.items() if value!='auto'}
            self.cpu_choice_default=min(manual,key=lambda label:abs(manual[label]-saved_threads))
        self.recheck_choices={'1 minute / 1 分鐘':60.0,'5 minutes / 5 分鐘':300.0,'10 minutes / 10 分鐘':600.0}
        saved_recheck=float(self.config.get('roi_stable_recheck_seconds',600))
        self.recheck_default=min(self.recheck_choices,key=lambda label:abs(self.recheck_choices[label]-saved_recheck))
        self.style()
        self.build()
        self.add_files([SOURCE_DIR/n for n in SAMPLES if (SOURCE_DIR/n).exists()])
        root.after(100,self.poll)
        root.protocol('WM_DELETE_WINDOW',self.close)
        root.bind('<space>',self.space)
        root.bind('<Left>',lambda e:self.key_step(e,-1))
        root.bind('<Right>',lambda e:self.key_step(e,1))
        root.bind('<Return>',self.finish_polygon)
        root.bind('<Escape>',self.cancel_polygon)

    def style(self):
        style=ttk.Style()
        style.theme_use('clam')
        style.configure('.',font=('Segoe UI',10),background='#eef2f6',foreground='#172b40')
        style.configure('TButton',padding=(10,7))
        style.configure('Accent.TButton',background='#076b68',foreground='white',font=('Segoe UI',10,'bold'))
        style.map('Accent.TButton',background=[('active','#09817b'),('disabled','#93aba9')])
        style.configure('Title.TLabel',font=('Segoe UI',20,'bold'))
        style.configure('Small.TLabel',font=('Segoe UI',9),foreground='#506174')
        style.configure('Treeview',rowheight=27,background='white',fieldbackground='white')
        style.configure('Treeview.Heading',font=('Segoe UI',10,'bold'))
        style.configure('TProgressbar',background='#13867d')

    def build(self):
        header=ttk.Frame(self.root,padding=(20,14))
        header.pack(fill='x')
        ttk.Label(header,text='Drip Review',style='Title.TLabel').pack(side='left')
        ttk.Label(header,text='SIDE TRIMMER  /  LOCAL VIDEO ANALYSIS',style='Small.TLabel').pack(side='left',padx=22)
        ttk.Button(header,text='Open results folder',command=self.open_output).pack(side='right')
        self.header_scan=ttk.Button(header,text='Scan selected / 重新掃描',style='Accent.TButton',command=lambda:self.run(False))
        self.header_scan.pack(side='right',padx=(0,8))
        self.pause_scan_button=ttk.Button(header,text='Pause scan / 暫停掃描',command=self.toggle_scan_pause,state='disabled')
        self.pause_scan_button.pack(side='right',padx=(0,8))
        main=ttk.Frame(self.root,padding=(16,0,16,8))
        main.pack(fill='both',expand=True)
        left=ttk.Frame(main,width=270,padding=(0,0,14,0))
        left.pack(side='left',fill='y')
        left.pack_propagate(False)
        ttk.Label(left,text='1  VIDEO LIBRARY',font=('Segoe UI',10,'bold')).pack(anchor='w',pady=(0,8))
        ttk.Button(left,text='Add MP4 videos…',command=self.choose_files).pack(fill='x')
        ttk.Button(left,text='Use camera source…',command=self.choose_camera).pack(fill='x',pady=(6,0))
        self.library=tk.Listbox(left,height=6,font=('Segoe UI',9),bg='white',fg='#172b40',
                                selectbackground='#076b68',selectforeground='white',exportselection=False,
                                relief='flat',borderwidth=0)
        self.library.pack(fill='x',pady=8)
        self.library.bind('<<ListboxSelect>>',self.choose_video)
        self.info=tk.StringVar(value='Choose a video to begin.')
        ttk.Label(left,textvariable=self.info,wraplength=240,style='Small.TLabel').pack(anchor='w',pady=(0,16))
        ttk.Label(left,text='2  DETECTION SETTINGS',font=('Segoe UI',10,'bold')).pack(anchor='w')
        self.auto_area=tk.BooleanVar(value=self.config.get('auto_area',True))
        ttk.Checkbutton(left,text='Auto-follow strip edges / 自動跟隨',variable=self.auto_area,command=self.change_area_mode).pack(anchor='w',pady=4)
        ttk.Button(left,text='Re-locate areas / 重新定位區域',command=self.change_area_mode).pack(fill='x')
        self.area_label=tk.StringVar(value='Automatic area location')
        ttk.Label(left,textvariable=self.area_label,wraplength=240,style='Small.TLabel').pack(anchor='w')
        ttk.Label(left,text='Area to adjust',style='Small.TLabel').pack(anchor='w',pady=(8,2))
        self.region=tk.StringVar(value='Left')
        self.region_combo=ttk.Combobox(left,textvariable=self.region,
                                       values=[r['name'] for r in self.config['regions']],state='readonly')
        self.region_combo.pack(fill='x')
        self.region_combo.bind('<<ComboboxSelected>>',lambda e:self.render())
        preset_row=ttk.Frame(left)
        preset_row.pack(fill='x',pady=(4,0))
        self.preset_name=tk.StringVar(value='')
        self.preset_combo=ttk.Combobox(preset_row,textvariable=self.preset_name,values=sorted(self.area_presets),state='readonly',width=15)
        self.preset_combo.pack(side='left',fill='x',expand=True)
        ttk.Button(preset_row,text='Save shape',command=self.save_shape_preset).pack(side='left',padx=(4,0))
        ttk.Button(preset_row,text='Use',command=self.apply_shape_preset).pack(side='left',padx=(4,0))
        ttk.Button(left,text='Draw irregular contour / 畫輪廓',command=self.begin_polygon).pack(fill='x',pady=(5,0))
        ttk.Button(left,text='Edit existing contour / 編輯節點',command=lambda:self.begin_polygon(False,True)).pack(fill='x',pady=(4,0))
        ttk.Button(left,text='Add another area / 新增區域',command=lambda:self.begin_polygon(True)).pack(fill='x',pady=(4,0))
        ttk.Button(left,text='Delete selected area',command=self.delete_area).pack(fill='x',pady=(4,0))
        ttk.Button(left,text='Manual rectangle',command=self.begin_area).pack(fill='x',pady=(4,0))
        ttk.Button(left,text='Restore auto Left / Center / Right',command=self.reset_areas).pack(fill='x',pady=(4,8))
        ttk.Label(left,text='Contrast threshold (lower = more sensitive)',wraplength=240,style='Small.TLabel').pack(anchor='w')
        self.threshold=tk.DoubleVar(value=self.config['threshold'])
        ttk.Spinbox(left,from_=4,to=60,increment=1,textvariable=self.threshold).pack(fill='x',pady=(3,8))
        ttk.Label(left,text='CPU usage / CPU 使用量',style='Small.TLabel').pack(anchor='w')
        self.cpu_mode=tk.StringVar(value=self.cpu_choice_default)
        ttk.Combobox(left,textvariable=self.cpu_mode,values=list(self.cpu_choices),state='readonly').pack(fill='x',pady=(3,6))
        ttk.Label(left,text='Stable area full recheck',style='Small.TLabel').pack(anchor='w')
        self.recheck=tk.StringVar(value=self.recheck_default)
        recheck_combo=ttk.Combobox(left,textvariable=self.recheck,values=list(self.recheck_choices),state='readonly')
        recheck_combo.pack(fill='x',pady=(3,8))
        recheck_combo.bind('<<ComboboxSelected>>',lambda e:self.change_area_mode())
        pads=ttk.Frame(left)
        pads.pack(fill='x')
        self.pre=tk.DoubleVar(value=self.config['pre_seconds'])
        self.post=tk.DoubleVar(value=self.config['post_seconds'])
        for label,var in [('Before (s)',self.pre),('After (s)',self.post)]:
            col=ttk.Frame(pads)
            col.pack(side='left',fill='x',expand=True)
            ttk.Label(col,text=label,style='Small.TLabel').pack(anchor='w')
            ttk.Spinbox(col,from_=0,to=30,increment=.25,textvariable=var,width=10).pack(fill='x')
        self.stabilize=tk.BooleanVar(value=self.config['stabilize'])
        self.stabilize_check=ttk.Checkbutton(left,text='Manual-mode vibration correction',variable=self.stabilize,
                                            state='disabled' if self.auto_area.get() else 'normal')
        self.stabilize_check.pack(anchor='w',pady=8)
        self.analyze_one=ttk.Button(left,text='Scan / re-analyze selected video',style='Accent.TButton',command=lambda:self.run(False))
        self.analyze_one.pack(fill='x',pady=(5,4))
        self.analyze_all=ttk.Button(left,text='Analyze all originals',command=lambda:self.run(True))
        self.analyze_all.pack(fill='x')
        action_row=ttk.Frame(left)
        action_row.pack(fill='x',pady=4)
        self.cancel_button=ttk.Button(action_row,text='Cancel',command=self.cancel_scan,state='disabled')
        self.cancel_button.pack(fill='x',expand=True)
        ttk.Label(left,text='Counts are candidate coolant events. Review each event before using the total. Nearby or simultaneous droplets can merge into one event.',
                  wraplength=240,style='Small.TLabel').pack(anchor='w',pady=14)
        ttk.Button(left,text='Open saved results…',command=self.load_result_dialog).pack(fill='x')

        right=ttk.Frame(main)
        right.pack(side='left',fill='both',expand=True)
        self.title=tk.StringVar(value='Video preview')
        ttk.Label(right,textvariable=self.title,font=('Segoe UI',12,'bold')).pack(anchor='w',pady=(0,5))
        self.canvas=tk.Canvas(right,bg='#111c28',highlightthickness=0,height=410)
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',lambda e:self.render())
        self.canvas.bind('<ButtonPress-1>',self.drag_down)
        self.canvas.bind('<Double-Button-1>',self.finish_polygon)
        self.canvas.bind('<Button-3>',self.add_polygon_vertex)
        self.canvas.bind('<Shift-Button-3>',self.remove_polygon_vertex)
        self.canvas.bind('<B1-Motion>',self.drag_move)
        self.canvas.bind('<ButtonRelease-1>',self.drag_up)
        controls=ttk.Frame(right,padding=(0,7))
        controls.pack(fill='x')
        self.play_button=ttk.Button(controls,text='Play',command=self.toggle_play)
        self.play_button.pack(side='left')
        ttk.Button(controls,text='◀ Frame',command=lambda:self.step(-1)).pack(side='left',padx=4)
        ttk.Button(controls,text='Frame ▶',command=lambda:self.step(1)).pack(side='left')
        self.speed=tk.StringVar(value='0.25x')
        ttk.Combobox(controls,textvariable=self.speed,values=['0.25x','0.5x','1x'],width=7,state='readonly').pack(side='left',padx=8)
        self.zoom=tk.BooleanVar(value=False)
        ttk.Checkbutton(controls,text='Zoom selected area',variable=self.zoom,command=self.render).pack(side='left')
        self.time_label=tk.StringVar(value='0.000 s')
        ttk.Label(controls,textvariable=self.time_label).pack(side='right')
        self.position=tk.DoubleVar(value=0)
        self.slider=ttk.Scale(right,from_=0,to=1,variable=self.position,command=self.scrub)
        self.slider.pack(fill='x')
        results_head=ttk.Frame(right,padding=(0,8,0,5))
        results_head.pack(fill='x')
        ttk.Label(results_head,text='3  REVIEW EVENTS',font=('Segoe UI',10,'bold')).pack(side='left')
        self.counts=tk.StringVar(value='No analysis loaded')
        ttk.Label(results_head,textvariable=self.counts).pack(side='right')
        filters=ttk.Frame(right,padding=(0,0,0,5))
        filters.pack(fill='x')
        self.event_view=tk.StringVar(value='Location groups / 位置分組')
        view=ttk.Combobox(filters,textvariable=self.event_view,
                          values=['Location groups / 位置分組','All individual events / 全部事件','Timing regular only / 只看時間規律'],
                          state='readonly',width=26)
        view.pack(side='left')
        view.bind('<<ComboboxSelected>>',lambda e:self.refresh_events())
        self.group_filter=tk.StringVar(value='All locations / 全部位置')
        self.group_filter_box=ttk.Combobox(filters,textvariable=self.group_filter,
                                           values=['All locations / 全部位置'],state='readonly',width=26)
        self.group_filter_box.pack(side='left',padx=6)
        self.group_filter_box.bind('<<ComboboxSelected>>',lambda e:self.refresh_events())
        self.visual_filter=tk.StringVar(value='All visual classes / 全部影像分類')
        self.visual_filter_box=ttk.Combobox(filters,textvariable=self.visual_filter,
            values=['All visual classes / 全部影像分類','Likely reflection jitter / 疑似反光抖動',
                    'Possible moving droplet / 疑似移動油滴','Uncertain / 無法確定'],state='readonly',width=29)
        self.visual_filter_box.pack(side='left')
        self.visual_filter_box.bind('<<ComboboxSelected>>',lambda e:self.refresh_events())
        treeframe=ttk.Frame(right)
        treeframe.pack(fill='x')
        self.tree=ttk.Treeview(treeframe,columns=('time','side','kind','pattern','state','count'),show='tree headings',height=5,selectmode='browse')
        self.tree.heading('#0',text='Event')
        self.tree.column('#0',width=105,stretch=False)
        for key,title,width in [('time','Video time (s)',105),('side','Area',65),('kind','Visual class / 影像分類',180),('pattern','Timing / 時間規律',165),('state','Review status',105),('count','Confirmed droplets',125)]:
            self.tree.heading(key,text=title)
            self.tree.column(key,width=width)
        self.tree.pack(side='left',fill='x',expand=True)
        scroll=ttk.Scrollbar(treeframe,orient='vertical',command=self.tree.yview)
        scroll.pack(side='right',fill='y')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>',self.choose_event)
        review=ttk.Frame(right,padding=(0,7))
        review.pack(fill='x')
        ttk.Button(review,text='Accept event',command=lambda:self.mark('accepted')).pack(side='left')
        ttk.Button(review,text='Reject',command=lambda:self.mark('rejected')).pack(side='left',padx=4)
        ttk.Button(review,text='Unreviewed',command=lambda:self.mark('unreviewed')).pack(side='left')
        ttk.Button(review,text='Play event clip',command=self.play_event).pack(side='left',padx=8)
        ttk.Button(review,text='Export event video / 匯出影片',command=self.export_event_video).pack(side='left')
        ttk.Label(review,text='Export speed:').pack(side='left',padx=(8,3))
        self.export_speed=tk.StringVar(value='0.25x')
        ttk.Combobox(review,textvariable=self.export_speed,
                     values=['0.10x','0.25x','0.50x','1.00x'],width=7).pack(side='left')
        ttk.Button(review,text='Add missed event',command=self.add_manual).pack(side='right')
        detail=ttk.Frame(right)
        detail.pack(fill='x')
        ttk.Label(detail,text='Droplets (optional):',style='Small.TLabel').pack(side='left')
        self.drip_count=tk.StringVar(value='')
        ttk.Entry(detail,textvariable=self.drip_count,width=6).pack(side='left',padx=5)
        ttk.Label(detail,text='Note:',style='Small.TLabel').pack(side='left')
        self.note=tk.StringVar(value='')
        ttk.Entry(detail,textvariable=self.note).pack(side='left',fill='x',expand=True,padx=5)
        ttk.Button(detail,text='Save details',command=self.save_details).pack(side='left')
        self.warning=tk.StringVar(value='Use original MP4 files. The _C copies repeat the same events with drawn circles.')
        ttk.Label(right,textvariable=self.warning,wraplength=970,style='Small.TLabel').pack(anchor='w',pady=(6,0))
        footer=ttk.Frame(self.root,padding=(20,5,20,12))
        footer.pack(fill='x')
        self.status=tk.StringVar(value='Ready. All processing and outputs stay on this computer.')
        ttk.Label(footer,textvariable=self.status).pack(anchor='w')
        self.progress=ttk.Progressbar(footer,maximum=100)
        self.progress.pack(fill='x',pady=(4,0))

    def source_key(self, source):
        if source is None:
            return ('none', '')
        text=str(source).strip()
        if text.lower().startswith('camera:'):
            return ('camera', text.lower())
        if '://' in text:
            return ('stream', text.lower())
        try:
            return ('file', str(Path(text).resolve()))
        except Exception:
            return ('text', text.lower())

    def add_files(self,files):
        for item in files:
            path=Path(item).resolve()
            if path not in self.files:
                self.files.append(path)
                self.library.insert('end',path.name)
        if self.files and self.current is None:
            self.library.selection_set(0)
            self.load_video(self.files[0])

    def choose_files(self):
        self.add_files(filedialog.askopenfilenames(filetypes=[('Video','*.mp4 *.avi *.mkv *.mov'),('All files','*.*')]))

    def choose_camera(self):
        self.pause()
        dialog=tk.Toplevel(self.root)
        dialog.title('Choose camera source')
        dialog.transient(self.root)
        dialog.resizable(False,False)
        dialog.grab_set()
        frame=ttk.Frame(dialog,padding=16)
        frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Detected cameras',font=('Segoe UI',12,'bold')).pack(anchor='w')
        ttk.Label(frame,text='Select a connected camera, or add a remote camera stream.',style='Small.TLabel').pack(anchor='w',pady=(2,10))
        camera_list=tk.Listbox(frame,height=7,width=58,exportselection=False,
                               selectbackground='#076b68',selectforeground='white')
        camera_list.pack(fill='both',expand=True)
        status=tk.StringVar(value='Scanning for connected cameras…')
        ttk.Label(frame,textvariable=status,style='Small.TLabel').pack(anchor='w',pady=(8,10))

        detected=[]

        def scan():
            detected.clear()
            camera_list.delete(0,'end')
            status.set('Scanning for connected cameras…')
            dialog.update_idletasks()
            for index in range(10):
                cap=cv2.VideoCapture(index,cv2.CAP_DSHOW)
                if not cap.isOpened():
                    cap.release()
                    continue
                ok,frame=cap.read()
                width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or (frame.shape[1] if ok else 0)
                height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or (frame.shape[0] if ok else 0)
                fps=cap.get(cv2.CAP_PROP_FPS) or 30.0
                cap.release()
                if ok:
                    detected.append(index)
                    camera_list.insert('end',f'Camera {index}  —  {width} × {height}  |  {fps:.1f} fps')
            if detected:
                camera_list.selection_set(0)
                camera_list.focus_set()
                status.set(f'{len(detected)} connected camera(s) detected.')
            else:
                status.set('No connected cameras detected. You can add a remote stream below.')

        def use_selected():
            selected=camera_list.curselection()
            if not selected:
                messagebox.showinfo('Choose a camera','Select a detected camera first.',parent=dialog)
                return
            source=f'camera:{detected[selected[0]]}'
            dialog.destroy()
            self.open_camera_source(source)

        def add_camera():
            source=simpledialog.askstring(
                'Add camera or stream',
                'Enter a remote stream URL (RTSP, HTTP, RTMP, UDP, or TCP):',
                parent=dialog,
            )
            if source is None or not source.strip():
                return
            dialog.destroy()
            self.open_camera_source(source.strip())

        def capture_screen():
            dialog.destroy()
            self.choose_screen()

        buttons=ttk.Frame(frame)
        buttons.pack(fill='x',pady=(2,0))
        ttk.Button(buttons,text='Refresh').pack(side='left')
        buttons.winfo_children()[0].configure(command=scan)
        ttk.Button(buttons,text='Use selected',style='Accent.TButton',command=use_selected).pack(side='left',padx=(6,0))
        ttk.Button(buttons,text='Add camera or stream…',command=add_camera).pack(side='left',padx=(6,0))
        ttk.Button(buttons,text='Capture screen area…',command=capture_screen).pack(side='left',padx=(6,0))
        ttk.Button(buttons,text='Cancel',command=dialog.destroy).pack(side='right')
        dialog.protocol('WM_DELETE_WINDOW',dialog.destroy)
        dialog.after(50,scan)

    def choose_screen(self):
        try:
            import mss
            with mss.mss() as sct:
                monitor=sct.monitors[0]
                shot=sct.grab(monitor)
                image=Image.frombytes('RGB',shot.size,shot.rgb)
        except Exception as exc:
            messagebox.showerror('Screen capture unavailable',f'Could not capture the desktop: {exc}')
            return
        overlay=tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.attributes('-topmost',True)
        overlay.geometry(f'{monitor["width"]}x{monitor["height"]}+{monitor["left"]}+{monitor["top"]}')
        canvas=tk.Canvas(overlay,width=monitor['width'],height=monitor['height'],highlightthickness=0)
        canvas.pack()
        photo=ImageTk.PhotoImage(image)
        overlay._screen_photo=photo
        canvas.create_image(0,0,anchor='nw',image=photo)
        canvas.create_text(20,20,anchor='nw',fill='white',text='Drag over the video window. Esc cancels.',font=('Segoe UI',14,'bold'))
        start=[None,None]
        rectangle=[None]

        def down(event):
            start[:]=[event.x,event.y]
            if rectangle[0]:canvas.delete(rectangle[0])

        def move(event):
            if start[0] is None:return
            if rectangle[0]:canvas.delete(rectangle[0])
            rectangle[0]=canvas.create_rectangle(start[0],start[1],event.x,event.y,outline='#65e5bd',width=4)

        def up(event):
            if start[0] is None:return
            x0,x1=sorted((start[0],event.x))
            y0,y1=sorted((start[1],event.y))
            if x1-x0<32 or y1-y0<32:return
            source=f'screen:{monitor["left"]+x0},{monitor["top"]+y0},{x1-x0},{y1-y0}'
            overlay.destroy()
            self.open_camera_source(source)

        canvas.bind('<ButtonPress-1>',down)
        canvas.bind('<B1-Motion>',move)
        canvas.bind('<ButtonRelease-1>',up)
        overlay.bind('<Escape>',lambda event:overlay.destroy())
        overlay.focus_force()

    def open_camera_source(self, source):
        source=source.strip()
        try:
            if self.cap:self.cap.release()
            screen_source=source.lower().startswith('screen:')
            if screen_source:
                cap,meta=open_video(source)
                source_value=source
            elif source.lower().startswith('camera:'):
                source_value=int(source.split(':',1)[1] or 0)
                cap=cv2.VideoCapture(source_value)
            elif source.lower().startswith(('rtsp://','http://','https://','rtmp://','udp://','tcp://')):
                source_value=source
                cap=cv2.VideoCapture(str(source_value))
            else:
                source_value=int(source) if source.isdigit() else source
                cap=cv2.VideoCapture(source_value if isinstance(source_value,(int,float)) else str(source_value))
            if not cap.isOpened():
                raise ValueError(f'Cannot open source: {source}')
            fps=meta['fps'] if screen_source else cap.get(cv2.CAP_PROP_FPS) or 30.0
            width=meta['width'] if screen_source else int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1280
            height=meta['height'] if screen_source else int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 720
            self.camera_mode=True
            self.camera_device=source_value if isinstance(source_value,(int,float)) else source_value
            self.cap=cap
            self.meta=dict(width=width,height=height,fps=fps,frames=1_000_000,duration_seconds=0)
            self.current=source_value if isinstance(source_value,(int,float)) else source_value
            if isinstance(self.current, (int,float)):
                self.current=f'camera:{int(self.current)}'
            self.title.set('Screen capture' if screen_source else (f'Camera {self.current}' if self.current.lower().startswith('camera:') else self.current))
            self.info.set(f'Screen capture\n{width} × {height}  |  {fps:.2f} fps' if screen_source else f'Live camera feed\n{width} × {height}  |  {fps:.2f} fps')
            self.slider.configure(state='disabled')
            self.play_button.configure(text='Live')
            self.index=0
            self.frame=None
            self.preview_regions=[]
            self.auto_area.set(False)
            self.change_area_mode()
            status = f'Camera source ready on device {self.current}' if self.current.lower().startswith('camera:') else f'Remote stream ready: {self.current}'
            self.status.set(f'{status}. Manual detection areas are active for live sources.')
            self.read_camera_frame()
        except Exception as exc:
            messagebox.showerror('Cannot open camera',str(exc))

    def choose_video(self,event=None):
        selected=self.library.curselection()
        if selected:self.load_video(self.files[selected[0]])

    def read_camera_frame(self):
        if not self.camera_mode or not self.cap:return
        ok,frame=self.cap.read()
        if not ok:
            self.status.set('Camera frame read failed.')
            return
        self.index=0
        self.frame=frame
        self.time_label.set('Live camera feed')
        self.position.set(0)
        self.render()

    def load_video(self,path,find_report=True):
        self.camera_mode=False
        self.camera_device=None
        self.pause()
        try:
            cap,meta=open_video(path)
            if self.cap:self.cap.release()
            self.cap,self.meta,self.current=cap,meta,Path(path).resolve()
            self.slider.configure(state='normal')
            self.auto_tracker=AreaTracker(meta['fps'],self.live_tracking_config())
            self.auto_state=None
            self.preview_regions=None
            self.area_timeline=None
            self.reviewing=False
            if isinstance(self.current, str) and self.current.lower().startswith('camera:'):
                self.title.set(f"Camera {self.current.split(':',1)[1]}")
            elif isinstance(self.current, str) and '://' in self.current:
                self.title.set(self.current.split('://',1)[1].split('/',1)[0])
            else:
                self.title.set(Path(str(self.current)).name)
            self.info.set(f"{meta['width']} × {meta['height']}  |  {meta['fps']:.2f} fps\n{meta['duration_seconds']:.2f} seconds")
            self.slider.configure(to=max(1,meta['frames']-1))
            self.report=None
            self.report_dir=None
            self.refresh_events()
            self.seek(0)
            if find_report:
                results=[]
                for p in (ROOT/'output').glob('*/results.json'):
                    try:
                        r=json.loads(p.read_text(encoding='utf-8'))
                        if self.source_key(r.get('source')) == self.source_key(self.current) and r['state']=='complete':results.append((p,r))
                    except (OSError,ValueError,KeyError):continue
                if results:
                    p,r=max(results,key=lambda item:item[0].stat().st_mtime)
                    self.set_report(p.parent,r)
        except Exception as exc:messagebox.showerror('Cannot open video',str(exc))

    def seek(self,index):
        if self.camera_mode:
            self.read_camera_frame()
            return
        if not self.cap:return
        index=max(0,min(int(index),self.meta['frames']-1))
        if index!=self.index+1 or self.frame is None:self.cap.set(cv2.CAP_PROP_POS_FRAMES,index)
        ok,frame=self.cap.read()
        if not ok:
            self.pause()
            self.status.set(f'Could not decode frame {index}.')
            return
        previous=self.index
        self.index,self.frame=index,frame
        if self.auto_tracker and index!=previous+1:self.auto_tracker=AreaTracker(self.meta['fps'],self.live_tracking_config())
        self.update_areas()
        pts=self.cap.get(cv2.CAP_PROP_POS_MSEC)/1000
        self.time_label.set(f"{pts:.3f} s  |  frame {index+1}/{self.meta['frames']}")
        self.updating_slider=True
        self.position.set(index)
        self.updating_slider=False
        self.render()

    def current_region(self):
        regions=self.display_regions()
        return next((r for r in regions if r['name']==self.region.get()),regions[0] if regions else None)

    def refresh_region_names(self,select=None):
        names=[r['name'] for r in self.config['regions']]
        self.region_combo.configure(values=names)
        if select in names:self.region.set(select)
        elif self.region.get() not in names and names:self.region.set(names[0])

    def save_shape_preset(self):
        region=next((r for r in self.config['regions'] if r['name']==self.region.get()),None)
        if region is None:return
        name=simpledialog.askstring('Save shape preset','Preset name:',initialvalue=f"{region['name']} shape",parent=self.root)
        if not name:return
        name=name.strip()
        if not name:return
        if name in self.area_presets and not messagebox.askyesno('Save shape preset',f'Replace preset “{name}”?'):return
        preset={'mode':'automatic' if region.get('strip_polygon') else 'manual',
                'source_region':region['name'],
                'box':copy.deepcopy(region.get('box',[0,0,1,1])),
                'polygon':copy.deepcopy(region.get('polygon',[[0,0],[1,0],[1,1],[0,1]]))}
        if region.get('strip_polygon'):preset['strip_polygon']=copy.deepcopy(region['strip_polygon'])
        self.area_presets[name]=preset
        save_json(self.presets_path,self.area_presets)
        self.preset_combo.configure(values=sorted(self.area_presets))
        self.preset_name.set(name)
        self.status.set(f'Shape preset “{name}” saved for future sessions.')

    def apply_shape_preset(self):
        name=self.preset_name.get()
        preset=self.area_presets.get(name)
        if not preset:
            messagebox.showinfo('Shape presets','Select a saved shape preset first.')
            return
        region=next((r for r in self.config['regions'] if r['name']==self.region.get()),None)
        if region is None:return
        if preset.get('strip_polygon'):
            region['strip_polygon']=copy.deepcopy(preset['strip_polygon'])
            self.auto_area.set(True)
        else:
            region.pop('strip_polygon',None)
            region['box']=copy.deepcopy(preset.get('box',region.get('box',[0,0,1,1])))
            region['polygon']=copy.deepcopy(preset.get('polygon',[[0,0],[1,0],[1,1],[0,1]]))
            self.auto_area.set(False)
        self.change_area_mode()
        self.status.set(f'Shape preset “{name}” applied to {region["name"]}. Scan to save it with new results.')

    def display_regions(self):
        if self.reviewing and self.report:
            if self.area_timeline and self.index<len(self.area_timeline):
                return self.area_timeline[self.index]['regions'] or []
            if self.report.get('state')=='paused_preview':
                selected=self.selected_event() if hasattr(self,'tree') else None
                if selected and selected.get('peak_region'):
                    regions=copy.deepcopy(self.report['config']['regions'])
                    return [copy.deepcopy(selected['peak_region']) if r['name']==selected['region'] else r for r in regions]
            return self.report['config']['regions']
        return self.preview_regions if self.auto_area.get() else self.config['regions']

    def live_tracking_config(self):
        cfg=copy.deepcopy(self.config)
        if hasattr(self,'recheck'):
            cfg['roi_stable_recheck_seconds']=self.recheck_choices.get(self.recheck.get(),600.0)
        return cfg

    def update_areas(self):
        if self.reviewing and self.report:
            state=self.area_timeline[self.index]['status'] if self.area_timeline else ('paused preview' if self.report.get('state')=='paused_preview' else 'manual')
            self.area_label.set('Saved result areas: '+state)
        elif self.auto_area.get() and self.frame is not None:
            self.auto_state=self.auto_tracker.update(self.frame,self.index)
            self.preview_regions=regions_from_edges(self.auto_state['edges'],self.config['regions']) if self.auto_state['edges'] else []
            label={'tracking':'Tracking / 邊緣已定位',
                   'held':'Held / 暫用上次位置，停止計數',
                   'lost':'Lost / 無法定位，停止計數',
                   'no_material':'No material / 無卷材，停止計數',
                   'material_confirming':'Material found / 確認卷材中，停止計數'}[self.auto_state['status']]
            self.area_label.set(label)
        else:self.area_label.set('Manual areas / 手動區域')

    def change_area_mode(self):
        self.reviewing=False
        self.config['auto_area']=self.auto_area.get()
        self.stabilize_check.configure(state='disabled' if self.auto_area.get() else 'normal')
        if self.meta:self.auto_tracker=AreaTracker(self.meta['fps'],self.live_tracking_config())
        self.update_areas()
        self.render()

    def event_source_box(self,event,w,h):
        if event.get('peak_source_bbox'):return event['peak_source_bbox']
        regions=(self.report or {}).get('config',{}).get('regions',[]) if self.report else []
        region=event.get('peak_region') or next((r for r in regions if r['name']==event.get('region')),None)
        if region is None or not event.get('bbox'):return None
        x0,y0,_,_=pixel_box(region,w,h)
        bx0,by0,bx1,by1=event['bbox']
        corners=np.array([[x0+bx0,y0+by0,1],[x0+bx1,y0+by1,1]],np.float32)
        transform=np.array(event.get('peak_transform',[[1,0,0],[0,1,0]]),np.float32)
        raw=corners@transform.T
        return [float(raw[:,0].min()),float(raw[:,1].min()),float(raw[:,0].max()),float(raw[:,1].max())]

    def render(self):
        if self.frame is None:return
        h,w=self.frame.shape[:2]
        regions=self.display_regions() or []
        region=next((r for r in regions if r['name']==self.region.get()),None)
        frame=self.frame.copy()
        selected=self.selected_event() if self.reviewing and hasattr(self,'tree') else None
        if selected and selected['start_frame']<=self.index<=selected['last_frame']:
            detected=self.event_source_box(selected,w,h)
            if detected:
                x0,y0,x1,y1=[int(round(v)) for v in detected]
                pad=12
                center=((x0+x1)//2,(y0+y1)//2)
                axes=(max(14,(x1-x0)//2+pad),max(14,(y1-y0)//2+pad))
                cv2.ellipse(frame,center,axes,0,0,360,(0,80,255),4,cv2.LINE_AA)
                cv2.putText(frame,'DETECTED',(max(0,x0-pad),max(28,y0-pad)),cv2.FONT_HERSHEY_SIMPLEX,.8,(0,80,255),2,cv2.LINE_AA)
        if self.zoom.get() and region:
            x1,y1,x2,y2=pixel_box(region,w,h)
            pad=35
            xa,ya=max(0,x1-pad),max(0,y1-pad)
            xb,yb=min(w,x2+pad),min(h,y2+pad)
            frame=frame[ya:yb,xa:xb]
        else:
            xa,ya,xb,yb=0,0,w,h
            for r in regions:
                x1,y1,x2,y2=pixel_box(r,w,h)
                color=(90,225,175) if r['name']==self.region.get() else (200,175,70)
                poly=np.array(r.get('polygon',[[0,0],[1,0],[1,1],[0,1]]))*(x2-x1,y2-y1)+(x1,y1)
                cv2.polylines(frame,[poly.astype(np.int32)],True,color,2)
                cv2.putText(frame,r['name'],(x1,y1-9),cv2.FONT_HERSHEY_SIMPLEX,.8,color,2,cv2.LINE_AA)
        width=max(20,self.canvas.winfo_width())
        height=max(20,self.canvas.winfo_height())
        im=Image.fromarray(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB))
        im.thumbnail((width,height),Image.Resampling.LANCZOS)
        ox,oy=(width-im.width)//2,(height-im.height)//2
        self.viewport=(ox,oy,im.width,im.height,xa,ya,xb,yb)
        self.photo=ImageTk.PhotoImage(im)
        self.canvas.delete('all')
        self.canvas.create_image(ox,oy,anchor='nw',image=self.photo)
        if self.selecting:self.canvas.create_text(16,18,anchor='nw',fill='white',text='Drag a rectangle around the strip area to monitor',font=('Segoe UI',11,'bold'))
        if self.polygon_drawing:
            canvas_points=[]
            for sx,sy in self.polygon_points:
                cx,cy=self.to_canvas(sx,sy)
                canvas_points.extend((cx,cy))
            if len(canvas_points)>=4:self.canvas.create_line(*canvas_points,fill='#65e5bd',width=3,tags='selection')
            for cx,cy in zip(canvas_points[::2],canvas_points[1::2]):
                self.canvas.create_oval(cx-4,cy-4,cx+4,cy+4,fill='#65e5bd',outline='white',tags='selection')
            instruction=('Drag nodes; right-click an edge to add a node; Shift+right-click deletes. Enter saves.'
                         if self.polygon_editing else
                         'Click around the contour. Double-click or press Enter to finish; Esc cancels.')
            self.canvas.create_text(16,18,anchor='nw',fill='white',text=instruction,font=('Segoe UI',11,'bold'))

    def pause(self):
        self.playing=False
        if self.play_job:
            self.root.after_cancel(self.play_job)
            self.play_job=None
        if hasattr(self,'play_button'):self.play_button.configure(text='Play')

    def toggle_play(self):
        if self.playing:
            self.pause()
        elif self.camera_mode and self.cap:
            self.playing=True
            self.play_button.configure(text='Stop')
            self.live_tick()
        elif self.cap:
            if self.index>=self.meta['frames']-1:self.seek(0)
            self.playing=True
            self.play_button.configure(text='Pause')
            self.tick()

    def tick(self):
        self.play_job=None
        if not self.playing:return
        if self.index>=self.meta['frames']-1:
            self.pause()
            return
        self.seek(self.index+1)
        if self.playing:
            self.play_job=self.root.after(max(1,round(1000/self.meta['fps']/float(self.speed.get()[:-1]))),self.tick)

    def live_tick(self):
        self.play_job=None
        if not self.playing or not self.camera_mode or not self.cap:
            return
        ok,frame=self.cap.read()
        if not ok:
            self.pause()
            self.status.set('Camera feed stopped. Reconnect the device to continue.')
            return
        self.frame=frame
        self.time_label.set('Live camera feed')
        self.render()
        self.play_job=self.root.after(max(1, int(1000 / max(self.meta['fps'], 1.0))), self.live_tick)

    def scrub(self,value):
        if not self.updating_slider:
            self.pause()
            self.seek(round(float(value)))

    def step(self,direction):
        self.pause()
        self.seek(self.index+direction)

    def key_step(self,event,direction):
        if event.widget.winfo_class() not in ('TEntry','TSpinbox','Entry'):
            self.step(direction)
            return 'break'

    def space(self,event):
        if event.widget.winfo_class() not in ('TEntry','TSpinbox','Entry'):
            self.toggle_play()
            return 'break'

    def begin_area(self):
        self.pause()
        self.cancel_polygon()
        self.auto_area.set(False)
        self.change_area_mode()
        self.zoom.set(False)
        self.selecting=True
        self.render()

    def to_source(self,x,y):
        ox,oy,w,h,xa,ya,xb,yb=self.viewport
        return (xa+min(1,max(0,(x-ox)/w))*(xb-xa),ya+min(1,max(0,(y-oy)/h))*(yb-ya))

    def to_canvas(self,x,y):
        ox,oy,w,h,xa,ya,xb,yb=self.viewport
        return (ox+(x-xa)/(xb-xa)*w,oy+(y-ya)/(yb-ya)*h)

    def drag_down(self,event):
        if self.polygon_drawing and self.viewport:
            if self.polygon_editing:
                distances=[np.hypot(*(np.array(self.to_canvas(*point))-[event.x,event.y])) for point in self.polygon_points]
                self.drag_vertex=int(np.argmin(distances)) if distances and min(distances)<=16 else None
                return
            self.polygon_points.append(self.to_source(event.x,event.y))
            self.render()
            return
        if self.selecting and self.viewport:self.drag_start=(event.x,event.y)

    def drag_move(self,event):
        if self.polygon_editing and self.drag_vertex is not None:
            self.polygon_points[self.drag_vertex]=self.to_source(event.x,event.y)
            self.render()
            return
        if self.drag_start:
            self.canvas.delete('selection')
            self.canvas.create_rectangle(*self.drag_start,event.x,event.y,outline='#65e5bd',width=2,tags='selection')

    def drag_up(self,event):
        if self.polygon_editing and self.drag_vertex is not None:
            self.polygon_points[self.drag_vertex]=self.to_source(event.x,event.y)
            self.drag_vertex=None
            self.render()
            return
        if not self.drag_start:return
        a=self.to_source(*self.drag_start)
        b=self.to_source(event.x,event.y)
        x1,x2=sorted([a[0],b[0]])
        y1,y2=sorted([a[1],b[1]])
        self.drag_start=None
        self.selecting=False
        if x2-x1>=12 and y2-y1>=12:
            region=next(r for r in self.config['regions'] if r['name']==self.region.get())
            region['box']=[x1/self.meta['width'],y1/self.meta['height'],x2/self.meta['width'],y2/self.meta['height']]
            region.pop('polygon',None)
            self.status.set('Area changed for the next analysis. Existing results keep their original settings.')
        self.render()

    def reset_areas(self):
        self.config['regions']=copy.deepcopy(DEFAULT_CONFIG['regions'])
        self.auto_area.set(True)
        self.refresh_region_names('Left')
        self.change_area_mode()
        self.render()

    def begin_polygon(self,new=False,edit=False):
        if self.frame is None:return
        self.pause()
        self.selecting=False
        self.drag_start=None
        self.zoom.set(False)
        self.reviewing=False
        if self.auto_area.get():
            self.auto_tracker=AreaTracker(self.meta['fps'],self.live_tracking_config())
            self.update_areas()
        if self.auto_area.get() and (not self.auto_state or not self.auto_state.get('edges')):
            messagebox.showwarning('Area tracking','The strip edges are not reliable on this frame. Move to a clear frame, then draw the contour.')
            return
        name=self.region.get()
        if new:
            name=simpledialog.askstring('New detection area','Name this area (for example: Center 2):',parent=self.root)
            if not name:return
            name=name.strip()
            if not name or any(r['name'].casefold()==name.casefold() for r in self.config['regions']):
                messagebox.showerror('New detection area','Enter a unique area name.')
                return
            self.config['regions'].append(dict(name=name,box=[.4,.72,.6,.98],polygon=[[0,0],[1,0],[1,1],[0,1]]))
            self.polygon_new_name=name
            self.refresh_region_names(name)
        else:
            self.polygon_new_name=None
        self.polygon_points=[]
        if edit:
            current=next((r for r in (self.display_regions() or []) if r['name']==name),None)
            if current is None:
                messagebox.showerror('Edit contour','The current contour is unavailable on this frame.')
                return
            x0,y0,x1,y1=current['box']
            self.polygon_points=[((x0+p[0]*(x1-x0))*self.meta['width'],
                                  (y0+p[1]*(y1-y0))*self.meta['height'])
                                 for p in current.get('polygon',[[0,0],[1,0],[1,1],[0,1]])]
        self.polygon_drawing=True
        self.polygon_editing=edit
        self.drag_vertex=None
        self.status.set('Drag nodes to avoid reflections. Right-click an edge to add a notch; Shift+right-click deletes a node.' if edit
                        else 'Click points around the area. Double-click or press Enter to finish; Esc cancels.')
        self.render()

    def add_polygon_vertex(self,event):
        if not (self.polygon_drawing and self.polygon_editing and len(self.polygon_points)>=2):return
        target=np.array([event.x,event.y],np.float32)
        canvas=[np.array(self.to_canvas(*point),np.float32) for point in self.polygon_points]
        best=None
        for i,a in enumerate(canvas):
            b=canvas[(i+1)%len(canvas)]
            delta=b-a
            t=float(np.clip(np.dot(target-a,delta)/max(1e-6,np.dot(delta,delta)),0,1))
            projected=a+t*delta
            distance=float(np.linalg.norm(target-projected))
            if best is None or distance<best[0]:best=(distance,i)
        self.polygon_points.insert(best[1]+1,self.to_source(event.x,event.y))
        self.render()
        return 'break'

    def remove_polygon_vertex(self,event):
        if not (self.polygon_drawing and self.polygon_editing and len(self.polygon_points)>3):return
        distances=[np.hypot(*(np.array(self.to_canvas(*point))-[event.x,event.y])) for point in self.polygon_points]
        if distances and min(distances)<=18:self.polygon_points.pop(int(np.argmin(distances)))
        self.render()
        return 'break'

    def finish_polygon(self,event=None):
        if not self.polygon_drawing:return
        points=[]
        for point in self.polygon_points:
            if not points or np.linalg.norm(np.array(point)-np.array(points[-1]))>3:points.append(point)
        if len(points)>2 and np.linalg.norm(np.array(points[0])-np.array(points[-1]))<=3:points.pop()
        if len(points)<3:
            self.status.set('The contour needs at least three different points.')
            return 'break'
        name=self.region.get()
        region=next(r for r in self.config['regions'] if r['name']==name)
        normalized=[[x/self.meta['width'],y/self.meta['height']] for x,y in points]
        try:
            if self.auto_area.get():
                strip_polygon=source_to_strip(normalized,self.auto_state['edges'])
                if any(not (0<=u<=1 and 0<=v<=1) for u,v in strip_polygon):
                    raise ValueError('Every point must be inside the detected aluminum strip.')
                region['strip_polygon']=strip_polygon
                current=regions_from_edges(self.auto_state['edges'],[region])[0]
                region['box']=current['box']
                region['polygon']=current['polygon']
            else:
                x0=min(p[0] for p in normalized); x1=max(p[0] for p in normalized)
                y0=min(p[1] for p in normalized); y1=max(p[1] for p in normalized)
                if (x1-x0)*self.meta['width']<12 or (y1-y0)*self.meta['height']<12:
                    raise ValueError('The contour is too small.')
                region['box']=[x0,y0,x1,y1]
                region['polygon']=[[(x-x0)/(x1-x0),(y-y0)/(y1-y0)] for x,y in normalized]
                region.pop('strip_polygon',None)
        except ValueError as exc:
            messagebox.showerror('Detection contour',str(exc))
            return 'break'
        self.polygon_drawing=False
        self.polygon_editing=False
        self.polygon_points=[]
        self.polygon_new_name=None
        self.drag_vertex=None
        self.update_areas()
        self.status.set('Contour saved. In automatic mode it follows strip width and camera movement.')
        self.render()
        return 'break'

    def cancel_polygon(self,event=None):
        if not self.polygon_drawing:return
        if self.polygon_new_name:
            self.config['regions']=[r for r in self.config['regions'] if r['name']!=self.polygon_new_name]
            self.refresh_region_names()
        self.polygon_drawing=False
        self.polygon_editing=False
        self.polygon_points=[]
        self.polygon_new_name=None
        self.drag_vertex=None
        self.status.set('Contour drawing cancelled.')
        self.render()
        return 'break'

    def delete_area(self):
        if len(self.config['regions'])<=1:
            messagebox.showwarning('Detection areas','Keep at least one detection area.')
            return
        name=self.region.get()
        if not messagebox.askyesno('Delete detection area',f'Delete “{name}”?'):return
        self.config['regions']=[r for r in self.config['regions'] if r['name']!=name]
        self.refresh_region_names()
        self.update_areas()
        self.render()

    def settings(self):
        cfg=copy.deepcopy(self.config)
        cpu_choice=self.cpu_choices[self.cpu_mode.get()]
        cfg.update(threshold=self.threshold.get(),pre_seconds=self.pre.get(),post_seconds=self.post.get(),
                   stabilize=self.stabilize.get(),export_clips=False,auto_area=self.auto_area.get(),
                   cpu_threads=cpu_choice,cpu_mode='auto' if cpu_choice=='auto' else 'manual',
                   roi_stable_recheck_seconds=self.recheck_choices[self.recheck.get()])
        from drip_core import validate_config
        validate_config(cfg)
        return cfg

    def run(self,all_files):
        if self.busy:return
        targets=[p for p in self.files if not p.stem.lower().endswith('_c')] if all_files else ([self.current] if self.current else [])
        if not targets:return
        try:
            cfg=self.settings()
            save_json(ROOT/'settings.json',cfg)
        except Exception as exc:
            messagebox.showerror('Settings',str(exc))
            return
        self.pause()
        self.reviewing=False
        self.cancel.clear()
        self.run_gate.set()
        self.busy=True
        self.analyze_one.configure(state='disabled')
        self.analyze_all.configure(state='disabled')
        self.header_scan.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.pause_scan_button.configure(state='normal',text='Pause scan / 暫停掃描')
        def worker():
            errors=[]
            for i,path in enumerate(targets):
                if self.cancel.is_set():break
                try:
                    source_label=path.name if isinstance(path,Path) else str(path)
                    def callback(value,message):
                        self.messages.put(('progress',((i+value)/len(targets)*100,f'{source_label}: {message}')))
                    def partial_callback(folder,report):
                        self.messages.put(('partial_report',(folder,report)))
                    folder,report=analyze(path,ROOT/'output',cfg,callback,self.cancel,self.run_gate,partial_callback)
                    self.messages.put(('report',(folder,report)))
                except Cancelled:break
                except Exception as exc:errors.append(f'{source_label}: {exc}')
            self.messages.put(('done',errors))
        threading.Thread(target=worker,daemon=True).start()

    def poll(self):
        try:
            while True:
                kind,data=self.messages.get_nowait()
                if kind=='progress':
                    self.progress['value']=data[0]
                    self.status.set(data[1])
                elif kind=='report':
                    folder,report=data
                    if self.current and self.source_key(report.get('source')) == self.source_key(self.current):self.set_report(folder,report)
                elif kind=='partial_report':
                    folder,report=data
                    if not self.run_gate.is_set() and self.current and self.source_key(report.get('source')) == self.source_key(self.current):
                        self.set_report(folder,report)
                        scanned=report.get('scan_progress',{}).get('frames_scanned',0)
                        self.status.set(f'Paused: showing {len(report["events"])} provisional candidates from {scanned} scanned frames.')
                elif kind=='done':
                    self.busy=False
                    self.analyze_one.configure(state='normal')
                    self.analyze_all.configure(state='normal')
                    self.header_scan.configure(state='normal')
                    self.cancel_button.configure(state='disabled')
                    self.pause_scan_button.configure(state='disabled',text='Pause scan / 暫停掃描')
                    self.run_gate.set()
                    self.status.set('Analysis cancelled; completed results are saved.' if self.cancel.is_set() else 'Analysis complete. Select a video and review its candidate events.')
                    if data:messagebox.showerror('Some videos could not be analyzed','\n'.join(data))
        except queue.Empty:pass
        self.root.after(100,self.poll)

    def toggle_scan_pause(self):
        if not self.busy:return
        if self.run_gate.is_set():
            self.run_gate.clear()
            self.pause_scan_button.configure(text='Resume scan / 繼續掃描')
            self.status.set('Scan paused. Completed progress is retained.')
        else:
            self.run_gate.set()
            self.pause_scan_button.configure(text='Pause scan / 暫停掃描')
            self.status.set('Scan resumed.')
            if self.report and self.report.get('state')=='paused_preview':
                self.warning.set('Scan resumed. The visible event list is provisional and covers only the earlier scanned portion; final results will replace it.')

    def cancel_scan(self):
        self.cancel.set()
        self.run_gate.set()
        self.status.set('Cancelling scan…')

    def set_report(self,folder,report):
        self.report_dir,self.report=Path(folder).resolve(),report
        if 'patterns' not in report:
            report['patterns']=classify_regular_patterns(report.get('events',[]),report.get('metadata',{}))
        event_ids={event.get('id') for event in report.get('events',[])}
        saved_group_ids={event_id for group in report.get('location_groups',[]) for event_id in group.get('event_ids',[])}
        if ('location_groups' not in report or
                (report.get('location_groups') and not saved_group_ids.issubset(event_ids)) or
                (not report.get('location_groups') and len(event_ids)>=3)):
            report['location_groups']=classify_location_groups(report.get('events',[]),report.get('metadata',{}))
        if (any('visual_classification' not in group for group in report.get('location_groups',[])) or
                any('visual_classification' not in event for event in report.get('events',[]))):
            classify_visual_behavior(report.get('events',[]),report.get('location_groups',[]),report.get('metadata',{}))
        self.area_timeline=None
        if report.get('region_timeline'):
            self.area_timeline=json.loads((self.report_dir/report['region_timeline']).read_text(encoding='utf-8'))
        self.reviewing=True
        self.update_areas()
        self.render()
        self.update_group_filter_options()
        self.refresh_events()
        if report.get('state')=='paused_preview':
            scan=report.get('scan_progress',{})
            seconds=scan.get('seconds_scanned',0)
            total=scan.get('total_frames',0)
            frames=scan.get('frames_scanned',0)
            groups=report.get('location_groups',[])
            self.warning.set(f'PAUSED PREVIEW • {len(report["events"])} provisional candidates • {len(groups)} same-location groups • scanned {seconds:.1f}s ({frames}/{total} frames). Unscanned video is not included.')
            return
        p=report.get('performance',{})
        ratio=p.get('detection_realtime_ratio')
        speed=f"{p.get('analysis_fps','?')} fps"+(f" ({ratio}× real-time)" if ratio is not None else '')
        cpu_note=f"CPU {p.get('cpu_threads','?')} threads"+(' auto-selected' if report.get('config',{}).get('cpu_mode')=='auto' else '')
        patterns=report.get('patterns',[])
        regular=sum(pattern.get('occurrences',0) for pattern in patterns)
        groups=report.get('location_groups',[])
        grouped=sum(group.get('occurrences',0) for group in groups)
        reflection=sum(event.get('visual_classification')=='likely_reflection_jitter' for event in report['events'])
        moving=sum(event.get('visual_classification')=='possible_moving_droplet' for event in report['events'])
        self.warning.set(f"{len(report['events'])} candidates • {len(groups)} same-location groups ({grouped} events) • {reflection} likely reflection-jitter • {moving} possible moving-droplet • {len(patterns)} timing patterns ({regular} events) • Detection {speed} • {cpu_note} • Human review required. "
                         +f"Skipped alignment frames: {p.get('alignment_skipped_frames',0)}. Times are relative to this video.")

    def update_group_filter_options(self):
        groups=self.report.get('location_groups',[]) if self.report else []
        values=['All locations / 全部位置','Ungrouped / 未分組']
        self.group_filter_ids={values[0]:None,values[1]:''}
        for number,group in enumerate(groups,1):
            label=f"{group['id']} · 位置群組 {number} ({group['occurrences']})"
            values.append(label)
            self.group_filter_ids[label]=group['id']
        self.group_filter_box.configure(values=values)
        if self.group_filter.get() not in values:self.group_filter.set(values[0])

    def refresh_events(self,selected=None):
        self.tree.delete(*self.tree.get_children())
        self.tree_event_map={}
        self.tree_pattern_map={}
        self.tree_location_map={}
        if not self.report:
            self.counts.set('No analysis loaded')
            return
        events=self.report['events']
        patterns=self.report.get('patterns',[])
        groups=self.report.get('location_groups',[])
        by_id={e['id']:e for e in events}
        regular_ids={event_id for pattern in patterns for event_id in pattern.get('event_ids',[])}
        chosen_group=getattr(self,'group_filter_ids',{}).get(self.group_filter.get(),None)
        if chosen_group is None:filtered_events=events
        elif chosen_group=='':filtered_events=[e for e in events if not e.get('location_group_id')]
        else:filtered_events=[e for e in events if e.get('location_group_id')==chosen_group]
        visual_codes={'All visual classes / 全部影像分類':None,
                      'Likely reflection jitter / 疑似反光抖動':'likely_reflection_jitter',
                      'Possible moving droplet / 疑似移動油滴':'possible_moving_droplet',
                      'Uncertain / 無法確定':'uncertain'}
        chosen_visual=visual_codes.get(self.visual_filter.get())
        if chosen_visual:filtered_events=[e for e in filtered_events if e.get('visual_classification','uncertain')==chosen_visual]
        filtered_ids={e['id'] for e in filtered_events}
        visual_labels={'likely_reflection_jitter':'Likely reflection jitter / 疑似反光抖動',
                       'possible_moving_droplet':'Possible moving droplet / 疑似移動油滴',
                       'uncertain':'Uncertain / 無法確定'}
        def add_event(e,parent=''):
            kind=visual_labels.get(e.get('visual_classification','uncertain'),visual_labels['uncertain'])
            pattern=e.get('pattern_id') or 'No timing pattern / 無時間規律'
            self.tree.insert(parent,'end',iid=e['id'],text=e['id'],values=(f"{e['peak_seconds']:.3f}",e['region'],kind,pattern,e['status'],'' if e.get('confirmed_drips') is None else e['confirmed_drips']))
            self.tree_event_map[e['id']]=e
        def add_pattern(pattern,allowed_ids=None):
            member_ids=[event_id for event_id in pattern.get('event_ids',[]) if allowed_ids is None or event_id in allowed_ids]
            if not member_ids:return
            iid='pattern_'+pattern['id']
            label=f"{pattern['id']} ({len(member_ids)}×)"
            trend=pattern.get('frequency_trend','stable')
            if trend=='frequency_increasing':
                timing=f"{pattern.get('interval_start_seconds',pattern['interval_seconds']):.3g}→{pattern.get('interval_end_seconds',pattern['interval_seconds']):.3g}s · frequency rising / 頻率上升"
            elif trend=='frequency_decreasing':
                timing=f"{pattern.get('interval_start_seconds',pattern['interval_seconds']):.3g}→{pattern.get('interval_end_seconds',pattern['interval_seconds']):.3g}s · frequency falling / 頻率下降"
            else:timing=f"{pattern['interval_seconds']:.3g}s interval · {pattern['frequency_per_minute']:.2g}/min"
            span=f"{pattern['first_seconds']:.3f}–{pattern['last_seconds']:.3f}"
            self.tree.insert('','end',iid=iid,text=label,open=False,
                             values=(span,pattern['region'],'Regular pattern',timing,'Pattern review',''))
            self.tree_pattern_map[iid]=pattern
            for event_id in member_ids:
                if event_id in by_id:add_event(by_id[event_id],iid)
        def add_location_group(group):
            member_ids=[event_id for event_id in group.get('event_ids',[]) if event_id in filtered_ids]
            if not member_ids:return
            iid='location_'+group['id']
            number=groups.index(group)+1
            label=f"位置群組 {number} ({len(member_ids)}×)"
            span=f"{group['first_seconds']:.3f}–{group['last_seconds']:.3f}"
            timing=f"{group.get('timing_regular_events',0)} timing-regular · {group.get('timing_unclassified_events',0)} variable"
            group_visual=chosen_visual or group.get('visual_classification','uncertain')
            self.tree.insert('','end',iid=iid,text=label,open=False,
                             values=(span,group['region'],visual_labels.get(group_visual,visual_labels['uncertain']),timing,'Group review',''))
            self.tree_location_map[iid]=group
            for event_id in member_ids:
                if event_id in by_id:add_event(by_id[event_id],iid)
        mode=self.event_view.get() if hasattr(self,'event_view') else 'Location groups / 位置分組'
        if mode.startswith('All'):
            for e in filtered_events:add_event(e)
        elif mode.startswith('Timing'):
            for pattern in patterns:add_pattern(pattern,filtered_ids)
        else:
            matching_groups=[group for group in groups if chosen_group is None or group['id']==chosen_group]
            for group in matching_groups:add_location_group(group)
            if chosen_group in (None,''):
                for e in filtered_events:
                    if not e.get('location_group_id'):add_event(e)
        accepted=sum(e['status']=='accepted' for e in events)
        pending=sum(e['status']=='unreviewed' for e in events)
        confirmed=sum((e.get('confirmed_drips') or 0) for e in events if e['status']=='accepted')
        known=any(e.get('confirmed_drips') is not None and e['status']=='accepted' for e in events)
        label=f'{confirmed} confirmed droplets entered' if known else 'Droplet total: not established'
        regular_count=len(regular_ids)
        irregular_count=len(events)-regular_count
        grouped_count=sum(bool(e.get('location_group_id')) for e in events)
        reflection=sum(e.get('visual_classification')=='likely_reflection_jitter' for e in events)
        moving=sum(e.get('visual_classification')=='possible_moving_droplet' for e in events)
        self.counts.set(f'{len(events)} raw  |  {len(groups)} location groups ({grouped_count})  |  {reflection} likely reflection  |  {moving} moving-droplet  |  {label}')
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
            self.tree.see(selected)

    def selected_event(self):
        selected=self.tree.selection()
        if self.report and selected:
            if selected[0] in getattr(self,'tree_event_map',{}):return self.tree_event_map[selected[0]]
            pattern=getattr(self,'tree_pattern_map',{}).get(selected[0])
            if pattern:
                return next((e for e in self.report['events'] if e['id']==pattern.get('representative_event_id')),None)
            group=getattr(self,'tree_location_map',{}).get(selected[0])
            if group:
                return next((e for e in self.report['events'] if e['id']==group.get('representative_event_id')),None)

    def selected_pattern(self):
        selected=self.tree.selection()
        return getattr(self,'tree_pattern_map',{}).get(selected[0]) if selected else None

    def selected_location_group(self):
        selected=self.tree.selection()
        return getattr(self,'tree_location_map',{}).get(selected[0]) if selected else None

    def choose_event(self,event=None):
        e=self.selected_event()
        if not e:return
        pattern=self.selected_pattern()
        location_group=self.selected_location_group()
        self.pause()
        self.reviewing=True
        self.region.set(e['region'])
        self.zoom.set(True)
        # Review using the exact area saved when this result was generated.
        self.config['regions']=copy.deepcopy(self.report['config']['regions'])
        self.drip_count.set('' if e.get('confirmed_drips') is None else str(e['confirmed_drips']))
        self.note.set(e.get('notes',''))
        self.seek(e['peak_frame'])
        if pattern:
            trend=pattern.get('frequency_trend','stable')
            if trend=='frequency_increasing':detail=f"interval changes from {pattern['interval_start_seconds']:.3g}s to {pattern['interval_end_seconds']:.3g}s (frequency rising)"
            elif trend=='frequency_decreasing':detail=f"interval changes from {pattern['interval_start_seconds']:.3g}s to {pattern['interval_end_seconds']:.3g}s (frequency falling)"
            else:detail=f"about every {pattern['interval_seconds']:.3g}s"
            self.status.set(f"{pattern['id']}: {pattern['occurrences']} occurrences near one location; {detail}. Possible synchronized reflection or periodic defect; review required.")
        elif location_group:
            visual={'likely_reflection_jitter':'likely reflection jitter / 疑似反光抖動',
                    'possible_moving_droplet':'possible moving droplet / 疑似移動油滴',
                    'uncertain':'uncertain / 無法確定'}.get(location_group.get('visual_classification'),'uncertain / 無法確定')
            self.status.set(f"{location_group['name']}: {location_group['occurrences']} events recur near the same point; {visual}, score {location_group.get('reflection_jitter_score',0):.2f}. This is a review aid, not automatic rejection.")

    def persist(self,e):
        self.report['patterns']=classify_regular_patterns(self.report.get('events',[]),self.report.get('metadata',{}))
        self.report['location_groups']=classify_location_groups(self.report.get('events',[]),self.report.get('metadata',{}))
        classify_visual_behavior(self.report.get('events',[]),self.report['location_groups'],self.report.get('metadata',{}))
        save_json(self.report_dir/'results.json',self.report)
        write_csv(self.report_dir,self.report['events'])
        write_patterns_csv(self.report_dir,self.report['patterns'])
        write_location_groups_csv(self.report_dir,self.report['location_groups'])
        self.update_group_filter_options()
        self.refresh_events(e['id'])
        self.status.set('Review saved to JSON and event, location-group, and timing-pattern CSV files.')

    def read_details(self,e):
        raw=self.drip_count.get().strip()
        if raw and (not raw.isdigit() or int(raw)>10000):
            raise ValueError('Enter a whole number of confirmed droplets, or leave blank when unknown.')
        e['confirmed_drips']=int(raw) if raw else None
        e['notes']=self.note.get().strip()

    def mark(self,state):
        e=self.selected_event()
        if not e:return
        if self.selected_pattern() or self.selected_location_group():
            self.status.set('Expand the group and select an individual event before accepting or rejecting it.')
            return
        if self.report.get('state')=='paused_preview':
            self.status.set('This is a provisional pause preview. Resume and finish the scan before accepting or rejecting events.')
            return
        try:
            self.read_details(e)
            e['status']=state
            if state!='accepted':e['confirmed_drips']=None
            self.persist(e)
        except Exception as exc:messagebox.showerror('Review',str(exc))

    def save_details(self):
        e=self.selected_event()
        if not e:return
        if self.selected_pattern() or self.selected_location_group():
            self.status.set('Expand the group and select an individual event before saving review details.')
            return
        if self.report.get('state')=='paused_preview':
            self.status.set('Finish the scan before saving review details.')
            return
        try:
            self.read_details(e)
            self.persist(e)
        except Exception as exc:messagebox.showerror('Review',str(exc))

    def play_event(self):
        e=self.selected_event()
        if not e:return
        try:speed=self.selected_export_speed()
        except ValueError as exc:
            self.status.set(str(exc));return
        match=next((item for item in e.get('exports',[]) if
                    abs(float(item.get('playback_speed',1))-speed)<1e-9),None)
        current_speed=float(e.get('export_playback_speed',1))
        relative=(match.get('closeup_clip','') if match else
                  e.get('closeup_clip','') if abs(current_speed-speed)<1e-9 else '')
        path=self.report_dir/relative
        if relative and path.is_file():os.startfile(str(path))
        else:
            self.status.set(f"No {speed:g}x clip has been exported for {e['id']}. Choose Export event video first.")

    def selected_export_speed(self):
        raw=self.export_speed.get().strip().lower()
        try:
            speed=float(raw[:-1])/100 if raw.endswith('%') else float(raw[:-1] if raw.endswith('x') else raw)
        except ValueError:raise ValueError('Enter an export speed such as 0.10x, 0.25x, 0.50x, 1.00x, or 25%.')
        if not .05<=speed<=2.0:raise ValueError('Export playback speed must be from 0.05x to 2.00x.')
        return speed

    def export_event_video(self):
        e=self.selected_event()
        if not e or self.busy:return
        try:speed=self.selected_export_speed()
        except ValueError as exc:
            messagebox.showerror('Export event video',str(exc));return
        match=next((item for item in e.get('exports',[]) if
                    abs(float(item.get('playback_speed',1))-speed)<1e-9),None)
        if not match and abs(float(e.get('export_playback_speed',1))-speed)<1e-9 and e.get('closeup_clip'):
            match={'closeup_clip':e['closeup_clip'],'clip':e.get('clip','')}
        existing=self.report_dir/match.get('closeup_clip','') if match else None
        if existing and existing.is_file():
            e['closeup_clip']=match['closeup_clip'];e['clip']=match.get('clip','')
            self.status.set(f"{e['id']} already has a {speed:g}x export. Use Play event clip to open it.")
            return
        self.pause()
        cfg=copy.deepcopy(self.report['config'])
        cfg['export_clips']=True
        cfg['export_playback_speed']=speed
        region=e.get('peak_region')
        if self.area_timeline and e['peak_frame']<len(self.area_timeline):
            region=next((r for r in self.area_timeline[e['peak_frame']]['regions'] if r['name']==e['region']),region)
        if region is None:
            region=next((r for r in cfg['regions'] if r['name']==e['region']),None)
        if region is None:
            messagebox.showerror('Export event video','The saved detection area is unavailable for this event.')
            return
        try:
            self.status.set(f"Exporting {e['id']} video at {speed:g}x…")
            self.root.update_idletasks()
            box=pixel_box(region,self.meta['width'],self.meta['height'])
            cap,meta=open_video(self.current)
            try:export_event(cap,meta,e,box,self.report_dir/'clips',cfg,None,timeline=self.area_timeline)
            finally:cap.release()
            self.persist(e)
            self.status.set(f"{e['id']} full and close-up videos exported at {speed:g}x. Use Play event clip to open the close-up.")
        except Exception as exc:messagebox.showerror('Export event video',str(exc))

    def add_manual(self):
        if not self.report or self.busy:
            self.status.set('Analyze this video first, then add a missed event at the current frame.')
            return
        self.pause()
        name=self.region.get()
        cfg=self.report['config']
        region=next(r for r in cfg['regions'] if r['name']==name)
        if self.area_timeline:
            region=next((r for r in self.area_timeline[self.index]['regions'] if r['name']==name),None)
            if region is None:
                self.status.set('No reliable saved area here. Review this interval manually; automatic crop is unavailable.')
                return
        box=pixel_box(region,self.meta['width'],self.meta['height'])
        index=self.index
        n=1
        ids={e['id'] for e in self.report['events']}
        while f'M{n:03d}' in ids:n+=1
        e=dict(id=f'M{n:03d}',region=name,start_frame=index,last_frame=index,peak_frame=index,
               start_seconds=round(index/self.meta['fps'],4),end_seconds=round((index+1)/self.meta['fps'],4),
               peak_seconds=round(index/self.meta['fps'],4),peak_score=0,positive_frames=1,max_components=0,
               bbox=[0,0,box[2]-box[0],box[3]-box[1]],peak_transform=[[1,0,0],[0,1,0]],
               status='accepted',confirmed_drips=None,notes='Manually added during review.',kind='candidate_drip_event')
        if self.area_timeline:
            e['peak_region']=copy.deepcopy(region)
            e['peak_source_bbox']=list(box)
        self.report['events'].append(e)
        self.report['events'].sort(key=lambda item:item['start_frame'])
        self.persist(e)
        self.status.set('Missed event added. Select it and choose Export event video only if a clip is needed.')

    def load_result_dialog(self):
        path=filedialog.askopenfilename(initialdir=ROOT/'output',filetypes=[('Results JSON','results.json')])
        if path:self.load_result(path)

    def load_result(self,path):
        try:
            path=Path(path)
            report=json.loads(path.read_text(encoding='utf-8'))
            if report['state']!='complete':raise ValueError('This analysis did not complete. Re-run it to obtain complete results.')
            self.add_files([report['source']])
            self.load_video(report['source'],False)
            self.library.selection_clear(0,'end')
            self.library.selection_set(self.files.index(Path(report['source']).resolve()))
            self.set_report(path.parent,report)
        except Exception as exc:messagebox.showerror('Results',str(exc))

    def open_output(self):
        path=self.report_dir or ROOT/'output'
        path.mkdir(exist_ok=True)
        os.startfile(str(path))

    def close(self):
        self.cancel.set()
        self.run_gate.set()
        self.pause()
        if self.cap:self.cap.release()
        self.root.destroy()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    root=tk.Tk()
    app=App(root)
    if args.report:root.after(200,lambda:app.load_result(args.report))
    root.mainloop()

if __name__=='__main__':main()
