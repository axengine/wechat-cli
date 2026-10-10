# AGENTS.md

> 本文件为 AI 编码助手提供项目上下文，帮助快速理解代码结构、开发规范与关键流程。

---

## 1. 项目概览

**WeChat CLI** 是一个本地微信数据查询命令行工具，专为 LLM / AI Agent 设计。

- **核心能力**: 读取本地 SQLCipher 加密的微信数据库，提取密钥后透明解密，提供结构化 JSON 查询接口
- **语言**: Python 3.10+
- **入口**: `wechat-cli`（由 `pyproject.toml` 注册，指向 `wechat_cli.main:cli`）
- **分发方式**:
  - npm (`@canghe_ai/wechat-cli`) — 打包为平台独立二进制（PyInstaller）
  - pip (`wechat-cli`) — 纯 Python 安装

---

## 2. 项目结构

```
wechat-cli/
├── entry.py                  # PyInstaller 入口（避免相对导入问题）
├── pyproject.toml            # Python 包配置与依赖声明
├── wechat_cli/               # 主包
│   ├── main.py               # CLI 入口（click 命令组，注册所有子命令）
│   ├── bin/                  # 平台相关 C 二进制（macOS 密钥扫描）
│   ├── commands/             # 11 个子命令实现
│   │   ├── init.py           # 初始化：提取密钥 + 生成配置
│   │   ├── sessions.py       # 最近会话列表
│   │   ├── history.py        # 聊天历史记录
│   │   ├── search.py         # 消息搜索（单聊/多聊/全局）
│   │   ├── contacts.py       # 联系人搜索/详情
│   │   ├── members.py        # 群成员列表
│   │   ├── export.py         # 导出聊天记录（markdown/txt）
│   │   ├── stats.py          # 聊天统计（类型分布、发言排行、24小时活跃）
│   │   ├── unread.py         # 未读会话
│   │   ├── new_messages.py   # 增量新消息（状态持久化）
│   │   └── favorites.py      # 微信收藏
│   ├── core/                 # 核心逻辑层
│   │   ├── context.py        # AppContext — 单例持有配置、缓存、密钥
│   │   ├── config.py         # 配置加载与自动检测（~/.wechat-cli/）
│   │   ├── crypto.py         # SQLCipher 4 解密（AES-256-CBC，含 WAL 处理）
│   │   ├── db_cache.py       # 解密数据库缓存（mtime 检测，跨会话复用）
│   │   ├── key_utils.py      # 密钥路径匹配与元数据剥离
│   │   ├── contacts.py       # 联系人加载、缓存、模糊匹配
│   │   └── messages.py       # 消息查询核心（分表查找、分页、格式化、统计）
│   ├── keys/                 # 密钥提取（平台相关）
│   │   ├── common.py         # 共享逻辑：HMAC 验证、DB 收集、hex 扫描、结果保存
│   │   ├── scanner_macos.py  # macOS：调用 C 二进制扫描进程内存
│   │   ├── scanner_windows.py# Windows：ctypes + ReadProcessMemory
│   │   └── scanner_linux.py  # Linux：/proc/pid/mem 读取
│   └── output/
│       └── formatter.py      # 输出格式化（JSON / Text）
├── npm/                      # npm 分发相关
│   ├── scripts/build.py      # PyInstaller 多平台打包脚本
│   ├── platforms/            # 各平台二进制输出目录
│   └── wechat-cli/           # npm 主包（install.js 处理平台二进制链接）
├── image/                    # README 截图
├── README.md                 # 英文文档
└── README_CN.md              # 中文文档
```

---

## 3. 核心架构与数据流

### 3.1 初始化流程 (`init`)

1. 自动检测微信数据目录（`db_storage`）
   - macOS: `~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/*/db_storage`
   - Windows: `%APPDATA%/Tencent/xwechat/config/*.ini` 解析
   - Linux: `~/Documents/xwechat_files/*/db_storage`
2. 按平台调用对应密钥扫描器：
   - **macOS**: 执行 `wechat_cli/bin/find_all_keys_macos.{arch}`（C 二进制，扫描 WeChat 进程内存）
   - **Windows**: `ctypes` 调用 `kernel32.ReadProcessMemory` 扫描 `Weixin.exe`
   - **Linux**: 读取 `/proc/<pid>/maps` + `/proc/<pid>/mem`
3. 扫描内存中的 hex 模式（`x'...'`），通过 HMAC-SHA512 验证密钥正确性
4. 结果保存到 `~/.wechat-cli/all_keys.json`，配置保存到 `~/.wechat-cli/config.json`

### 3.2 查询流程（所有命令共享）

```
用户命令 → AppContext 初始化 → DBCache.get(rel_key) → 解密 DB 到临时文件 → SQLite 查询 → 格式化输出
```

- `AppContext` 在 CLI 入口创建，加载配置、密钥、缓存
- `DBCache` 以 **mtime** 判断数据库是否变化，未变化则复用已解密的临时文件
- 解密在 `crypto.py` 完成：SQLCipher 4, AES-256-CBC, 4KB 页大小，含 WAL 补丁
- 所有查询走标准库 `sqlite3`，直接读解密后的临时文件

### 3.3 消息分表机制

微信消息按聊天对象分表存储：
- 表名: `Msg_{md5(username).hexdigest()}`
- 数据库: `message/message_N.db`（N 为数字，一个聊天可能跨多个分片）
- 查询时通过 `_find_msg_tables_for_user` 找到所有包含该用户表的 DB，按 `MAX(create_time)` 排序，合并查询

---

## 4. 关键模块说明

| 模块 | 职责 |
|------|------|
| `core/context.py` | 应用上下文，组合 config / keys / cache / msg_db_keys |
| `core/config.py` | 加载/自动检测 `db_dir`，管理 `~/.wechat-cli/` 状态目录 |
| `core/crypto.py` | SQLCipher 页级解密、WAL 帧补丁 |
| `core/db_cache.py` | 解密文件缓存（`tempfile.gettempdir()/wechat_cli_cache/`），含持久化 mtime 记录 |
| `core/contacts.py` | 联系人加载（内存缓存）、名称解析（模糊匹配）、群成员查询 |
| `core/messages.py` | 消息查询核心：分表发现、时间解析、内容解压（zstd）、XML 解析、类型格式化、统计聚合 |
| `core/key_utils.py` | 处理密钥 JSON 中的路径变体（`/` vs `\` vs `os.sep`）与元数据剥离 |
| `keys/common.py` | 跨平台共享：DB 收集、HMAC 验证、hex 扫描、交叉验证、结果保存 |
| `output/formatter.py` | 统一输出：JSON（默认，LLM 友好）或 Text |

---

## 5. 开发规范

### 5.1 代码风格
- 中文注释 + docstring
- 使用 `click` 构建 CLI，选项用 `click.option`，参数用 `click.argument`
- 所有子命令通过 `@click.pass_context` 获取 `ctx.obj`（`AppContext`）
- 输出统一走 `output(data, fmt)`，默认 `json`

### 5.2 错误处理
- 参数错误：`click.echo(..., err=True)` + `ctx.exit(2)`
- 找不到聊天/联系人：`ctx.exit(1)`
- 数据库解密失败：`ctx.exit(3)`
- `init` 命令失败：`sys.exit(1)`

### 5.3 安全与边界
- 所有文件路径操作前做安全校验（如 `_is_safe_rel_path` 防止路径遍历）
- SQL 查询使用参数化，表名通过 `_is_safe_msg_table_name` 白名单校验
- XML 解析前检查 `<!DOCTYPE` / `<!ENTITY` 防止 XXE，限制长度 20000
- 密钥文件、配置文件默认存 `~/.wechat-cli/`，不提交到仓库

### 5.4 依赖管理
- 运行时依赖仅 3 个：`click`, `pycryptodome`, `zstandard`
- 构建时依赖：`pyinstaller`（npm 打包用）
- 不引入新的重型依赖，优先使用标准库

---

## 6. 常用开发命令

```bash
# 本地开发安装
pip install -e .

# 运行 CLI
wechat-cli --help
wechat-cli init
wechat-cli sessions --limit 5

# 构建 npm 平台二进制（当前平台）
python npm/scripts/build.py

# 构建指定平台
python npm/scripts/build.py darwin-arm64 linux-x64 win32-x64

# 清理构建产物
rm -rf build/ dist/
```

---

## 7. 状态目录与文件

| 路径 | 说明 |
|------|------|
| `~/.wechat-cli/config.json` | 用户配置（db_dir 等） |
| `~/.wechat-cli/all_keys.json` | 提取的数据库密钥 |
| `~/.wechat-cli/last_check.json` | `new-messages` 增量状态 |
| `~/.wechat-cli/decrypted/` | 预解密数据库目录（预留） |
| `~/.wechat-cli/decoded_images/` | 解码图片目录（预留） |
| `$TMPDIR/wechat_cli_cache/` | DBCache 解密缓存（`.db` 临时文件 + `_mtimes.json`） |

---

## 8. 平台差异要点

| 平台 | 密钥提取方式 | 权限要求 | 特殊处理 |
|------|-------------|---------|---------|
| macOS | C 二进制扫描进程内存 | sudo + Full Disk Access | 自动检测 `task_for_pid` 失败并重签名 WeChat |
| Windows | `ctypes` + `ReadProcessMemory` | 管理员权限 | 扫描 `Weixin.exe`，按内存排序 |
| Linux | `/proc/<pid>/mem` | root 或 `CAP_SYS_PTRACE` | 通过 `/proc/<pid>/comm` 和 `exe` 识别微信进程 |

---

## 9. 注意事项

1. **微信版本兼容性**: 仅支持 WeChat for Mac ≤ 4.1.8.100，其他平台版本可能随微信更新失效
2. **数据只读**: 本工具只读取本地数据，不发送、不修改、不删除任何消息
3. **密钥安全**: `all_keys.json` 包含数据库密钥，属于敏感文件，已加入 `.gitignore`
4. **C 二进制**: `wechat_cli/bin/find_all_keys_macos.*` 是预编译的 macOS 密钥扫描器，源码为 `find_all_keys_macos.c`
5. **消息内容解压**: 部分消息内容使用 zstd 压缩（`WCDB_CT_message_content = 4`），`messages.py` 中统一解压
6. **群聊发送者**: 群聊消息内容前会带 `wxid:\n` 前缀，解析时需要剥离并映射为显示名

---

## 10. 修改建议

- 新增命令：在 `commands/` 下新建文件，在 `main.py` 中注册
- 修改查询逻辑：优先改 `core/messages.py`，保持 `commands/` 层轻薄
- 修改输出格式：统一走 `output/formatter.py`，不要在命令中直接 `print` JSON
- 涉及密钥提取：先改 `keys/common.py`，再同步平台特定 scanner
