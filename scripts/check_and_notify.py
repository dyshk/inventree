#!/usr/bin/env python
"""
InvenTree 通用库存预警脚本
- 扫描所有 active 且设置了 minimum_stock 的部件
- 库存低于阈值 → 发送钉钉告警
- 同一部件 24 小时内只告警一次（冷却机制）

用法:
  手动扫描（立即告警，忽略冷却）:
    docker exec inventree-server python /home/inventree/data/scripts/check_and_notify.py --scan

  定时检查（受冷却限制，24h 内不重复告警）:
    docker exec inventree-server python /home/inventree/data/scripts/check_and_notify.py

  强制告警（等价 --scan）:
    docker exec inventree-server python /home/inventree/data/scripts/check_and_notify.py --force

  仅扫描不发送（查看库存状态）:
    docker exec inventree-server python /home/inventree/data/scripts/check_and_notify.py --dry-run
定时: 创建 Windows 任务计划调用 run_check.bat
"""
import os, sys, json, argparse
from pathlib import Path

# ---- Django 初始化 ----
sys.path.insert(0, '/home/inventree/src/backend/InvenTree')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'InvenTree.settings')
import django; django.setup()

from django.apps import apps
from django.db.models import Sum
from datetime import datetime, timedelta
import requests, time, hmac, hashlib, base64, urllib.parse

Part = apps.get_model('part', 'Part')
StockItem = apps.get_model('stock', 'StockItem')

# ============ 配置（从环境变量读取，兼容 .env 中的 DINGTALK_WEBHOOK / DINGTALK_SECRET）============
DINGTALK_WEBHOOK = os.environ.get('DINGTALK_WEBHOOK', '').strip()
DINGTALK_SECRET  = os.environ.get('DINGTALK_SECRET', '').strip()
INVENTREE_URL    = os.environ.get('INVENTREE_SITE_URL', 'http://localhost').strip('"')

# 冷却文件：记录每个部件上次告警时间
COOLDOWN_FILE = Path('/home/inventree/data/plugins/_alert_cooldown.json')
COOLDOWN_HOURS = 24   # 同一部件 24 小时内只告警一次


def send_dingtalk(title, text):
    if not DINGTALK_WEBHOOK:
        print('❌ 未配置 DINGTALK_WEBHOOK，请在 .env 中设置后重启容器')
        return {'errcode': -1, 'errmsg': 'webhook not configured'}
    timestamp = str(round(time.time() * 1000))
    sign_src = f'{timestamp}\n{DINGTALK_SECRET}'
    sign = urllib.parse.quote_plus(base64.b64encode(
        hmac.new(DINGTALK_SECRET.encode(), sign_src.encode(), hashlib.sha256).digest()
    ))
    url = f'{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}'
    r = requests.post(url,
                      json={'msgtype': 'markdown', 'markdown': {'title': title, 'text': text}},
                      headers={'Content-Type': 'application/json'}, timeout=10)
    return r.json()


def load_cooldown():
    if COOLDOWN_FILE.exists():
        try:
            return json.loads(COOLDOWN_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_cooldown(data):
    COOLDOWN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def scan():
    """扫描所有部件，返回库存不足的列表"""
    result = []
    for part in Part.objects.filter(active=True, virtual=False, minimum_stock__gt=0):
        total = StockItem.objects.filter(part=part).aggregate(total=Sum('quantity'))['total'] or 0
        if total < part.minimum_stock:
            result.append({
                'id': part.pk,
                'name': part.name,
                'IPN': part.IPN or '',
                'current': float(total),
                'minimum': float(part.minimum_stock),
                'shortage': float(part.minimum_stock - total),
            })
    return result


def main():
    parser = argparse.ArgumentParser(description='InvenTree 库存不足 → 钉钉告警')
    parser.add_argument('--scan', action='store_true', help='手动扫描：立即扫描并发送告警，忽略冷却')
    parser.add_argument('--force', action='store_true', help='等价 --scan，忽略冷却强制告警')
    parser.add_argument('--dry-run', action='store_true', help='仅扫描并显示结果，不发送钉钉')
    args = parser.parse_args()

    # --scan 和 --force 等效，都忽略冷却
    skip_cooldown = args.scan or args.force

    now = datetime.now()
    print(f'[{now:%Y-%m-%d %H:%M:%S}] 开始库存扫描 ...')

    # 1. 扫描
    low = scan()
    print(f'  共 {low.__len__()} 个部件库存不足')

    if not low:
        print('✅ 全部库存充足，无需告警。')
        return

    # --dry-run: 仅显示，不发送
    if args.dry_run:
        print('  （--dry-run 模式：不发送钉钉）')
        return

    # 2. 冷却过滤
    cooldown = load_cooldown()
    to_notify = []
    skipped = []

    for item in low:
        key = str(item['id'])
        last = cooldown.get(key)
        if skip_cooldown or not last:
            to_notify.append(item)
            continue
        last_time = datetime.fromisoformat(last)
        if (now - last_time) >= timedelta(hours=COOLDOWN_HOURS):
            to_notify.append(item)
        else:
            remain = (last_time + timedelta(hours=COOLDOWN_HOURS) - now).total_seconds() / 3600
            skipped.append(f"{item['name']} ({remain:.1f}h 后可再告警)")

    if skipped:
        print(f'  冷却中跳过 {len(skipped)} 个:')
        for s in skipped:
            print(f'    ⏭ {s}')

    if not to_notify:
        print('ℹ️ 所有低库存部件均在冷却期内，本次不发送。')
        return

    # 3. 构造消息 + 发送
    print(f'\n  准备发送 {len(to_notify)} 条告警 ...')

    # 钉钉单条消息长度有限，超过 10 个部件拆成多条
    chunk_size = 10
    sent_count = 0
    for i in range(0, len(to_notify), chunk_size):
        chunk = to_notify[i:i + chunk_size]
        lines = [f'## ⚠️ 库存不足告警（{i+1}-{i+len(chunk)}/{len(to_notify)}）\n']
        for idx, p in enumerate(chunk, 1):
            lines.append(f'### {idx}. {p["name"]}')
            if p['IPN']:
                lines.append(f'> **编号**: {p["IPN"]}')
            lines.append(f'> **当前**: {p["current"]}  **最低**: {p["minimum"]}  **缺口**: {p["shortage"]}\n')
        lines.append('---')
        lines.append(f'**时间**: {now:%Y-%m-%d %H:%M:%S}')
        lines.append(f'[系统入口]({INVENTREE_URL})')

        result = send_dingtalk('库存不足告警', '\n'.join(lines))
        if result.get('errcode') == 0:
            sent_count += len(chunk)
            print(f'  ✅ 批次 {i//chunk_size+1} 发送成功 ({len(chunk)} 条)')
        else:
            print(f'  ❌ 批次 {i//chunk_size+1} 发送失败: {result}')

    # 4. 更新冷却时间
    for item in to_notify:
        cooldown[str(item['id'])] = now.isoformat()
    save_cooldown(cooldown)

    print(f'\n✅ 完成: 扫描 {low.__len__()} 个不足部件，成功告警 {sent_count} 个')


if __name__ == '__main__':
    main()
