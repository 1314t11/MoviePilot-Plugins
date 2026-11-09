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
from pathlib import Path
from typing import Any, Dict, List

from apscheduler.schedulers.background import BackgroundScheduler

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
    plugin_version = "1.0.1"
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

    def init_plugin(self, config: dict = None):
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
            logger.info("综艺加更整理插件已启动，扫描间隔 %s 分钟", self._scan_interval)

        if self._onlyonce:
            self._onlyonce = False
            logger.info("综艺加更整理：立即运行一次")
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
        return []

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
        if not self._src_dir or not self._dst_dir:
            logger.warning("综艺加更整理：源目录或媒体库目录未配置")
            return

        src = Path(self._src_dir)
        dst = Path(self._dst_dir)
        if not src.is_dir():
            logger.warning("综艺加更整理：源目录不存在 %s", self._src_dir)
            return

        title_map = self._parse_title_map()
        total = 0

        try:
            for season_dir in sorted(src.iterdir()):
                if not season_dir.is_dir():
                    continue
                sm = re.search(r'\.S(\d+)\.', season_dir.name)
                if not sm:
                    continue
                season = int(sm.group(1))

                # 收集加更文件
                try:
                    files = [f for f in os.listdir(season_dir) if self._is_jia(f)]
                except OSError as e:
                    logger.error("综艺加更整理：读取目录失败 %s：%s", season_dir, e)
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
                    logger.info("综艺加更整理：%s Season %d 无正片，跳过", title, season)
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
                        continue
                    try:
                        os.link(str(season_dir / f), str(dst_file))
                        total += 1
                        logger.info("综艺加更整理：%s -> %s", f, new_name)
                    except Exception as e:
                        logger.error("综艺加更整理：硬链接失败 %s：%s", f, e)
        except Exception as e:
            logger.error("综艺加更整理：执行出错 %s", e)

        logger.info("综艺加更整理：本次共硬链接 %d 个加更文件", total)

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
