"""The desktop window.

One window: the free models, the state of each one, and a row of buttons.
Everything the command line can do is reachable from here.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from . import ccswitch, ccswitch_writer, keyring, launcher, openrouter, snapshot

APP = "codex"

ROW_TAGS = {
    "ok": "#eaf7ee",
    "bad": "#fdecec",
    "new": "#fff8e1",
}


def _price(value: float) -> str:
    """A price per million tokens."""
    if value == 0:
        return "免费"
    return "$%.2f/M" % value


def _size(value: int) -> str:
    """A context window in human units."""
    if value >= 1_000_000:
        return "%.1fM" % (value / 1_000_000)
    if value >= 1000:
        return "%dK" % (value // 1000)
    return str(value)


def _api_key() -> str:
    """The key the user typed into this program. Never taken from another app."""
    key = keyring.load_key()
    if not key:
        raise RuntimeError(
            "还没有填 OpenRouter API Key。请点窗口右上角的「API Key」填入你自己的 Key。"
        )
    return key


class OrFreeApp:
    """The main window."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.queue: queue.Queue = queue.Queue()
        self.models: list[Any] = []
        self.checked: set[str] = set()
        self.probe_results: dict[str, Any] = {}
        self.new_models: set[str] = set()
        self.card_name = "OpenRouter 免费模型 %s" % snapshot.today()
        self.view_mode: str = "free"

        root.title("OR 免费模型")
        root.geometry("940x660")
        root.minsize(820, 560)
        self._build()
        if not keyring.load_key():
            # First launch: ask for the user's own key before any network call.
            self._ask_api_key(first_run=True)
        self.root.after(120, self._drain)
        self.root.after(400, self.refresh_async)

    # END_INIT

    def _build(self) -> None:
        """Lay the window out."""
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(3, weight=1)

        top = ttk.Frame(self.root, padding=(10, 8))
        top.grid(row=0, column=0, sticky="ew")
        title = ttk.Label(top, text="OR 免费模型")
        title.font = ("Microsoft YaHei UI", 15, "bold")
        title.pack(side="left")
        self.status = ttk.Label(
            top, text="正在加载…" if keyring.load_key() else "未设置 API Key"
        )
        self.status.pack(side="right")

        bar = ttk.Frame(self.root, padding=(10, 0))
        bar.grid(row=1, column=0, sticky="ew")
        ttk.Button(bar, text="免费模型", command=lambda: self.switch_tab("free")).pack(side="left")
        ttk.Button(bar, text="付费模型", command=lambda: self.switch_tab("paid")).pack(side="left", padx=4)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(bar, text="刷新", command=self.refresh_async).pack(side="left")
        ttk.Button(bar, text="测可用性", command=self.probe_async).pack(
            side="left", padx=4
        )
        ttk.Button(bar, text="导入并启用", command=self.import_async).pack(side="left")
        ttk.Button(bar, text="API Key", command=self._ask_api_key).pack(
            side="right", padx=4
        )
        ttk.Button(bar, text="打开 CC Switch", command=self.open_cc_switch).pack(
            side="right"
        )
        ttk.Button(bar, text="打开 Codex", command=self.open_codex).pack(
            side="right", padx=4
        )

        hint = ttk.Label(
            self.root,
            text=(
                "勾选要用的模型，然后点「导入并启用」。推荐：可用+推理+工具+上下文≥128K=强烈推荐；"
                " 可用+推理+工具=推荐； 可用+推理=适合推理； 仅可用=可用。已有的卡片不会被修改。"
            ),
        )
        hint.grid(row=2, column=0, sticky="ew", padx=10, pady=(6, 0))

        holder = ttk.Frame(self.root, padding=(10, 0))
        holder.grid(row=3, column=0, sticky="nsew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)

        columns = ("pick", "model", "recommend", "ctx", "in", "out", "rsn", "tools", "state")
        tree = ttk.Treeview(
            holder,
            columns=columns,
            show="headings",
            selectmode="extended",
            height=14,
        )
        headings = {
            "pick": ("✓", 34),
            "model": ("模型", 300),
            "recommend": ("推荐", 110),
            "ctx": ("上下文", 78),
            "in": ("输入价", 82),
            "out": ("输出价", 82),
            "rsn": ("推理", 48),
            "tools": ("工具", 48),
            "state": ("状态", 150),
        }
        for key, (text, width) in headings.items():
            tree.heading(key, text=text)
            tree.column(key, width=width, anchor="w")
        tree.tag_configure("ok", background=ROW_TAGS["ok"])
        tree.tag_configure("bad", background=ROW_TAGS["bad"])
        tree.tag_configure("new", background=ROW_TAGS["new"])
        tree.tag_configure("rec_strong", background="#d4f0d4")
        tree.tag_configure("rec_good", background="#e4f0e4")
        tree.tag_configure("rec_reasoning", background="#e4f0ff")
        tree.tag_configure("rec_ok", background="#ffffff")
        tree.tag_configure("rec_bad", background="#fdecec")
        tree.tag_configure("rec_untested", background="#f0f0f0")
        tree.bind("<Button-1>", self._on_click)
        tree.bind("<Double-1>", self._toggle)

        scroll = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")
        tree.grid(row=0, column=0, sticky="nsew")
        self.tree = tree

        bottom = ttk.Frame(self.root, padding=(10, 8))
        bottom.grid(row=4, column=0, sticky="ew")
        self.progress = ttk.Progressbar(bottom, mode="indeterminate", length=140)
        self.progress.pack(side="left")
        self.message = ttk.Label(bottom, text="")
        self.message.pack(side="left", padx=10)
        ttk.Button(bottom, text="回滚", command=self.restore).pack(side="right")
        ttk.Button(bottom, text="全部勾选", command=self.select_all).pack(
            side="right", padx=4
        )
        ttk.Button(bottom, text="全不选", command=self.select_none).pack(side="right")

    # END_BUILD

    def _ask_api_key(self, first_run: bool = False) -> None:
        """Ask for the user's own OpenRouter key and keep it on this machine."""
        win = tk.Toplevel(self.root)
        win.title("填入你的 OpenRouter API Key")
        win.transient(self.root)
        win.resizable(False, False)
        win.update_idletasks()
        try:
            win.grab_set()  # modal; some window managers refuse, that is fine
        except tk.TclError:
            pass

        frame = ttk.Frame(win, padding=16)
        frame.pack(fill="both", expand=True)
        heading = (
            "首次使用：填入你自己的 OpenRouter API Key"
            if first_run
            else "OpenRouter API Key"
        )
        ttk.Label(frame, text=heading).pack(anchor="w")
        ttk.Label(
            frame,
            text=(
                "本工具只支持 OpenRouter，不含任何内置 Key。\n"
                "请到 https://openrouter.ai/keys 免费申请一个。"
            ),
        ).pack(anchor="w", pady=(8, 0))
        ttk.Label(
            frame,
            text="Key 只保存在本机 %s，不会上传、不会写进仓库。"
            % keyring.config_path(),
            wraplength=520,
        ).pack(anchor="w", pady=(8, 0))

        var = tk.StringVar(value=keyring.load_key() or "")
        entry = ttk.Entry(frame, textvariable=var, show="*", width=62)
        entry.pack(fill="x", pady=(12, 0))
        entry.focus_set()

        note = ttk.Label(frame, text="")
        note.pack(anchor="w", pady=(6, 0))
        current = keyring.load_key()
        if current:
            note.configure(text="当前已保存：%s" % keyring.mask(current))

        def save() -> None:
            key = var.get().strip()
            if not key:
                note.configure(text="Key 不能为空。")
                return
            if not key.startswith("sk-or-") and not messagebox.askyesno(
                "格式提示",
                "OpenRouter 的 Key 通常以 sk-or- 开头，确定要保存吗？",
                parent=win,
            ):
                return
            try:
                keyring.save_key(key)
            except OSError as error:
                messagebox.showerror("保存失败", str(error), parent=win)
                return
            self.message.configure(text="API Key 已保存在本机")
            win.destroy()
            if not first_run:
                self.refresh_async()

        def clear() -> None:
            keyring.clear_key()
            var.set("")
            note.configure(text="已清除本机保存的 Key。")
            self.message.configure(text="API Key 已清除")

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text="清除", command=clear).pack(side="left")
        ttk.Button(buttons, text="取消", command=win.destroy).pack(side="right")
        ttk.Button(buttons, text="保存", command=save).pack(side="right", padx=4)
        entry.bind("<Return>", lambda _event: save())
        self.root.wait_window(win)

    def _recommend(self, model: Any, result: Any) -> str:
        """按模型名称和常见定位给出的能力等级。

        OpenRouter 不直接给智商评分，这里只根据模型 ID 里的命名惯例
        做粗略分级。上下文长短、是否支持工具都不算入聪明度。
        """
        model_id = (model.id or "").lower()
        name = (model.name or "").lower()
        text = model_id + " " + name

        # 推理模型（专门做逻辑推理的）
        is_reasoning = model.supports_reasoning or any(
            k in text for k in ("r1", "o1", "o3", "reasoning", "think")
        )

        # 顶尖：通常被公认为最强的系列
        top_tier = any(
            k in text
            for k in (
                "opus",
                "claude-3.5",
                "claude-4",
                "gpt-4.5",
                "gpt-5",
                "o1",
                "o3",
                "gemini-2.5",
                "deepseek-v3",
                "deepseek-r1",
                "qwen-2.5-max",
                "qwen3-",
                "nemotron-253b",
                "llama-3.3-70b",
                "llama-4",
                "gpt-4o",
                "gpt-4-0",
                "gemini-2-0",
                "gemini-2.0",
            )
        )

        # 强：次梯队的强模型
        strong = any(
            k in text
            for k in (
                "sonnet",
                "claude-3",
                "gpt-4",
                "gemini-2",
                "deepseek-chat",
                "qwen-2.5-72b",
                "qwen-2.5-32b",
                "llama-3.1-70b",
                "llama-3.2-90b",
                "nemotron-70b",
                "gpt-4o",
                "gpt-4-0",
                "gemini-2-0",
                "gemini-2.0",
            )
        )

        # 中等：通用中型模型
        medium = any(
            k in text
            for k in (
                "llama-3.1-8b",
                "llama-3.2-11b",
                "gemma-2-27b",
                "qwen-2.5-14b",
                "command-r+",
                "mistral-large",
            )
        )

        # 轻量：明确标小的模型
        light = any(
            k in text
            for k in (
                "mini",
                "flash",
                "nano",
                "lite",
                "small",
                "8b",
                "7b",
                "3b",
                "1b",
                "gemma-2-9b",
                "gemma-2-2b",
                "llama-3.2-3b",
                "llama-3.2-1b",
            )
        )

        if result is None:
            return "等待检测"
        if not result.ok:
            return "不可用"
        if top_tier:
            return "顶尖"
        if strong:
            return "强"
        if medium:
            return "中等"
        if light:
            return "基础"
        if is_reasoning:
            return "推理型"
        return "标准"

    def _on_click(self, event: Any) -> None:
        """Toggle the checkbox when the first column is clicked."""
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return
        row = self.tree.identify_row(event.y)
        if row:
            self._toggle_row(row)

    def _toggle(self, event: Any) -> None:
        """Toggle the row that was double clicked."""
        row = self.tree.identify_row(event.y)
        if row:
            self._toggle_row(row)

    def _toggle_row(self, row: str) -> None:
        """Flip one row's checkbox."""
        model_id = self.tree.item(row, "values")[1]
        if model_id in self.checked:
            self.checked.discard(model_id)
            self.tree.set(row, "pick", "")
        else:
            self.checked.add(model_id)
            self.tree.set(row, "pick", "✓")

    def select_all(self) -> None:
        """Check every model."""
        self.checked = {model.id for model in self.models}
        self.refresh_rows()

    def select_none(self) -> None:
        """Clear the selection."""
        self.checked = set()
        self.refresh_rows()

    def refresh_rows(self) -> None:
        """Redraw the table."""
        for row in self.tree.get_children():
            self.tree.delete(row)
        for model in self.models:
            tags = []
            result = self.probe_results.get(model.id)
            state = "检测中..."
            recommend = self._recommend(model, result)
            if result is not None:
                state = "可用" if result.ok else result.reason
            elif model.id in self.new_models:
                state = "今日新增"
            rec_tags = {
                "顶尖": "rec_strong",
                "强": "rec_good",
                "中等": "rec_reasoning",
                "基础": "rec_ok",
                "推理型": "rec_ok",
                "标准": "rec_ok",
                "不可用": "rec_bad",
                "等待检测": "rec_untested",
            }
            tags.append(rec_tags[recommend])
            self.tree.insert(
                "",
                "end",
                iid=model.id,
                values=(
                    "✓" if model.id in self.checked else "",
                    model.id,
                    recommend,
                    _size(model.context_length),
                    _price(model.prompt_price),
                    _price(model.completion_price),
                    "✓" if model.supports_reasoning else "",
                    "✓" if model.supports_tools else "",
                    state,
                ),
                tags=tuple(tags),
            )

    # END_ROWS

    def _run(self, work: Any, done: Any) -> None:
        """Run work off the UI thread and report back.

        ``work`` returns a pair: the value for the callback and the status
        text to show.  A ``_busy`` lock prevents refresh/probe from being
        triggered again while one is already running, which used to clear
        the table mid-probe.
        """
        if getattr(self, "_busy", False):
            messagebox.showinfo("正在操作", "正在获取或检测模型，请稍候再试。")
            return
        self._busy = True
        self.progress.start(10)

        def worker() -> None:
            try:
                result = work()
            except Exception as error:  # noqa: BLE001 - shown to the user
                self.queue.put(("error", str(error)))
            else:
                self.queue.put(("done", (result, done)))
            finally:
                self._busy = False

        threading.Thread(target=worker, daemon=True).start()

    def _drain(self) -> None:
        """Handle messages from the worker threads."""
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "error":
                    self.progress.stop()
                    self.message.configure(text=payload)
                    messagebox.showerror("出错了", payload)
                elif kind == "done":
                    self.progress.stop()
                    (value, text), done = payload
                    self.message.configure(text=text)
                    done(value)
                elif kind == "probe_one":
                    model_id, result = payload
                    self.probe_results[model_id] = result
                    self._update_one_row(model_id, result)
                elif kind == "probe_done":
                    self.progress.stop()
                    self.message.configure(text=payload)
        except queue.Empty:
            pass
        self.root.after(120, self._drain)

    def refresh_async(self) -> None:
        """Fetch the catalogue."""
        if not keyring.load_key():
            self.message.configure(text="请先点「API Key」填入你自己的 OpenRouter Key")
            return
        self.message.configure(text="正在获取模型列表…")

        def work() -> Any:
            key = _api_key()
            models = openrouter.fetch_models(api_key=key, with_endpoints=False)
            return (models, snapshot.just_added(models)), "已加载 %d 个模型" % len(models)

        self._run(work, self._refreshed)

    def _refreshed(self, payload: Any) -> None:
        """Show the fetched models."""
        models, fresh = payload
        if self.view_mode == "paid":
            self.models = [model for model in models if not model.free]
            label = "付费"
        else:
            self.models = [model for model in models if model.free]
            label = "免费"
        self.new_models = fresh
        self.checked = set()
        self.refresh_rows()
        info = "已加载 %d 个%s模型" % (len(self.models), label)
        if fresh:
            info += "（%d 个今日新增）" % len(fresh)
        self.message.configure(text=info)
        self.status.configure(text=self._route_text())
        # 打开后自动检测所有模型的可用性，无需手动点「测可用性」
        self.probe_async()

    def switch_tab(self, mode: str) -> None:
        """切换免费/付费视图并重新加载列表。"""
        self.view_mode = mode
        self.refresh_async()

    # END_ASYNC

    def probe_async(self) -> None:
        """Probe all currently shown models for reachability, one by one."""
        targets = list(self.models)
        if not targets:
            messagebox.showinfo("没有模型", "列表里没有模型可检测。")
            return
        for model in targets:
            if self.tree.exists(model.id):
                self.tree.set(model.id, "state", "检测中…")
                self.tree.item(model.id, tags=("rec_untested",))
        self.message.configure(text="正在检测 %d 个模型…" % len(targets))
        self.progress.start(10)
        self._busy = True

        def worker() -> None:
            try:
                key = _api_key()
                working = 0
                for model in targets:
                    result = openrouter.probe_model(model.id, key)
                    self.queue.put(("probe_one", (model.id, result)))
                    if result.ok:
                        working += 1
                self.queue.put(("probe_done", "检测完成 %d/%d 可用" % (working, len(targets))))
            except Exception as error:
                self.queue.put(("error", str(error)))
            finally:
                self._busy = False

        threading.Thread(target=worker, daemon=True).start()

    def _update_one_row(self, model_id: str, result: Any) -> None:
        """Update a single row's state and tag without redrawing the whole table."""
        if not self.tree.exists(model_id):
            return
        model = next((m for m in self.models if m.id == model_id), None)
        if model is None:
            return
        state = "可用" if result.ok else result.reason
        self.tree.set(model_id, "state", state)
        recommend = self._recommend(model, result)
        rec_tags = {
            "顶尖": "rec_strong",
            "强": "rec_good",
            "中等": "rec_reasoning",
            "基础": "rec_ok",
            "推理型": "rec_ok",
            "标准": "rec_ok",
            "不可用": "rec_bad",
            "未测试": "rec_untested",
        }
        self.tree.item(model_id, tags=(rec_tags.get(recommend, ""),))

    def import_async(self) -> None:
        """Create the card and route to it."""
        chosen = [model for model in self.models if model.id in self.checked]
        if not chosen:
            messagebox.showinfo("先选模型", "请先勾选要导入的模型。")
            return
        self.message.configure(text="正在导入 %d 个模型…" % len(chosen))

        def work() -> Any:
            key = _api_key()
            result = ccswitch_writer.create_provider(
                name=self.card_name,
                models=chosen,
                api_key=key,
                app=APP,
            )
            ccswitch_writer.route_to(result.provider_id, APP)
            return result, "已导入并启用"

        self._run(work, self._imported)

    def _imported(self, result: Any) -> None:
        """Report the import."""
        self.message.configure(text="已导入并启用")
        self.status.configure(text=self._route_text())
        messagebox.showinfo(
            "导入完成",
            "新卡片「%s」已创建并启用。\n\n"
            "Codex 需要重启才能在模型列表里看到新模型。\n\n"
            "备份：%s" % (self.card_name, result.backup_folder),
        )

    def restore(self) -> None:
        """Restore the newest backup."""
        if not messagebox.askyesno("回滚", "恢复到最近一次备份？"):
            return
        folder = ccswitch.latest_backup()
        if not folder:
            messagebox.showinfo("没有备份", "找不到备份。")
            return
        ccswitch.restore_backup(folder)
        self.message.configure(text="已回滚，请重启 CC Switch")
        self.status.configure(text=self._route_text())
        messagebox.showinfo("已回滚", "已恢复备份，重启 CC Switch 后生效。")

    def open_cc_switch(self) -> None:
        """Show CC Switch."""
        ok, message = launcher.open_cc_switch()
        if not ok:
            messagebox.showinfo("CC Switch", message)

    def open_codex(self) -> None:
        """Show Codex."""
        ok, message = launcher.open_codex()
        if not ok:
            messagebox.showinfo("Codex", message)

    def _route_text(self) -> str:
        """A short description of the current routing."""
        state = ccswitch.routing_for(APP)
        if not state.proxy_route:
            return "CC Switch 未在路由"
        row = ccswitch.provider_row(state.proxy_route, APP)
        name = row["name"] if row else state.proxy_route[:8]
        models = 0
        if row:
            models = len(
                row["settings_config"].get("modelCatalog", {}).get("models", [])
            )
        return "当前：%s（%d 个模型）" % (name, models)


def main() -> int:
    """Show the window."""
    root = tk.Tk()
    OrFreeApp(root)
    root.mainloop()
    return 0
