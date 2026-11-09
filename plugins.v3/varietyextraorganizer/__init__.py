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
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot/main/app.ico"
    # 插件版本
    plugin_version = "1.1.0"
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
    _logs = deque(maxlen=40)  # 运行日志环形缓冲，详情页展示用
    _last_run: Dict[str, Any] = {}  # 上次运行统计
    _state_loaded = False

    def init_plugin(self, config: dict = None):
        self._ensure_state()
        if config:
            self._enabled = config.get("enabled") or False
            self._onlyonce = config.get("onlyonce") or False
            self._src_dir = config.get("src_dir") or ""
            self._dst_dir = config.get("dst_dir") or ""
            self._title_map = config.get("title_map") or ""
            self._scan_interval = int(config.get("scan_interval") or 60)

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
        }

    def get_page(self) -> List[dict]:
        """插件详情页：运行状态、词表前置条件检查、最近运行日志"""
        self._ensure_state()
        blocked, _ = self._check_block_word()
        last = self._last_run or {}

        if not self._src_dir or not self._dst_dir:
            state_color, state_text = "warning", "未配置目录"
        elif not self._enabled:
            state_color, state_text = "info", "已配置 · 未启用"
        else:
            state_color, state_text = "success", "运行中"

        stats_row = {
            "component": "VRow",
            "content": [
                self._stat_card("状态", state_text, state_color),
                self._stat_card("上次运行", last.get("time") or "从未运行", "primary"),
                self._stat_card("上次新增", f"{last.get('linked', 0)} 个", "info"),
                self._stat_card(
                    "上次失败",
                    f"{last.get('errors', 0)} 个",
                    "error" if last.get("errors") else "grey",
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
                        "title": "词表屏蔽词已配置" if blocked else "前置条件未满足：缺少屏蔽词「加更」",
                        "text": (
                            "MoviePilot 词表中已存在屏蔽词「加更」，加更文件不会被主程序误当成正片识别。"
                            if blocked else
                            "请到「设置 → 词表 → 自定义识别词」新增一行：加更"
                            "（只要这两个汉字，不要加 => 和空格）。"
                            "否则 MoviePilot 可能先把加更文件识别成正片并处理掉，插件就无从下手。"
                        ),
                    },
                }],
            }],
        }

        log_items: List[dict] = [
            {
                "component": "VCardText",
                "props": {"class": "text-caption py-1"},
                "text": line,
            }
            for line in list(self._logs)[-12:][::-1]
        ]
        if not log_items:
            log_items = [{
                "component": "VCardText",
                "props": {"class": "text-caption py-2"},
                "text": "暂无运行记录。到「配置」页勾选「立即运行一次」并保存，跑完这里就会有结果。",
            }]

        log_row = {
            "component": "VRow",
            "content": [{
                "component": "VCol",
                "props": {"cols": 12},
                "content": [{
                    "component": "VCard",
                    "props": {"variant": "outlined"},
                    "content": [
                        {"component": "VCardTitle", "props": {"class": "text-subtitle-1"}, "text": "运行日志"},
                        *log_items,
                    ],
                }],
            }],
        }

        info_row = {
            "component": "VRow",
            "content": [{
                "component": "VCol",
                "props": {"cols": 12},
                "content": [{
                    "component": "VAlert",
                    "props": {
                        "type": "info",
                        "variant": "tonal",
                        "title": "命名规则",
                        "text": (
                            "输出：剧名 - SxxEyy - 第 ZZ 集加更.ext。"
                            "yy = 该季已有正片最大集号 + 序号（排在季末），ZZ = 源文件期号 × 2。"
                            "源文件只读不写，删除媒体库里的链接不会影响源文件。"
                        ),
                    },
                }],
            }],
        }

        return [stats_row, block_row, log_row, info_row]

    # ==================== 运行时状态 ====================

    @staticmethod
    def _stat_card(title: str, value: str, color: str) -> dict:
        """构造一个统计卡片"""
        return {
            "component": "VCol",
            "props": {"cols": 12, "md": 3},
            "content": [{
                "component": "VCard",
                "props": {"variant": "tonal", "color": color},
                "content": [
                    {"component": "VCardText", "props": {"class": "text-caption"}, "text": title},
                    {"component": "VCardText", "props": {"class": "text-h6"}, "text": value},
                ],
            }],
        }

    def _ensure_state(self):
        """首次访问时从插件数据里恢复上次统计与日志"""
        if self._state_loaded:
            return
        self._state_loaded = True
        try:
            self._last_run = self.get_data("last_run") or {}
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
            self.save_data("run_logs", list(self._logs))
        except Exception as e:
            logger.error(f"综艺加更整理：保存运行记录失败：{e}")

    def _check_block_word(self) -> Tuple[bool, str]:
        """检查 MoviePilot 词表里是否配了屏蔽词「加更」"""
        try:
            rules = self.systemconfig.get(SystemConfigKey.CustomIdentifiers) or []
        except Exception as e:
            return False, f"读取词表失败：{e}"
        for rule in rules:
            if not isinstance(rule, str):
                continue
            text = rule.strip()
            if not text or text.startswith("#"):
                continue
            if text == "加更":
                return True, ""
        return False, "词表里没有屏蔽词「加更」"

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
        return ('加更' in filename) and ('.Extra' in filename or '.Plus' in filename)

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

    def __organize(self):
        """执行加更整理"""
        started = time.time()
        stats: Dict[str, Any] = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "seasons": 0,
            "linked": 0,
            "skipped": 0,
            "errors": 0,
            "seconds": 0.0,
            "message": "",
        }

        def finish(message: str, level: str = "info"):
            """收尾：补上耗时、写日志、落盘统计"""
            stats["seconds"] = round(time.time() - started, 1)
            stats["linked"] = total
            text = f"{message}（用时 {stats['seconds']}s）"
            stats["message"] = text
            self._last_run = stats
            self._add_log(level, text)
            self._save_state()

        total = 0
        if not self._src_dir or not self._dst_dir:
            finish("源目录或媒体库目录未配置，跳过本次扫描", "warning")
            return

        src = Path(self._src_dir)
        dst = Path(self._dst_dir)
        if not src.is_dir():
            finish(f"源目录不存在：{self._src_dir}", "warning")
            return

        title_map = self._parse_title_map()

        try:
            for season_dir in sorted(src.iterdir()):
                if not season_dir.is_dir():
                    continue
                sm = re.search(r'\.S(\d+)\.', season_dir.name)
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
                ym = re.search(r'\.S\d+\.(\d{4})\.', season_dir.name)
                # 年份以媒体库已有目录为准（通常为首播年），没有再退回源目录年份
                target_show_dir = None
                show_year = ym.group(1) if ym else None
                if dst.is_dir():
                    for sub in dst.iterdir():
                        if sub.is_dir() and title in sub.name:
                            ym2 = re.search(r'\((\d{4})\)', sub.name)
                            if ym2:
                                show_year = ym2.group(1)
                            target_show_dir = sub
                            break

                if target_show_dir is None:
                    if show_year:
                        target_show_dir = dst / f"{title} ({show_year})"
                    else:
                        target_show_dir = dst / title

                season_target = target_show_dir / f"Season {season}"

                # 读取该季正片的最大集号（跳过已整理的加更文件）
                max_ep = 0
                if season_target.is_dir():
                    for f in os.listdir(season_target):
                        if '加更' in f:
                            continue
                        ep = self._get_ep(f)
                        if ep > max_ep:
                            max_ep = ep

                if max_ep == 0:
                    self._add_log("info", f"{title} Season {season} 无正片，跳过（先让正片入库）")
                    continue

                season_target.mkdir(parents=True, exist_ok=True)

                for i, f in enumerate(jias, 1):
                    new_ep = max_ep + i
                    qihao = self._get_ep(f)
                    jihao = qihao * 2
                    ext = self._get_ext(f)
                    new_name = f"{title} - S{season:02d}E{new_ep:02d} - 第 {jihao:02d} 集加更{ext}"
                    dst_file = season_target / new_name
                    if dst_file.exists():
                        stats["skipped"] += 1
                        continue
                    try:
                        os.link(str(season_dir / f), str(dst_file))
                        total += 1
                        self._add_log("info", f"{f} -> {new_name}")
                    except Exception as e:
                        stats["errors"] += 1
                        self._add_log("error", f"硬链接失败 {f}：{e}")
        except Exception as e:
            stats["errors"] += 1
            self._add_log("error", f"执行出错 {e}")

        finish(f"扫描完成：{stats['seasons']} 个季目录，新增 {total} 个链接，跳过 {stats['skipped']} 个已存在，失败 {stats['errors']} 个",
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
