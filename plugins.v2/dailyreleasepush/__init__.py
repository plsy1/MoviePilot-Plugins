import datetime
import threading
from typing import Any, List, Dict, Tuple
import json
import pytz
import re
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.core.config import settings
from app.log import logger
from app.plugins import _PluginBase
from app.core.metainfo import MetaInfo
from app.chain.media import MediaChain
from app.utils.http import RequestUtils


class DailyReleasePush(_PluginBase):
    # 插件名称
    plugin_name = "今日上映剧集"
    # 插件描述
    plugin_desc = "推送今日上映的剧集信息到消息通知工具"
    # 插件图标
    plugin_icon = "statistic.png"
    # 插件版本
    plugin_version = "0.3.7"
    # 插件作者
    plugin_author = "plsy1"
    # 作者主页
    author_url = "https://github.com/plsy1"
    # 插件配置项ID前缀
    plugin_config_prefix = "daily_release"
    # 加载顺序
    plugin_order = 1
    # 可使用的用户级别
    auth_level = 1

    # 退出事件
    _event = threading.Event()

    # 私有属性
    _scheduler = None
    _enabled = False
    _onlyonce = False
    _cron = None
    _remove_noCover = False
    _push_category: list = []

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled")
            self._onlyonce = config.get("onlyonce")
            self._cron = config.get("cron")
            self._remove_noCover = config.get("remove_noCover") or False
            self._push_category = config.get("push_category") or []

        # 停止现有任务
        self.stop_service()

        # 启动服务
        if self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            self._scheduler.add_job(
                func=self.job,
                trigger="date",
                run_date=datetime.datetime.now(tz=pytz.timezone(settings.TZ))
                + datetime.timedelta(seconds=3),
            )
            logger.info(f"当天上映推送服务启动，立即运行一次")
            # 关闭一次性开关
            self._onlyonce = False
            # 保存配置
            self.__update_config()
            # 启动服务
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

    def __update_config(self):
        """
        更新配置
        """
        self.update_config(
            {
                "enabled": self._enabled,
                "onlyonce": self._onlyonce,
                "cron": self._cron,
                "remove_noCover": self._remove_noCover,
                "push_category": self._push_category,
            }
        )

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        [{
            "id": "服务ID",
            "name": "服务名称",
            "trigger": "触发器：cron/interval/date/CronTrigger.from_crontab()",
            "func": self.xxx,
            "kwargs": {} # 定时器参数
        }]
        """
        if self._enabled and self._cron:
            return [
                {
                    "id": "DailyRelease",
                    "name": "推送当日上映剧集信息",
                    "trigger": CronTrigger.from_crontab(self._cron),
                    "func": self.job,
                    "kwargs": {},
                }
            ]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        option_category = [
            {"title": "剧集", "value": 1},
            {"title": "电影", "value": 2},
        ]

        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "onlyonce",
                                            "label": "立即运行一次",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "remove_noCover",
                                            "label": "只返回横向背景图",
                                        },
                                    }
                                ],
                            },
                        ],
                    }
                ],
            },
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 6},
                        "content": [
                            {
                                "component": "VTextField",
                                "props": {
                                    "model": "cron",
                                    "label": "服务执行周期",
                                    "placeholder": "5位cron表达式",
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 6},
                        "content": [
                            {
                                "component": "VSelect",
                                "props": {
                                    "chips": True,
                                    "multiple": True,
                                    "model": "push_category",
                                    "label": "处理类型",
                                    "items": option_category,
                                },
                            }
                        ],
                    },
                ],
            },
        ], {
            "enabled": False,
            "onlyonce": False,
            "cron": "",
            "remove_noCover": True,
            "push_category": [],
        }

    def get_page(self) -> List[dict]:
        pass

    def job(self):
        """
        获取当日上映的剧集信息，推送消息
        """
        items = self.get_huoxing_items()

        for item in items:
            mediainfo_raw = MediaChain().recognize_by_meta(
                MetaInfo(item.get("english_title"))
            )
            mediainfo_zhs = MediaChain().recognize_by_meta(MetaInfo(item.get("title")))

            ## 识别正确替换
            image_type = ""
            if self.isDateEqual(mediainfo_raw) and self.isDateEqual(mediainfo_zhs):
                backdrop_or_poster, image_type = (
                    self.get_background(mediainfo_raw)
                    or self.get_background(mediainfo_zhs)
                    or self.get_poster(mediainfo_raw)
                    or self.get_poster(mediainfo_zhs)
                )
                overview = self.get_overview(mediainfo_raw) or self.get_overview(
                    mediainfo_zhs
                )
                if backdrop_or_poster:
                    item["poster_url"] = backdrop_or_poster
                if overview:
                    item["description"] = overview

            if self._remove_noCover and (
                item["poster_url"].startswith("https://img.huo720.com")
                or item["poster_url"].startswith("https://m.media-amazon.com")
                or image_type == "poster"
            ):
                continue

            if item["poster_url"].startswith(
                "https://img.huo720.com/files/movie-default"
            ):
                continue

            total_value = sum(self._push_category)

            if (total_value == 1 and item.get("category") == "电影") or (
                total_value == 2 and item.get("category") == "电视"
            ):
                continue
            title = item.get("title", "")
            english_title = item.get("english_title", "")
            cleaned_english_title = re.sub(r"\(\d{4}\)", "", english_title).strip()

            if title == cleaned_english_title:
                name = f"名称: {title}\n"
            else:
                name = f"名称: {title} ({cleaned_english_title})\n"
            self.post_message(
                title="【今日上映】",
                text=(
                    name
                    + f"类型: {item.get('category', '')}\n"
                    + f"日期: {item.get('date', '')}\n"
                    + f"地区: {item.get('country', '')}\n"
                    + (
                        f"标签: {', '.join(item.get('genres', []))}\n"
                        if item.get("genres")
                        else ""
                    )
                    + f"简介: {self.clean_spaces(item.get('description'))}\n"
                ),
                image=item.get("poster_url"),
            )

    def convert_to_mmdd(self, date_str):
        try:
            date_obj = datetime.datetime.strptime(date_str, "%m月%d日")
            return date_obj.strftime("%m%d")
        except ValueError as e:
            logger.error(f"日期转换错误")

    def get_background(self, mediainfo):
        if mediainfo and mediainfo.backdrop_path:
            return mediainfo.backdrop_path, "background"
        return None, None

    def get_poster(self, mediainfo):
        if mediainfo and mediainfo.poster_path:
            return mediainfo.poster_path, "poster"
        return None, None

    def get_overview(self, mediainfo):
        if mediainfo and mediainfo.overview:
            return mediainfo.overview
        return None

    def clean_spaces(self, text):
        text = text.strip()
        text = re.sub(r"\s+", " ", text)
        return text

    def isDateEqual(self, mediainfo):
        if not mediainfo:
            return True
        if mediainfo and mediainfo.release_date == datetime.datetime.now().strftime(
            "%Y-%m-%d"
        ):
            return True
        return False

    def get_huoxing_items(self):
        base = "https://plsy1.github.io/dailyrelease/data/huoxing"
        date = datetime.datetime.now().strftime("%Y%m%d")
        url = f"{base}/{date}.json"
        try:
            response_text = RequestUtils(
                ua=settings.USER_AGENT if settings.USER_AGENT else None,
                proxies=settings.PROXY if settings.PROXY else None,
            ).get(url=url)
            items = json.loads(response_text)
            return items
        except Exception as e:
            logger.error(f"请求失败: {e}")
            return None

    def stop_service(self):
        """
        停止服务
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._event.set()
                    self._scheduler.shutdown()
                    self._event.clear()
                self._scheduler = None
        except Exception as e:
            logger.error(str(e))
