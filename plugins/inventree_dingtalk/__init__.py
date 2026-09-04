"""
InvenTree DingTalk Notification Plugin
将 InvenTree 的库存告警、订单状态变更等事件推送到钉钉群机器人
"""

from .InvenTreeDingTalk import DingTalkNotificationPlugin

PLUGIN_NAME = "inventree_dingtalk"
PLUGIN_VERSION = "1.0.0"
PLUGIN_AUTHOR = "Admin"
PLUGIN_DESCRIPTION = "钉钉群机器人通知插件 - 库存告警/订单变更/部件创建等事件实时推送"
PLUGIN_LICENSE = "MIT"
PLUGIN_METHODS = []
