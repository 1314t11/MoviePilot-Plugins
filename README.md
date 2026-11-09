# MoviePilot-Plugins（个人插件仓库 · V3）

MoviePilot 自定义插件集合，按 **V3 索引规范**（`package.v3.json` + `plugins.v3/`）维护。
每个插件都有独立的使用文档，插件市场详情页可直接打开。

- 适用版本：MoviePilot **v3.0.0 及以上**
- 索引地址：`https://github.com/1314t11/MoviePilot-Plugins`
- 插件市场只读取 `main` 分支，提交合并到 `main` 才会生效

---

## 插件列表

| 插件 | 插件 ID | 版本 | 一句话说明 | 文档 |
|---|---|---|---|---|
| 综艺加更整理 | `VarietyExtraOrganizer` | 1.1.3 | 把综艺「加更 / 加更 PLUS」文件硬链接进媒体库并规范命名为 `SxxEyy`，让飞牛、Emby 正常刮削；源文件不动 | [README](plugins.v3/varietyextraorganizer/README.md) |

> 表格随插件增减手动维护；权威版本号以 `package.v3.json` 为准。

---

## 仓库结构

```text
MoviePilot-Plugins/
├── package.v3.json                  # V3 索引：市场读这个文件
├── plugins.v3/                      # V3 插件源码
│   └── varietyextraorganizer/
│       ├── __init__.py              # 插件主类（文件名必须是 __init__.py）
│       └── README.md                # 使用文档，市场详情页会读取
├── package.json                     # 旧版索引（历史保留，V3 不会选中）
└── plugins/                         # 旧版插件目录（历史保留）
```

约定：

- 插件目录名 = **插件类名的小写**（`class VarietyExtraOrganizer` → `varietyextraorganizer/`）；
- 插件主类必须定义在该目录的 `__init__.py` 中；
- 索引键 = **插件类名**（与类名保持完全一致，目录则用小写）；
- 需要额外依赖时放 `pyproject.toml`（V3 规范），不要用 `requirements.txt`。

---

## 安装插件

### 方式一：插件市场（推荐）

1. 打开 MoviePilot → 插件 → 插件市场 → 右上角「插件市场设置」；
2. 在「插件仓库地址」里追加本仓库地址，多个地址用**英文逗号**分隔：

   ```
   https://github.com/jxxghp/MoviePilot-Plugins,https://github.com/1314t11/MoviePilot-Plugins
   ```

3. 保存 → 点「同步插件源」/刷新；
4. 在市场里搜索插件名 → 安装 → 打开配置页。

### 方式二：环境变量

```bash
PLUGIN_MARKET=https://github.com/jxxghp/MoviePilot-Plugins,https://github.com/1314t11/MoviePilot-Plugins
```

修改后重启容器生效。

### 关于网络

插件市场要多次访问 GitHub，网络不通时表现为「仓库加了但市场是空的」，不是索引写错：

| 变量 | 作用 |
|---|---|
| `GITHUB_TOKEN` | 提高 GitHub API 调用配额，避免 403 限流 |
| `GITHUB_PROXY` | 加速站前缀，用于下载索引和插件文件 |
| `PROXY_HOST` / 代理 | 让容器整体走代理，连通性最稳 |

---

## 更新与缓存

- 市场索引用 `package.v3.json`，**缓存 30 分钟**；刚推送完看不到是正常的，等一会儿或重启容器；
- 插件详情页的「使用说明」来自 `plugins.v3/<插件ID>/README.md`，缓存 5 分钟；
- 只有索引里的 `version` 变化，市场才会提示「有更新」；纯文档改动不会触发更新提示，但详情页文档会刷新；
- 升级插件版本时要同步三处：插件类里的 `plugin_version`、索引里的 `version`、`history` 顶部那条记录。

---

## 兼容性说明

| 宿主版本 | 状态 | 说明 |
|---|---|---|
| V3（3.0.0+） | ✅ 支持 | 走 `package.v3.json` + `plugins.v3/` |
| V2 | ❌ 不支持 | 未提供 `package.v2.json`，旧索引也没有 `"v2": true` 标记 |
| V1 | ❌ 不支持 | 旧目录仅为历史保留 |

背景：V3 宿主的索引查找顺序是 `package.v3.json` → `package.v2.json` → `package.json`。
前两个 404 时会回退到旧索引，但旧索引条目**必须显式声明 `"v3": true`** 才被认作兼容；
本仓库的旧条目没有该标记，所以在 V3 上只会读到 `package.v3.json`，行为可预期。

---

## 开发与提交约定

新增一个插件的最小步骤：

1. 建目录 `plugins.v3/<插件ID小写>/`，在其中写 `__init__.py`（主类继承 `app.sdk.plugin._PluginBase`）；
2. 在 `package.v3.json` 增加条目，键名用类名：

```json
{
  "MyPlugin": {
    "name": "插件名",
    "description": "一句话说明",
    "version": "1.0.0",
    "author": "T1677",
    "level": 1,
    "icon": "https://raw.githubusercontent.com/jxxghp/MoviePilot/main/app.ico",
    "labels": "文件整理",
    "system_version": ">=3.0.0",
    "history": { "v1.0.0": "首个版本" }
  }
}
```

3. 在插件目录补 `README.md`（市场详情页会展示）；
4. 本地自检：`python -m py_compile plugins.v3/<id>/__init__.py`、`python -c "import json;json.load(open('package.v3.json'))"`；
5. 提交到 `main`，回 MoviePilot 刷新市场验证。

索引字段的几个硬性约束（写错会导致整条被丢弃，且日志里不一定明显）：

- `version` 必须是非空字符串；
- `release`、`v2`、`v3` 这类字段必须是**布尔**，写成 `"true"` 会被判非法；
- 条目一旦自带 `id` 字段，必须与索引键**大小写不敏感地一致**；
- `description` ≤ 4096 字符，`labels` ≤ 65 个。

---

## 反馈

提 Issue 时请附上：MoviePilot 版本、插件版本、相关日志片段、出问题的目录/文件名（敏感信息请打码）。
