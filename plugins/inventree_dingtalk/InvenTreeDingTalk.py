"""
InvenTree DingTalk Notification Plugin
监听 InvenTree 事件并推送到钉钉群机器人 Webhook
"""

import os
import requests
from datetime import datetime
from decimal import Decimal

from plugin.base import InvenTreePlugin
from plugin.mixins import EventMixin, SettingsMixin


class DingTalkNotificationPlugin(EventMixin, SettingsMixin, InvenTreePlugin):
    """钉钉通知插件 - 监听 InvenTree 事件并推送到钉钉群"""

    NAME = "inventree_dingtalk"
    TITLE = "DingTalk Notification"
    DESCRIPTION = "钉钉群机器人通知插件"
    VERSION = "1.1.0"
    AUTHOR = "Admin"
    LICENSE = "MIT"

    # 已发送的告警缓存（避免重复发送）
    _notified_parts = set()

    # ---- 插件设置项 ----
    # 默认值留空：优先读取 .env 中的 DINGTALK_WEBHOOK / DINGTALK_SECRET
    # 也可在 InvenTree 后台「插件设置」里单独覆盖
    SETTINGS = {
        'DINGTALK_WEBHOOK': {
            'name': '钉钉 Webhook 地址',
            'description': '钉钉群自定义机器人的 Webhook URL（留空则读取 .env 中的 DINGTALK_WEBHOOK）',
            'default': '',
            'validator': str,
        },
        'DINGTALK_SECRET': {
            'name': '钉钉加签密钥',
            'description': '钉钉机器人加签安全设置的 secret（留空则读取 .env 中的 DINGTALK_SECRET）',
            'default': '',
            'validator': str,
        },
        'DINGTALK_NOTIFY_LOW_STOCK': {
            'name': '库存不足告警',
            'description': '库存低于最小阈值时通知',
            'default': True,
            'validator': bool,
        },
        'DINGTALK_NOTIFY_PO_STATUS': {
            'name': '采购订单状态变更',
            'description': '采购订单状态变化时通知',
            'default': True,
            'validator': bool,
        },
        'DINGTALK_NOTIFY_SO_STATUS': {
            'name': '销售订单状态变更',
            'description': '销售订单状态变化时通知',
            'default': True,
            'validator': bool,
        },
        'DINGTALK_NOTIFY_BUILD': {
            'name': '生产工单通知',
            'description': '生产工单状态变化时通知',
            'default': True,
            'validator': bool,
        },
        'DINGTALK_NOTIFY_PART_NEW': {
            'name': '新部件创建通知',
            'description': '创建新部件时通知',
            'default': False,
            'validator': bool,
        },
    }

    def send_dingtalk(self, title: str, text: str, is_markdown: bool = True):
        """发送消息到钉钉群机器人"""
        # 优先用插件设置，留空则回退到 .env 环境变量
        webhook = self.get_setting('DINGTALK_WEBHOOK') or os.environ.get('DINGTALK_WEBHOOK', '')
        if not webhook:
            print('[DingTalk] 未配置 Webhook（插件设置或 .env 的 DINGTALK_WEBHOOK）')
            return

        url = webhook

        # 加签
        secret = self.get_setting('DINGTALK_SECRET') or os.environ.get('DINGTALK_SECRET', '')
        if secret:
            import hmac
            import hashlib
            import base64
            import urllib.parse
            import time
            timestamp = str(round(time.time() * 1000))
            string_to_sign = f"{timestamp}\n{secret}"
            hmac_code = hmac.new(
                secret.encode('utf-8'),
                string_to_sign.encode('utf-8'),
                digestmod=hashlib.sha256
            ).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
            url = f"{webhook}&timestamp={timestamp}&sign={sign}"

        if is_markdown:
            payload = {
                "msgtype": "markdown",
                "markdown": {"title": title, "text": text},
            }
        else:
            payload = {
                "msgtype": "text",
                "text": {"content": f"{title}\n{text}"},
            }

        try:
            resp = requests.post(
                url, json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            result = resp.json()
            if result.get('errcode') != 0:
                print(f"[DingTalk] 发送失败: {result}")
            return result
        except Exception as e:
            print(f"[DingTalk] 发送异常: {e}")
            return None

    def check_low_stock(self, part):
        """检查指定部件的库存是否低于最低阈值"""
        try:
            from django.db.models import Sum
            from stock.models import StockItem

            total = StockItem.objects.filter(part=part).aggregate(
                total=Sum('quantity')
            )['total'] or 0

            min_stock = part.minimum_stock or 0
            if total < min_stock:
                return {
                    'name': part.name,
                    'IPN': part.IPN or '无编号',
                    'current': total,
                    'minimum': min_stock,
                    'shortage': min_stock - total,
                }
        except Exception as e:
            print(f"[DingTalk] 库存检查异常: {e}")
        return None

    def notify_low_stock(self, part):
        """检查并发送库存不足告警"""
        if not self.get_setting('DINGTALK_NOTIFY_LOW_STOCK'):
            return

        info = self.check_low_stock(part)
        if not info:
            return

        # 避免短时间内重复发送
        part_key = f"{part.pk}_{info['current']}"
        if part_key in self._notified_parts:
            return
        self._notified_parts.add(part_key)
        # 保留最近 50 条
        if len(self._notified_parts) > 50:
            self._notified_parts = set(list(self._notified_parts)[-50:])

        text = (
            f"## ⚠️ 库存不足告警\n\n"
            f"**部件名称**: {info['name']}\n\n"
            f"**部件编号**: {info['IPN']}\n\n"
            f"**当前库存**: {info['current']}\n\n"
            f"**最低阈值**: {info['minimum']}\n\n"
            f"**缺口**: {info['shortage']}\n\n"
            f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"[查看详情]({__import__('os').environ.get('INVENTREE_SITE_URL', 'http://localhost').strip('\"')})"
        )
        self.send_dingtalk("库存不足告警", text)

    def process_event(self, event, **kwargs):
        """InvenTree 事件触发时调用 - EventMixin 核心方法"""
        event_name = str(event) if not isinstance(event, str) else event
        print(f"[DingTalk] 收到事件: {event_name}, kwargs: {list(kwargs.keys())}")

        # ---- 库存相关事件 ----
        # InvenTree 事件名: stockitem.created / stockitem.updated / stockitem.deleted 等
        if 'stock' in event_name.lower():
            if not self.get_setting('DINGTALK_NOTIFY_LOW_STOCK'):
                return

            # 尝试从 kwargs 中获取 StockItem 实例
            instance = kwargs.get('instance') or kwargs.get('stock_item') or kwargs.get('item')
            if instance is None:
                return

            # 获取关联的 Part
            part = getattr(instance, 'part', None)
            if part:
                self.notify_low_stock(part)

        # ---- 采购订单状态变更 ----
        elif 'purchase_order' in event_name or 'po_' in event_name:
            if not self.get_setting('DINGTALK_NOTIFY_PO_STATUS'):
                return
            instance = kwargs.get('instance', None)
            po_number = getattr(instance, 'reference', getattr(instance, 'name', '未知')) if instance else '未知'
            status = getattr(instance, 'status_string', str(getattr(instance, 'status', '未知'))) if instance else '未知'

            text = (
                f"## 📦 采购订单状态变更\n\n"
                f"**订单号**: {po_number}\n\n"
                f"**新状态**: {status}\n\n"
                f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            self.send_dingtalk("采购订单状态变更", text)

        # ---- 销售订单状态变更 ----
        elif 'sales_order' in event_name or 'so_' in event_name:
            if not self.get_setting('DINGTALK_NOTIFY_SO_STATUS'):
                return
            instance = kwargs.get('instance', None)
            so_number = getattr(instance, 'reference', getattr(instance, 'name', '未知')) if instance else '未知'
            status = getattr(instance, 'status_string', str(getattr(instance, 'status', '未知'))) if instance else '未知'

            text = (
                f"## 🛒 销售订单状态变更\n\n"
                f"**订单号**: {so_number}\n\n"
                f"**新状态**: {status}\n\n"
                f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            self.send_dingtalk("销售订单状态变更", text)

        # ---- 生产工单 ----
        elif 'build' in event_name:
            if not self.get_setting('DINGTALK_NOTIFY_BUILD'):
                return
            instance = kwargs.get('instance', None)
            build_number = getattr(instance, 'reference', getattr(instance, 'name', '未知')) if instance else '未知'
            status = getattr(instance, 'status_string', str(getattr(instance, 'status', '未知'))) if instance else '未知'

            text = (
                f"## 🔧 生产工单状态变更\n\n"
                f"**工单号**: {build_number}\n\n"
                f"**新状态**: {status}\n\n"
                f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            self.send_dingtalk("生产工单状态变更", text)

        # ---- 新部件创建 ----
        elif 'part' in event_name and ('created' in event_name or 'new' in event_name):
            if not self.get_setting('DINGTALK_NOTIFY_PART_NEW'):
                return
            instance = kwargs.get('instance', None)
            part_name = getattr(instance, 'name', '未知') if instance else '未知'
            part_ipn = getattr(instance, 'IPN', '') if instance else ''

            text = (
                f"## 🆕 新部件创建\n\n"
                f"**名称**: {part_name}\n\n"
                f"**编号**: {part_ipn}\n\n"
                f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            self.send_dingtalk("新部件创建", text)
