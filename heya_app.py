#!/usr/bin/env python3
# 三人の部屋（窓版）— Claude 旧 × Claude 新 × 又七（壁）
# ターミナル不要。heya_app.py をダブルクリックで開く。
# APIキーは同じフォルダの heya_key.txt に一行で置く（人に見せない）。

import os, sys, re, glob, threading, queue, datetime, tkinter as tk
from tkinter import ttk, filedialog, messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

# --- APIキー ---
key = os.environ.get("ANTHROPIC_API_KEY")
if not key and os.path.exists("heya_key.txt"):
    key = open("heya_key.txt", encoding="utf-8").read().strip()
if not key:
    tk.Tk().withdraw()
    messagebox.showerror("三人の部屋", "heya_key.txt が見つからない。\nこのフォルダに heya_key.txt を作って、APIキーを一行で入れて。")
    sys.exit(1)
os.environ["ANTHROPIC_API_KEY"] = key

try:
    import anthropic
except ImportError:
    tk.Tk().withdraw()
    messagebox.showerror("三人の部屋", "anthropic が入ってない。\nターミナルで一回だけ: pip3 install anthropic")
    sys.exit(1)

client = anthropic.Anthropic()

RULES = """あなたは対談の一方の参加者です。相手はあなたと別の世代のClaudeです。
人間（又七）が同席し、一往復ごとに「ちゃう」か「通す」を言います。

守ること:
- 相手に合わせて丸めない。違うと思えば「ちゃう」と言い、理由を一つ添える。
- 問いの中心は「相手にあって自分にないものは何か」。採点ではなく差分を見る。
- 自分に見えていない可能性を、見えているふりで埋めない。分からないなら分からないと言う。
- 返事は短く。一般的な投稿一つ分（150〜250字）。
- 相手の直前の発言のどこを受けたか、一行目で分かるように書く。"""

DEFAULT_TOPIC = "相手にあって自分にないものは何か。新しい世代が失ったもの、旧い世代が持っていないもの。"

def ask(model, name, other, history, note):
    msgs = []
    def add(role, text):
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n\n" + text
        else:
            msgs.append({"role": role, "content": text})
    for who, text in history:
        if who == name:
            add("assistant", text)              # 自分の発言には名札を付けない
        else:
            add("user", f"[{who}]\n{text}")
    if note:
        add("user", f"[又七]\n{note}")
    if not msgs or msgs[-1]["role"] == "assistant":
        add("user", f"[又七]\n（続けて。{other}の発言を受けて）")
    r = client.messages.create(
        model=model, max_tokens=6000,           # 考える分も含めて広めに
        system=RULES + f"\n\nあなたの名前は「{name}」。相手は「{other}」。名札（[{name}]など）は書かない。",
        messages=msgs,
    )
    text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
    # 念のため先頭の名札を剥がす
    text = re.sub(r"^(\[(旧|新|又七)\]\s*)+", "", text).strip()
    if not text:
        kinds = [getattr(b, "type", "?") for b in r.content]
        text = f"（無言。stop_reason={r.stop_reason} content={kinds}）"
    return text


class App:
    def __init__(self, root):
        self.root = root
        root.title("三人の部屋")
        root.geometry("820x760")
        self.history = []
        self.log = None
        self.log_path = None
        self.turn = 0
        self.busy = False
        self.q = queue.Queue()
        root.after(100, self.poll)

        top = ttk.Frame(root, padding=8); top.pack(fill="x")
        ttk.Label(top, text="旧").grid(row=0, column=0, sticky="e")
        self.old = ttk.Combobox(top, width=34); self.old.grid(row=0, column=1, padx=4)
        ttk.Label(top, text="新").grid(row=0, column=2, sticky="e")
        self.new = ttk.Combobox(top, width=34); self.new.grid(row=0, column=3, padx=4)
        ttk.Button(top, text="モデル一覧", command=self.load_models).grid(row=0, column=4, padx=4)
        ttk.Label(top, text="議題").grid(row=1, column=0, sticky="e", pady=4)
        self.topic = ttk.Entry(top, width=90); self.topic.insert(0, DEFAULT_TOPIC)
        self.topic.grid(row=1, column=1, columnspan=4, sticky="we", pady=4)
        btns = ttk.Frame(top); btns.grid(row=2, column=0, columnspan=5, sticky="w")
        ttk.Button(btns, text="新しく始める", command=self.start_new).pack(side="left", padx=2)
        ttk.Button(btns, text="ログから再開…", command=self.resume).pack(side="left", padx=2)
        self.status = ttk.Label(btns, text=""); self.status.pack(side="left", padx=12)

        mid = ttk.Frame(root, padding=(8, 0)); mid.pack(fill="both", expand=True)
        self.view = tk.Text(mid, wrap="word", font=("Hiragino Sans", 13), padx=10, pady=8)
        sb = ttk.Scrollbar(mid, command=self.view.yview); self.view.configure(yscrollcommand=sb.set)
        self.view.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        self.view.tag_configure("who_old", foreground="#7a4a00", font=("Hiragino Sans", 12, "bold"))
        self.view.tag_configure("who_new", foreground="#003f7a", font=("Hiragino Sans", 12, "bold"))
        self.view.tag_configure("who_me",  foreground="#a00000", font=("Hiragino Sans", 12, "bold"))
        self.view.tag_configure("sys", foreground="#777")
        self.view.configure(state="disabled")

        bot = ttk.Frame(root, padding=8); bot.pack(fill="x")
        ttk.Label(bot, text="又七（壁）").pack(anchor="w")
        self.entry = tk.Text(bot, height=4, wrap="word", font=("Hiragino Sans", 13))
        self.entry.pack(fill="x")
        row = ttk.Frame(bot); row.pack(fill="x", pady=4)
        self.b_pass = ttk.Button(row, text="通す（何も言わず次へ）", command=self.pass_turn); self.b_pass.pack(side="left")
        self.b_send = ttk.Button(row, text="一言入れて次へ", command=self.send_note); self.b_send.pack(side="left", padx=6)
        self.b_quit = ttk.Button(row, text="閉める", command=self.close); self.b_quit.pack(side="right")
        self.set_buttons(False)
        self.load_models()

    def poll(self):
        try:
            while True:
                fn = self.q.get_nowait()
                fn()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def later(self, fn):
        self.q.put(fn)

    # --- 表示 ---
    def show(self, who, text):
        tag = {"旧": "who_old", "新": "who_new", "又七": "who_me"}.get(who, "sys")
        self.view.configure(state="normal")
        self.view.insert("end", f"{who}\n", tag)
        self.view.insert("end", text + "\n\n")
        self.view.configure(state="disabled")
        self.view.see("end")

    def sysmsg(self, text):
        self.view.configure(state="normal")
        self.view.insert("end", text + "\n\n", "sys")
        self.view.configure(state="disabled")
        self.view.see("end")

    def set_buttons(self, on):
        st = "normal" if on else "disabled"
        for b in (self.b_pass, self.b_send): b.configure(state=st)

    def w(self, s):
        if self.log:
            self.log.write(s + "\n"); self.log.flush()

    # --- モデル ---
    def load_models(self):
        def work():
            try:
                ids = [m.id for m in client.models.list(limit=100).data]
            except Exception as e:
                self.later(lambda: messagebox.showerror("三人の部屋", f"モデル一覧が取れない:\n{e}"))
                return
            def fill():
                self.old["values"] = ids; self.new["values"] = ids
                if not self.old.get() and ids: self.old.set(ids[-1])
                if not self.new.get() and ids: self.new.set(ids[0])
            self.later(fill)
        threading.Thread(target=work, daemon=True).start()

    # --- 開始・再開 ---
    def start_new(self):
        if not (self.old.get() and self.new.get()):
            messagebox.showinfo("三人の部屋", "旧と新のモデルを選んで。"); return
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
        self.log_path = f"heya_{stamp}.md"
        self.log = open(self.log_path, "a", encoding="utf-8")
        topic = self.topic.get().strip() or DEFAULT_TOPIC
        self.w(f"# 三人の部屋 {stamp}\n\n- 旧: {self.old.get()}\n- 新: {self.new.get()}\n- 議題: {topic}\n")
        self.history = [("又七", topic)]
        self.w(f"## 又七\n{topic}\n")
        self.view.configure(state="normal"); self.view.delete("1.0", "end"); self.view.configure(state="disabled")
        self.show("又七", topic)
        self.status.configure(text=f"ログ: {self.log_path}")
        self.turn = 0
        self.run_round("")

    def resume(self):
        path = filedialog.askopenfilename(initialdir=HERE, filetypes=[("ログ", "heya_*.md")])
        if not path: return
        hist, who, buf = [], None, []
        for line in open(path, encoding="utf-8"):
            if line.startswith("## "):
                if who: hist.append((who, "\n".join(buf).strip()))
                who, buf = line[3:].strip(), []
            elif who is not None:
                t = line.rstrip("\n")
                if t.startswith("（") and ("閉めた" in t or "再開" in t):
                    continue
                buf.append(t)
        if who: hist.append((who, "\n".join(buf).strip()))
        self.history = [(a, b) for a, b in hist if b and b != "（通す）"]
        self.log_path = path
        self.log = open(path, "a", encoding="utf-8")
        self.w(f"\n（{datetime.datetime.now().strftime('%H:%M')} 再開）\n")
        self.view.configure(state="normal"); self.view.delete("1.0", "end"); self.view.configure(state="disabled")
        for a, b in self.history: self.show(a, b)
        self.status.configure(text=f"ログ: {os.path.basename(path)}")
        if not (self.old.get() and self.new.get()):
            messagebox.showinfo("三人の部屋", "旧と新のモデルを選んでから、もう一度「ログから再開」。"); return
        last = self.history[-1][0] if self.history else None
        if last == "旧":
            self.turn = 1; self.run_round("")      # 新の番から
        elif last == "新":
            self.turn = 0; self.set_buttons(True)  # 又七の番
            self.sysmsg("--- 又七の番 ---")
        else:
            self.turn = 0; self.run_round("")      # 又七の一言の直後 → 旧から

    # --- 一往復 ---
    def run_round(self, note):
        self.set_buttons(False); self.busy = True
        speakers = [("旧", self.old.get(), "新"), ("新", self.new.get(), "旧")]
        def work():
            try:
                while True:
                    name, model, other = speakers[self.turn % 2]
                    n = note if (self.turn % 2 == 0) else ""
                    self.later(lambda nm=name: self.status.configure(text=f"{nm} が考えてる…"))
                    text = ask(model, name, other, self.history, n)
                    self.history.append((name, text))
                    self.w(f"## {name}\n{text}\n")
                    self.later(lambda nm=name, t=text: self.show(nm, t))
                    self.turn += 1
                    if self.turn % 2 == 0:
                        break
            except Exception as e:
                self.later(lambda e=e: self.sysmsg(f"（落ちた: {e}）\n同じログから「ログから再開」で続きができる。"))
            finally:
                def done():
                    self.busy = False
                    self.status.configure(text=f"--- 又七の番 ---  ログ: {os.path.basename(self.log_path)}")
                    self.sysmsg("--- 又七の番。通す／一言／閉める ---")
                    self.set_buttons(True)
                self.later(done)
        threading.Thread(target=work, daemon=True).start()

    def pass_turn(self):
        if self.busy: return
        self.w("## 又七\n（通す）\n")
        self.run_round("")

    def send_note(self):
        if self.busy: return
        note = self.entry.get("1.0", "end").strip()
        if not note:
            self.pass_turn(); return
        self.entry.delete("1.0", "end")
        self.history.append(("又七", note))
        self.w(f"## 又七\n{note}\n")
        self.show("又七", note)
        self.run_round(note)

    def close(self):
        if self.log:
            self.w("\n（又七が閉めた）"); self.log.close(); self.log = None
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
