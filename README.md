# orfree — OpenRouter 免费模型挑选器

一个**只支持 [OpenRouter](https://openrouter.ai)** 的桌面小工具：拉取平台当天的模型目录，逐个发一条极小的真实请求测试哪些免费模型现在真的能回答，再把挑中的模型做成一张新的 CC Switch 卡片，并让 Codex 路由过去。原来的卡片不会被改动，随时可以回滚。

> **本仓库不含任何 API Key。** 第一次打开程序会弹窗让你填**你自己的** OpenRouter Key；程序不会去读别的软件里存的 Key，也不会用作者或任何第三方的额度。

---

## 先确认这三点

| | |
|---|---|
| 平台 | 只对接 `openrouter.ai/api/v1`，不是通用客户端，别的平台/中转用不了 |
| Key | 需要你自己在 <https://openrouter.ai/keys> 申请（`sk-or-` 开头）；免费模型不扣费，账号规则以 OpenRouter 为准 |
| 依赖 | Python 3.10+，**纯标准库**（tkinter / urllib / sqlite3），无需 `pip install`；「导入并启用」需要本机装有 CC Switch 与 Codex，只看列表和测可用性则不需要 |

本项目是个人开源工具，与 OpenRouter、CC Switch、Codex 官方均无关联。

## 快速开始

```bash
git clone https://github.com/sillre/orfree.git
cd orfree
python start_gui.py          # 或： python -m orfree --gui
```

> Windows 提示：某些精简版 / 嵌入式 Python 不带 `tkinter`，启动会报 `ModuleNotFoundError`。请用 Python 官方安装包（本机可用 `py -3.11 start_gui.py`），Linux 上则装 `python3-tk`。

首次启动会弹出输入框：

1. 粘贴你自己的 OpenRouter API Key → 点「保存」。
2. 程序自动拉取模型列表并逐个测可用性。
3. 勾选要用的模型 → 「导入并启用」→ 重启 Codex。
4. 想换 Key、清掉 Key：点窗口右上角「**API Key**」。

没填 Key 时程序不会发起任何请求。列表里标「今日新增」的是和昨天快照相比新出现的模型。

## API Key 存在哪 / 安全说明

- 保存在本机 **`~/.orfree/config.json`**（Windows 为 `C:\Users\<你>\.orfree\config.json`），在仓库目录之外，`.gitignore` 也做了兜底。这个文件**是明文**，别在公用电脑上用，别截图、别外传。
- 程序只把 Key 作为 `Authorization: Bearer` 发给 `openrouter.ai`；没有遥测、没有统计上报、没有别的服务器。
- 「导入并启用」会把 Key 写进本机 CC Switch 数据库，**该数据库的备份目录 `~/.cc-switch/backups/` 里也会包含它**——分享备份或日志前先检查。
- 命令行的优先级：`--api-key` > 环境变量 `OPENROUTER_API_KEY` > 上面那个本地文件。用 `--api-key` 会留在 shell 历史里，日常不推荐。
- ⚠️ 万一误把 Key 提交到 GitHub：立刻去 OpenRouter 控制台**撤销并重建**，删提交历史是没用的（会被爬走）。
- 命令行用户可以不装图形界面，用 `python -m orfree set-key` 交互输入（输入不回显）。

## 命令行

```bash
python -m orfree set-key                         # 首次：把 Key 存到本机
python -m orfree list                            # 列出模型、价格、上下文
python -m orfree list --snapshot                 # 顺手存一份当日快照，用于标记 NEW
python -m orfree probe                           # 逐个实测哪些模型现在能回答
python -m orfree import --route --probe --free-only
python -m orfree import --route --probe --model google/gemma-3-27b-it:free
python -m orfree status                          # 当前 CC Switch 路由到哪张卡
python -m orfree restore                         # 回滚到最近一次备份
python -m orfree daily                           # 每日刷新：加进新可用的、移除失效的
```

`--api-key` 是全局参数，要写在子命令**前面**：`python -m orfree --api-key sk-or-... list`。

`probe` 不是多余的：OpenRouter 上标着 `:free` 的模型常因上游共享池耗尽返回 `429 Too Many Requests`，加进配置也没用，得等池子补充。测过再导入才靠谱。如果你自己的 OpenRouter 账号绑定了 Google AI Studio 的 BYOK，可以走自己的额度，不受共享池影响。

## 打包成 exe（可选，不上传仓库）

```powershell
pip install pyinstaller
pyinstaller --onefile --windowed --name "OR免费模型" `
  --add-data "orfree/resources/gpt5_5_template.json;orfree/resources" start_gui.py
```

Windows 用 `;`，macOS / Linux 用 `:`。`dist/` 与 `*.exe` 已在 `.gitignore` 里，别把打包产物或本机路径写死进仓库。

## 文件布局

| 文件 | 作用 |
|---|---|
| `start_gui.py` | 图形界面入口（双击/命令行启动） |
| `orfree/gui.py` | 桌面窗口、首次填 Key 的对话框 |
| `orfree/keyring.py` | 在本机保存 / 读取使用者自己的 Key |
| `orfree/openrouter.py` | 拉模型目录、定价、健康探测 |
| `orfree/catalog.py` | 生成与 CC Switch 字节一致的 Codex 模型目录 |
| `orfree/ccswitch.py` | 读写 CC Switch 数据库、live-state、备份与回滚 |
| `orfree/ccswitch_writer.py` | 新建卡片、切换路由、写模型目录 |
| `orfree/snapshot.py` | 每日目录快照对比 |
| `orfree/daily.py` | 每日刷新任务 |
| `orfree/cli.py` | 命令行入口 |
| `orfree/launcher.py` | 打开 CC Switch / Codex（主要面向 Windows） |

## 改动配置前的保护

每次「导入并启用」都会把 CC Switch 数据库、live-state、Codex `config.toml` 和模型目录备份到 `~/.cc-switch/backups/` 下带时间戳的文件夹；新卡片是**复制**当前卡片后创建的，原卡片一行不动。`list`、`probe`、`status`、`snapshot` 都是只读操作。`restore` 一键还原。

## 测试

```bash
python tests/test_catalog_matches_live.py
python tests/test_import_sandbox.py
```

第一个把生成的模型目录与 CC Switch 实际写出的文件逐字节比对；第二个把 CC Switch / Codex 目录复制到临时文件夹里跑完整导入，不会碰真实配置（需要本机存在 `~/.cc-switch` 与 `~/.codex`）。

## 已知限制

- 只针对 OpenRouter；不支持其它平台的模型目录或计费规则。
- 免费模型可能连续几小时全部 429，这是上游配额问题，不是本工具的 bug。
- 「打开 CC Switch / 打开 Codex」按钮按 Windows 安装路径查找，其它系统可能找不到，不影响其余功能。

## Star History

<a href="https://star-history.com/#sillre/orfree&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=sillre/orfree&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=sillre/orfree&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=sillre/orfree&type=Date" />
  </picture>
</a>

## License

本仓库尚未指定许可证（默认「保留所有权利」）。若希望他人使用或二次分发，请补一个 `LICENSE`（MIT / Apache-2.0 都可以），并在 GitHub 仓库设置里选好。
