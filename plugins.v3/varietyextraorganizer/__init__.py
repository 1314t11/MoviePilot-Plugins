"""
综艺加更整理插件 (VarietyExtraOrganizer)

扫描综艺源目录，将 MoviePilot 无法识别的「加更」文件（文件名含"加更"且带
.Extra/.Plus 后缀）硬链接到媒体库目录并规范命名，使飞牛/Emby 等能正常刮削。

命名规则：
    剧名 - SxxEyy - 第 ZZ 集加更.ext
    yy = 该季正片最大集号 + 序号（加更排到季末续号）
    ZZ = 加更对应的"期号" × 2（第1期上=第1集，第1期下=第2集，故第N期加更=第2N集）

源文件全程不动（使用硬链接 os.link，不修改、不移动、不删除源文件）。
"""
import os
import re
import time
from xml.sax.saxutils import escape as xml_escape
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from apscheduler.schedulers.background import BackgroundScheduler

from app.schemas.types import SystemConfigKey
from app.sdk.config import settings
from app.sdk.logging import logger
from app.sdk.plugin import _PluginBase


class VarietyExtraOrganizer(_PluginBase):
    # 插件名称
    plugin_name = "综艺加更整理"
    # 插件描述
    plugin_desc = "扫描综艺源目录，将无法识别的加更(Extra/Plus)文件硬链接到媒体库并规范命名。"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/1314t11/MoviePilot-Plugins/main/icons/varietyextraorganizer.png"
    # 插件版本
    plugin_version = "1.2.0"
    # 插件作者
    plugin_author = "T1677"
    # 作者主页
    author_url = "https://github.com/1314t11"
    # 插件配置项ID前缀
    plugin_config_prefix = "varietyextraorganizer_"
    # 加载顺序
    plugin_order = 50
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _scheduler = None
    _enabled = False
    _onlyonce = False
    _src_dir = ""
    _dst_dir = ""
    _title_map = ""  # 剧名映射，格式：源目录关键字=显示剧名，多个用换行分隔
    _scan_interval = 60  # 定时扫描间隔（分钟）
    _write_nfo = True  # 是否生成同名 NFO，让飞牛/Emby 显示「第 ZZ 集加更」
    _logs = deque(maxlen=40)  # 运行日志环形缓冲，详情页展示用
    _last_run: Dict[str, Any] = {}  # 上次运行统计
    _totals: Dict[str, int] = {}  # 累计统计：runs/linked/errors
    _state_loaded = False
    _running = False  # 防止定时与手动触发并发

    def __init__(self):
        super().__init__()
        # 每个实例独立持有，避免重载后日志重复叠加
        self._logs = deque(maxlen=40)
        self._last_run = {}
        self._totals = {}
        self._state_loaded = False
        self._running = False

    def init_plugin(self, config: dict = None):
        self._ensure_state()
        if config:
            self._enabled = config.get("enabled") or False
            self._onlyonce = config.get("onlyonce") or False
            self._src_dir = config.get("src_dir") or ""
            self._dst_dir = config.get("dst_dir") or ""
            self._title_map = config.get("title_map") or ""
            try:
                self._scan_interval = max(int(config.get("scan_interval") or 60), 1)
            except (TypeError, ValueError):
                self._scan_interval = 60
            self._write_nfo = bool(config.get("write_nfo", True))

        # 停止旧任务，保证可重复初始化
        self.stop_service()

        if self._enabled and self._src_dir and self._dst_dir:
            # 启动定时任务
            self._scheduler = BackgroundScheduler(
                timezone=getattr(settings, "TZ", "Asia/Shanghai")
            )
            self._scheduler.add_job(
                func=self.__organize,
                trigger="interval",
                minutes=max(self._scan_interval, 1),
                id="variety_extra_organizer",
                name="综艺加更整理",
            )
            self._scheduler.start()
            self._add_log("info", f"插件已启动，扫描间隔 {self._scan_interval} 分钟")

        if self._onlyonce:
            self._onlyonce = False
            self._add_log("info", "手动触发：立即运行一次")
            self.__organize()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return [{
            "path": "/organize_now",
            "endpoint": self.organize_now,
            "methods": ["POST"],
            "auth": "apikey",
            "summary": "立即整理一次",
            "description": "立即扫描综艺源目录，整理加更文件",
        }]

    def get_service(self) -> List[Dict[str, Any]]:
        return []

    def get_form(self):
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {'model': 'enabled', 'label': '启用插件'},
                                }],
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {'model': 'onlyonce', 'label': '立即运行一次'},
                                }],
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'scan_interval',
                                        'label': '扫描间隔(分钟)',
                                        'type': 'number',
                                        'placeholder': '60',
                                    },
                                }],
                            },
                        ],
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {'model': 'write_nfo', 'label': '生成同名 NFO'},
                                }],
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 8},
                                'content': [{
                                    'component': 'VAlert',
                                    'props': {
                                        'type': 'info',
                                        'variant': 'tonal',
                                        'density': 'compact',
                                        'text': '生成 .nfo 后，飞牛影视在媒体库高级设置里开启「优先读取本地 NFO 和图片」，加更集就会显示成「第 ZZ 集加更」而不是空白标题。',
                                    },
                                }],
                            },
                        ],
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'src_dir',
                                        'label': '综艺源目录(容器内路径)',
                                        'placeholder': '/vol2/Movie/MP观影数据/电视剧/综艺',
                                    },
                                }],
                            },
                        ],
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'dst_dir',
                                        'label': '媒体库目录(容器内路径)',
                                        'placeholder': '/vol2/Movie/综合Link/电视剧/综艺',
                                    },
                                }],
                            },
                        ],
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [{
                                    'component': 'VTextarea',
                                    'props': {
                                        'model': 'title_map',
                                        'label': '剧名映射(每行一条: 源目录关键字=显示剧名)',
                                        'rows': 3,
                                        'placeholder': '示例综艺=示例综艺',
                                    },
                                }],
                            },
                        ],
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [{
                                    'component': 'VAlert',
                                    'props': {
                                        'type': 'info',
                                        'variant': 'tonal',
                                        'text': '加更文件将被硬链接到媒体库目录，命名格式：剧名 - SxxEyy - 第 ZZ 集加更.ext（yy=季末续号，ZZ=期号×2）。源文件不动。',
                                    },
                                }],
                            },
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "src_dir": "",
            "dst_dir": "",
            "title_map": "",
            "scan_interval": 60,
            "write_nfo": True,
        }

    def get_page(self) -> List[dict]:
        """插件详情页：运行状态、词表前置条件检查、最近运行日志"""
        self._ensure_state()
        blocked, _ = self._check_block_word()
        last = self._last_run or {}

        if not self._src_dir or not self._dst_dir:
            state_class, state_text = "text-h6 text-warning", "未配置目录"
        elif not self._enabled:
            state_class, state_text = "text-h6 text-medium-emphasis", "已配置 · 未启用"
        else:
            state_class, state_text = "text-h6 text-success", "运行中"

        total_linked = int(self._totals.get("linked", 0))
        total_errors = int(self._totals.get("errors", 0))
        total_runs = int(self._totals.get("runs", 0))

        stats_row = {
            "component": "VRow",
            "content": [
                self._stat_card("状态", state_text, state_class),
                self._stat_card(
                    "上次运行",
                    last.get("time") or "从未运行",
                    "text-subtitle-1",
                    sub=(f"用时 {last.get('seconds', 0)}s · {last.get('seasons', 0)} 个季目录"
                         if last else ""),
                ),
                self._stat_card(
                    "上次新增",
                    f"{last.get('linked', 0)} 个",
                    sub=(f"跳过 {last.get('skipped', 0)} · 失败 {last.get('errors', 0)}"),
                ),
                self._stat_card(
                    "库内加更",
                    f"{last.get('in_library', 0)} 个",
                    sub=f"累计新增 {total_linked} · 失败 {total_errors} · 运行 {total_runs} 次",
                ),
            ],
        }

        block_row = {
            "component": "VRow",
            "content": [{
                "component": "VCol",
                "props": {"cols": 12},
                "content": [{
                    "component": "VAlert",
                    "props": {
                        "type": "success" if blocked else "warning",
                        "variant": "tonal",
                        "density": "compact",
                        "title": "屏蔽词已配置" if blocked else "前置条件未满足：缺少屏蔽词「加更」",
                        "text": (
                            "「右上角『捷径』→ 词表 → 文件整理屏蔽词」里已有能命中加更文件的规则，"
                            "MoviePilot 整理时会跳过这些文件，留给插件处理。"
                            if blocked else
                            "请点设置页右上角「捷径」→「词表」→「文件整理屏蔽词」，新增一条：加更"
                            "（这里是正则，写这两个汉字即可，不需要加 .* 或引号）。"
                            "否则 MoviePilot 整理时会先把加更文件当成正片处理走，插件就没得处理了。"
                        ),
                    },
                }],
            }],
        }

        shown = list(self._logs)[-20:][::-1]
        if shown:
            log_items: List[dict] = [
                {
                    "component": "div",
                    "props": {
                        "class": "text-caption py-1",
                        "style": "white-space: pre-wrap; word-break: break-all;",
                    },
                    "text": line,
                }
                for line in shown
            ]
            log_hint = f"最近 {len(shown)} 条，框内可滚动 · 完整记录见 MoviePilot 主日志"
        else:
            log_items = [{
                "component": "div",
                "props": {"class": "text-caption py-2"},
                "text": "暂无运行记录。到「配置」页勾选「立即运行一次」并保存，跑完这里就会有结果。",
            }]
            log_hint = ""

        log_card: List[dict] = [
            {"component": "VCardTitle", "props": {"class": "text-subtitle-1 pb-0"}, "text": "运行日志"},
        ]
        if log_hint:
            log_card.append({
                "component": "VCardText",
                "props": {"class": "text-caption text-medium-emphasis py-1"},
                "text": log_hint,
            })
        log_card.append({
            "component": "div",
            # 固定高度 + 内部滚动，避免日志把详情页撑长
            "props": {"style": "max-height: 260px; overflow-y: auto; padding: 4px 16px 12px;"},
            "content": log_items,
        })

        log_row = {
            "component": "VRow",
            "content": [{
                "component": "VCol",
                "props": {"cols": 12},
                "content": [{
                    "component": "VCard",
                    "props": {"variant": "outlined"},
                    "content": log_card,
                }],
            }],
        }

        info_row = {
            "component": "VRow",
            "content": [{
                "component": "VCol",
                "props": {"cols": 12},
                "content": [{
                    "component": "VCard",
                    "props": {"variant": "outlined"},
                    "content": [{
                        "component": "VCardText",
                        "props": {"class": "text-caption text-medium-emphasis"},
                        "content": [
                            {"component": "div", "props": {"class": "text-subtitle-2 mb-2"}, "text": "命名规则"},
                            {"component": "div", "text": "剧名 - SxxEyy - 第 ZZ 集加更.ext"},
                            {"component": "div", "text": "yy = 该季正片最大集号 + 序号（排在季末）"},
                            {"component": "div", "text": "ZZ = 源文件期号 × 2"},
                            {"component": "div", "props": {"class": "mt-2"}, "text": "源文件只读不写，删除媒体库里的链接不影响源文件。"},
                        ],
                    }],
                }],
            }],
        }

        if self._write_nfo:
            info_row["content"][0]["content"][0]["content"][0]["content"].append({
                "component": "div",
                "props": {"class": "mt-2"},
                "text": "已开启同名 NFO：飞牛媒体库开启「优先读取本地 NFO 和图片」后，加更集会显示成「第 ZZ 集加更」。",
            })

        return [stats_row, block_row, log_row, info_row]

    # ==================== 运行时状态 ====================

    @staticmethod
    def _stat_card(title: str, value: str, value_class: str = "text-h6",
                   sub: str = "") -> dict:
        """构造一个统计卡片：标题 / 数值 / 可选附注"""
        texts = [
            {
                "component": "VCardText",
                "props": {"class": "text-caption text-medium-emphasis pb-1"},
                "text": title,
            },
            {"component": "VCardText", "props": {"class": f"{value_class} py-1"}, "text": value},
        ]
        if sub:
            texts.append({
                "component": "VCardText",
                "props": {"class": "text-caption text-medium-emphasis pt-0"},
                "text": sub,
            })
        return {
            "component": "VCol",
            "props": {"cols": 12, "sm": 6, "md": 3},
            "content": [{
                "component": "VCard",
                "props": {"variant": "outlined", "class": "h-100"},
                "content": texts,
            }],
        }

    def _ensure_state(self):
        """首次访问时从插件数据里恢复上次统计与日志"""
        if self._state_loaded:
            return
        self._state_loaded = True
        try:
            self._last_run = self.get_data("last_run") or {}
            for key, val in (self.get_data("totals") or {}).items():
                self._totals[key] = int(val)
            for item in (self.get_data("run_logs") or []):
                self._logs.append(str(item))
        except Exception as e:
            logger.debug(f"综艺加更整理：读取历史运行记录失败：{e}")

    def _add_log(self, level: str, message: str):
        """同时写入宿主日志和插件内日志缓冲"""
        try:
            getattr(logger, level if level in ("debug", "info", "warning", "error") else "info")(
                f"综艺加更整理：{message}"
            )
        except Exception:
            pass
        stamp = datetime.now().strftime("%m-%d %H:%M:%S")
        self._logs.append(f"{stamp} [{level.upper()}] {message}")

    def _save_state(self):
        """持久化统计与日志，重启后详情页仍能看到"""
        try:
            self.save_data("last_run", self._last_run)
            self.save_data("totals", self._totals)
            self.save_data("run_logs", list(self._logs))
        except Exception as e:
            logger.error(f"综艺加更整理：保存运行记录失败：{e}")

    # 判断屏蔽词是否覆盖加更文件时使用的样例名
    _SAMPLE_NAME = "示例综艺.加更.DemoShow.S05E01.Plus.2025.1080p.mkv"

    def _check_block_word(self) -> Tuple[bool, str]:
        """检查「右上角捷径 → 词表 → 文件整理屏蔽词」里是否有能命中加更文件的规则"""
        try:
            rules = self.systemconfig.get(SystemConfigKey.TransferExcludeWords) or []
        except Exception as e:
            return False, f"读取屏蔽词失败：{e}"
        if isinstance(rules, str):
            rules = rules.splitlines()
        for rule in rules:
            if not isinstance(rule, str):
                continue
            pattern = rule.strip()
            if not pattern or pattern.startswith("#"):
                continue
            try:
                # 屏蔽词是正则，用真实样例名试匹配
                if re.search(pattern, self._SAMPLE_NAME):
                    return True, ""
            except re.error:
                # 非法正则跳过，不影响其它规则
                continue
        return False, "文件整理屏蔽词里没有能匹配加更文件的规则"

    # ==================== 核心逻辑 ====================

    @staticmethod
    def _get_ep(filename: str) -> int:
        m = re.search(r'[Ss]\d+[Ee](\d+)', filename)
        return int(m.group(1)) if m else 0

    @staticmethod
    def _get_ext(filename: str) -> str:
        return os.path.splitext(filename)[1]

    @staticmethod
    def _is_jia(filename: str) -> bool:
        """判断是否加更文件：含「加更」且有 Extra/Plus 后缀"""
        name = filename.lower()
        return ('加更' in filename) and ('.extra' in name or '.plus' in name)

    def _parse_title_map(self) -> Dict[str, str]:
        """解析剧名映射配置"""
        mapping = {}
        for line in (self._title_map or "").splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, val = line.split("=", 1)
            mapping[key.strip()] = val.strip()
        return mapping

    @staticmethod
    def _resolve_title(dirname: str, title_map: Dict[str, str]) -> str:
        """根据源目录名解析显示剧名"""
        for key, val in title_map.items():
            if key and key in dirname:
                return val
        # 默认取第一个中文片段
        m = re.search(r'[\u4e00-\u9fa5]+', dirname)
        return m.group(0) if m else dirname

    def _write_episode_nfo(self, video: Path, show_title: str, season: int,
                           episode: int, title: str, plot: str) -> bool:
        """生成同名 episodedetails NFO，供飞牛/Emby「优先读取本地 NFO」使用"""
        if not self._write_nfo:
            return False
        nfo = video.with_suffix(".nfo")
        if nfo.exists():
            return False
        content = (
            '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n'
            "<episodedetails>\n"
            f"  <title>{xml_escape(title)}</title>\n"
            f"  <showtitle>{xml_escape(show_title)}</showtitle>\n"
            f"  <season>{season}</season>\n"
            f"  <episode>{episode}</episode>\n"
            f"  <plot>{xml_escape(plot)}</plot>\n"
            "</episodedetails>\n"
        )
        try:
            nfo.write_text(content, encoding="utf-8")
            return True
        except OSError as e:
            self._add_log("error", f"写入 NFO 失败 {nfo.name}：{e}")
            return False

    @staticmethod
    def _count_jia(path: Path) -> int:
        """统计目录里已存在的加更文件数"""
        if not path.is_dir():
            return 0
        try:
            return sum(1 for name in os.listdir(path) if '加更' in name)
        except OSError:
            return 0

    def _max_ep_in_dir(self, path: Path) -> Tuple[int, int]:
        """返回目录内（跳过加更文件）的最大集号与文件数"""
        if not path.is_dir():
            return 0, 0
        max_ep, count = 0, 0
        try:
            for name in os.listdir(path):
                if '加更' in name:
                    continue
                count += 1
                ep = self._get_ep(name)
                if ep > max_ep:
                    max_ep = ep
        except OSError as e:
            self._add_log("error", f"读取目录失败 {path}：{e}")
        return max_ep, count

    def __organize(self):
        """执行加更整理"""
        if self._running:
            self._add_log("warning", "上一轮扫描还没结束，本次触发已跳过")
            return
        self._running = True
        started = time.time()
        stats: Dict[str, Any] = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "seasons": 0,
            "linked": 0,
            "skipped": 0,
            "errors": 0,
            "nfo": 0,
            "seconds": 0.0,
            "message": "",
        }

        def finish(message: str, level: str = "info"):
            """收尾：补上耗时、写日志、落盘统计"""
            stats["seconds"] = round(time.time() - started, 1)
            stats["linked"] = total
            stats["in_library"] = lib_jia
            text = f"{message}（用时 {stats['seconds']}s）"
            stats["message"] = text
            self._last_run = stats
            self._running = False
            self._totals["runs"] = int(self._totals.get("runs", 0)) + 1
            self._totals["linked"] = int(self._totals.get("linked", 0)) + stats["linked"]
            self._totals["errors"] = int(self._totals.get("errors", 0)) + stats["errors"]
            self._add_log(level, text)
            self._save_state()

        total = 0
        lib_jia = 0
        if not self._src_dir or not self._dst_dir:
            finish("源目录或媒体库目录未配置，跳过本次扫描", "warning")
            return

        src = Path(self._src_dir)
        dst = Path(self._dst_dir)
        if not src.is_dir():
            finish(f"源目录不存在：{self._src_dir}", "warning")
            return
        try:
            if src.resolve() == dst.resolve():
                finish("源目录和媒体库目录不能是同一个目录，否则会往源目录里写链接", "error")
                return
        except OSError:
            pass

        title_map = self._parse_title_map()

        try:
            for season_dir in sorted(src.iterdir()):
                if not season_dir.is_dir():
                    continue
                sm = re.search(r'\.S(\d+)(?:\.|$)', season_dir.name)
                if not sm:
                    continue
                season = int(sm.group(1))
                stats["seasons"] += 1

                # 收集加更文件
                try:
                    files = [f for f in os.listdir(season_dir) if self._is_jia(f)]
                except OSError as e:
                    self._add_log("error", f"读取目录失败 {season_dir}：{e}")
                    continue
                if not files:
                    continue
                jias = sorted(files, key=self._get_ep)

                # 解析剧名
                title = self._resolve_title(season_dir.name, title_map)
                ym = re.search(r'\.S\d+\.(\d{4})(?:\.|$)', season_dir.name)
                # 年份以媒体库已有目录为准（通常为首播年），没有再退回源目录年份
                show_year = ym.group(1) if ym else None

                # 媒体库里同名剧集可能有多个目录（年份不同），选该季文件最多的那个
                candidates = []
                if dst.is_dir():
                    try:
                        candidates = [s for s in dst.iterdir() if s.is_dir() and title in s.name]
                    except OSError as e:
                        self._add_log("error", f"读取媒体库目录失败 {dst}：{e}")

                target_show_dir = None
                if candidates:
                    target_show_dir = max(
                        candidates,
                        key=lambda s: self._max_ep_in_dir(s / f"Season {season}")[1],
                    )
                    ym2 = re.search(r'\((\d{4})\)', target_show_dir.name)
                    if ym2:
                        show_year = ym2.group(1)

                if target_show_dir is None:
                    if show_year:
                        target_show_dir = dst / f"{title} ({show_year})"
                    else:
                        target_show_dir = dst / title

                season_target = target_show_dir / f"Season {season}"

                # 集号同时参考源目录和媒体库：库内命名不一定带 SxxEyy，源目录通常带
                src_max, src_count = self._max_ep_in_dir(season_dir)
                dst_max, dst_count = self._max_ep_in_dir(season_target)
                max_ep = max(src_max, dst_max)

                if max_ep == 0:
                    self._add_log(
                        "warning",
                        f"{title} S{season:02d} 解析不到集号，跳过。"
                        f"源目录 {src_count} 个文件、媒体库 {dst_count} 个文件都不含 SxxEyy；"
                        f"目标路径：{season_target}。"
                        "如果正片确实已入库，检查正片文件名是否带 SxxEyy（如 S04E01），"
                        "或确认「媒体库目录」指向的是正片实际所在目录。",
                    )
                    lib_jia += self._count_jia(season_target)
                    continue

                if dst_max == 0:
                    self._add_log(
                        "info",
                        f"{title} S{season:02d}：媒体库未解析到集号，按源目录最大集号 E{src_max} 续排",
                    )

                season_target.mkdir(parents=True, exist_ok=True)

                for i, f in enumerate(jias, 1):
                    new_ep = max_ep + i
                    qihao = self._get_ep(f)
                    jihao = qihao * 2
                    ext = self._get_ext(f)
                    new_name = f"{title} - S{season:02d}E{new_ep:02d} - 第 {jihao:02d} 集加更{ext}"
                    dst_file = season_target / new_name
                    ep_title = f"第 {jihao:02d} 集加更"
                    ep_plot = f"{title} 第 {qihao} 期加更（源文件：{f}）"
                    if dst_file.exists():
                        stats["skipped"] += 1
                        if self._write_episode_nfo(dst_file, title, season, new_ep, ep_title, ep_plot):
                            stats["nfo"] += 1
                        continue
                    try:
                        os.link(str(season_dir / f), str(dst_file))
                        total += 1
                        self._add_log("info", f"{f} -> {new_name}")
                        if self._write_episode_nfo(dst_file, title, season, new_ep, ep_title, ep_plot):
                            stats["nfo"] += 1
                    except Exception as e:
                        stats["errors"] += 1
                        self._add_log("error", f"硬链接失败 {f}：{e}")
                lib_jia += self._count_jia(season_target)
        except Exception as e:
            stats["errors"] += 1
            self._add_log("error", f"执行出错 {e}")

        nfo_info = f"，生成 {stats['nfo']} 个 NFO" if self._write_nfo else ""
        finish(f"扫描完成：{stats['seasons']} 个季目录，新增 {total} 个链接，跳过 {stats['skipped']} 个已存在，失败 {stats['errors']} 个{nfo_info}",
               "warning" if stats["errors"] else "info")

    def organize_now(self, apikey: str = None) -> dict:
        """立即整理一次（API 入口）"""
        self.__organize()
        return {"status": 0, "msg": "整理完成"}

    def stop_service(self):
        """停止插件服务"""
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception:
            pass
